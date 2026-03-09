#!/usr/bin/env python3
"""
Telegram-бот для скачивания книг с Литрес.

Пользователь присылает ссылку на книгу -> бот скачивает книгу:
  - Текстовые книги: прямое скачивание FB2 через API Литреса
  - PDF-книги: скриншоты страниц через headless Chrome -> PDF

Команды:
  /start  — приветствие
  /help   — справка
  /logs   — последние 30 строк лога скачивания
  /status — текущий статус (качает/свободен)
"""

import os
import re
import base64
import zipfile
import asyncio
import logging
import shutil
import time as _time
from collections import deque

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
)

from downloader import LitresDownloader

# ─── Настройки (из переменных окружения) ─────────────────────
TELEGRAM_TOKEN = os.environ.get("TELEGRAM_TOKEN", "")
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "")
WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
# Интервал обновления прогресса в чате (секунды)
PROGRESS_INTERVAL = 10
# ──────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("litres-bot")

# Блокировка: одна книга за раз (Chrome жрёт ~300-500 MB RAM)
download_lock = asyncio.Lock()

# Кольцевой буфер логов для команды /logs
log_buffer = deque(maxlen=50)

# Текущий статус для /status и прогресса
current_status = {"active": False, "book": "", "pages": 0, "total": 0, "phase": ""}


class TelegramLogHandler(logging.Handler):
    """Сохраняет логи litres-dl в буфер для команды /logs."""
    def emit(self, record):
        msg = self.format(record)
        log_buffer.append(msg)


# Подключаем handler к логгеру загрузчика
_handler = TelegramLogHandler()
_handler.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
logging.getLogger("litres-dl").addHandler(_handler)


def is_allowed(user_id: int) -> bool:
    if not ALLOWED_USERS:
        return True
    allowed = [int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


def is_litres_url(text: str) -> bool:
    return bool(re.search(r'litres\.ru/', text))


# ── Команды ──────────────────────────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли мне ссылку на книгу с litres.ru и я скачаю её.\n\n"
        "Текстовые книги → FB2\nPDF-книги → PDF (скриншоты)\n\n"
        "Пример:\nhttps://www.litres.ru/book/avtor/nazvanie-knigi-12345/\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/logs — логи скачивания"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь ссылку на книгу с litres.ru → получишь файл.\n\n"
        "Текстовые книги (or4) → FB2 (быстро, ~10 сек)\n"
        "PDF-книги (or3) → PDF (скриншоты, ~2.5 сек/стр.)\n\n"
        "Ссылки:\n"
        "- https://www.litres.ru/book/...\n"
        "- https://www.litres.ru/static/or3/view/or.html?...\n\n"
        "/status — текущий статус\n"
        "/logs — последние строки лога"
    )


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает последние строки лога скачивания."""
    if not is_allowed(update.effective_user.id):
        return

    if not log_buffer:
        await update.message.reply_text("Лог пуст.")
        return

    lines = list(log_buffer)[-30:]
    text = "\n".join(lines)
    # Telegram лимит на сообщение: 4096 символов
    if len(text) > 4000:
        text = text[-4000:]
    await update.message.reply_text(f"```\n{text}\n```", parse_mode="Markdown")


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Показывает текущий статус бота."""
    if not is_allowed(update.effective_user.id):
        return

    if not current_status["active"]:
        await update.message.reply_text("Свободен. Жду ссылку на книгу.")
    else:
        book = current_status["book"]
        pages = current_status["pages"]
        total = current_status["total"]
        phase = current_status["phase"]
        total_str = str(total) if total > 0 else "?"
        await update.message.reply_text(
            f"Качаю: {book}\n"
            f"Фаза: {phase}\n"
            f"Страниц: {pages}/{total_str}"
        )


# ── Обработка сообщений ─────────────────────────────────────

async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not is_allowed(user_id):
        await update.message.reply_text("Нет доступа.")
        return

    if not is_litres_url(text):
        await update.message.reply_text(
            "Это не похоже на ссылку с litres.ru.\n"
            "Пришли ссылку на страницу книги."
        )
        return

    if download_lock.locked():
        book = current_status.get("book", "")
        pages = current_status.get("pages", 0)
        await update.message.reply_text(
            f"Уже качаю: {book} ({pages} стр.)\n"
            "Подожди завершения."
        )
        return

    async with download_lock:
        await download_and_send(update, text)


# ── Скачивание с прогрессом ──────────────────────────────────

async def download_and_send(update: Update, book_url: str):
    """Скачивает книгу с обновлением прогресса в чате."""
    status_msg = await update.message.reply_text("Запускаю Chrome...")
    log_buffer.clear()

    os.makedirs(WORK_DIR, exist_ok=True)
    current_status.update(active=True, book="", pages=0, total=0, phase="запуск")

    # Shared dict для передачи прогресса из потока скачивания
    progress = {"pages": 0, "total": 0, "book": "", "phase": "запуск", "done": False, "error": None}

    try:
        loop = asyncio.get_event_loop()

        # Запускаем скачивание в фоновом потоке
        download_future = loop.run_in_executor(
            None,
            lambda: _download_book(book_url, progress)
        )

        # Цикл обновления прогресса пока скачивание идёт
        last_text = ""
        while not download_future.done():
            text = _format_progress(progress)
            if text != last_text:
                try:
                    await status_msg.edit_text(text)
                    last_text = text
                except Exception:
                    pass
                # Обновляем current_status для /status
                current_status.update(
                    book=progress["book"],
                    pages=progress["pages"],
                    total=progress["total"],
                    phase=progress["phase"],
                )
            await asyncio.sleep(PROGRESS_INTERVAL)

        # Получаем результат
        result = download_future.result()

        if result is None:
            error = progress.get("error", "неизвестная ошибка")
            await status_msg.edit_text(f"Ошибка: {error}")
            return

        file_path, book_name, page_count, file_type = result

        if not os.path.isfile(file_path):
            await status_msg.edit_text(f"Ошибка: файл не создан.")
            return

        size_mb = os.path.getsize(file_path) / (1024 * 1024)

        if file_type == "fb2":
            # FB2 — отправляем сразу (обычно маленький файл)
            await status_msg.edit_text(
                f"FB2 готов: {size_mb:.2f} MB\nОтправляю..."
            )
            try:
                with open(file_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=f"{book_name}.fb2",
                        caption=f"{book_name}\nFB2, {size_mb:.2f} MB",
                    )
                await status_msg.edit_text(
                    f"Готово! {book_name}\nFB2, {size_mb:.2f} MB"
                )
            except Exception as send_err:
                logger.error(f"Ошибка отправки FB2: {send_err}")
                await status_msg.edit_text(f"Ошибка отправки: {send_err}")
        else:
            # PDF — может потребоваться сжатие
            await status_msg.edit_text(
                f"Скачано {page_count} стр.\nPDF: {size_mb:.1f} MB\nОтправляю..."
            )

            # Сжатие если >50 MB
            if size_mb > 50:
                await status_msg.edit_text(
                    f"PDF {size_mb:.1f} MB (лимит Telegram 50 MB). Сжимаю..."
                )
                compressed = file_path.replace(".pdf", "_small.pdf")
                compressed_ok = await loop.run_in_executor(
                    None, lambda: _compress_pdf(file_path, compressed)
                )
                if compressed_ok:
                    size_mb2 = os.path.getsize(compressed) / (1024 * 1024)
                    file_path = compressed
                    size_mb = size_mb2
                    await status_msg.edit_text(f"Сжато до {size_mb:.1f} MB. Отправляю...")
                else:
                    logger.warning(f"Ghostscript не сработал, пробую отправить {size_mb:.1f} MB как есть")
                    await status_msg.edit_text(f"Не удалось сжать. Пробую отправить {size_mb:.1f} MB...")

            try:
                with open(file_path, "rb") as f:
                    await update.message.reply_document(
                        document=f,
                        filename=f"{book_name}.pdf",
                        caption=f"{book_name}\n{page_count} стр., {size_mb:.1f} MB",
                    )
                await status_msg.edit_text(
                    f"Готово! {book_name}\n{page_count} стр., {size_mb:.1f} MB"
                )
            except Exception as send_err:
                logger.error(f"Ошибка отправки: {send_err}")
                await status_msg.edit_text(
                    f"Не удалось отправить ({size_mb:.1f} MB).\n"
                    f"Лимит Telegram — 50 MB.\nОшибка: {send_err}"
                )

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {e}")

    finally:
        current_status.update(active=False, book="", pages=0, total=0, phase="")
        _cleanup_work_dir()


def _format_progress(progress):
    """Форматирует строку прогресса для сообщения в чате."""
    phase = progress.get("phase", "")
    book = progress.get("book", "")
    pages = progress.get("pages", 0)
    total = progress.get("total", 0)

    lines = []
    if book:
        lines.append(f"Книга: {book}")

    if phase == "логин":
        lines.append("Авторизация на litres.ru...")
    elif phase == "инфо":
        lines.append("Открываю страницу книги...")
    elif phase == "проверка FB2":
        lines.append("Проверяю доступность FB2...")
    elif phase == "скачивание FB2":
        lines.append("Скачиваю FB2...")
    elif phase == "читалка":
        lines.append("Открываю читалку...")
    elif phase == "скачивание":
        total_str = str(total) if total > 0 else "?"
        pct = f" ({pages * 100 // total}%)" if total > 0 else ""
        bar = ""
        if total > 0:
            filled = pages * 20 // total
            bar = f"\n[{'#' * filled}{'.' * (20 - filled)}]"
        lines.append(f"Скачиваю: {pages}/{total_str}{pct}{bar}")
    elif phase == "pdf":
        lines.append("Собираю PDF...")
    elif phase == "запуск":
        lines.append("Запускаю Chrome...")
    else:
        lines.append(phase or "...")

    return "\n".join(lines)


def _try_download_fb2(book_url, driver, progress):
    """Пытается скачать книгу как FB2 через API Литреса.

    Возвращает (fb2_path, book_name) или None если FB2 недоступен.
    """
    # Извлекаем art_id из URL книги
    art_match = re.search(r'-(\d+)/?$', book_url.rstrip('/'))
    if not art_match:
        logger.info("Не удалось извлечь art_id из URL")
        return None
    art_id = art_match.group(1)

    progress["phase"] = "инфо"
    logger.info(f"Art ID: {art_id}")

    # Открываем страницу книги
    driver.get(book_url)
    _time.sleep(4)

    # Закрываем попапы
    driver.execute_script("""
        var buttons = document.querySelectorAll('button');
        for (var btn of buttons) {
            if (btn.textContent.trim() === 'OK') { btn.click(); break; }
        }
    """)
    _time.sleep(1)

    # Извлекаем название книги из <h1>
    try:
        title = driver.execute_script(
            "var h = document.querySelector('h1'); return h ? h.textContent.trim() : null;"
        )
    except Exception:
        title = None
    book_name = re.sub(r'[<>:"/\\|?*]', '_', title or f"book_{art_id}").strip()[:120]
    progress["book"] = book_name

    # Ищем ссылку на читалку с baseurl (or4 = текстовые книги)
    reader_url = driver.execute_script("""
        // Ищем ссылку «Читать» ведущую на or4 читалку
        var links = document.querySelectorAll('a');
        for (var l of links) {
            if (l.href && l.href.includes('baseurl=') && l.href.includes('/download_book/')) {
                return l.href;
            }
        }
        // Пробуем кнопку «Читать»
        for (var l of links) {
            var text = l.textContent.trim();
            if ((text === 'Читать' || text === 'Читать онлайн') && l.href) {
                return l.href;
            }
        }
        return null;
    """)
    logger.info(f"Reader URL: {reader_url}")

    if not reader_url:
        logger.info("Ссылка на читалку не найдена")
        return None

    # Извлекаем baseurl для API скачивания
    baseurl_match = re.search(r'baseurl=(/download_book/\d+/\d+/)', reader_url)
    if not baseurl_match:
        logger.info("baseurl не найден в ссылке читалки")
        return None

    baseurl = baseurl_match.group(1)
    logger.info(f"Baseurl: {baseurl}")

    # Проверяем доступность FB2 через fetch (куки отправляются автоматически)
    progress["phase"] = "проверка FB2"
    try:
        check = driver.execute_async_script(f"""
            var callback = arguments[arguments.length - 1];
            fetch('{baseurl}fb2', {{credentials: 'include'}})
            .then(function(resp) {{
                callback({{
                    status: resp.status,
                    type: resp.headers.get('content-type'),
                    size: parseInt(resp.headers.get('content-length') || '0')
                }});
            }}).catch(function(e) {{
                callback({{error: e.message}});
            }});
        """)
        logger.info(f"FB2 check: {check}")
    except Exception as e:
        logger.info(f"FB2 check failed: {e}")
        return None

    if not check or check.get("error") or check.get("status") != 200:
        logger.info("FB2 недоступен для этой книги")
        return None

    # Скачиваем FB2 через fetch + FileReader → base64
    progress["phase"] = "скачивание FB2"
    logger.info("Скачиваю FB2...")
    try:
        b64data = driver.execute_async_script(f"""
            var callback = arguments[arguments.length - 1];
            fetch('{baseurl}fb2', {{credentials: 'include'}})
            .then(function(r) {{ return r.blob(); }})
            .then(function(blob) {{
                var reader = new FileReader();
                reader.onloadend = function() {{
                    callback(reader.result.split(',')[1]);
                }};
                reader.readAsDataURL(blob);
            }}).catch(function(e) {{
                callback(null);
            }});
        """)
    except Exception as e:
        logger.error(f"Ошибка скачивания FB2: {e}")
        return None

    if not b64data:
        logger.info("Не удалось получить данные FB2")
        return None

    data = base64.b64decode(b64data)
    logger.info(f"Получено {len(data)} байт")

    # API возвращает ZIP с FB2 внутри — распаковываем
    os.makedirs(WORK_DIR, exist_ok=True)
    zip_path = os.path.join(WORK_DIR, f"{book_name}.fb2.zip")
    with open(zip_path, "wb") as f:
        f.write(data)

    fb2_path = os.path.join(WORK_DIR, f"{book_name}.fb2")
    try:
        with zipfile.ZipFile(zip_path, 'r') as zf:
            # Ищем .fb2 файл внутри архива
            fb2_names = [n for n in zf.namelist() if n.lower().endswith('.fb2')]
            if fb2_names:
                with zf.open(fb2_names[0]) as src, open(fb2_path, 'wb') as dst:
                    dst.write(src.read())
                logger.info(f"Распакован FB2: {fb2_path}")
            else:
                # Может быть просто FB2 без ZIP-обёртки
                logger.info("В архиве нет .fb2 файла, пробуем как сырой FB2")
                os.rename(zip_path, fb2_path)
    except zipfile.BadZipFile:
        # Не ZIP — значит это сырой FB2
        logger.info("Не ZIP-архив, сохраняю как FB2 напрямую")
        os.rename(zip_path, fb2_path)

    # Очищаем ZIP если остался
    if os.path.isfile(zip_path):
        os.remove(zip_path)

    if os.path.isfile(fb2_path) and os.path.getsize(fb2_path) > 100:
        size_kb = os.path.getsize(fb2_path) / 1024
        logger.info(f"FB2 готов: {size_kb:.0f} KB")
        progress["done"] = True
        return fb2_path, book_name
    else:
        logger.info("FB2 файл пуст или не создан")
        return None


def _download_book(book_url, progress):
    """Синхронная обёртка скачивания с обновлением progress dict.

    Сначала пробует скачать FB2 (текстовые книги), при неудаче — PDF (скриншоты).
    Возвращает (file_path, book_name, page_count, file_type) или None.
    """
    downloader = None
    try:
        progress["phase"] = "логин"
        downloader = LitresDownloader(headless=True)
        downloader.login()

        # Для прямых ссылок на or3 читалку — сразу качаем PDF
        is_direct_or3 = "/static/or3/" in book_url

        # Пробуем FB2 (если это не прямая ссылка на or3)
        if not is_direct_or3:
            fb2_result = _try_download_fb2(book_url, downloader.driver, progress)
            if fb2_result:
                fb2_path, book_name = fb2_result
                return fb2_path, book_name, 0, "fb2"

        # FB2 не удалось — качаем PDF через скриншоты
        logger.info("FB2 недоступен, переключаюсь на PDF (скриншоты)")

        if is_direct_or3:
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(book_url)
            params = parse_qs(parsed.query)
            bname = params.get("bname", ["book"])[0]
            try:
                bname = unquote(unquote(bname))
            except Exception:
                bname = "book"
            book_name = re.sub(r'[<>:"/\\|?*]', '_', bname).strip()[:120] or "book"
            total_pages = 0

            progress.update(phase="читалка", book=book_name)
            downloader.driver.get(book_url)
            _time.sleep(8)
        else:
            progress["phase"] = "инфо"
            title, total_pages = downloader.get_book_info(book_url)
            book_name = re.sub(r'[<>:"/\\|?*]', '_', title).strip()[:120] or "book"

            progress.update(phase="читалка", book=book_name, total=total_pages)

            if not downloader.click_read_button():
                progress["error"] = "Кнопка «Читать» не найдена"
                return None

        output_dir = os.path.join(WORK_DIR, book_name)
        pdf_path = os.path.join(WORK_DIR, f"{book_name}.pdf")

        # Устанавливаем callback для прогресса
        progress["phase"] = "скачивание"
        downloader.on_page_downloaded = lambda p, t: progress.update(pages=p, total=t or progress["total"])

        count = downloader.download_book(total_pages, output_dir)

        if count == 0:
            progress["error"] = "Не удалось скачать ни одной страницы"
            return None

        progress["phase"] = "pdf"
        if not downloader.create_pdf(output_dir, pdf_path):
            progress["error"] = "Ошибка создания PDF"
            return None

        shutil.rmtree(output_dir, ignore_errors=True)
        progress["done"] = True

        return pdf_path, book_name, count, "pdf"

    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}", exc_info=True)
        progress["error"] = str(e)[:200]
        return None
    finally:
        if downloader and downloader.driver:
            try:
                downloader.driver.quit()
            except Exception:
                pass


def _compress_pdf(input_path, output_path):
    """Сжимает PDF через ghostscript. Пробует /ebook (150 dpi), потом /screen (72 dpi)."""
    import subprocess

    presets = [
        ("/ebook", "150 dpi"),
        ("/screen", "72 dpi"),
    ]

    for preset, desc in presets:
        logger.info(f"Сжимаю PDF через ghostscript ({desc})...")
        try:
            result = subprocess.run([
                "gs", "-sDEVICE=pdfwrite",
                "-dCompatibilityLevel=1.4",
                f"-dPDFSETTINGS={preset}",
                "-dNOPAUSE", "-dBATCH", "-dQUIET",
                f"-sOutputFile={output_path}",
                input_path
            ], capture_output=True, text=True, timeout=300)

            if result.returncode != 0:
                logger.error(f"Ghostscript ({desc}) returncode={result.returncode}: {result.stderr[:200]}")
                continue

            size_mb = os.path.getsize(output_path) / (1024 * 1024)
            logger.info(f"Сжато ({desc}): {size_mb:.1f} MB")

            if size_mb <= 50:
                return True
            else:
                logger.info(f"Всё ещё > 50 MB, пробую агрессивнее...")
                continue

        except Exception as e:
            logger.error(f"Ghostscript ошибка ({desc}): {e}")
            continue

    return os.path.isfile(output_path)


def _cleanup_work_dir():
    """Удаляет всё из рабочей директории."""
    if not os.path.isdir(WORK_DIR):
        return
    for entry in os.listdir(WORK_DIR):
        path = os.path.join(WORK_DIR, entry)
        try:
            if os.path.isdir(path):
                shutil.rmtree(path, ignore_errors=True)
            else:
                os.remove(path)
        except OSError:
            pass
    logger.info("Рабочая директория очищена")


def main():
    if not TELEGRAM_TOKEN:
        print("Установите переменную TELEGRAM_TOKEN!")
        print("Пример: export TELEGRAM_TOKEN='123456:ABC-DEF...'")
        return

    logger.info("Запускаю бота...")
    app = Application.builder().token(TELEGRAM_TOKEN).build()

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
