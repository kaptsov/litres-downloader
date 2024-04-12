# Litres books downloader

Script allows you to download books that normally acessible only online or in Android/iOS LitRes application using Python and Selenium. It's assumed that the book has already been purchased and exists in your personal account.

## Usage

Create .env
```bash
PASSWORD=pass
LOGIN=login
BOOK_URL=actual book url
PAGE_COUNT=page count from info
CHROMEDRIVER_PATH="/usr/lib/chromium-browser/chromedriver"
USER_AGENT="Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/111.0.0.0 Safari/537.36"
```
```bash
$ virtualenv .venv
$ pip3 install -r requirements.txt
$ ./test.py 
```


The book will be saved into final_book.pdf file.

## Requirements

The tool requires Selenium, img2pdf and Pillow libraries which are listed in [requirements.txt](requirements.txt).
