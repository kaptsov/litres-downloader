#!/usr/bin/env python3

import os, shutil
import re
import sys
import time
import img2pdf
import logging

from io import BytesIO
from PIL import Image, UnidentifiedImageError
from base64 import b64decode
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.common.action_chains import ActionChains
from selenium.common.exceptions import NoSuchElementException
from dotenv import load_dotenv

load_dotenv()

logger = logging.getLogger(__name__)

class App():

    def __init__(self):
        pass

    def __prepare_browser(self):
        options = webdriver.ChromeOptions()
        #options.headless = True
        options.headless = False
        options.add_argument("--no-sandbox")
        options.add_argument("--window-size=1920,1080")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument(f"--user-agent={os.environ['CHROMEDRIVER_PATH']}")
        options.add_argument("--user-data-dir=./selenium")


        self.driver = webdriver.Chrome(os.environ['CHROMEDRIVER_PATH'], options=options)

    def hide_toolbar(self):
        js_script = '''\
        element1 = document.getElementsByClassName('toolbar');
        element1[0].style.display = 'none';
        '''
        self.driver.execute_script(js_script)


    def __setup_logging(self):
        logging.basicConfig(format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
                            level=logging.INFO, stream=sys.stdout)

    def __init__(self):
        self.__setup_logging()
        self.__prepare_browser()


    def login(self):

        self.driver.find_element(By.CSS_SELECTOR, 'input[name="login"]').send_keys(os.environ['LOGIN'])
        time.sleep(1)
        self.driver.find_element(By.CSS_SELECTOR, 'input[id="open_pwd_main"]').send_keys(os.environ['PASSWORD'])
        time.sleep(1)
        self.driver.find_element(By.CSS_SELECTOR, 'button[id="login_btn"]').click()
        logger.info('Logging...')
        time.sleep(1)


    def download_page_using_screenshot(self, page):
        self.hide_toolbar()

        element = self.driver.find_element("id", f"p_{page}")
        actions = ActionChains(self.driver)
        actions.move_to_element(element).perform()

        with open(self.get_page_filename(page), 'wb') as f:
            f.write(element.find_element(By.CSS_SELECTOR, 'img').screenshot_as_png)


    def download_page(self, page, book_name):
        logger.info(f"Downloading page {page}")

        try:
            element = self.driver.find_element("id", f"p_{page}")
        except Exception as e:
            return True, True, 3


        actions = ActionChains(self.driver)
        actions.move_to_element(element).perform()

        if os.path.isfile(self.get_page_filename(page, book_name)):
            logger.info(f"Page {page} exists, skipping")
            return False, True, 1

        b64img = self.driver.execute_script(f'''
            var img = document.querySelector("#p_{page} > img");
            var canvas = document.createElement("canvas");
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            console.log(canvas.width, canvas.height);
            var ctx = canvas.getContext("2d");
            ctx.drawImage(img, 0, 0);
            var dataURL = canvas.toDataURL("image/png");
            return dataURL.replace(/^data:image\\/(png|jpg);base64,/, "");
        ''')

        try:
            img = Image.open(BytesIO(b64decode(b64img)))
        except UnidentifiedImageError:
            logger.error(f"Could not open image, b64img was: {b64img}")
            return False, False, 15

        img.save(self.get_page_filename(page, book_name))

        return False, True, 3

    def get_page_filename(self, page, book_name):
        return os.path.join(book_name, f'page_{page}.png')


    def create_book(self, book_name):
        def sorted_alphanumeric(data):
            convert = lambda text: int(text) if text.isdigit() else text.lower()
            alphanum_key = lambda key: [ convert(c) for c in re.split('([0-9]+)', key) ]
            return sorted(data, key=alphanum_key)

        os.chdir(book_name)

        files = [f for f in os.listdir(os.getcwd()) if f.endswith('.png')]

        try:
            with open(f'../{book_name}.pdf',"wb") as file:
                file.write(img2pdf.convert(sorted_alphanumeric(files)))       
            os.chdir('..')
            logger.info(f'The book {book_name}.pdf is ready')
        except Exception as e:
            print(e)
            pass


    def run(self):
        logger.info(self.driver.title)
        logger.info(os.environ['LOGIN'])
        book_url = input('Input book url: ')

        try:
            self.driver.get('https://www.litres.ru/pages/login/')
            self.login()
            logger.info("Logging in sussecc")
        except NoSuchElementException:
            logger.info("Already logged in")

        time.sleep(1)
        self.driver.get(book_url)
        time.sleep(3)

        book_name = self.driver.find_element(By.CLASS_NAME, "art-page-link").text
        logger.info(f'Downloading: {book_name}')
        try:
            os.mkdir(book_name)
            logger.info(f'Folder {book_name} created')
        except Exception as e:
            logger.info(f'Folder {book_name} already exist')
            pass
        
        page = 0
        book_finished = False
        while not book_finished:
            success = False;
            while not success:
                book_finished, success, time_to_sleep = self.download_page(page, book_name)
                time.sleep(time_to_sleep)
            page += 1


        self.create_book(book_name)
        shutil.rmtree(book_name)


if __name__ == '__main__':
    sys.exit(App().run())
