# Litres Book Downloader

Скачивает купленные книги с [litres.ru](https://www.litres.ru) в PDF.
Работает через Selenium + headless Chrome. Есть CLI и Telegram-бот.

## Возможности

- Автоматический логин на litres.ru
- Скачивание всех страниц книги (включая PDF-книги в or3 reader)
- Сборка в PDF с оптимальным размером (JPEG, ~90 MB на 256 стр.)
- Telegram-бот: пришли ссылку — получи PDF
- Resume: при повторном запуске скачанные страницы пропускаются
- Сжатие больших PDF через ghostscript

## Быстрый старт (CLI)

```bash
pip3 install -r requirements.txt

# Создать .env с учётными данными
echo 'LITRES_LOGIN=your@email.com' > .env
echo 'LITRES_PASSWORD=yourpassword' >> .env

# Скачать книгу
python3 litres-downloader.py "https://www.litres.ru/book/avtor/nazvanie-12345/"
```

Скрипт откроет Chrome, залогинится, скачает все страницы и соберёт PDF.

## Telegram-бот

```bash
export TELEGRAM_TOKEN='...'
export LITRES_LOGIN='...'
export LITRES_PASSWORD='...'
python3 bot.py
```

Отправьте боту ссылку на книгу — он вернёт PDF.

## Структура проекта

| Файл | Назначение |
|---|---|
| `downloader.py` | Ядро: логин, скачивание страниц, сборка PDF |
| `litres-downloader.py` | CLI-интерфейс |
| `bot.py` | Telegram-бот |
| `litres-bot.service` | systemd unit для сервера |
| `CLAUDE.md` | Документация для разработчика |

## Требования

- Python 3.10+
- Google Chrome (snap-версия Chromium не работает в headless на серверах)
- ghostscript (опционально, для сжатия PDF >50 MB)

## Переменные окружения

| Переменная | Описание |
|---|---|
| `LITRES_LOGIN` | Email аккаунта litres.ru |
| `LITRES_PASSWORD` | Пароль |
| `TELEGRAM_TOKEN` | Токен бота от @BotFather |
| `ALLOWED_USERS` | Telegram user IDs через запятую (пустой = все) |
| `CHROME_BINARY` | Путь к Chrome (если нестандартный) |
| `CHROMEDRIVER_PATH` | Путь к chromedriver (если нестандартный) |
