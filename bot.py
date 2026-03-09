#!/usr/bin/env python3
"""
Telegram-бот для скачивания книг с Литрес.

Пользователь присылает ссылку на книгу → бот скачивает все страницы
через headless Chrome, собирает в PDF и отправляет файл.

Поддерживает два типа ссылок:
  - Страница книги: https://www.litres.ru/book/avtor/nazvanie-12345/
  - Прямая ссылка на читалку: https://www.litres.ru/static/or3/view/or.html?...

Ограничения:
  - Одновременно качается только одна книга (asyncio.Lock)
  - Telegram лимит на файл: 50 MB (при превышении сжимает через ghostscript)
  - Все временные файлы (картинки, PDF) удаляются после отправки
"""

import os
import re
import asyncio
import logging
import shutil

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
# Telegram user ID тех, кому разрешено пользоваться ботом.
# Пустая строка = доступ для всех. Через запятую: "123456,789012"
ALLOWED_USERS = os.environ.get("ALLOWED_USERS", "")
# Рабочая директория для временных файлов (картинки + PDF)
WORK_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads")
# ──────────────────────────────────────────────────────────────

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger("litres-bot")

# Блокировка: одновременно только одна книга (Chrome жрёт память, на VPS 1 GB RAM)
download_lock = asyncio.Lock()


def is_allowed(user_id: int) -> bool:
    """Проверяет, разрешён ли доступ пользователю."""
    if not ALLOWED_USERS:
        return True
    allowed = [int(x.strip()) for x in ALLOWED_USERS.split(",") if x.strip()]
    return user_id in allowed


def is_litres_url(text: str) -> bool:
    """Проверяет, похожа ли строка на URL с litres.ru."""
    return bool(re.search(r'litres\.ru/', text))


# ── Обработчики команд Telegram ──────────────────────────────

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Пришли мне ссылку на книгу с litres.ru и я скачаю её в PDF.\n\n"
        "Пример ссылки:\n"
        "https://www.litres.ru/book/avtor/nazvanie-knigi-12345/"
    )


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Отправь ссылку на книгу с litres.ru -> получишь PDF.\n\n"
        "Поддерживаются ссылки вида:\n"
        "- https://www.litres.ru/book/...\n"
        "- https://www.litres.ru/static/or3/view/or.html?...\n\n"
        "Скачивание занимает ~1-2 мин на 100 страниц."
    )


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Обработчик текстовых сообщений. Ожидает URL книги."""
    user_id = update.effective_user.id
    text = update.message.text.strip()

    if not is_allowed(user_id):
        await update.message.reply_text("Нет доступа. Обратитесь к администратору.")
        return

    if not is_litres_url(text):
        await update.message.reply_text(
            "Это не похоже на ссылку с litres.ru. "
            "Пришли ссылку на страницу книги."
        )
        return

    # Только одна книга за раз
    if download_lock.locked():
        await update.message.reply_text(
            "Уже качаю другую книгу. Подожди завершения и попробуй снова."
        )
        return

    async with download_lock:
        await download_and_send(update, text)


# ── Основная логика скачивания ───────────────────────────────

async def download_and_send(update: Update, book_url: str):
    """Скачивает книгу, собирает PDF, отправляет пользователю, чистит файлы."""
    status_msg = await update.message.reply_text("Начинаю скачивание...")

    os.makedirs(WORK_DIR, exist_ok=True)
    pdf_path = None

    try:
        # Selenium блокирующий — запускаем в отдельном потоке
        loop = asyncio.get_event_loop()
        result = await loop.run_in_executor(
            None,
            lambda: _download_book(book_url)
        )

        if result is None:
            await status_msg.edit_text("Ошибка: не удалось скачать книгу.")
            return

        pdf_path, book_name, page_count = result

        if not os.path.isfile(pdf_path):
            await status_msg.edit_text("Ошибка: PDF не создан.")
            return

        size_mb = os.path.getsize(pdf_path) / (1024 * 1024)
        await status_msg.edit_text(
            f"Скачано {page_count} стр. PDF: {size_mb:.1f} MB. Отправляю..."
        )

        # Telegram лимит: 50 MB для файлов через Bot API
        if size_mb > 50:
            await status_msg.edit_text(
                f"PDF {size_mb:.1f} MB (лимит Telegram — 50 MB). Сжимаю..."
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
                    await status_msg.edit_text(
                        f"Сжато до {size_mb:.1f} MB. Отправляю..."
                    )
                else:
                    await status_msg.edit_text(
                        f"После сжатия {size_mb2:.1f} MB — всё ещё слишком большой."
                    )
                    return
            else:
                await status_msg.edit_text(
                    f"Не удалось сжать ({size_mb:.1f} MB). Слишком большой для Telegram."
                )
                return

        # Отправляем PDF
        with open(pdf_path, "rb") as f:
            await update.message.reply_document(
                document=f,
                filename=f"{book_name}.pdf",
                caption=f"{book_name}\n{page_count} стр., {size_mb:.1f} MB",
            )

        await status_msg.edit_text("Готово!")

    except Exception as e:
        logger.error(f"Ошибка: {e}", exc_info=True)
        await status_msg.edit_text(f"Ошибка: {e}")

    finally:
        # Удаляем все временные файлы чтобы не забивать диск
        _cleanup_work_dir()


def _download_book(book_url):
    """Синхронная обёртка скачивания (запускается в ThreadPoolExecutor).

    Создаёт headless Chrome, логинится, скачивает страницы, собирает PDF.

    Returns:
        (pdf_path, book_name, page_count) или None при ошибке.
    """
    downloader = None
    try:
        downloader = LitresDownloader(headless=True)
        downloader.login()

        # Два типа URL: прямая ссылка на читалку или страница книги
        if "/static/or3/" in book_url:
            # Прямая ссылка на читалку — название из URL-параметра bname
            from urllib.parse import urlparse, parse_qs, unquote
            parsed = urlparse(book_url)
            params = parse_qs(parsed.query)
            bname = params.get("bname", ["book"])[0]
            try:
                bname = unquote(unquote(bname))
            except Exception:
                bname = "book"
            book_name = re.sub(r'[<>:"/\\|?*]', '_', bname).strip()[:120] or "book"
            total_pages = 0  # кол-во страниц неизвестно

            import time
            downloader.driver.get(book_url)
            time.sleep(8)
        else:
            # Страница книги — берём инфо и нажимаем «Читать»
            title, total_pages = downloader.get_book_info(book_url)
            book_name = re.sub(r'[<>:"/\\|?*]', '_', title).strip()[:120] or "book"

            if not downloader.click_read_button():
                logger.error("Кнопка «Читать» не найдена")
                return None

        output_dir = os.path.join(WORK_DIR, book_name)
        pdf_path = os.path.join(WORK_DIR, f"{book_name}.pdf")

        count = downloader.download_book(total_pages, output_dir)

        if count == 0:
            return None

        if not downloader.create_pdf(output_dir, pdf_path):
            return None

        # Удаляем папку с картинками (PDF уже собран)
        shutil.rmtree(output_dir, ignore_errors=True)

        return pdf_path, book_name, count

    except Exception as e:
        logger.error(f"Ошибка скачивания: {e}", exc_info=True)
        return None
    finally:
        if downloader and downloader.driver:
            try:
                downloader.driver.quit()
            except Exception:
                pass


def _compress_pdf(input_path, output_path):
    """Сжимает PDF через ghostscript (уменьшает до ~150 dpi).

    Используется /ebook preset — хороший баланс качество/размер.
    """
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
    """Удаляет всё содержимое рабочей директории (PDF, картинки, папки)."""
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
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    logger.info("Бот запущен. Ожидаю сообщения...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
