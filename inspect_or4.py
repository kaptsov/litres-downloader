#!/usr/bin/env python3
"""Inspect litres.ru or4 text reader DOM structure."""
import os
import sys
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC

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
else:
    print("Already logged in")

# Close any popup - try multiple approaches
time.sleep(2)
print("\n=== CHECKING FOR POPUPS ===")
popup_html = driver.execute_script("""
    // Check for modals/dialogs
    var modals = document.querySelectorAll('[role="dialog"], [class*="modal"], [class*="popup"], [class*="overlay"]');
    var result = 'Modals found: ' + modals.length + '\\n';
    for (var m of modals) {
        result += 'Tag: ' + m.tagName + ' class: ' + m.className.substring(0, 100) + '\\n';
        result += 'Visible: ' + (m.offsetParent !== null || m.style.display !== 'none') + '\\n';
        result += 'HTML (500): ' + m.innerHTML.substring(0, 500) + '\\n---\\n';
    }

    // Also check for any fixed/absolute positioned elements that might be popups
    var all = document.querySelectorAll('*');
    for (var el of all) {
        var style = window.getComputedStyle(el);
        if (style.position === 'fixed' && style.zIndex > 100 && el.offsetHeight > 100) {
            result += 'FIXED z>' + style.zIndex + ': ' + el.tagName + '.' + el.className.substring(0, 60) + '\\n';
            result += '  Text: ' + el.textContent.substring(0, 200) + '\\n';
            // Find buttons inside
            var btns = el.querySelectorAll('button, a');
            for (var b of btns) {
                result += '  BTN: ' + b.tagName + ' text="' + b.textContent.trim().substring(0, 50) + '" class=' + b.className.substring(0, 50) + '\\n';
            }
        }
    }
    return result;
""")
print(popup_html)

# Try clicking OK/close buttons
closed = driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        var text = btn.textContent.trim();
        if (text === 'OK' || text === 'Ок' || text === 'Ok') {
            btn.click();
            return 'Clicked: ' + text;
        }
    }
    return 'No OK button found';
""")
print(f"Popup close attempt: {closed}")
time.sleep(1)

# Open book page
print(f"\n=== OPENING BOOK PAGE ===")
driver.get(BOOK_URL)
time.sleep(4)

# Close popup again if it reappears
driver.execute_script("""
    var buttons = document.querySelectorAll('button');
    for (var btn of buttons) {
        var text = btn.textContent.trim();
        if (text === 'OK' || text === 'Ок' || text === 'Ok') {
            btn.click();
            return;
        }
    }
""")
time.sleep(1)

print(f"URL: {driver.current_url}")
print(f"Title: {driver.title}")

# Find and click "Читать" button
print("\n=== FINDING READ BUTTON ===")
read_btn_info = driver.execute_script("""
    var buttons = document.querySelectorAll('a, button');
    var result = '';
    for (var b of buttons) {
        var text = b.textContent.trim();
        if (text.includes('Читать') && !text.includes('онлайн') && text.length < 30) {
            result += 'FOUND: ' + b.tagName + ' text="' + text + '" href=' + (b.href || 'none') + ' class=' + b.className.substring(0, 60) + '\\n';
        }
    }
    return result || 'No read button found';
""")
print(read_btn_info)

# Click the read button
print("\nClicking Читать...")
try:
    read_clicked = driver.execute_script("""
        var buttons = document.querySelectorAll('a, button');
        for (var b of buttons) {
            var text = b.textContent.trim();
            if (text === 'Читать' || text === 'Читать книгу') {
                b.click();
                return 'clicked: ' + text;
            }
        }
        return 'not found';
    """)
    print(f"Result: {read_clicked}")
except Exception as e:
    print(f"Error: {e}")

time.sleep(5)

# Check if new tab/window opened
handles = driver.window_handles
print(f"\nWindow handles: {len(handles)}")
if len(handles) > 1:
    driver.switch_to.window(handles[-1])
    print(f"Switched to new tab")

print(f"Current URL: {driver.current_url}")
print(f"Title: {driver.title}")

# Now inspect the or4 reader
print("\n=== OR4 READER STRUCTURE ===")
if "or4" in driver.current_url or "or.html" in driver.current_url:
    time.sleep(5)  # Wait for reader to load

    structure = driver.execute_script("""
        var result = '';

        // Check iframes
        var iframes = document.querySelectorAll('iframe');
        result += 'Iframes: ' + iframes.length + '\\n';

        // Main structure
        var body = document.body;
        result += 'Body children: ' + body.children.length + '\\n';
        for (var i = 0; i < body.children.length; i++) {
            var ch = body.children[i];
            result += '  ' + ch.tagName + ' id=' + ch.id + ' class=' + (ch.className || '').substring(0, 80) + '\\n';
        }

        // Look for text content containers
        var selectors = [
            '#reader', '#content', '#book-content', '.reader', '.content',
            '[class*="reader"]', '[class*="page"]', '[class*="text"]',
            '#fb3-reader', '.fb3-reader', '[class*="fb3"]'
        ];
        for (var s of selectors) {
            var el = document.querySelector(s);
            if (el) {
                result += '\\nFOUND: ' + s + '\\n';
                result += '  Tag: ' + el.tagName + ' id=' + el.id + '\\n';
                result += '  Children: ' + el.children.length + '\\n';
                result += '  HTML(1000): ' + el.innerHTML.substring(0, 1000) + '\\n';
            }
        }

        // Check for paragraphs
        var ps = document.querySelectorAll('p');
        result += '\\nParagraphs: ' + ps.length + '\\n';
        for (var i = 0; i < Math.min(ps.length, 5); i++) {
            result += '  P[' + i + ']: ' + ps[i].outerHTML.substring(0, 200) + '\\n';
        }

        // Full body overview
        result += '\\nBody HTML (2000): ' + document.body.innerHTML.substring(0, 2000);

        return result;
    """)
    print(structure)
else:
    print(f"Not in or4 reader. URL: {driver.current_url}")
    # Maybe still on book page - dump what we see
    page_html = driver.execute_script("return document.body.innerHTML.substring(0, 3000)")
    print(page_html[:2000])

print("\n\nDone.")
sys.stdout.flush()
time.sleep(3)
driver.quit()
