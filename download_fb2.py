#!/usr/bin/env python3
"""Download FB2 directly from litres.ru via Selenium."""
import os
import sys
import time
import glob
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

load_dotenv()

BOOK_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.litres.ru/book/georgiy-chelpanov/logika-71350810/"

# Set up download directory
download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads_fb2")
os.makedirs(download_dir, exist_ok=True)

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")

# Configure Chrome to download files to our directory
prefs = {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
    "plugins.always_open_pdf_externally": True,
}
opts.add_experimental_option("prefs", prefs)

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
else:
    print("Already logged in")

# Close popup if any
time.sleep(1)
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        var text = btn.textContent.trim();
        if (text === 'OK' || text === 'Ок') { btn.click(); return; }
    }
""")
time.sleep(1)

# Open book page
print(f"\nOpening: {BOOK_URL}")
driver.get(BOOK_URL)
time.sleep(4)

# Close popup again
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        var text = btn.textContent.trim();
        if (text === 'OK' || text === 'Ок') { btn.click(); return; }
    }
""")
time.sleep(1)

# Find and explore download options
print("\n=== Looking for download links ===")
info = driver.execute_script("""
    var result = '';
    var allLinks = document.querySelectorAll('a');
    for (var l of allLinks) {
        var text = l.textContent.trim().toLowerCase();
        if (text.includes('fb2') || text.includes('epub') || text.includes('скачать') || text.includes('download')) {
            result += 'Link: text="' + l.textContent.trim() + '" href=' + (l.href||'none') +
                      ' onclick=' + (l.onclick ? 'yes' : 'no') +
                      ' data-format=' + (l.dataset.format || 'none') +
                      ' data-type=' + (l.dataset.type || 'none') +
                      ' class=' + l.className.substring(0,60) + '\\n';
            // Check parent for data attributes
            var parent = l.closest('[data-format], [data-type], [class*="download"]');
            if (parent) {
                result += '  Parent: ' + parent.tagName + ' data-format=' + (parent.dataset.format||'') + ' class=' + parent.className.substring(0,60) + '\\n';
            }
        }
    }

    // Also check for all elements with download-related classes or data
    var downloadEls = document.querySelectorAll('[class*="download"], [data-testid*="download"], [class*="format"]');
    result += '\\nDownload elements: ' + downloadEls.length + '\\n';
    for (var d of downloadEls) {
        result += '  ' + d.tagName + ' class=' + d.className.substring(0,80) + ' text=' + d.textContent.trim().substring(0,60) + '\\n';
        // Show all data- attributes
        for (var attr of d.attributes) {
            if (attr.name.startsWith('data-')) {
                result += '    ' + attr.name + '=' + attr.value + '\\n';
            }
        }
    }

    return result;
""")
print(info)

# Try to click on the Fb2 download link
print("\n=== Clicking Fb2 download ===")
before_files = set(os.listdir(download_dir))

clicked = driver.execute_script("""
    var allLinks = document.querySelectorAll('a');
    for (var l of allLinks) {
        if (l.textContent.includes('Fb2') || l.textContent.includes('fb2')) {
            // Observe what happens when clicked
            l.click();
            return 'Clicked: ' + l.textContent.trim() + ' href=' + l.href;
        }
    }
    return 'Not found';
""")
print(clicked)

# Wait for download
print("Waiting for download...")
for i in range(30):
    time.sleep(2)
    current_files = set(os.listdir(download_dir))
    new_files = current_files - before_files
    if new_files:
        # Filter out .crdownload (still downloading)
        completed = [f for f in new_files if not f.endswith('.crdownload')]
        downloading = [f for f in new_files if f.endswith('.crdownload')]
        if completed:
            print(f"Downloaded: {completed}")
            break
        elif downloading:
            print(f"  Still downloading: {downloading}")

    # Check if a new page/dialog opened
    if len(driver.window_handles) > 1:
        print(f"  New window opened, switching...")
        driver.switch_to.window(driver.window_handles[-1])
        print(f"  URL: {driver.current_url}")

# Check network requests via performance log
print(f"\nFinal URL: {driver.current_url}")
print(f"Files in download dir: {os.listdir(download_dir)}")

driver.quit()
print("Done.")
