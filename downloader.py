#!/usr/bin/env python3
"""
Модуль скачивания книг с litres.ru.

Используется как из CLI (litres-downloader.py), так и из Telegram-бота (bot.py).

Принцип работы:
  1. Selenium открывает Chrome и логинится на litres.ru
  2. Переходит на страницу книги → извлекает название и кол-во страниц
  3. Нажимает «Читать» → открывается встроенная читалка or3
  4. Читалка грузит страницы как <div id="p_N"><img src="..."></div> с lazy-loading
  5. Скрипт последовательно скроллит к каждой странице, ждёт загрузки img,
     извлекает изображение через canvas → base64 → JPEG
  6. Собирает все JPEG в один PDF через img2pdf
"""

import os
import re
import time
import logging
import img2pdf

from io import BytesIO
from base64 import b64decode

from PIL import Image, UnidentifiedImageError
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import (
    NoSuchElementException,
    NoAlertPresentException,
    JavascriptException,
)

# ─── Настройки (переопределяются через переменные окружения) ──
LITRES_LOGIN = os.environ.get("LITRES_LOGIN", "")
LITRES_PASSWORD = os.environ.get("LITRES_PASSWORD", "")
PAGE_DELAY = 1.5       # пауза между скачиванием страниц (сек)
LOAD_TIMEOUT = 20      # макс. ожидание загрузки одной картинки (сек)
MAX_RETRIES = 3        # попыток скачать одну страницу перед пропуском
# ──────────────────────────────────────────────────────────────

logger = logging.getLogger("litres-dl")


class LitresDownloader:
    """Скачивает книгу с litres.ru через Selenium + headless Chrome."""

    def __init__(self, headless=False):
        self.driver = None
        self.headless = headless
        # Callback для отчёта о прогрессе: on_page_downloaded(скачано, всего)
        self.on_page_downloaded = None
        self._start_browser()

    def _start_browser(self):
        """Запускает Chrome. На сервере — headless, локально — с окном.

        Selenium 4.10+ автоматически скачивает chromedriver.
        Для серверного chromium можно задать CHROME_BINARY и CHROMEDRIVER_PATH.
        """
        opts = Options()
        opts.add_argument("--no-sandbox")
        opts.add_argument("--disable-dev-shm-usage")
        opts.add_argument("--disable-gpu")
        opts.add_argument("--window-size=1280,900")
        # Экономия RAM на серверах с ≤1 GB
        opts.add_argument("--disable-extensions")
        opts.add_argument("--disable-background-networking")
        opts.add_argument("--disable-default-apps")
        opts.add_argument("--disable-sync")
        opts.add_argument("--disable-translate")
        opts.add_argument("--disable-features=TranslateUI,PasswordLeakDetection")
        opts.add_argument("--js-flags=--max-old-space-size=256")
        opts.add_argument("--renderer-process-limit=1")

        # Отключаем Google Password Manager (попап "Change your password")
        opts.add_experimental_option("prefs", {
            "credentials_enable_service": False,
            "profile.password_manager_enabled": False,
            "profile.password_manager_leak_detection": False,
        })

        if self.headless:
            opts.add_argument("--headless=new")

        # Профиль Chrome сохраняет куки между запусками — не нужно логиниться каждый раз
        profile_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "chrome_profile")
        opts.add_argument(f"--user-data-dir={profile_dir}")

        # На сервере может быть нестандартный путь к Chrome/Chromium
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

    def close_popup(self):
        """Закрывает любые попапы: browser alert, модалки, cookie consent."""
        # 1. Browser alert (например "Change your password")
        try:
            alert = self.driver.switch_to.alert
            logger.info(f"Попап (alert): {alert.text}")
            alert.accept()
            time.sleep(1)
            return
        except NoAlertPresentException:
            pass

        # 2. DOM-модалки: кнопки OK, Принять, Закрыть в диалогах/оверлеях
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

    # ── Авторизация ──────────────────────────────────────────

    def login(self):
        """Двухшаговый логин: email → пароль.

        Литрес использует SPA (Next.js). Форма логина:
          Шаг 1: input[name="email"] + button[type="submit"] («Продолжить»)
          Шаг 2: input[type="password"] + button[type="submit"] («Войти»)

        Если профиль Chrome сохранён, куки уже есть и логин пропускается.
        В headless-режиме при неудаче — бросает RuntimeError.
        В GUI-режиме — просит пользователя войти вручную.
        """
        self.driver.get("https://www.litres.ru/pages/login/")
        time.sleep(3)

        # Редирект на главную = уже залогинен (куки из chrome_profile)
        if "login" not in self.driver.current_url and "auth" not in self.driver.current_url:
            logger.info("Уже авторизован")
            return

        try:
            # Шаг 1: email
            el = self.driver.find_element(By.CSS_SELECTOR, 'input[name="email"]')
            el.clear()
            el.send_keys(LITRES_LOGIN)
            time.sleep(1)
            self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
            time.sleep(3)

            # Шаг 2: пароль
            el = self.driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
            el.clear()
            el.send_keys(LITRES_PASSWORD)
            time.sleep(1)
            self.driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
            time.sleep(5)
            self.close_popup()
            logger.info("Авторизация ОК")
        except Exception as e:
            logger.warning(f"Автологин не удался: {e}")
            if not self.headless:
                input("Нажмите Enter после входа... ")
            else:
                raise RuntimeError(f"Не удалось залогиниться в headless режиме: {e}")

    # ── Страница с информацией о книге ───────────────────────

    def get_book_info(self, book_page_url):
        """Открывает страницу книги на litres.ru, парсит название и кол-во страниц.

        Название берётся из <h1>, кол-во страниц — из текста вида «256 стр.»

        Returns:
            (title: str, total_pages: int)
        """
        logger.info("Открываю страницу книги...")
        self.driver.get(book_page_url)
        time.sleep(5)
        self.close_popup()

        # Название из H1
        title = "book"
        try:
            h1 = self.driver.find_element(By.CSS_SELECTOR, "h1")
            title = h1.text.strip()
        except NoSuchElementException:
            pass

        # Ищем текст вида "256 стр" среди всех элементов (короче 30 символов)
        total_pages = 0
        try:
            text = self.driver.execute_script("""
                var els = document.querySelectorAll('*');
                for (var i = 0; i < els.length; i++) {
                    var t = els[i].textContent.trim();
                    var m = t.match(/(\\d+)\\s*стр/);
                    if (m && t.length < 30) return m[1];
                }
                return '0';
            """)
            total_pages = int(text)
        except Exception:
            pass

        logger.info(f"Книга: {title}")
        logger.info(f"Страниц: {total_pages}")
        return title, total_pages

    def click_read_button(self):
        """Нажимает кнопку «Читать» на странице книги.

        Кнопка открывает читалку or3 (может в той же или новой вкладке).
        Если новая вкладка — переключаемся на неё.

        Returns:
            True если читалка открылась, False если кнопка не найдена.
        """
        logger.info("Ищу кнопку «Читать»...")
        try:
            btn = self.driver.execute_script("""
                var buttons = document.querySelectorAll('button, a');
                for (var i = 0; i < buttons.length; i++) {
                    var txt = buttons[i].textContent.trim();
                    if (txt === 'Читать' || txt === 'Читать онлайн') {
                        return buttons[i];
                    }
                }
                return null;
            """)
            if btn:
                self.close_popup()
                btn.click()
                logger.info("Нажал «Читать»")
                time.sleep(8)
                self.close_popup()

                # Читалка может открыться в новой вкладке
                if len(self.driver.window_handles) > 1:
                    self.driver.switch_to.window(self.driver.window_handles[-1])
                    logger.info("Переключился на вкладку читалки")
                    time.sleep(3)

                return True
            else:
                logger.error("Кнопка «Читать» не найдена")
                return False
        except Exception as e:
            logger.error(f"Ошибка при клике: {e}")
            return False

    # ── Работа с читалкой or3 ────────────────────────────────
    #
    # Структура DOM читалки:
    #   <div id="canvas" class="pdf_canvas">     ← контейнер со скроллом
    #     <div id="pole">                         ← длинная полоса со всеми страницами
    #       <div id="p_0" class="img_page">       ← страница (lazy-loaded)
    #         <img src="/pages/get_pdf_page/...">  ← картинка страницы
    #       <div id="p_1" class="img_page">
    #         <img src="...">
    #       ...
    #
    # Страницы подгружаются по мере скролла (lazy loading, ~3 шт за раз).

    def hide_toolbar(self):
        """Скрывает тулбар читалки, чтобы не мешал скриншотам."""
        try:
            self.driver.execute_script("""
                var tb = document.querySelector('.toolbar');
                if (tb) tb.style.display = 'none';
            """)
        except JavascriptException:
            pass

    def close_popup(self):
        """Закрывает попапы читалки (напр. «Книга закончена»)."""
        try:
            self.driver.execute_script("""
                var overlays = document.querySelectorAll(
                    '[class*="modal"], [class*="popup"], [class*="overlay"], [class*="dialog"]'
                );
                overlays.forEach(function(el) {
                    if (el.offsetWidth > 0) el.style.display = 'none';
                });
                var closes = document.querySelectorAll(
                    '[class*="close"], [aria-label="close"], [class*="dismiss"]'
                );
                closes.forEach(function(el) {
                    try { el.click(); } catch(e) {}
                });
            """)
        except JavascriptException:
            pass

    def scroll_to_first_page(self):
        """Скроллит контейнер #canvas к началу книги."""
        self.driver.execute_script("""
            var canvas = document.getElementById('canvas');
            if (canvas) canvas.scrollTop = 0;
        """)
        time.sleep(2)

    def scroll_to_page_element(self, page_id):
        """Скроллит к div#p_N, триггерит lazy-loading соседних страниц."""
        self.driver.execute_script(f"""
            var el = document.getElementById('{page_id}');
            if (el) {{
                el.scrollIntoView({{block: 'center'}});
            }}
        """)

    def wait_for_page_image(self, page_id, timeout=LOAD_TIMEOUT):
        """Ждёт пока img внутри div#p_N полностью загрузится.

        Проверяет img.complete и img.naturalWidth > 0.
        Если div не существует (no_div), периодически скроллит к нему,
        т.к. элемент мог ещё не создаться в DOM.

        Returns:
            True если картинка загружена, False если таймаут.
        """
        for sec in range(timeout):
            try:
                result = self.driver.execute_script(f"""
                    var div = document.getElementById('{page_id}');
                    if (!div) return 'no_div';
                    var img = div.querySelector('img');
                    if (!img) return 'no_img';
                    if (!img.complete) return 'loading';
                    if (img.naturalWidth === 0) return 'no_width';
                    return 'ok';
                """)
                if result == 'ok':
                    return True
                if result == 'no_div':
                    # Элемент ещё не создан — скроллим чтобы триггернуть lazy-load
                    if sec % 3 == 0:
                        self.scroll_to_page_element(page_id)
            except JavascriptException:
                pass
            time.sleep(1)
        return False

    def _cleanup_page_dom(self, page_id):
        """Заменяет src картинок на 1x1 gif, освобождая память без разрушения DOM."""
        try:
            self.driver.execute_script(f"""
                var EMPTY = 'data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///yH5BAEAAAAALAAAAAABAAEAAAIBRAA7';
                var div = document.getElementById('{page_id}');
                if (div) {{
                    var img = div.querySelector('img');
                    if (img) img.src = EMPTY;
                }}
                // Очищаем картинки старых страниц, кроме последних 3
                var pageNum = parseInt('{page_id}'.replace('p_', ''));
                for (var i = 0; i < pageNum - 3; i++) {{
                    var old = document.getElementById('p_' + i);
                    if (old) {{
                        var oldImg = old.querySelector('img');
                        if (oldImg && oldImg.src !== EMPTY) {{
                            oldImg.src = EMPTY;
                        }}
                    }}
                }}
            """)
            # Принудительный GC через Chrome DevTools Protocol
            try:
                self.driver.execute_cdp_cmd('HeapProfiler.collectGarbage', {})
            except Exception:
                pass
        except Exception:
            pass

    def extract_image(self, page_id):
        """Извлекает изображение страницы через canvas → base64.

        Создаёт временный <canvas>, рисует на нём img, возвращает base64 PNG.
        Это обходит CORS-ограничения на прямое скачивание img.src.

        Returns:
            base64-строка или None при ошибке.
        """
        try:
            b64 = self.driver.execute_script(f"""
                var div = document.getElementById('{page_id}');
                if (!div) return null;
                var img = div.querySelector('img');
                if (!img || !img.complete || img.naturalWidth === 0) return null;

                // Уменьшаем до макс. 1200px по ширине для экономии RAM
                var maxW = 1200;
                var w = img.naturalWidth;
                var h = img.naturalHeight;
                if (w > maxW) {{
                    h = Math.round(h * maxW / w);
                    w = maxW;
                }}

                var canvas = document.createElement('canvas');
                canvas.width = w;
                canvas.height = h;
                var ctx = canvas.getContext('2d');
                ctx.drawImage(img, 0, 0, w, h);
                // JPEG вместо PNG — в ~5 раз меньше base64 строка
                var result = canvas.toDataURL('image/jpeg', 0.80).replace(/^data:image\\/\\w+;base64,/, '');
                canvas.width = 0;
                canvas.height = 0;
                canvas = null;
                ctx = null;
                return result;
            """)
            return b64
        except JavascriptException as e:
            logger.error(f"JS ошибка: {e}")
            return None

    def save_image(self, b64data, filepath):
        """Декодирует base64 и сохраняет как JPEG (quality=85).

        Конвертирует RGBA → RGB (JPEG не поддерживает альфа-канал).
        JPEG вместо PNG даёт ~3-4x меньший размер PDF.

        Returns:
            (width, height) или None при ошибке.
        """
        try:
            img_data = b64decode(b64data)
            img = Image.open(BytesIO(img_data))
            if img.mode in ('RGBA', 'LA', 'P'):
                img = img.convert('RGB')
            filepath = filepath.replace('.png', '.jpg')
            img.save(filepath, 'JPEG', quality=75)
            return img.size
        except Exception as e:
            logger.error(f"Ошибка сохранения: {e}")
            return None

    # ── Скачивание всех страниц ──────────────────────────────

    def download_book(self, total_pages, output_dir):
        """Последовательно скачивает все страницы книги.

        Для каждой страницы p_N:
          1. Скроллит к ней (триггерит lazy-load)
          2. Ждёт полной загрузки img (wait_for_page_image)
          3. Извлекает через canvas → base64 (extract_image)
          4. Сохраняет как JPEG (save_image)

        Пропускает уже скачанные файлы (resume после обрыва).
        Останавливается при 5 отсутствующих div подряд или достижении total_pages.

        Args:
            total_pages: кол-во страниц из инфо книги (0 = неизвестно)
            output_dir: папка для сохранения JPEG-файлов

        Returns:
            Общее кол-во страниц (скачанных + пропущенных).
        """
        os.makedirs(output_dir, exist_ok=True)

        self.hide_toolbar()
        self.scroll_to_first_page()

        downloaded = 0
        skipped = 0
        failed = 0
        consecutive_not_found = 0

        page = 0
        while True:
            page_id = f"p_{page}"
            filename = os.path.join(output_dir, f"page_{page:04d}.jpg")

            # Пропускаем уже скачанные (resume)
            if os.path.isfile(filename) and os.path.getsize(filename) > 1000:
                skipped += 1
                page += 1
                consecutive_not_found = 0
                # Скроллим даже к скачанным — чтобы подгрузились следующие
                self.scroll_to_page_element(page_id)
                time.sleep(0.3)
                continue

            # Скроллим к странице и ждём загрузки
            self.scroll_to_page_element(page_id)
            time.sleep(0.5)

            if not self.wait_for_page_image(page_id, timeout=LOAD_TIMEOUT):
                exists = self.driver.execute_script(
                    f"return !!document.getElementById('{page_id}')"
                )
                if not exists:
                    consecutive_not_found += 1
                    logger.info(f"Стр. {page}: не найдена "
                                f"(подряд: {consecutive_not_found})")

                    if total_pages > 0 and (downloaded + skipped) >= total_pages:
                        logger.info(f"Достигнут лимит: {total_pages} страниц")
                        break

                    if consecutive_not_found >= 5:
                        logger.info("5 отсутствующих подряд — конец книги")
                        break

                    page += 1
                    continue
                else:
                    # Div есть, но img не загрузился — попап или сбой
                    logger.warning(f"Стр. {page}: таймаут, повтор")
                    self.close_popup()
                    self.scroll_to_page_element(page_id)
                    time.sleep(3)
                    if not self.wait_for_page_image(page_id, timeout=10):
                        logger.error(f"Стр. {page}: ПРОПУЩЕНА")
                        failed += 1
                        page += 1
                        continue

            consecutive_not_found = 0

            # Извлекаем и сохраняем
            success = False
            for attempt in range(MAX_RETRIES):
                b64 = self.extract_image(page_id)
                if b64:
                    size = self.save_image(b64, filename)
                    if size:
                        downloaded += 1
                        success = True
                        if downloaded % 10 == 0 or downloaded <= 3:
                            logger.info(f"Стр. {page} OK ({size[0]}x{size[1]}) — "
                                        f"всего: {downloaded}/{downloaded + skipped + failed}")
                        break

                logger.warning(f"Стр. {page}: попытка {attempt + 1}/{MAX_RETRIES}")
                self.scroll_to_page_element(page_id)
                time.sleep(3)

            if not success:
                logger.error(f"Стр. {page}: ПРОПУЩЕНА")
                failed += 1

            # Удаляем img из DOM — иначе Chrome копит память и падает после ~80 стр.
            self._cleanup_page_dom(page_id)

            # Callback прогресса для Telegram-бота
            if self.on_page_downloaded:
                try:
                    self.on_page_downloaded(downloaded + skipped, total_pages)
                except Exception:
                    pass

            page += 1
            time.sleep(PAGE_DELAY)

        logger.info(f"Итого: скачано={downloaded}, было={skipped}, ошибок={failed}")
        return downloaded + skipped

    # ── Сборка PDF ───────────────────────────────────────────

    def create_pdf(self, image_dir, pdf_path):
        """Собирает все JPEG/PNG из папки в один PDF.

        Файлы сортируются по числовому суффиксу (page_0001 < page_0002 < ...).
        Используется img2pdf — он встраивает JPEG без перекодирования.

        Returns:
            True если PDF создан, False если нет файлов.
        """
        files = sorted(
            [f for f in os.listdir(image_dir) if f.endswith((".jpg", ".png"))],
            key=lambda x: [
                int(c) if c.isdigit() else c.lower()
                for c in re.split(r"(\d+)", x)
            ],
        )
        if not files:
            logger.error("Нет файлов для PDF!")
            return False

        full_paths = [os.path.join(image_dir, f) for f in files]
        logger.info(f"Собираю PDF из {len(files)} страниц...")

        with open(pdf_path, "wb") as f:
            f.write(img2pdf.convert(full_paths))

        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        logger.info(f"PDF готов: {pdf_path} ({size_mb:.1f} MB)")
        return True
