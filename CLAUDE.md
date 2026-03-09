# Litres Book Downloader

Скачивает купленные книги с litres.ru. Текстовые → FB2, PDF → PDF. Selenium + headless Chrome.

## Архитектура

```
litres-downloader.py   — CLI-интерфейс (запуск локально)
bot.py                 — Telegram-бот (запуск на сервере)
downloader.py          — Ядро: логин, скачивание PDF-страниц, сборка PDF
gen_status.py          — Генератор страницы мониторинга сервера
litres-bot.service     — systemd unit для сервера
```

### Вспомогательные скрипты (отладка/эксперименты)

```
download_fb2_v3.py     — Standalone скрипт скачивания FB2 (референс для bot.py)
text_downloader.py     — Парсер текстового ридера or4 → FB2 (superseded прямым API)
screenshot_flow.py     — Скриншоты каждого шага для отладки UI
find_popup.py          — Поиск и диагностика попапов Chrome
inspect_or4.py         — Инспекция DOM ридера or4
inspect_download.py    — Отладка API скачивания
inspect_text_reader.py — Отладка текстового ридера
```

## Два режима скачивания

### 1. FB2 (текстовые книги, or4 reader)
Реализовано в `bot.py` → `_try_download_fb2()`.

1. Открывает страницу книги
2. Находит ссылку на читалку с `baseurl=(/download_book/{art_id}/{hash}/)`
3. Проверяет доступность: `fetch('{baseurl}fb2')` → status 200?
4. Скачивает: `fetch → blob → FileReader → base64` в контексте браузера
5. Декодирует base64, распаковывает ZIP → FB2

Ключевой момент: `fetch()` выполняется в браузере, куки (авторизация) отправляются автоматически.

### 2. PDF (скан-книги, or3 reader)
Реализовано в `downloader.py` → `LitresDownloader`.

1. **Логин** — двухшаговая форма: email → пароль
   - Куки сохраняются в `chrome_profile/`
   - Селекторы: `input[name="email"]`, `input[type="password"]`, `button[type="submit"]`
   - Chrome prefs отключают Password Manager popup

2. **Читалка or3** — встроенный PDF-ридер литреса
   - DOM: `#canvas > #pole > div#p_0.img_page > img[src=...]`
   - Страницы подгружаются lazy (по 3 шт при скролле)
   - Скрипт скроллит к каждой `#p_N`, ждёт `img.complete && naturalWidth > 0`

3. **Извлечение изображения** — canvas trick:
   - `drawImage(img)` → `toDataURL('image/jpeg', 0.80)` при max width 1200px
   - Обходит CORS (напрямую скачать img.src нельзя)

4. **Управление памятью** — `_cleanup_page_dom()`:
   - Заменяет `img.src` на 1x1 transparent GIF (не разрушая DOM)
   - Сохраняет структуру div для lazy-loader
   - `HeapProfiler.collectGarbage` через CDP
   - Позволяет скачивать книги 240+ стр. на VPS с 1 GB RAM

5. **Сборка PDF** — img2pdf (встраивает JPEG без перекодирования)

### Автоопределение формата (bot.py)
`_download_book()` сначала пробует FB2 (`_try_download_fb2`).
Если FB2 недоступен (PDF-книга или ошибка) — переключается на PDF-скриншоты.
Прямые ссылки на or3 (`/static/or3/`) сразу идут в PDF-режим.

## Telegram-бот (bot.py)

- python-telegram-bot (async), polling mode
- `asyncio.Lock` — одна книга за раз (на VPS 1 GB RAM)
- Selenium запускается в `ThreadPoolExecutor` (блокирующий код)
- Прогресс обновляется в чате каждые 10 сек (фазы: логин, проверка FB2, скачивание, pdf)
- При PDF >50 MB — сжатие через ghostscript (`/ebook` → `/screen`)
- Все временные файлы удаляются после отправки

## Мониторинг сервера (gen_status.py)

- Генерирует `index.html` (статика с JS fetch) + `data.json`
- Показывает: диск, RAM, статус бота, содержимое downloads/
- AJAX-обновление каждые 10 сек
- Деплой: `/var/www/server-status/`, nginx → `sweateratops.ru/server-status/`

## Деплой

### Сервер
- **VPS**: FirstVDS, Ubuntu 24.04, 1 GB RAM
- **Путь**: `/var/www/litres-bot/`
- **Сервис**: `systemctl {start|stop|restart|status} litres-bot`
- **Логи**: `journalctl -u litres-bot -f`

### Переменные окружения (в systemd unit)
```
TELEGRAM_TOKEN=...
LITRES_LOGIN=...
LITRES_PASSWORD=...
ALLOWED_USERS=
```

### Обновление кода на сервере
```bash
rsync -avz --exclude='chrome_profile' --exclude='__pycache__' --exclude='*.pdf' \
  --exclude='downloads' --exclude='downloads_fb2' --exclude='.git' --exclude='venv' \
  --exclude='screenshots' --exclude='.DS_Store' \
  ./ root@SERVER:/var/www/litres-bot/

ssh root@SERVER "systemctl restart litres-bot"
```

### Установка с нуля
```bash
# Google Chrome
wget -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install -y /tmp/chrome.deb

# Ghostscript для сжатия PDF
apt install -y ghostscript

# Проект
mkdir -p /var/www/litres-bot
cd /var/www/litres-bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Systemd
cp litres-bot.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now litres-bot
```

## Локальная разработка

```bash
pip3 install -r requirements.txt
cp .env.example .env
# Отредактировать .env

# CLI — скачивание одной книги (с GUI Chrome)
python3 litres-downloader.py "https://www.litres.ru/book/..."

# Бот локально
export TELEGRAM_TOKEN=...
python3 bot.py
```

## Известные ограничения

- **1 книга за раз** — Chrome в headless жрёт ~300-500 MB RAM
- **Telegram лимит 50 MB** — большие PDF сжимаются ghostscript
- **Snap Chromium не работает** — нужен Google Chrome deb-пакет
- **Литрес может менять UI** — селекторы логина/читалки могут сломаться
- PDF: ~2.5 сек/стр., FB2: ~10 сек на всю книгу
