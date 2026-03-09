#!/usr/bin/env python3
"""Download FB2 from litres.ru - via browser fetch + download API."""
import os
import sys
import time
import base64
import json
import re
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By

load_dotenv()

BOOK_URL = sys.argv[1] if len(sys.argv) > 1 else "https://www.litres.ru/book/georgiy-chelpanov/logika-71350810/"

download_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "downloads_fb2")
os.makedirs(download_dir, exist_ok=True)

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
prefs = {
    "download.default_directory": download_dir,
    "download.prompt_for_download": False,
    "credentials_enable_service": False,
    "profile.password_manager_enabled": False,
    "profile.password_manager_leak_detection": False,
}
opts.add_experimental_option("prefs", prefs)
opts.add_argument("--disable-features=PasswordLeakDetection")

driver = webdriver.Chrome(options=opts)
driver.execute_cdp_cmd("Page.setDownloadBehavior", {
    "behavior": "allow",
    "downloadPath": download_dir,
})

# Login
driver.get("https://www.litres.ru/pages/login/")
time.sleep(3)
if "login" in driver.current_url or "auth" in driver.current_url:
    print("Logging in...")
    driver.find_element(By.CSS_SELECTOR, 'input[name="email"]').send_keys(os.environ["LITRES_LOGIN"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(2)
    driver.find_element(By.CSS_SELECTOR, 'input[type="password"]').send_keys(os.environ["LITRES_PASSWORD"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(3)
    print(f"Logged in")

# Close popup
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); break; }
    }
""")
time.sleep(1)

# Extract art_id from URL
art_match = re.search(r'-(\d+)/?$', BOOK_URL.rstrip('/'))
art_id = art_match.group(1) if art_match else None
print(f"Art ID: {art_id}")

# Open book page to get user/hash info
print(f"Opening: {BOOK_URL}")
driver.get(BOOK_URL)
time.sleep(4)

# Close popup
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        if (btn.textContent.trim() === 'OK') { btn.click(); break; }
    }
""")
time.sleep(1)

# Get the reader URL to extract baseurl
reader_url = driver.execute_script("""
    var links = document.querySelectorAll('a[href*="or4"], a[href*="or3"]');
    for (var l of links) {
        if (l.href.includes('download_book')) return l.href;
    }
    // Also check for "Читать" button
    var btns = document.querySelectorAll('a');
    for (var b of btns) {
        if (b.textContent.trim() === 'Читать' || b.textContent.trim() === 'Читать онлайн') {
            if (b.href && b.href.includes('or')) return b.href;
        }
    }
    return null;
""")
print(f"Reader URL: {reader_url}")

# Extract baseurl
baseurl_match = re.search(r'baseurl=(/download_book/\d+/\d+/)', reader_url or "")
if baseurl_match:
    baseurl = baseurl_match.group(1)
    print(f"Baseurl: {baseurl}")
else:
    print("Could not find baseurl")
    driver.quit()
    sys.exit(1)

# Try to download the book file using various API URLs from browser context
print("\n=== Trying to download FB2 via fetch ===")
formats_to_try = ["fb2.zip", "fb2", "txt"]

for fmt in formats_to_try:
    print(f"\nTrying format: {fmt}")
    # Use fetch from within the browser (cookies will be sent automatically)
    result = driver.execute_script(f"""
        var callback = arguments[arguments.length - 1];
        fetch('{baseurl}{fmt}', {{
            credentials: 'include'
        }}).then(function(resp) {{
            return resp.blob().then(function(blob) {{
                return {{
                    status: resp.status,
                    type: resp.headers.get('content-type'),
                    size: blob.size,
                    disposition: resp.headers.get('content-disposition')
                }};
            }});
        }}).then(callback).catch(function(e) {{
            callback({{error: e.message}});
        }});
    """)
    # Oops, execute_script is synchronous. Use execute_async_script
    pass

# Let's use execute_async_script properly
for fmt in formats_to_try:
    print(f"\nTrying format: {fmt}")
    try:
        result = driver.execute_async_script(f"""
            var callback = arguments[arguments.length - 1];
            fetch('{baseurl}{fmt}', {{
                credentials: 'include'
            }}).then(function(resp) {{
                callback({{
                    status: resp.status,
                    type: resp.headers.get('content-type'),
                    size: parseInt(resp.headers.get('content-length') || '0'),
                    disposition: resp.headers.get('content-disposition'),
                    url: resp.url
                }});
            }}).catch(function(e) {{
                callback({{error: e.message}});
            }});
        """)
        print(f"  Result: {result}")

        if result and result.get("status") == 200 and result.get("size", 0) > 1000:
            # Download the actual content
            print(f"  Success! Downloading {fmt}...")
            b64data = driver.execute_async_script(f"""
                var callback = arguments[arguments.length - 1];
                fetch('{baseurl}{fmt}', {{credentials: 'include'}})
                .then(r => r.blob())
                .then(blob => {{
                    var reader = new FileReader();
                    reader.onloadend = function() {{
                        callback(reader.result.split(',')[1]);
                    }};
                    reader.readAsDataURL(blob);
                }})
                .catch(e => callback(null));
            """)
            if b64data:
                data = base64.b64decode(b64data)
                filename = f"book_{art_id}.{fmt}"
                filepath = os.path.join(download_dir, filename)
                with open(filepath, "wb") as f:
                    f.write(data)
                print(f"  Saved: {filepath} ({len(data)} bytes)")
                break
    except Exception as e:
        print(f"  Error: {e}")

# Also try direct download via navigating
print("\n=== Trying direct navigation download ===")
before = set(os.listdir(download_dir))

# Try using window.location to trigger download
driver.execute_script(f"""
    var a = document.createElement('a');
    a.href = '{baseurl}fb2.zip';
    a.download = 'book.fb2.zip';
    document.body.appendChild(a);
    a.click();
    a.remove();
""")

time.sleep(10)
after = set(os.listdir(download_dir))
new = after - before
if new:
    print(f"  New files: {new}")

print(f"\nAll files in download dir: {os.listdir(download_dir)}")
driver.quit()
print("Done.")
