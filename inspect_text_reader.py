#!/usr/bin/env python3
"""Inspect litres.ru text reader DOM structure."""
import os
import time
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

# Login if needed
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

# Open reader
reader_url = BOOK_URL + "chitat-onlayn/"
print(f"\nOpening reader: {reader_url}")
driver.get(reader_url)
time.sleep(5)

print(f"Current URL: {driver.current_url}")
print(f"Title: {driver.title}")

# Dump page structure
print("\n=== PAGE STRUCTURE ===")
# Get main content area
html = driver.execute_script("""
    // Find the main content - try different selectors
    var selectors = [
        '.reading-content', '.book-content', '.reader-content',
        '.text-content', '#reading', '#content', '.chapter',
        '[class*="reader"]', '[class*="reading"]', '[class*="content"]',
        'article', 'main'
    ];

    for (var s of selectors) {
        var el = document.querySelector(s);
        if (el) {
            return 'FOUND: ' + s + '\\n' +
                   'Tag: ' + el.tagName + '\\n' +
                   'Class: ' + el.className + '\\n' +
                   'Children: ' + el.children.length + '\\n' +
                   'HTML (first 3000 chars): ' + el.innerHTML.substring(0, 3000);
        }
    }

    // If nothing found, dump body classes and structure
    var body = document.body;
    var result = 'Body classes: ' + body.className + '\\n';
    result += 'Direct children:\\n';
    for (var i = 0; i < body.children.length; i++) {
        var ch = body.children[i];
        result += '  ' + ch.tagName + ' id=' + ch.id + ' class=' + ch.className.substring(0, 80) + '\\n';
        // Show grandchildren
        for (var j = 0; j < Math.min(ch.children.length, 5); j++) {
            var gc = ch.children[j];
            result += '    ' + gc.tagName + ' id=' + gc.id + ' class=' + gc.className.substring(0, 80) + '\\n';
        }
    }
    return result;
""")
print(html)

# Check pagination
print("\n=== PAGINATION ===")
pagination = driver.execute_script("""
    var links = document.querySelectorAll('a[href*="page="]');
    var result = 'Links with page=: ' + links.length + '\\n';
    for (var i = 0; i < Math.min(links.length, 10); i++) {
        result += links[i].href + ' | ' + links[i].textContent.trim() + '\\n';
    }

    // Also check for next/prev buttons
    var navBtns = document.querySelectorAll('[class*="paginat"], [class*="page"], [class*="nav"]');
    result += '\\nNav elements: ' + navBtns.length + '\\n';
    for (var i = 0; i < Math.min(navBtns.length, 5); i++) {
        result += navBtns[i].tagName + ' class=' + navBtns[i].className.substring(0, 80) + '\\n';
    }

    return result;
""")
print(pagination)

# Get text content sample
print("\n=== TEXT CONTENT SAMPLE ===")
text_sample = driver.execute_script("""
    var paragraphs = document.querySelectorAll('p');
    var result = 'Total <p> elements: ' + paragraphs.length + '\\n\\n';
    for (var i = 0; i < Math.min(paragraphs.length, 10); i++) {
        var p = paragraphs[i];
        var parentInfo = p.parentElement ? p.parentElement.tagName + '.' + p.parentElement.className.substring(0, 40) : 'none';
        result += 'P[' + i + '] parent=' + parentInfo + '\\n';
        result += '  HTML: ' + p.outerHTML.substring(0, 200) + '\\n\\n';
    }
    return result;
""")
print(text_sample)

# Get headings
print("\n=== HEADINGS ===")
headings = driver.execute_script("""
    var result = '';
    for (var level = 1; level <= 4; level++) {
        var hs = document.querySelectorAll('h' + level);
        if (hs.length > 0) {
            result += 'H' + level + ': ' + hs.length + ' found\\n';
            for (var i = 0; i < Math.min(hs.length, 5); i++) {
                result += '  ' + hs[i].outerHTML.substring(0, 200) + '\\n';
            }
        }
    }
    return result || 'No headings found';
""")
print(headings)

# Check for images
print("\n=== IMAGES ===")
images = driver.execute_script("""
    var imgs = document.querySelectorAll('img');
    var result = 'Total images: ' + imgs.length + '\\n';
    for (var i = 0; i < Math.min(imgs.length, 10); i++) {
        result += imgs[i].src.substring(0, 100) + ' | ' + imgs[i].width + 'x' + imgs[i].height + '\\n';
    }
    return result;
""")
print(images)

print("\nDone. Closing browser.")
import sys; sys.stdout.flush()
driver.quit()
