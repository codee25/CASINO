import logging
import os
import json
import random
import urllib.parse
from datetime import datetime, timedelta, timezone # Додано імпорти для часу

import psycopg2
from psycopg2 import sql

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart, Command
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

ADMIN_ID_STR = os.getenv('ADMIN_ID')
ADMIN_ID = None
try:
    if ADMIN_ID_STR:
        ADMIN_ID = int(ADMIN_ID_STR)
    else:
        logger.warning("ADMIN_ID environment variable is not set. /add_balance command will not work.")
except ValueError:
    logger.error(f"Invalid ADMIN_ID provided in environment variables: '{ADMIN_ID_STR}'. It must be an integer.")
    ADMIN_ID = None

DATABASE_URL = os.getenv('DATABASE_URL')

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

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
    except psycopg2.Error as err:
        logger.error(f"DB connection error: {err}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during DB connection: {e}")
        raise

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Створення таблиці users, якщо вона не існує
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance INTEGER DEFAULT 1000
                -- last_free_coins_claim ДОДАЄТЬСЯ НИЖЧЕ, ЯКЩО ЙОГО НЕМАЄ
            )
        ''')
        conn.commit()
        logger.info("Table 'users' initialized or already exists.")

        # 2. Додавання нового стовпця last_free_coins_claim, якщо його немає
        # Ця операція може викликати виняток, якщо стовпець вже існує, тому використовуємо TRY/EXCEPT
        try:
            cursor.execute('''
                ALTER TABLE users ADD COLUMN IF NOT EXISTS last_free_coins_claim TIMESTAMP DEFAULT NULL;
            ''')
            conn.commit()
            logger.info("Column 'last_free_coins_claim' added or already exists.")
        except psycopg2.Error as e:
            # Ігноруємо помилку, якщо стовпець вже існує (хоча IF NOT EXISTS має це обробляти)
            # Якщо виникла інша помилка ALTER TABLE, її слід було б зареєструвати.
            logger.warning(f"Could not add column 'last_free_coins_claim' (might already exist): {e}")
        
        logger.info("DB schema migration checked.")

    except Exception as e:
        logger.error(f"DB init/migration error: {e}")
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
    ('💎', '�', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150,
    ('🍒', '🍒'): 100,
    ('🍋', '🍋'): 80,
}

def spin_slot(user_id):
    current_balance = get_user_balance(user_id)
    if current_balance < BET_AMOUNT:
        logger.info(f"User {user_id} tried to spin with insufficient balance: {current_balance}.")
        return {'error': 'Недостатньо коштів для спіна!'}, current_balance

    update_user_balance(user_id, -BET_AMOUNT)
    
    result_symbols = [random.choice(SYMBOLS) for _ in range(3)]
    winnings = 0

    if result_symbols[0] == result_symbols[1] == result_symbols[2]:
        winnings = PAYOUTS.get(tuple(result_symbols), 0)
        logger.info(f"User {user_id} hit 3 of a kind: {result_symbols}")
    elif result_symbols[0] == result_symbols[1]:
        winnings = PAYOUTS.get((result_symbols[0], result_symbols[1]), 0)
        logger.info(f"User {user_id} hit 2 of a kind: {result_symbols}")
    elif result_symbols[1] == result_symbols[2]:
        winnings = PAYOUTS.get((result_symbols[1], result_symbols[2]), 0)
        logger.info(f"User {user_id} hit 2 of a kind (middle-right): {result_symbols}")

    if winnings > 0:
        update_user_balance(user_id, winnings)
        logger.info(f"User {user_id} won {winnings}.")
    else:
        logger.info(f"User {user_id} lost on spin. Symbols: {result_symbols}")

    final_balance = get_user_balance(user_id)
    return {
        'symbols': result_symbols,
        'winnings': winnings,
        'new_balance': final_balance
    }, final_balance

@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db() # Викликаємо ініціалізацію схеми БД при старті
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

FREE_COINS_AMOUNT = 500
COOLDOWN_HOURS = 24

@dp.message(Command("get_coins"))
async def get_free_coins_command(message: Message):
    user_id = message.from_user.id
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Отримуємо останній час отримання фантиків та поточний баланс
        cursor.execute('SELECT last_free_coins_claim, balance FROM users WHERE user_id = %s', (user_id,))
        result = cursor.fetchone()
        
        last_claim_time = result[0] if result and result[0] else None
        current_balance = result[1] if result and result[1] is not None else 1000 # Отримуємо поточний баланс або 1000 за замовчуванням

        current_time = datetime.now(timezone.utc)

        if last_claim_time and (current_time - last_claim_time) < timedelta(hours=COOLDOWN_HOURS):
            time_left = timedelta(hours=COOLDOWN_HOURS) - (current_time - last_claim_time)
            hours = int(time_left.total_seconds() // 3600)
            minutes = int((time_left.total_seconds() % 3600) // 60)
            await message.reply(
                f"💰 Ви вже отримували фантики нещодавно. Спробуйте знову через {hours} год {minutes} хв."
            )
            logger.info(f"User {user_id} tried to claim free coins but is on cooldown.")
        else:
            # Додаємо фантики
            new_balance_after_add = current_balance + FREE_COINS_AMOUNT
            cursor.execute(sql.SQL('''
                INSERT INTO users (user_id, balance, last_free_coins_claim) VALUES (%s, %s, %s)
                ON CONFLICT (user_id) DO UPDATE SET balance = users.balance + %s, last_free_coins_claim = %s
            '''), (user_id, FREE_COINS_AMOUNT, current_time, FREE_COINS_AMOUNT, current_time))
            conn.commit()

            await message.reply(
                f"🎉 Вітаємо! Ви отримали {FREE_COINS_AMOUNT} безкоштовних фантиків!\n"
                f"Ваш новий баланс: {new_balance_after_add} фантиків. 🎉"
            )
            logger.info(f"User {user_id} claimed {FREE_COINS_AMOUNT} free coins. New balance: {new_balance_after_add}.")

    except Exception as e:
        logger.error(f"Error handling /get_coins for user {user_id}: {e}")
        await message.reply("Виникла помилка при обробці вашого запиту. Спробуйте пізніше.")
    finally:
        if conn:
            conn.close()

async def api_get_balance(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        logger.warning("api_get_balance: User ID is missing in request.")
        return json_response({'error': 'User ID is required'}, status=400)
    
    balance = get_user_balance(user_id)
    return json_response({'balance': balance})

async def api_spin(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        logger.warning("api_spin: User ID is missing in request.")
        return json_response({'error': 'User ID is required'}, status=400)
    
    result, new_balance = spin_slot(user_id)
    if 'error' in result:
        return json_response(result, status=400)
    
    return json_response(result)

async def on_startup_webhook(web_app: Application):
    logger.warning('Starting bot and webhook...')
    init_db() # Ця функція тепер відповідає за створення/оновлення схеми
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to: {WEBHOOK_URL}")

async def on_shutdown_webhook(web_app: Application):
    logger.warning('Shutting down bot and webhook...')
    await bot.delete_webhook()

app_aiohttp = Application()

app_aiohttp.router.add_post('/api/get_balance', api_get_balance, name='api_get_balance')
app_aiohttp.router.add_post('/api/spin', api_spin, name='api_spin')

cors = aiohttp_cors.setup(app_aiohttp, defaults={
    WEB_APP_URL: aiohttp_cors.ResourceOptions(
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
