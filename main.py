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
import aiohttp_cors

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

API_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
WEB_APP_URL = os.getenv('WEB_APP_FRONTEND_URL', 'https://placeholder.com')
WEBHOOK_HOST = os.getenv('WEBHOOK_HOST')
WEBHOOK_PATH = f'/webhook/{API_TOKEN}'
WEBHOOK_URL = f"{WEBHOOK_HOST}{WEBHOOK_PATH}"

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
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
    except Exception as e:
        logger.error(f"DB connection error: {e}")
        raise

def init_db():
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
        logger.info("DB initialized")
    except Exception as e:
        logger.error(f"DB init error: {e}")
    finally:
        if conn:
            conn.close()

def get_user_balance(user_id):
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
        logger.error(f"Balance fetch error for {user_id}: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def update_user_balance(user_id, amount):
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(sql.SQL('''
            INSERT INTO users (user_id, balance) VALUES (%s, %s)
            ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + %s
        '''), (user_id, amount, amount))
        conn.commit()
        logger.info(f"User {user_id} balance updated by {amount}.")
    except Exception as e:
        logger.error(f"Balance update error for {user_id}: {e}")
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

    final_balance = get_user_balance(user_id)
    return {
        'symbols': result_symbols,
        'winnings': winnings,
        'new_balance': final_balance
    }, final_balance

@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db()
    current_balance = get_user_balance(user_id)

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Відкрити Слот-Казино 🎰", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])

    await message.reply(
    f"""Привіт, {message.from_user.first_name}!
Ласкаво просимо до віртуального Слот-Казино!
Ваш поточний баланс: {current_balance} фантиків.
Натисніть кнопку нижче, щоб почати грати!""",
    reply_markup=keyboard
)


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

async def on_startup_webhook(web_app: Application):
    logger.warning('Starting bot and webhook...')
    init_db()
    await bot.set_webhook(WEBHOOK_URL)

async def on_shutdown_webhook(web_app: Application):
    logger.warning('Shutting down bot and webhook...')
    await bot.delete_webhook()

app_aiohttp = Application()

# Додаємо маршрути до того, як CORS буде застосовано
app_aiohttp.router.add_post('/api/get_balance', api_get_balance, name='api_get_balance')
app_aiohttp.router.add_post('/api/spin', api_spin, name='api_spin')

# Тепер застосовуємо CORS
cors = aiohttp_cors.setup(app_aiohttp, defaults={
    "https://my-slot-webapp.onrender.com": aiohttp_cors.ResourceOptions(

        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*",
    )
})

for route in list(app_aiohttp.router.routes()):
    if route.resource and route.resource.name in ['api_get_balance', 'api_spin']:
        cors.add(route)

SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(app_aiohttp, WEBHOOK_PATH)

app_aiohttp.on_startup.append(on_startup_webhook)
app_aiohttp.on_shutdown.append(on_shutdown_webhook)

if __name__ == '__main__':
    pass
