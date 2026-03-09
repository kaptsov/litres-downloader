#!/usr/bin/env python3
"""Try to download book file directly from litres API."""
import os
import sys
import time
import requests
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

load_dotenv()

BOOK_URL = "https://www.litres.ru/book/georgiy-chelpanov/logika-71350810/"

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=opts)

# Login
driver.get("https://www.litres.ru/pages/login/")
time.sleep(3)

if "login" in driver.current_url or "auth" in driver.current_url:
    print("Logging in...")
    email_input = driver.find_element(By.CSS_SELECTOR, 'input[name="email"]')
    email_input.clear()
    email_input.send_keys(os.environ["LITRES_LOGIN"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(2)

    pwd_input = driver.find_element(By.CSS_SELECTOR, 'input[type="password"]')
    pwd_input.clear()
    pwd_input.send_keys(os.environ["LITRES_PASSWORD"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(3)
    print(f"After login: {driver.current_url}")

# Get cookies
cookies = {c["name"]: c["value"] for c in driver.get_cookies()}
print(f"Cookies: {len(cookies)} total")

# Try downloading book in various formats
art_id = "71350810"
session = requests.Session()
session.cookies.update(cookies)
session.headers.update({
    "User-Agent": driver.execute_script("return navigator.userAgent"),
    "Referer": "https://www.litres.ru/",
})

# 1. Try the download_book URL from or4 reader
print("\n=== Try 1: download_book API ===")
urls_to_try = [
    f"https://www.litres.ru/download_book/{art_id}/113824054/",
    f"https://www.litres.ru/download_book/{art_id}/113824054/fb2",
    f"https://www.litres.ru/pages/get_book_file/?art={art_id}&type=fb2",
    f"https://www.litres.ru/pages/get_book_file/?art={art_id}&type=epub",
    f"https://www.litres.ru/pages/catalit_download_book/?art={art_id}&type=fb2",
    f"https://www.litres.ru/pages/catalit_download_book/?art={art_id}&type=fb2.zip",
]

for url in urls_to_try:
    try:
        resp = session.get(url, allow_redirects=True, timeout=10)
        ct = resp.headers.get("content-type", "unknown")
        cd = resp.headers.get("content-disposition", "none")
        print(f"  {url[-60:]}")
        print(f"    Status: {resp.status_code}, Type: {ct}, Size: {len(resp.content)}, Disposition: {cd}")
        if resp.status_code == 200 and len(resp.content) > 1000:
            # Check first bytes
            first = resp.content[:50]
            print(f"    First bytes: {first}")
    except Exception as e:
        print(f"  {url[-60:]}: ERROR {e}")

# 2. Try through Selenium - open book page and look for download links
print("\n=== Try 2: Download links on book page ===")
driver.get(BOOK_URL)
time.sleep(3)

# Close popup if any
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); return; }
    }
""")
time.sleep(1)

download_links = driver.execute_script("""
    var result = '';
    var links = document.querySelectorAll('a[href*="download"], a[href*="get_book"], button[class*="download"]');
    result += 'Download links: ' + links.length + '\\n';
    for (var l of links) {
        result += '  ' + l.tagName + ' href=' + (l.href||'') + ' text=' + l.textContent.trim().substring(0,50) + '\\n';
    }

    // Check "Мои книги" section or download buttons
    var allBtns = document.querySelectorAll('a, button');
    for (var b of allBtns) {
        var text = b.textContent.trim().toLowerCase();
        if (text.includes('скачать') || text.includes('download') || text.includes('fb2') || text.includes('epub')) {
            result += '  FOUND: ' + b.tagName + ' text=' + b.textContent.trim().substring(0,50) + ' href=' + (b.href||'') + '\\n';
        }
    }
    return result;
""")
print(download_links)

# 3. Check "Мои книги" page for download options
print("\n=== Try 3: My books page ===")
driver.get("https://www.litres.ru/pages/my_books_all/")
time.sleep(3)

# Close popup
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); return; }
    }
""")
time.sleep(1)

my_books = driver.execute_script("""
    var result = 'URL: ' + window.location.href + '\\n';
    // Find book title "Логика" or download links
    var allLinks = document.querySelectorAll('a');
    for (var l of allLinks) {
        if (l.textContent.includes('Логика') || l.href.includes('download') || l.href.includes('71350810')) {
            result += l.tagName + ' text=' + l.textContent.trim().substring(0,60) + ' href=' + l.href.substring(0,100) + '\\n';
        }
    }
    // Find download buttons
    var btns = document.querySelectorAll('[class*="download"], [data-testid*="download"]');
    result += 'Download buttons: ' + btns.length + '\\n';
    for (var b of btns) {
        result += '  ' + b.tagName + '.' + b.className.substring(0,50) + ' text=' + b.textContent.trim().substring(0,50) + '\\n';
    }
    return result;
""")
print(my_books)

driver.quit()
print("\nDone.")
