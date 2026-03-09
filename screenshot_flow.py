#!/usr/bin/env python3
"""Take screenshots at every step to find the popup."""
import os
import time
from dotenv import load_dotenv
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.common.exceptions import NoAlertPresentException

load_dotenv()

BOOK_URL = "https://www.litres.ru/book/georgiy-chelpanov/logika-71350810/"
SHOTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
os.makedirs(SHOTS_DIR, exist_ok=True)

def shot(driver, name):
    path = os.path.join(SHOTS_DIR, f"{name}.png")
    driver.save_screenshot(path)
    print(f"  Screenshot: {name}.png")
    # Also check for alert
    try:
        alert = driver.switch_to.alert
        print(f"  *** ALERT: {alert.text} ***")
        alert_path = os.path.join(SHOTS_DIR, f"{name}_ALERT.txt")
        with open(alert_path, "w") as f:
            f.write(alert.text)
    except NoAlertPresentException:
        pass

opts = Options()
opts.add_argument("--no-sandbox")
opts.add_argument("--disable-dev-shm-usage")
opts.add_argument("--window-size=1280,900")

driver = webdriver.Chrome(options=opts)

print("Step 1: Open login page")
driver.get("https://www.litres.ru/pages/login/")
time.sleep(3)
shot(driver, "01_login_page")

if "login" in driver.current_url or "auth" in driver.current_url:
    print("Step 2: Enter email")
    driver.find_element(By.CSS_SELECTOR, 'input[name="email"]').send_keys(os.environ["LITRES_LOGIN"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(2)
    shot(driver, "02_after_email")

    print("Step 3: Enter password")
    driver.find_element(By.CSS_SELECTOR, 'input[type="password"]').send_keys(os.environ["LITRES_PASSWORD"])
    driver.find_element(By.CSS_SELECTOR, 'button[type="submit"]').click()
    time.sleep(3)
    shot(driver, "03_after_password")

    time.sleep(3)
    shot(driver, "04_after_wait")
else:
    print("Already logged in")
    shot(driver, "02_already_logged")

print(f"Step 5: Current URL: {driver.current_url}")
shot(driver, "05_current_state")

print("Step 6: Navigate to book page")
driver.get(BOOK_URL)
time.sleep(3)
shot(driver, "06_book_page_3s")
time.sleep(3)
shot(driver, "07_book_page_6s")

print("Step 7: Click Читать")
driver.execute_script("""
    var links = document.querySelectorAll('a');
    for (var l of links) {
        if (l.textContent.trim() === 'Читать') { l.click(); return; }
    }
""")
time.sleep(3)
shot(driver, "08_after_read_click_3s")
time.sleep(5)
shot(driver, "09_after_read_click_8s")
time.sleep(5)
shot(driver, "10_after_read_click_13s")

print(f"Final URL: {driver.current_url}")
print(f"\nAll screenshots in: {SHOTS_DIR}")
print("Keeping browser open 60s for manual observation...")
time.sleep(60)
driver.quit()
