#!/usr/bin/env python3
"""Find and analyze the 'Change your password' popup."""
import os
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoAlertPresentException

load_dotenv()

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")

driver = webdriver.Chrome(options=opts)

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
    time.sleep(5)
    print(f"After login: {driver.current_url}")

# Wait for popup to appear
print("\nWaiting 5 sec for popup...")
time.sleep(5)

# 1. Check for browser alert
print("\n=== Check 1: Browser alert ===")
try:
    alert = driver.switch_to.alert
    print(f"ALERT FOUND! Text: {alert.text}")
    print("Accepting alert...")
    alert.accept()
    print("Alert closed!")
except NoAlertPresentException:
    print("No browser alert")

# 2. Check for all visible elements with "password" or "change" text
print("\n=== Check 2: Elements with 'password'/'change' text ===")
results = driver.execute_script("""
    var result = '';
    var all = document.querySelectorAll('*');
    for (var el of all) {
        // Only direct text content (not children)
        var text = '';
        for (var node of el.childNodes) {
            if (node.nodeType === 3) text += node.textContent;
        }
        text = text.trim().toLowerCase();
        if (text.includes('password') || text.includes('пароль') || text.includes('change')) {
            var style = window.getComputedStyle(el);
            if (style.display !== 'none' && el.offsetHeight > 0) {
                result += el.tagName + ' class=' + el.className.toString().substring(0,60) +
                          ' text="' + el.textContent.trim().substring(0,100) + '"' +
                          ' visible=' + (el.offsetParent !== null) +
                          ' zIndex=' + style.zIndex + '\\n';
            }
        }
    }
    return result || 'Nothing found';
""")
print(results)

# 3. Check all elements with high z-index (popups/modals)
print("\n=== Check 3: High z-index elements ===")
results = driver.execute_script("""
    var result = '';
    var all = document.querySelectorAll('*');
    for (var el of all) {
        var style = window.getComputedStyle(el);
        var z = parseInt(style.zIndex);
        if (z > 100 && el.offsetHeight > 50 && el.offsetWidth > 50 &&
            (style.position === 'fixed' || style.position === 'absolute')) {
            result += 'z=' + z + ' pos=' + style.position + ' ' + el.tagName +
                      ' class=' + el.className.toString().substring(0,60) +
                      ' size=' + el.offsetWidth + 'x' + el.offsetHeight +
                      ' text="' + el.textContent.trim().substring(0,80) + '"\\n';
            // Show buttons inside
            var btns = el.querySelectorAll('button, a[role="button"], input[type="button"], input[type="submit"]');
            for (var b of btns) {
                result += '  BTN: ' + b.tagName + ' text="' + b.textContent.trim().substring(0,40) +
                          '" type=' + (b.type||'') + ' class=' + b.className.toString().substring(0,40) + '\\n';
            }
        }
    }
    return result || 'Nothing found';
""")
print(results)

# 4. Check for iframes (popup might be in iframe)
print("\n=== Check 4: Iframes ===")
iframes = driver.find_elements(By.TAG_NAME, "iframe")
print(f"Iframes: {len(iframes)}")
for i, iframe in enumerate(iframes):
    print(f"  [{i}] src={iframe.get_attribute('src')[:100] if iframe.get_attribute('src') else 'none'} "
          f"size={iframe.size}")

# 5. Take screenshot for visual inspection
screenshot_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "popup_screenshot.png")
driver.save_screenshot(screenshot_path)
print(f"\nScreenshot saved: {screenshot_path}")

print("\nScript done. Browser staying open for 30 sec for manual inspection...")
time.sleep(30)
driver.quit()
