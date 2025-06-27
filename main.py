import logging
import os
import json
import random
import urllib.parse

import psycopg2
from psycopg2 import sql

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from aiohttp.web import Application, json_response, Request
# ДОДАЄМО: aiohttp_cors для налаштування CORS
import aiohttp_cors # <--- НОВИЙ ІМПОРТ

# --- Налаштування логування ---
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Змінні середовища ---
API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_FRONTEND_URL', 'https://placeholder.com')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

# --- Налаштування бази даних PostgreSQL ---
DATABASE_URL = os.getenv('DATABASE_URL')

# ==========================================================
# ПОЧАТОК: ІНІЦІАЛІЗАЦІЯ BOT ТА DISPATCHER
bot = Bot(token=API_TOKEN)
dp = Dispatcher()
# КІНЕЦЬ: ІНІЦІАЛІЗАЦІЯ BOT ТА DISPATCHER
# ==========================================================

# ... (get_db_connection, init_db, get_user_balance, update_user_balance, SYMBOLS, BET_AMOUNT, PAYOUTS, spin_slot - залишаються без змін) ...

def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL за URL."""
    conn = None
    try:
        url = urllib.parse.urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require'
        )
        return conn
    except psycopg2.Error as err:
        logger.error(f"Error connecting to PostgreSQL: {err}")
        raise
    except Exception as e:
        logger.error(f"Failed to parse DATABASE_URL or establish connection: {e}")
        raise

def init_db():
    """Ініціалізує таблиці в базі даних PostgreSQL."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance INTEGER DEFAULT 1000
            )
        ''')
        conn.commit()
        logger.info("PostgreSQL database initialized or already exists.")
    except Exception as e:
        logger.error(f"Failed to initialize PostgreSQL database: {e}")
    finally:
        if conn:
            conn.close()

def get_user_balance(user_id):
    """Отримує баланс користувача з бази даних PostgreSQL."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT balance FROM users WHERE user_id = %s', (user_id,))
        result = cursor.fetchone()
        if result:
            return result[0]
        else:
            update_user_balance(user_id, 1000)
            return 1000
    except Exception as e:
        logger.error(f"Error getting user balance from PostgreSQL for {user_id}: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def update_user_balance(user_id, amount):
    """Оновлює баланс користувача в базі даних PostgreSQL."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql.SQL('''
            INSERT INTO users (user_id, balance) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + %s
        '''), (user_id, amount, amount))
        conn.commit()
        logger.info(f"User {user_id} balance updated by {amount} in PostgreSQL.")
    except Exception as e:
        logger.error(f"Error updating user balance in PostgreSQL for {user_id} by {amount}: {e}")
    finally:
        if conn:
            conn.close()

SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀']
BET_AMOUNT = 100

PAYOUTS = {
    ('🍒', '🍒', '🍒'): 1000,
    ('🍋', '🍋', '🍋'): 800,
    ('🍊', '🍊', '🍊'): 600,
    ('🍇', '🍇', '🍇'): 400,
    ('🔔', '🔔', '🔔'): 300,
    ('💎', '💎', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150,
    ('�', '🍒'): 100,
    ('🍋', '🍋'): 80,
}

def spin_slot(user_id):
    current_balance = get_user_balance(user_id)
    if current_balance < BET_AMOUNT:
        return {'error': 'Недостатньо коштів для спіна!'}, current_balance

    update_user_balance(user_id, -BET_AMOUNT)

    result_symbols = [random.choice(SYMBOLS) for _ in range(3)]
    winnings = 0

    if result_symbols[0] == result_symbols[1] == result_symbols[2]:
        winnings = PAYOUTS.get(tuple(result_symbols), 0)
    elif result_symbols[0] == result_symbols[1]:
        winnings = PAYOUTS.get((result_symbols[0], result_symbols[1]), 0)
    elif result_symbols[1] == result_symbols[2]:
        winnings = PAYOUTS.get((result_symbols[1], result_symbols[2]), 0)

    if winnings > 0:
        update_user_balance(user_id, winnings)
        logger.info(f"User {user_id} won {winnings}.")
    else:
        logger.info(f"User {user_id} lost on spin.")

    final_balance = get_user_balance(user_id)

    return {
        'symbols': result_symbols,
        'winnings': winnings,
        'new_balance': final_balance
    }, final_balance

# --- Обробники Telegram-бота ---

@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db()
    current_balance = get_user_balance(user_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Відкрити Слот-Казино 🎰", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])

    await message.reply(
        f"Привіт, {message.from_user.first_name}!\n"
        f"Ласкаво просимо до віртуального Слот-Казино!\n"
        f"Ваш поточний баланс: {current_balance} фантиків.\n"
        f"Натисніть кнопку нижче, щоб почати грати!",
        reply_markup=keyboard
    )

# --- Обробка запитів від Web App ---

async def api_get_balance(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        return json_response({'error': 'User ID is required'}, status=400)
    
    balance = get_user_balance(user_id)
    return json_response({'balance': balance})

async def api_spin(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        return json_response({'error': 'User ID is required'}, status=400)
    
    result, new_balance = spin_slot(user_id)
    if 'error' in result:
        return json_response(result, status=400)
    
    return json_response(result)

# --- Функції для запуску бота та веб-сервера ---

async def on_startup_webhook(web_app: Application):
    """Викликається при запуску Aiohttp веб-сервера."""
    logger.warning('Starting bot and webhook...')
    init_db()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to: {WEBHOOK_URL}")

async def on_shutdown_webhook(web_app: Application):
    """Викликається при завершенні роботи Aiohttp веб-сервера."""
    logger.warning('Shutting down bot and webhook...')
    await bot.delete_webhook()
    logger.warning('Webhook deleted.')

# Головний Aiohttp додаток, який Gunicorn буде запускати
app_aiohttp = Application()

# =================================================================
# ПОЧАТОК: НАЛАШТУВАННЯ CORS
# Важливо: WEB_APP_URL має бути АКТУАЛЬНИМ URL вашого Static Site
# (наприклад, https://my-slot-webapp.onrender.com)
# =================================================================
# Налаштовуємо CORS для дозволу запитів з Web App URL
cors = aiohttp_cors.setup(app_aiohttp, defaults={
    WEB_APP_URL: aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*",
    )
})

# Застосовуємо CORS до ваших API-маршрутів
# Огортаємо хендлери за допомогою cors.add(route)
for route in list(app_aiohttp.router.routes()): # Робимо копію, бо маршрути змінюються під час ітерації
    if route.resource.name in ['api_get_balance', 'api_spin']: # Перевіряємо, чи це ваші API маршрути
        cors.add(route)
# =================================================================
# КІНЕЦЬ: НАЛАШТУВАННЯ CORS
# =================================================================

# Реєстрація хендлера для Telegram webhook
SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(app_aiohttp, WEBHOOK_PATH)

# Реєстрація API ендпоінтів для Web App
# ЦІ МАРШРУТИ ТЕПЕР ОБРОБЛЯЮТЬСЯ ЧЕРЕЗ CORS
app_aiohttp.router.add_post('/api/get_balance', api_get_balance, name='api_get_balance') # Додаємо name
app_aiohttp.router.add_post('/api/spin', api_spin, name='api_spin') # Додаємо name

# Додаємо функції запуску/зупинки до Aiohttp додатка
app_aiohttp.on_startup.append(on_startup_webhook)
app_aiohttp.on_shutdown.append(on_shutdown_webhook)

if __name__ == '__main__':
    pass
