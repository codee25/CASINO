import logging
import os
import json
import random

import psycopg2
from psycopg2 import sql
import urllib.parse # Додаємо для парсингу URL

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp.web import Application, json_response, Request
# from aiohttp.web_runner import AppRunner, TCPSite # Цей рядок можна видалити

# --- Налаштування логування ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Змінні середовища (будуть встановлені на Render.com) ---
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_FRONTEND_URL', 'https://placeholder.com') # URL вашого Web App
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST') # URL вашого бота на Render.com
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# --- Налаштування бази даних PostgreSQL ---
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL за URL."""
    conn = None
    try:
        # Парсимо DATABASE_URL, щоб отримати окремі компоненти
        url = urllib.parse.urlparse(DATABASE_URL)
        
        conn = psycopg2.connect(
            database=url.path[1:], # Видаляємо перший слеш
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require' # Важливо для Render.com
        )
        return conn
    except psycopg2.Error as err:
        logger.error(f"Error connecting to PostgreSQL: {err}")
        raise
    except Exception as e:
        logger.error(f"Failed to parse DATABASE_URL or connect: {e}")
        raise
    finally:
        # Зазвичай тут не закривають з'єднання, оскільки воно повертається.
        # Закриття має відбуватися після використання.
        pass # Залишаємо `pass`


# ... (init_db, get_user_balance, update_user_balance, SYMBOLS, BET_AMOUNT, PAYOUTS, spin_slot - залишаються без змін) ...
# ... (Обробники Telegram-бота send_welcome - залишаються без змін) ...
# ... (Обробка запитів від Web App api_get_balance, api_spin - залишаються без змін) ...
# ... (Функції для запуску бота та веб-сервера: on_startup_webhook, on_shutdown_webhook - залишаються без змін) ...

# Головний Aiohttp додаток, який Gunicorn буде запускати
app_aiohttp = Application()

# Реєстрація хендлера для Telegram webhook
SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(app_aiohttp, WEBHOOK_PATH)

# Реєстрація API ендпоінтів для Web App
app_aiohttp.router.add_post('/api/get_balance', api_get_balance)
app_aiohttp.router.add_post('/api/spin', api_spin)

# Додаємо функції запуску/зупинки до Aiohttp додатка
app_aiohttp.on_startup.append(on_startup_webhook)
app_aiohttp.on_shutdown.append(on_shutdown_webhook)

# Цей блок `if __name__ == '__main__':` залишається без змін
if __name__ == '__main__':
    pass
