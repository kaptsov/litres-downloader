#!/usr/bin/env python3
"""
CLI для скачивания книг с litres.ru.

Использование:
    python3 litres-downloader.py <URL_страницы_книги>

Пример:
    python3 litres-downloader.py "https://www.litres.ru/book/avtor/nazvanie-12345/"

Требуются переменные окружения LITRES_LOGIN и LITRES_PASSWORD
(или файл .env в корне проекта).
"""

import os
import re
import sys
import shutil
import logging

from downloader import LitresDownloader

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
    stream=sys.stdout,
)


def main():
    if len(sys.argv) > 1:
        url = sys.argv[1]
    else:
        url = input("Введите URL страницы книги на Литрес: ").strip()

    if not url:
        print("URL не указан!")
        sys.exit(1)

    app = LitresDownloader(headless=False)

    try:
        app.login()

        title, total_pages = app.get_book_info(url)
        book_name = re.sub(r'[<>:"/\\|?*]', '_', title).strip()[:120] or "book"

        script_dir = os.path.dirname(os.path.abspath(__file__))
        output_dir = os.path.join(script_dir, book_name)
        pdf_path = os.path.join(script_dir, f"{book_name}.pdf")

        if not app.click_read_button():
            print("Не удалось открыть читалку")
            return

        count = app.download_book(total_pages, output_dir)

        if count > 0:
            app.create_pdf(output_dir, pdf_path)
            try:
                answer = input("Удалить папку с картинками? (y/n): ").strip().lower()
                if answer == "y":
                    shutil.rmtree(output_dir)
            except EOFError:
                pass
        else:
            print("Не удалось скачать ни одной страницы")

    except KeyboardInterrupt:
        print("\nПрервано.")
    finally:
        if app.driver:
            app.driver.quit()


if __name__ == "__main__":
    main()
