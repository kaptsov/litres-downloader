#!/usr/bin/env python3
"""
Telegram-бот для скачивания книг с Литрес.

Пользователь присылает ссылку на книгу -> бот скачивает все страницы
через headless Chrome, собирает в PDF и отправляет файл.

Команды:
  /start  — приветствие
  /help   — справка
  /logs   — последние 30 строк лога скачивания
  /status — текущий статус (качает/свободен)
"""

import os
import re
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
        "Привет! Пришли мне ссылку на книгу с litres.ru и я скачаю её в PDF.\n\n"
        "Пример:\nhttps://www.litres.ru/book/avtor/nazvanie-knigi-12345/\n\n"
        "Команды:\n"
        "/status — текущий статус\n"
        "/logs — логи скачивания"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь ссылку на книгу с litres.ru -> получишь PDF.\n\n"
        "Поддерживаются ссылки вида:\n"
        "- https://www.litres.ru/book/...\n"
        "- https://www.litres.ru/static/or3/view/or.html?...\n\n"
        "~2.5 сек/страница. Книга в 256 стр. ~ 10 мин.\n\n"
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

        pdf_path, book_name, page_count = result

        if not os.path.isfile(pdf_path):
            await status_msg.edit_text("Ошибка: PDF не создан.")
            return

        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        await status_msg.edit_text(
            f"Скачано {page_count} стр.\nPDF: {size_mb:.1f} MB\nОтправляю..."
        )

        # Сжатие если >50 MB
        if size_mb > 50:
            await status_msg.edit_text(
                f"PDF {size_mb:.1f} MB (лимит Telegram 50 MB). Сжимаю..."
            )
            compressed = pdf_path.replace(".pdf", "_small.pdf")
            compressed_ok = await loop.run_in_executor(
                None, lambda: _compress_pdf(pdf_path, compressed)
            )
            if compressed_ok:
                size_mb2 = os.path.getsize(compressed) / (1024 * 1024)
                if size_mb2 <= 50:
                    pdf_path = compressed
                    size_mb = size_mb2
                    await status_msg.edit_text(f"Сжато до {size_mb:.1f} MB. Отправляю...")
                else:
                    await status_msg.edit_text(
                        f"После сжатия {size_mb2:.1f} MB — слишком большой."
                    )
                    return
            else:
                await status_msg.edit_text(f"Не удалось сжать ({size_mb:.1f} MB).")
                return

        # Отправляем PDF
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{book_name}.pdf",
                caption=f"{book_name}\n{page_count} стр., {size_mb:.1f} MB",
            )

        await status_msg.edit_text(
            f"Готово! {book_name}\n{page_count} стр., {size_mb:.1f} MB"
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


def _download_book(book_url, progress):
    """Синхронная обёртка скачивания с обновлением progress dict."""
    downloader = None
    try:
        progress["phase"] = "логин"
        downloader = LitresDownloader(headless=True)
        downloader.login()

        if "/static/or3/" in book_url:
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

        return pdf_path, book_name, count

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
    """Сжимает PDF через ghostscript (/ebook preset, ~150 dpi)."""
    import subprocess
    try:
        result = subprocess.run([
            "gs", "-sDEVICE=pdfwrite",
            "-dCompatibilityLevel=1.4",
            "-dPDFSETTINGS=/ebook",
            "-dNOPAUSE", "-dBATCH", "-dQUIET",
            f"-sOutputFile={output_path}",
            input_path
        ], capture_output=True, text=True, timeout=300)
        return result.returncode == 0
    except Exception as e:
        logger.error(f"Ghostscript ошибка: {e}")
        return False


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
