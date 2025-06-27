import logging
import os
import json
import random
import urllib.parse # Додаємо для парсингу URL бази даних

import psycopg2
from psycopg2 import sql

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart # Для фільтрації команд
from aiogram.webhook.aiohttp_server import SimpleRequestHandler # Для webhook

from aiohttp.web import Application, json_response, Request # Для веб-сервера та API
# AppRunner та TCPSite більше не потрібні, оскільки Gunicorn запускає додаток.
# Якщо ви запускаєте локально без Gunicorn, розкоментуйте наступний рядок:
# from aiohttp.web_runner import AppRunner, TCPSite 

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
# ПОЧАТОК: ІНІЦІАЛІЗАЦІЯ BOT ТА DISPATCHER
# ЦІ ОБ'ЄКТИ ПОВИННІ БУТИ ВИЗНАЧЕНІ ТУТ, ПЕРЕД ТИМ, ЯК ВОНИ БУДУТЬ ВИКОРИСТАНІ
# У НАЛАШТУВАННЯХ AIOHTTP WEB SERVER
# ==========================================================
bot = Bot(token=API_TOKEN)
dp = Dispatcher() # Диспетчер ініціалізується без бота у v3
# ==========================================================
# КІНЕЦЬ: ІНІЦІАЛІЗАЦІЯ BOT ТА DISPATCHER
# ==========================================================


def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL за URL."""
    conn = None
    try:
        # Парсимо DATABASE_URL, щоб отримати окремі компоненти (хост, порт, користувач, пароль, назву БД)
        url = urllib.parse.urlparse(DATABASE_URL)
        
        conn = psycopg2.connect(
            database=url.path[1:], # Видаляємо перший слеш з шляху, щоб отримати назву БД
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require' # Важливо для Render.com, щоб використовувати SSL
        )
        return conn
    except psycopg2.Error as err:
        logger.error(f"Error connecting to PostgreSQL: {err}")
        raise # Перевикидаємо виняток, щоб проблема була помітна
    except Exception as e: # Загальний виняток для проблем парсингу URL
        logger.error(f"Failed to parse DATABASE_URL or establish connection: {e}")
        raise # Перевикидаємо виняток

def init_db():
    """Ініціалізує таблиці в базі даних PostgreSQL.
    Безпечно викликати багато разів.
    """
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
        # Якщо ініціалізація не вдалася, можливо, з'єднання не було встановлено
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
            # Якщо користувача немає, створити його з початковим балансом
            update_user_balance(user_id, 1000)
            return 1000
    except Exception as e:
        logger.error(f"Error getting user balance from PostgreSQL for {user_id}: {e}")
        return 0 # Повернути 0 або обробити помилку
    finally:
        if conn:
            conn.close()

def update_user_balance(user_id, amount):
    """Оновлює баланс користувача в базі даних PostgreSQL."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        # Для PostgreSQL використовуємо INSERT ... ON CONFLICT (col) DO UPDATE
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
    ('🍒', '🍒'): 100, # Додаємо правило для двох вишні
    ('🍋', '🍋'): 80,  # Додаємо правило для двох лимонів
}

def spin_slot(user_id):
    current_balance = get_user_balance(user_id)
    if current_balance < BET_AMOUNT:
        return {'error': 'Недостатньо коштів для спіна!'}, current_balance

    update_user_balance(user_id, -BET_AMOUNT) # Віднімаємо ставку

    result_symbols = [random.choice(SYMBOLS) for _ in range(3)]
    winnings = 0

    # Перевіряємо виграші
    # Перевірка на 3 однакові символи
    if result_symbols[0] == result_symbols[1] == result_symbols[2]:
        winnings = PAYOUTS.get(tuple(result_symbols), 0)
    # Перевірка на 2 однакові символи, якщо не було 3 однакових
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
async def send_welcome(message: Message): # message: Message - тип для aiogram v3
    user_id = message.from_user.id
    init_db() # Ініціалізуємо БД при першому старті (безпечно викликати багато разів)
    current_balance = get_user_balance(user_id) # Отримуємо або створюємо користувача

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
# Ці ендпоінти будуть інтегровані в той же веб-сервер, що й Telegram webhook

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
    # Ініціалізуємо БД при старті додатку
    init_db()
    # Встановлюємо webhook для Telegram
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


# Цей блок `if __name__ == '__main__':` більше не використовується для прямого запуску
# веб-сервера, оскільки його запускатиме Gunicorn.
# Він може бути корисним для локального тестування (якщо не використовується Gunicorn локально).
if __name__ == '__main__':
    pass # Gunicorn запустить 'app_aiohttp'
