#!/usr/bin/env python3
"""Download FB2 from litres.ru - intercept network requests."""
import os
import sys
import time
import json
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities

load_dotenv()

BOOK_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.litres.ru/book/georgiy-chelpanov/logika-71350810/"

download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads_fb2")
os.makedirs(download_dir, exist_ok=True)

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.set_capability("goog:loggingPrefs", {"performance": "ALL"})

prefs = {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "download.directory_upgrade": True,
}
opts.add_experimental_option("prefs", prefs)

driver = webdriver.Chrome(options=opts)

# Enable CDP download behavior
driver.execute_cdp_cmd("Page.setDownloadBehavior", {
    "behavior": "allow",
    "downloadPath": download_dir,
})

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
    print(f"Logged in: {driver.current_url}")

# Close popup
time.sleep(1)
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); return; }
    }
""")

# Open book page
print(f"\nOpening: {BOOK_URL}")
driver.get(BOOK_URL)
time.sleep(4)
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); return; }
    }
""")
time.sleep(1)

# Clear logs before clicking
driver.get_log("performance")

# Click FB2 download - observe what JS does
print("\nClicking FB2 download and monitoring network...")
before_files = set(os.listdir(download_dir))

# First, let's see what the onclick handler does
onclick_info = driver.execute_script("""
    var links = document.querySelectorAll('[data-testid="format__downloadButton"]');
    var result = '';
    for (var l of links) {
        if (l.textContent.includes('Fb2')) {
            // Get all event listeners info
            result += 'Tag: ' + l.tagName + '\\n';
            result += 'Text: ' + l.textContent.trim() + '\\n';
            result += 'OuterHTML: ' + l.outerHTML.substring(0, 500) + '\\n';
            result += 'Parent outerHTML: ' + l.parentElement.outerHTML.substring(0, 500) + '\\n';

            // Try to trigger click and capture URL
            l.click();
            result += 'Clicked!\\n';
            break;
        }
    }
    return result;
""")
print(onclick_info)

# Wait and check network logs
time.sleep(5)
logs = driver.get_log("performance")
download_urls = []
for log in logs:
    msg = json.loads(log["message"])["message"]
    method = msg.get("method", "")
    if method in ("Network.requestWillBeSent", "Network.responseReceived", "Page.downloadWillBegin"):
        params = msg.get("params", {})
        if method == "Page.downloadWillBegin":
            print(f"  DOWNLOAD: {params.get('url', '')} -> {params.get('suggestedFilename', '')}")
            download_urls.append(params.get("url", ""))
        elif method == "Network.requestWillBeSent":
            url = params.get("request", {}).get("url", "")
            if "download" in url or "catalit" in url or "get_book" in url or "fb2" in url.lower():
                print(f"  REQUEST: {url[:150]}")
                download_urls.append(url)
        elif method == "Network.responseReceived":
            resp = params.get("response", {})
            url = resp.get("url", "")
            ct = resp.get("headers", {}).get("content-type", "")
            if "download" in url or "zip" in ct or "octet" in ct:
                print(f"  RESPONSE: {url[:150]} type={ct}")

# Check for new windows/tabs
if len(driver.window_handles) > 1:
    for h in driver.window_handles:
        driver.switch_to.window(h)
        print(f"  Window: {driver.current_url[:100]}")

# Wait for file
print("\nWaiting for file...")
for i in range(20):
    time.sleep(2)
    current = set(os.listdir(download_dir))
    new = current - before_files
    if new:
        completed = [f for f in new if not f.endswith('.crdownload')]
        if completed:
            for f in completed:
                path = os.path.join(download_dir, f)
                size = os.path.getsize(path)
                print(f"  Downloaded: {f} ({size} bytes)")
            break
        else:
            print(f"  Downloading: {new}")

print(f"\nAll files in dir: {os.listdir(download_dir)}")
print(f"Current URL: {driver.current_url}")

driver.quit()
print("Done.")
