# Litres Book Downloader

Скачивает купленные книги с [litres.ru](https://www.litres.ru) через Selenium + headless Chrome.
Есть CLI и Telegram-бот.

## Возможности

- **Текстовые книги (or4)** → FB2 (прямое скачивание через API, ~10 сек)
- **PDF-книги (or3)** → PDF (скриншоты страниц, ~2.5 сек/стр.)
- Автоматический логин на litres.ru
- Telegram-бот: пришли ссылку — получи файл
- Resume: при повторном запуске скачанные страницы пропускаются (PDF)
- Сжатие больших PDF через ghostscript (лимит Telegram — 50 MB)
- Управление памятью Chrome: cleanup DOM для длинных книг (240+ стр.)

## Быстрый старт (CLI)

```bash
pip3 install -r requirements.txt

# Создать .env с учётными данными
cp .env.example .env
# Отредактировать .env

# Скачать книгу
python3 litres-downloader.py "https://www.litres.ru/book/avtor/nazvanie-12345/"
```

Скрипт откроет Chrome, залогинится, определит тип книги и скачает в нужном формате.

## Telegram-бот

```bash
export TELEGRAM_TOKEN='...'
export LITRES_LOGIN='...'
export LITRES_PASSWORD='...'
python3 bot.py
```

Отправьте боту ссылку на книгу — он автоматически определит формат и вернёт FB2 или PDF.

Команды бота:
- `/start` — приветствие
- `/help` — справка по форматам
- `/status` — текущий статус (качает/свободен)
- `/logs` — последние строки лога скачивания

## Как это работает

### Текстовые книги → FB2
1. Логин через Selenium
2. Открывает страницу книги, находит ссылку на читалку (or4)
3. Извлекает `baseurl` из URL читалки
4. Скачивает FB2 через `fetch()` в контексте браузера (куки отправляются автоматически)
5. Распаковывает ZIP → FB2

### PDF-книги → PDF
1. Логин через Selenium
2. Открывает читалку or3 (встроенный PDF-ридер Литреса)
3. Скроллит к каждой странице, ждёт загрузки изображения
4. Извлекает через canvas trick: `drawImage` → `toDataURL('image/jpeg')`
5. Собирает PDF через img2pdf

## Структура проекта

| Файл | Назначение |
|---|---|
| `downloader.py` | Ядро: логин, скачивание страниц, сборка PDF |
| `litres-downloader.py` | CLI-интерфейс |
| `bot.py` | Telegram-бот (FB2 + PDF, прогресс, сжатие) |
| `litres-bot.service` | systemd unit для сервера |
| `gen_status.py` | Генератор страницы мониторинга сервера |

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

## Деплой на сервер

```bash
# Обновление кода
rsync -avz --exclude='chrome_profile' --exclude='__pycache__' --exclude='*.pdf' \
  --exclude='downloads' --exclude='.git' --exclude='venv' \
  ./ root@YOUR_SERVER:/var/www/litres-bot/

ssh root@YOUR_SERVER "systemctl restart litres-bot"
```

## Известные ограничения

- **1 книга за раз** — Chrome в headless жрёт ~300-500 MB RAM
- **Telegram лимит 50 MB** — большие PDF сжимаются через ghostscript
- **Snap Chromium не работает** на серверах (sandbox конфликт) — нужен Google Chrome
- **Литрес может менять UI** — селекторы логина и читалки могут сломаться
