#!/usr/bin/env python3
"""
Скачивание текстовых книг с litres.ru и экспорт в FB2.

Работает с читалкой chitat-onlayn (не or3).
Текст + форматирование + иллюстрации → FB2 файл.
"""

import os
import re
import time
import uuid
import logging
import base64
import requests
from io import BytesIO
from urllib.parse import urljoin

from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

logger = logging.getLogger("litres-dl")


class TextBookDownloader:
    """Скачивает текстовую книгу с litres.ru и генерирует FB2."""

    def __init__(self, headless=False):
        self.driver = None
        self.headless = headless
        self.on_page_downloaded = None  # callback(current, total)
        self._start_browser()

    def _start_browser(self):
        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")

        # Отключаем Google Password Manager (попап "Change your password")
        opts.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.password_manager_leak_detection": False,
        })
        opts.add_argument("--disable-features=PasswordLeakDetection")

        if self.headless:
            opts.add_argument("--headless=new")
            opts.add_argument("--disable-extensions")
            opts.add_argument("--disable-background-networking")
            opts.add_argument("--disable-default-apps")
            opts.add_argument("--disable-sync")
            opts.add_argument("--disable-translate")

        chrome_binary = os.environ.get("CHROME_BINARY")
        if chrome_binary:
            opts.binary_location = chrome_binary

        chromedriver_path = os.environ.get("CHROMEDRIVER_PATH")
        if chromedriver_path:
            from selenium.webdriver.chrome.service import Service
            service = Service(chromedriver_path)
            self.driver = webdriver.Chrome(service=service, options=opts)
        else:
            self.driver = webdriver.Chrome(options=opts)

        logger.info("Браузер запущен" + (" (headless)" if self.headless else ""))

    def login(self):
        """Логин на litres.ru (как в downloader.py)."""
        login = os.environ.get("LITRES_LOGIN", "")
        password = os.environ.get("LITRES_PASSWORD", "")

        self.driver.get("https://www.litres.ru/pages/login/")
        time.sleep(3)

        if "login" not in self.driver.current_url and "auth" not in self.driver.current_url:
            logger.info("Уже авторизован")
            return

        logger.info("Авторизация...")
        try:
            email_input = self.driver.find_element(By.CSS_SELECTOR, 'input[name="email"]')
            email_input.clear()
            email_input.send_keys(login)
            self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
            time.sleep(2)

            pwd_input = self.driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            pwd_input.clear()
            pwd_input.send_keys(password)
            self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
            time.sleep(3)

            if "login" in self.driver.current_url or "auth" in self.driver.current_url:
                raise RuntimeError("Не удалось авторизоваться")
            logger.info("Авторизация успешна")

            # Закрываем попап "Change your password" если он появился
            self._close_popups()
        except RuntimeError:
            raise
        except Exception as e:
            raise RuntimeError(f"Ошибка авторизации: {e}")

    def _close_popups(self):
        """Закрывает любые попапы: browser alert, модалки, cookie consent."""
        from selenium.common.exceptions import NoAlertPresentException
        # 1. Browser alert (например "Change your password")
        try:
            alert = self.driver.switch_to.alert
            logger.info(f"Попап (alert): {alert.text}")
            alert.accept()
            time.sleep(1)
            return
        except NoAlertPresentException:
            pass

        # 2. DOM-модалки: кнопки OK, Принять, Закрыть
        try:
            self.driver.execute_script("""
                var buttons = document.querySelectorAll(
                    'button, a[role="button"], input[type="button"], input[type="submit"]'
                );
                var targets = ['ok', 'ок', 'принять', 'закрыть', 'close', 'accept', 'got it'];
                for (var btn of buttons) {
                    var text = btn.textContent.trim().toLowerCase();
                    if (targets.includes(text)) {
                        btn.click();
                        return;
                    }
                }
            """)
        except Exception:
            pass

    def get_book_info(self, book_url):
        """Извлекает информацию о книге."""
        self.driver.get(book_url)
        time.sleep(3)
        self._close_popups()

        title = self.driver.execute_script("""
            var h1 = document.querySelector('h1');
            return h1 ? h1.textContent.trim() : '';
        """) or "Без названия"

        author = self.driver.execute_script("""
            var a = document.querySelector('a[href*="/author/"]');
            return a ? a.textContent.trim() : '';
        """) or "Неизвестный автор"

        return {"title": title, "author": author, "url": book_url}

    def get_total_pages(self, reader_url):
        """Определяет количество страниц в читалке."""
        self.driver.get(reader_url)
        time.sleep(4)

        total = self.driver.execute_script("""
            var links = document.querySelectorAll('a[href*="page="]');
            var maxPage = 1;
            for (var link of links) {
                var match = link.href.match(/page=(\\d+)/);
                if (match) {
                    var p = parseInt(match[1]);
                    if (p > maxPage) maxPage = p;
                }
            }
            return maxPage;
        """)
        return total or 1

    def extract_page_content(self):
        """Извлекает HTML-контент текущей страницы читалки."""
        content = self.driver.execute_script("""
            var container = document.querySelector('[data-testid="readOnline__fontSize--style"]');
            if (!container) {
                // Fallback: ищем по классу
                container = document.querySelector('._43a405f8');
            }
            if (!container) return null;
            return container.innerHTML;
        """)
        return content

    def download_text_book(self, book_url):
        """Скачивает текстовую книгу: все страницы читалки.

        Returns:
            dict с ключами: title, author, pages (list of HTML strings), images (dict id→bytes)
        """
        info = self.get_book_info(book_url)
        logger.info(f"Книга: {info['title']} — {info['author']}")

        reader_url = book_url.rstrip("/") + "/chitat-onlayn/"
        total_pages = self.get_total_pages(reader_url)
        logger.info(f"Страниц в читалке: {total_pages}")

        pages_html = []
        images = {}  # id -> (content_type, base64_data)

        for page_num in range(1, total_pages + 1):
            url = reader_url if page_num == 1 else f"{reader_url}?page={page_num}"
            if page_num > 1:
                self.driver.get(url)
                time.sleep(3)

            html = self.extract_page_content()
            if html:
                # Скачиваем картинки из контента
                html, new_images = self._process_images(html)
                images.update(new_images)
                pages_html.append(html)
                logger.info(f"Страница {page_num}/{total_pages} OK "
                            f"({len(html)} символов, {len(new_images)} картинок)")
            else:
                logger.warning(f"Страница {page_num}/{total_pages}: контент не найден")

            if self.on_page_downloaded:
                try:
                    self.on_page_downloaded(page_num, total_pages)
                except Exception:
                    pass

        return {
            "title": info["title"],
            "author": info["author"],
            "pages": pages_html,
            "images": images,
        }

    def _process_images(self, html):
        """Скачивает картинки из HTML и заменяет src на внутренние ID."""
        images = {}
        img_pattern = re.compile(r'<img\s+[^>]*src="([^"]+)"[^>]*/?>',  re.IGNORECASE)

        def replace_img(match):
            src = match.group(1)
            # Пропускаем внешние картинки (обложки, рекламу и т.п.)
            if "litres.ru/pub/t/" not in src and "litres.ru/pub/c/" not in src:
                return ""  # убираем нерелевантные картинки

            img_id = f"img_{uuid.uuid4().hex[:8]}"
            try:
                # Скачиваем картинку через cookies из Selenium
                cookies = {c["name"]: c["value"] for c in self.driver.get_cookies()}
                resp = requests.get(src, cookies=cookies, timeout=10)
                if resp.status_code == 200:
                    content_type = resp.headers.get("content-type", "image/jpeg")
                    b64 = base64.b64encode(resp.content).decode("ascii")
                    images[img_id] = (content_type, b64)
                    return f'<image l:href="#{img_id}"/>'
            except Exception as e:
                logger.warning(f"Не удалось скачать картинку {src}: {e}")
            return ""

        new_html = img_pattern.sub(replace_img, html)
        return new_html, images

    def close(self):
        if self.driver:
            self.driver.quit()
            self.driver = None


def html_to_fb2_body(html):
    """Конвертирует HTML контент страницы в FB2 XML секции."""
    from html.parser import HTMLParser

    class FB2Converter(HTMLParser):
        def __init__(self):
            super().__init__()
            self.result = []
            self.current_section = []
            self.sections = []
            self.in_content = True
            self.tag_stack = []
            self.skip_tags = {"div", "span", "nav", "ul", "li", "button", "meta",
                              "script", "style", "a", "figure", "figcaption"}
            self.block_tags = {"p", "h1", "h2", "h3", "h4"}

        def handle_starttag(self, tag, attrs):
            attrs_dict = dict(attrs)
            self.tag_stack.append(tag)

            if tag in ("h2", "h3"):
                # Закрываем текущую секцию, начинаем новую
                if self.current_section:
                    self.sections.append("".join(self.current_section))
                    self.current_section = []
                title_text = ""  # будет заполнено в handle_data
                self.current_section.append(f"</section><section><title><p>")

            elif tag == "p":
                self.current_section.append("<p>")

            elif tag == "b" or tag == "strong":
                self.current_section.append("<strong>")

            elif tag in ("i", "em"):
                self.current_section.append("<emphasis>")

            elif tag == "br":
                self.current_section.append("\n")

            elif tag == "image":
                href = attrs_dict.get("l:href", "")
                if href:
                    self.current_section.append(f'<image l:href="{href}"/>')

            elif tag == "img":
                # Уже обработано в _process_images, но на всякий случай
                pass

        def handle_endtag(self, tag):
            if self.tag_stack and self.tag_stack[-1] == tag:
                self.tag_stack.pop()

            if tag in ("h2", "h3"):
                self.current_section.append("</p></title>")

            elif tag == "p":
                self.current_section.append("</p>")

            elif tag == "b" or tag == "strong":
                self.current_section.append("</strong>")

            elif tag in ("i", "em"):
                self.current_section.append("</emphasis>")

        def handle_data(self, data):
            text = data.strip()
            if text:
                # Экранируем XML-спецсимволы
                text = text.replace("&", "&amp;")
                text = text.replace("<", "&lt;")
                text = text.replace(">", "&gt;")
                self.current_section.append(text)

        def get_result(self):
            if self.current_section:
                self.sections.append("".join(self.current_section))
            return self.sections

    converter = FB2Converter()
    converter.feed(html)
    return converter.get_result()


def create_fb2(book_data, output_path):
    """Создаёт FB2 файл из скачанных данных.

    Args:
        book_data: dict от TextBookDownloader.download_text_book()
        output_path: путь для сохранения .fb2 файла
    """
    title = book_data["title"]
    author = book_data["author"]
    images = book_data["images"]

    # Разделяем автора на имя/фамилию
    author_parts = author.split()
    if len(author_parts) >= 2:
        first_name = author_parts[0]
        last_name = author_parts[-1]
        middle_name = " ".join(author_parts[1:-1]) if len(author_parts) > 2 else ""
    else:
        first_name = author
        last_name = ""
        middle_name = ""

    # Собираем контент всех страниц
    all_html = "\n".join(book_data["pages"])

    # Простая конвертация HTML → FB2 body
    body_content = _convert_html_to_fb2(all_html)

    # Собираем FB2
    author_xml = f"<first-name>{_esc(first_name)}</first-name>"
    if middle_name:
        author_xml += f"<middle-name>{_esc(middle_name)}</middle-name>"
    if last_name:
        author_xml += f"<last-name>{_esc(last_name)}</last-name>"

    binary_xml = ""
    for img_id, (content_type, b64_data) in images.items():
        binary_xml += f'<binary id="{img_id}" content-type="{content_type}">{b64_data}</binary>\n'

    fb2 = f"""<?xml version="1.0" encoding="utf-8"?>
<FictionBook xmlns="http://www.gribuser.ru/xml/fictionbook/2.0"
             xmlns:l="http://www.w3.org/1999/xlink">
<description>
  <title-info>
    <genre>science</genre>
    <author>{author_xml}</author>
    <book-title>{_esc(title)}</book-title>
    <lang>ru</lang>
  </title-info>
  <document-info>
    <author><nickname>litres-downloader</nickname></author>
    <program-used>litres-downloader</program-used>
    <id>{uuid.uuid4()}</id>
    <version>1.0</version>
  </document-info>
</description>
<body>
<title><p>{_esc(title)}</p></title>
<section>
{body_content}
</section>
</body>
{binary_xml}
</FictionBook>"""

    with open(output_path, "w", encoding="utf-8") as f:
        f.write(fb2)

    size_mb = os.path.getsize(output_path) / (1024 * 1024)
    logger.info(f"FB2 создан: {output_path} ({size_mb:.1f} MB)")
    return output_path


def _esc(text):
    """Экранирование XML."""
    return (text
            .replace("&", "&amp;")
            .replace("<", "&lt;")
            .replace(">", "&gt;")
            .replace('"', "&quot;"))


def _convert_html_to_fb2(html):
    """Конвертирует HTML в FB2-совместимый XML."""
    import re
    import html as html_module

    # Декодируем HTML entities (&nbsp; &mdash; и т.п.) → Unicode
    html = html_module.unescape(html)
    # Экранируем обратно только XML-обязательные символы
    html = html.replace("&", "&amp;")
    # Но не трогаем уже экранированные теги — восстановим их
    html = html.replace("&amp;lt;", "&lt;")
    html = html.replace("&amp;gt;", "&gt;")
    html = html.replace("&amp;amp;", "&amp;")
    html = html.replace("&amp;quot;", "&quot;")

    # Удаляем style атрибуты
    html = re.sub(r'\s+style="[^"]*"', '', html)
    html = re.sub(r'\s+class="[^"]*"', '', html)
    html = re.sub(r'\s+data-[a-z-]+="[^"]*"', '', html)
    html = re.sub(r'\s+id="[^"]*"', '', html)
    html = re.sub(r'\s+alt="[^"]*"', '', html)

    # Заменяем div'ы на ничто (оставляем содержимое)
    html = re.sub(r'<div[^>]*>', '', html)
    html = re.sub(r'</div>', '', html)
    html = re.sub(r'<span[^>]*>', '', html)
    html = re.sub(r'</span>', '', html)
    html = re.sub(r'<a[^>]*>', '', html)
    html = re.sub(r'</a>', '', html)

    # h2, h3 → section с title
    html = re.sub(r'<h2[^>]*>(.*?)</h2>',
                  r'</section><section><title><p>\1</p></title>', html, flags=re.DOTALL)
    html = re.sub(r'<h3[^>]*>(.*?)</h3>',
                  r'<subtitle>\1</subtitle>', html, flags=re.DOTALL)

    # b/strong → strong (FB2)
    html = re.sub(r'<b\b[^>]*>', '<strong>', html)
    html = re.sub(r'</b>', '</strong>', html)
    # i/em → emphasis (FB2)
    html = re.sub(r'<i\b[^>]*>', '<emphasis>', html)
    html = re.sub(r'</i>', '</emphasis>', html)
    html = re.sub(r'<em\b[^>]*>', '<emphasis>', html)
    html = re.sub(r'</em>', '</emphasis>', html)

    # <br> и <br/> → пробел (иначе слова слипаются)
    html = re.sub(r'<br\s*/?>', ' ', html)

    # image тег (уже обработан _process_images)
    # <image l:href="#img_xxx"/> — оставляем как есть

    # Убираем пустые <p></p>
    html = re.sub(r'<p>\s*</p>', '', html)

    # Убираем начальный </section> если он первый
    html = html.strip()
    if html.startswith('</section>'):
        html = html[len('</section>'):]

    return html


# ── CLI ────────────────────────────────────────────────────────

if __name__ == "__main__":
    import sys
    from dotenv import load_dotenv
    load_dotenv()

    logging.basicConfig(level=logging.INFO, format="%(message)s")

    if len(sys.argv) < 2:
        print("Usage: python3 text_downloader.py <litres_book_url>")
        sys.exit(1)

    book_url = sys.argv[1]
    dl = TextBookDownloader(headless=False)

    try:
        dl.login()
        data = dl.download_text_book(book_url)

        # Имя файла из названия книги
        safe_title = re.sub(r'[^\w\s-]', '', data["title"]).strip()[:80]
        output = f"{safe_title}.fb2"

        create_fb2(data, output)
        print(f"\nГотово: {output}")
    finally:
        dl.close()
