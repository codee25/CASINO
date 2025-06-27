import logging
import os
import json
import random

import psycopg2
from psycopg2 import sql

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart
from aiogram.webhook.aiohttp_server import SimpleRequestHandler
from aiohttp.web import Application, json_response, Request
# AppRunner та TCPSite більше не потрібні, оскільки Gunicorn запускає додаток.
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
# Тепер використовуємо ОДНУ змінну середовища для повного URL підключення
DATABASE_URL = os.getenv('DATABASE_URL')

# ==========================================================
# ПОЧАТОК: ПЕРЕМІЩЕНИЙ БЛОК ІНІЦІАЛІЗАЦІЇ BOT ТА DISPATCHER
# ==========================================================
# Ініціалізація бота та диспетчера ПОВИННА бути перед їхнім використанням
bot = Bot(token=API_TOKEN)
dp = Dispatcher() # Диспетчер ініціалізується без бота у v3
# ==========================================================
# КІНЕЦЬ: ПЕРЕМІЩЕНИЙ БЛОК ІНІЦІАЛІЗАЦІЇ BOT ТА DISPATCHER
# ==========================================================

def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL за URL."""
    try:
        # psycopg2 може підключатися безпосередньо за URL
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except psycopg2.Error as err:
        logger.error(f"Error connecting to PostgreSQL: {err}")
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

# --- Логіка Слот-машини ---
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '�']
BET_AMOUNT = 100

PAYOUTS = {
    ('🍒', '🍒', '🍒'): 1000,
    ('🍋', '🍋', '🍋'): 800,
    ('🍊', '🍊', '🍊'): 600,
    ('🍇', '🍇', '🍇'): 400,
    ('🔔', '🔔', '🔔'): 300,
    ('💎', '💎', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150,
    ('🍒', '🍒'): 100,
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

# --- Обробники Telegram-бота (aiogram v3 синтаксис) ---

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

# --- Обробка запитів від Web App (через aiohttp.web) ---

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
