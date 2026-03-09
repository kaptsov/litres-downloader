# Litres Book Downloader

Скачивает купленные книги с litres.ru в PDF. Работает через Selenium + headless Chrome.

## Архитектура

```
litres-downloader.py   — CLI-интерфейс (запуск локально)
bot.py                 — Telegram-бот (запуск на сервере)
downloader.py          — Ядро: логин, скачивание страниц, сборка PDF
litres-bot.service     — systemd unit для сервера
```

### Как работает скачивание (downloader.py)

1. **Логин** — Selenium открывает Chrome, заходит на litres.ru/pages/login/
   - Двухшаговая форма: email → пароль
   - Куки сохраняются в `chrome_profile/` — повторный логин не нужен
   - Селекторы: `input[name="email"]`, `input[type="password"]`, `button[type="submit"]`

2. **Инфо о книге** — открывает страницу книги (litres.ru/book/...)
   - Название из `<h1>`, кол-во страниц из текста «N стр.»
   - Нажимает кнопку «Читать» → открывается читалка or3

3. **Читалка or3** — встроенный PDF-ридер литреса
   - DOM: `#canvas > #pole > div#p_0.img_page > img[src=...]`
   - Страницы подгружаются lazy (по 3 шт при скролле)
   - Скрипт скроллит к каждой `#p_N`, ждёт `img.complete && naturalWidth > 0`

4. **Извлечение изображения** — через JavaScript canvas trick:
   - Создаёт `<canvas>`, рисует на нём `<img>`, делает `toDataURL('image/png')`
   - Это обходит CORS — напрямую скачать img.src нельзя (403 без правильной сессии)
   - Результат: base64 → decode → PIL → JPEG (quality=85)

5. **Сборка PDF** — img2pdf (встраивает JPEG без перекодирования)

### Telegram-бот (bot.py)

- python-telegram-bot (async), polling mode
- `asyncio.Lock` — одна книга за раз (на VPS 1 GB RAM)
- Selenium запускается в `ThreadPoolExecutor` (блокирующий код)
- При файле >50 MB — сжатие через ghostscript (`/ebook` preset, ~150 dpi)
- **Все временные файлы удаляются** после отправки (`_cleanup_work_dir`)

## Деплой

### Сервер
- **VPS**: 212.109.192.199 (FirstVDS), Ubuntu 24.04, 1 GB RAM
- **Путь**: `/var/www/litres-bot/`
- **Сервис**: `systemctl {start|stop|restart|status} litres-bot`
- **Логи**: `journalctl -u litres-bot -f`
- На том же сервере: sweateratops.ru, memo.sweateratops.ru

### Переменные окружения (в systemd unit)
```
TELEGRAM_TOKEN=...        # токен от @BotFather
LITRES_LOGIN=...          # email аккаунта litres.ru
LITRES_PASSWORD=...       # пароль
ALLOWED_USERS=            # telegram user IDs через запятую (пустой = все)
CHROME_BINARY=            # путь к chrome (если нестандартный)
CHROMEDRIVER_PATH=        # путь к chromedriver (если нестандартный)
```

### Обновление кода на сервере
```bash
rsync -avz --exclude='chrome_profile' --exclude='__pycache__' --exclude='*.pdf' \
  --exclude='downloads' --exclude='.git' --exclude='venv' \
  ./ root@212.109.192.199:/var/www/litres-bot/

ssh root@212.109.192.199 "systemctl restart litres-bot"
```

### Установка с нуля
```bash
ssh root@212.109.192.199

# Google Chrome (snap-версия Chromium не работает с Selenium на серверах)
wget -O /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
apt install -y /tmp/chrome.deb

# Ghostscript для сжатия PDF
apt install -y ghostscript

# Проект
mkdir -p /var/www/litres-bot
# ... rsync файлы ...
cd /var/www/litres-bot
python3 -m venv venv
./venv/bin/pip install -r requirements.txt

# Systemd
cp litres-bot.service /etc/systemd/system/
# Отредактировать токен и пароли в /etc/systemd/system/litres-bot.service
systemctl daemon-reload
systemctl enable --now litres-bot
```

## Локальная разработка

```bash
# Установка
pip3 install -r requirements.txt

# Создать .env
echo 'LITRES_LOGIN=your@email.com' > .env
echo 'LITRES_PASSWORD=yourpassword' >> .env

# CLI — скачивание одной книги (с GUI Chrome)
python3 litres-downloader.py "https://www.litres.ru/book/..."

# Бот локально
export TELEGRAM_TOKEN=...
python3 bot.py
```

## Известные ограничения

- **1 книга за раз** — Chrome в headless жрёт ~300-500 MB RAM
- **Telegram лимит 50 MB** — большие книги сжимаются ghostscript
- **Snap Chromium не работает** на серверах (sandbox конфликт) — нужен Google Chrome
- **Литрес может менять UI** — селекторы логина и читалки могут сломаться
- Скорость: ~2.5 сек/страница (загрузка + извлечение + пауза)
