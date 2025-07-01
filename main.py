# main.py - Серверний код FastAPI для Віртуального Казино

import logging
import os
import json
import random
import urllib.parse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

import psycopg2
from psycopg2 import sql

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart, Command
from aiogram.client.default import DefaultBotProperties

from fastapi.middleware.cors import CORSMiddleware

# --- Налаштування логування ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Змінні середовища ---
API_TOKEN = os.getenv('BOT_TOKEN')
WEB_APP_FRONTEND_URL = os.getenv('WEB_APP_FRONTEND_URL')
WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_HOSTNAME')

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

# --- FastAPI App Setup ---
app = FastAPI()

# Налаштування CORS для дозволу запитів з фронтенду
origins = [
    WEB_APP_FRONTEND_URL,
    f"https://{WEBHOOK_HOST}" if WEBHOOK_HOST else None, # Додати HTTPS версію хоста Render
    "http://localhost",
    "http://localhost:3000",
    "http://localhost:5173",
    "http://127.0.0.1:5173", # Додати для локального тестування
    "http://127.0.0.1:3000", # Додати для локального тестування
]
# Фільтруємо None значення, якщо WEBHOOK_HOST не встановлено
origins = [o for o in origins if o is not None]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Монтуємо статичні файли з директорії webapp
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# --- Telegram Bot Webhook Configuration ---
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL: Optional[str] = None

ADMIN_ID_STR = os.getenv('ADMIN_ID')
ADMIN_ID: Optional[int] = None
try:
    if ADMIN_ID_STR:
        ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    logger.error(f"Invalid ADMIN_ID provided: '{ADMIN_ID_STR}'. It must be an integer.")

# --- Налаштування бази даних PostgreSQL ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    logger.critical("DATABASE_URL environment variable is not set. The bot will not be able to connect to the database.")

if not API_TOKEN:
    logger.critical("API_TOKEN (BOT_TOKEN) environment variable not set. Telegram bot will not work.")
    bot = Bot(token="DUMMY_TOKEN", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
else:
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher()

# --- Конфігурація гри ---
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '�'] # Символ "" був виправлений на "🍋"
WILD_SYMBOL = '⭐'
SCATTER_SYMBOL = '💰'
ALL_REEL_SYMBOLS = SYMBOLS + [WILD_SYMBOL, SCATTER_SYMBOL]

BET_AMOUNT = 100 # Ставка для слотів
COIN_FLIP_BET_AMOUNT = 50 # Ставка для підкидання монетки

FREE_COINS_AMOUNT = 500
COOLDOWN_HOURS = 24

DAILY_BONUS_AMOUNT = 300
DAILY_BONUS_COOLDOWN_HOURS = 24

QUICK_BONUS_AMOUNT = 100
QUICK_BONUS_COOLDOWN_MINUTES = 15

XP_PER_SPIN = 10
XP_PER_COIN_FLIP = 5
XP_PER_WIN_MULTIPLIER = 2 
LEVEL_THRESHOLDS = [
    0,     # Level 1: 0 XP
    100,   # Level 2: 100 XP
    300,   # Level 3: 300 XP
    600,   # Level 4: 600 XP
    1000,  # Level 5: 1000 XP
    1500,  # Level 6: 1500 XP
    2200,  # Level 7: 2200 XP
    3000,  # Level 8: 3000 XP
    4000,  # Level 9: 4000 XP
    5500,  # Level 10: 5500 XP
    7500,  # Level 11: 7500 XP
    10000  # Level 12: 10000 XP (and beyond)
]

def get_level_from_xp(xp: int) -> int:
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp < threshold:
            return i 
    return len(LEVEL_THRESHOLDS) # Якщо XP більше або дорівнює останньому порогу

def get_xp_for_next_level(level: int) -> int:
    if level >= len(LEVEL_THRESHOLDS): 
        return LEVEL_THRESHOLDS[-1] # Якщо максимальний рівень, показуємо останній поріг
    return LEVEL_THRESHOLDS[level] 

PAYOUTS = {
    ('🍒', '🍒', '🍒'): 1000, ('🍋', '🍋', '🍋'): 800, ('🍊', '🍊', '🍊'): 600,
    ('🍇', '🍇', '🍇'): 400, ('🔔', '🔔', '🔔'): 300, ('💎', '💎', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150, ('⭐', '⭐', '⭐'): 2000, 
    ('🍒', '🍒'): 100, ('🍋', '🍋'): 80, ('🍊', '🍊'): 60,
    ('🍇', '🍇'): 40, ('🔔', '🔔'): 30, ('💎', '💎'): 20,
    ('🍀', '🍀'): 10,
    ('💰', '💰'): 200, ('💰', '💰', '💰'): 500,
}

# --- Функції для роботи з базою даних ---
def get_db_connection():
    conn = None
    if not DATABASE_URL:
        logger.error("Attempted to connect to DB, but DATABASE_URL is not set.")
        raise ValueError("DATABASE_URL is not configured.")
    try:
        url = urllib.parse.urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:], user=url.username, password=url.password,
            host=url.hostname, port=url.port, sslmode='require', 
            keepalives=1, keepalives_idle=30, keepalives_interval=10, keepalives_count=5
        )
        logger.info("Successfully connected to PostgreSQL database.")
    except psycopg2.Error as err:
        logger.error(f"DB connection error: {err}", exc_info=True)
        raise
    except Exception as e:
        logger.error(f"Unexpected error during DB connection: {e}", exc_info=True)
        raise
    return conn # Повертаємо conn, навіть якщо виникла помилка, щоб finally міг його закрити

def init_db():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY, username TEXT DEFAULT 'Unnamed Player',
                balance INTEGER DEFAULT 10000, xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
                last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL
            )
        ''')
        conn.commit()
        logger.info("Table 'users' initialized or already exists.")

        migrations_to_add = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT DEFAULT 'Unnamed Player';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;"
        ]
        for mig_sql in migrations_to_add:
            try:
                cursor.execute(mig_sql)
                conn.commit()
                logger.info(f"Migration applied: {mig_sql.strip()}")
            except psycopg2.ProgrammingError as e:
                logger.warning(f"Migration skipped/failed (might already exist or specific DB error): {e} -> {mig_sql.strip()}")
                conn.rollback()
        logger.info("DB schema migration checked.")
    except Exception as e:
        logger.error(f"DB init/migration error: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

def get_user_data(user_id: int | str) -> dict:
    user_id_int = int(user_id) 
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT username, balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim FROM users WHERE user_id = %s', 
            (user_id_int,)
        )
        result = cursor.fetchone()
        if result:
            logger.info(f"Retrieved user {user_id_int} data: balance={result[1]}, xp={result[2]}, level={result[3]}")
            last_free_coins_claim_db = result[4]
            if last_free_coins_claim_db and last_free_coins_claim_db.tzinfo is None:
                last_free_coins_claim_db = last_free_coins_claim_db.replace(tzinfo=timezone.utc)
            
            last_daily_bonus_claim_db = result[5]
            if last_daily_bonus_claim_db and last_daily_bonus_claim_db.tzinfo is None:
                last_daily_bonus_claim_db = last_daily_bonus_claim_db.replace(tzinfo=timezone.utc)

            last_quick_bonus_claim_db = result[6]
            if last_quick_bonus_claim_db and last_quick_bonus_claim_db.tzinfo is None:
                last_quick_bonus_claim_db = last_quick_bonus_claim_db.replace(tzinfo=timezone.utc)

            return {
                'username': result[0], 'balance': result[1], 'xp': result[2], 'level': result[3],
                'last_free_coins_claim': last_free_coins_claim_db,
                'last_daily_bonus_claim': last_daily_bonus_claim_db,
                'last_quick_bonus_claim': last_quick_bonus_claim_db
            }
        else:
            initial_balance = 10000
            cursor.execute(
                'INSERT INTO users (user_id, username, balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim) VALUES (%s, %s, %s, %s, %s, %s, %s, %s)', 
                (user_id_int, 'Unnamed Player', initial_balance, 0, 1, None, None, None)
            )
            conn.commit()
            logger.info(f"Created new user {user_id_int} with initial balance {initial_balance}.")
            return {
                'username': 'Unnamed Player', 'balance': initial_balance, 'xp': 0, 'level': 1, 
                'last_free_coins_claim': None, 'last_daily_bonus_claim': None, 'last_quick_bonus_claim': None
            }
    except Exception as e:
        logger.error(f"Error getting user data from PostgreSQL for {user_id_int}: {e}", exc_info=True)
        # Повертаємо дефолтні значення у випадку помилки, щоб фронтенд не зависав
        return {
            'username': 'Error Player', 'balance': 0, 'xp': 0, 'level': 1, 
            'last_free_coins_claim': None, 'last_daily_bonus_claim': None, 'last_quick_bonus_claim': None
        }
    finally:
        if conn:
            conn.close()

def update_user_data(user_id: int | str, **kwargs):
    user_id_int = int(user_id)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # Отримуємо поточні дані, щоб зберегти ті поля, які не оновлюються
        current_data_from_db = get_user_data(user_id_int) 
        logger.info(f"Before update for {user_id_int}: {current_data_from_db.get('balance', 'N/A')} balance, {current_data_from_db.get('xp', 'N/A')} xp, {current_data_from_db.get('level', 'N/A')} level.")

        update_fields_parts = []
        update_values = []

        # Формуємо словник з полями для оновлення, використовуючи kwargs або поточні дані
        fields_to_update = {
            'username': kwargs.get('username', current_data_from_db.get('username', 'Unnamed Player')),
            'balance': kwargs.get('balance', current_data_from_db.get('balance', 0)),
            'xp': kwargs.get('xp', current_data_from_db.get('xp', 0)),
            'level': kwargs.get('level', current_data_from_db.get('level', 1)),
            'last_free_coins_claim': kwargs.get('last_free_coins_claim', current_data_from_db.get('last_free_coins_claim')),
            'last_daily_bonus_claim': kwargs.get('last_daily_bonus_claim', current_data_from_db.get('last_daily_bonus_claim')),
            'last_quick_bonus_claim': kwargs.get('last_quick_bonus_claim', current_data_from_db.get('last_quick_bonus_claim'))
        }
        
        # Переконатися, що всі дати мають timezone info
        for key in ['last_free_coins_claim', 'last_daily_bonus_claim', 'last_quick_bonus_claim']:
            if fields_to_update[key] and fields_to_update[key].tzinfo is None:
                fields_to_update[key] = fields_to_update[key].replace(tzinfo=timezone.utc)

        for field, value in fields_to_update.items():
            update_fields_parts.append(sql.SQL("{} = %s").format(sql.Identifier(field)))
            update_values.append(value)
        
        if not update_fields_parts:
            logger.info(f"No fields specified for update for user {user_id_int}.")
            return

        update_query = sql.SQL('''
            UPDATE users SET {fields} WHERE user_id = %s
        ''').format(fields=sql.SQL(', ').join(update_fields_parts))

        update_values.append(user_id_int)

        cursor.execute(update_query, update_values)
        conn.commit()
        logger.info(f"User {user_id_int} data updated. New balance: {fields_to_update.get('balance')}, XP: {fields_to_update.get('xp')}, Level: {fields_to_update.get('level')}.")
    except Exception as e:
        logger.error(f"Error updating user data in PostgreSQL for {user_id_int}: {e}", exc_info=True)
    finally:
        if conn:
            conn.close()

def check_win_conditions(symbols: List[str]) -> int:
    winnings = 0
    s1, s2, s3 = symbols
    logger.info(f"Checking win conditions for symbols: {symbols}")

    scatter_count = symbols.count(SCATTER_SYMBOL)
    if scatter_count == 3:
        winnings = PAYOUTS.get((SCATTER_SYMBOL, SCATTER_SYMBOL, SCATTER_SYMBOL), 0)
        logger.info(f"3 Scatters detected! Winnings: {winnings}")
        return winnings
    elif scatter_count == 2:
        winnings = PAYOUTS.get((SCATTER_SYMBOL, SCATTER_SYMBOL), 0)
        logger.info(f"2 Scatters detected! Winnings: {winnings}")
        return winnings

    if s1 == WILD_SYMBOL and s2 == WILD_SYMBOL and s3 == WILD_SYMBOL:
        winnings = PAYOUTS.get(('⭐', '⭐', '⭐'), 0)
        logger.info(f"3 Wilds detected! Winnings: {winnings}")
        return winnings

    for base_symbol in SYMBOLS:
        match_count = 0
        if s1 == base_symbol or s1 == WILD_SYMBOL: match_count += 1
        if s2 == base_symbol or s2 == WILD_SYMBOL: match_count += 1
        if s3 == base_symbol or s3 == WILD_SYMBOL: match_count += 1
        
        if match_count == 3:
            winnings = PAYOUTS.get(tuple([base_symbol] * 3), 0)
            logger.info(f"3-of-a-kind (or with Wild) for {base_symbol} detected! Winnings: {winnings}")
            return winnings
    
    for base_symbol in SYMBOLS:
        if (s1 == base_symbol or s1 == WILD_SYMBOL) and \
           (s2 == base_symbol or s2 == WILD_SYMBOL):
            # Перевірка, що третій символ НЕ є тим самим базовим символом/вайлдом або скаттером,
            # щоб уникнути подвійного підрахунку 3-в-ряд або скаттерів
            if not ((s3 == base_symbol or s3 == WILD_SYMBOL) or s3 == SCATTER_SYMBOL):
                winnings = PAYOUTS.get((base_symbol, base_symbol), 0)
                logger.info(f"2-of-a-kind (or with Wild) for {base_symbol} detected! Winnings: {winnings}")
                return winnings
    
    logger.info(f"No winning combination found for symbols: {symbols}. Winnings: 0")
    return winnings 

def spin_slot_logic(user_id: int | str) -> Dict:
    user_data = get_user_data(user_id)
    current_balance = user_data['balance']
    current_xp = user_data['xp']
    current_level = user_data['level']

    logger.info(f"Spin requested for user {user_id}. Current balance: {current_balance}, XP: {current_xp}.")

    if current_balance < BET_AMOUNT:
        logger.info(f"User {user_id} tried to spin with insufficient balance: {current_balance}.")
        return {'error': 'Недостатньо коштів для спіна!'}

    result_symbols = [random.choice(ALL_REEL_SYMBOLS) for _ in range(3)]
    winnings = check_win_conditions(result_symbols)

    new_balance = current_balance - BET_AMOUNT + winnings
    xp_gained = XP_PER_SPIN
    if winnings > 0:
        xp_gained += (XP_PER_SPIN * XP_PER_WIN_MULTIPLIER)
        logger.info(f"User {user_id} won {winnings} with symbols {result_symbols}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    else:
        logger.info(f"User {user_id} lost on spin. Symbols: {result_symbols}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    
    new_xp = current_xp + xp_gained
    new_level = get_level_from_xp(new_xp)

    # Вирівнювання XP, якщо перевищує поріг для поточного рівня, але не досягає наступного
    if new_level == current_level and new_xp >= get_xp_for_next_level(current_level):
        new_xp = get_xp_for_next_level(current_level) - 1 # Залишаємо XP трохи менше порогу, щоб рівень не підвищувався раніше часу

    level_up_message = ""
    if new_level > current_level:
        level_up_message = f" 🎉 НОВИЙ РІВЕНЬ: {new_level}! 🎉"

    update_user_data(user_id, balance=new_balance, xp=new_xp, level=new_level)

    final_user_data = get_user_data(user_id) # Отримати оновлені дані після збереження
    
    return {
        'symbols': result_symbols, 'winnings': winnings, 'balance': final_user_data['balance'],
        'xp': final_user_data['xp'], 'level': final_user_data['level'],
        'next_level_xp': get_xp_for_next_level(final_user_data['level']),
        'message': level_up_message 
    }

def coin_flip_game_logic(user_id: int | str, choice: str) -> Dict:
    user_data = get_user_data(user_id)
    current_balance = user_data['balance']
    current_xp = user_data['xp']
    current_level = user_data['level']

    logger.info(f"Coin flip requested for user {user_id}. Choice: {choice}. Current balance: {current_balance}, XP: {current_xp}.")

    if current_balance < COIN_FLIP_BET_AMOUNT:
        logger.info(f"User {user_id} tried to coin flip with insufficient balance: {current_balance}.")
        return {'error': 'Недостатньо коштів для підкидання монетки!'}

    coin_result = random.choice(['heads', 'tails'])
    winnings = 0
    message = ""

    new_balance = current_balance - COIN_FLIP_BET_AMOUNT 
    xp_gained = XP_PER_COIN_FLIP

    if choice == coin_result:
        winnings = COIN_FLIP_BET_AMOUNT * 2 # Double the bet
        new_balance += winnings 
        message = f"🎉 Вітаємо! Монета показала {coin_result == 'heads' and 'Орла' or 'Решку'}! Ви виграли {winnings} фантиків!"
        xp_gained += (XP_PER_COIN_FLIP * XP_PER_WIN_MULTIPLIER) 
        logger.info(f"User {user_id} won coin flip. Result: {coin_result}. Winnings: {winnings}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    else:
        message = f"😢 На жаль, монета показала {coin_result == 'heads' and 'Орла' or 'Решку'}. Спробуйте ще раз!"
        logger.info(f"User {user_id} lost coin flip. Result: {coin_result}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    
    new_xp = current_xp + xp_gained
    new_level = get_level_from_xp(new_xp)

    # Вирівнювання XP
    if new_level == current_level and new_xp >= get_xp_for_next_level(current_level):
        new_xp = get_xp_for_next_level(current_level) - 1

    level_up_message = ""
    if new_level > current_level:
        level_up_message = f" 🎉 НОВИЙ РІВЕНЬ: {new_level}! 🎉"

    update_user_data(user_id, balance=new_balance, xp=new_xp, level=new_level)

    final_user_data = get_user_data(user_id) # Отримати оновлені дані після збереження

    return {
        'result': coin_result, 'winnings': winnings, 'balance': final_user_data['balance'],
        'message': message + level_up_message, 
        'xp': final_user_data['xp'], 'level': final_user_data['level'],
        'next_level_xp': get_xp_for_next_level(final_user_data['level'])
    }

# --- Обробники Telegram-бота (aiogram v3 синтаксис) ---
@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db() # Переконатися, що БД ініціалізована
    
    user_data = get_user_data(user_id)
    logger.info(f"CommandStart: User {user_id} fetched data: {user_data}")
    
    telegram_username = message.from_user.username
    telegram_first_name = message.from_user.first_name
    
    updated_username = user_data['username']
    if telegram_username and user_data['username'] != telegram_username:
        update_user_data(user_id, username=telegram_username)
        updated_username = telegram_username
    elif telegram_first_name and user_data['username'] == 'Unnamed Player':
        update_user_data(user_id, username=telegram_first_name)
        updated_username = telegram_first_name
    
    user_data = get_user_data(user_id) # Оновити дані після можливого оновлення username
    
    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Відкрити Слот-Казино 🎰", web_app=WebAppInfo(url=WEB_APP_FRONTEND_URL))]
    ])

    await message.reply(
        f"Привіт, {user_data['username']}!\n"
        f"Ласкаво просимо до віртуального Слот-Казино!\n"
        f"Ваш поточний баланс: {user_data['balance']} фантиків.\n"
        f"Натисніть кнопку нижче, щоб почати грати!",
        reply_markup=keyboard
    )
    logger.info(f"User {user_id} ({user_data['username']}) started the bot. Balance: {user_data['balance']}.")

@dp.message(Command("add_balance"))
async def add_balance_command(message: Message):
    user_id = message.from_user.id
    
    if ADMIN_ID is None or user_id != ADMIN_ID:
        await message.reply("У вас немає дозволу на використання цієї команди.")
        logger.warning(f"User {user_id} tried to use /add_balance without admin privileges.")
        return

    args = message.text.split()
    if len(args) != 2:
        await message.reply("Будь ласка, вкажіть суму для додавання. Використання: `/add_balance <сума>`")
        return

    try:
        amount = int(args[1])
        if amount <= 0:
            await message.reply("Сума має бути позитивним числом.")
            return
    except ValueError:
        await message.reply("Невірна сума. Будь ласка, введіть число.")
        return

    current_user_data = get_user_data(user_id)
    new_balance = current_user_data['balance'] + amount
    update_user_data(user_id, balance=new_balance)
    updated_user_data = get_user_data(user_id)

    await message.reply(f"🎉 {amount} фантиків успішно додано! Ваш новий баланс: {updated_user_data['balance']} фантиків. 🎉")
    logger.info(f"Admin {user_id} added {amount} to their balance. New balance: {updated_user_data['balance']}.")

@dp.message(Command("give_balance"))
async def give_balance_command(message: Message):
    sender_id = message.from_user.id
    logger.info(f"Attempting to use /give_balance command by user {sender_id}. ADMIN_ID is set to: {ADMIN_ID}")

    if ADMIN_ID is None:
        logger.warning(f"ADMIN_ID is not set. User {sender_id} cannot use /give_balance.")
        await message.reply("Помилка: ADMIN_ID не налаштовано на сервері. Ця команда недоступна.")
        return
    
    if sender_id != ADMIN_ID:
        logger.warning(f"User {sender_id} tried to use /give_balance without admin privileges (ADMIN_ID: {ADMIN_ID}).")
        await message.reply("У вас немає дозволу на використання цієї команди.")
        return

    args = message.text.split()
    if len(args) != 3:
        await message.reply("Будь ласка, вкажіть ID гравця та суму. Використання: `/give_balance <user_id> <amount>`")
        logger.warning(f"Admin {sender_id} used /give_balance with incorrect number of arguments: {message.text}")
        return

    try:
        target_user_id = int(args[1])
        amount = int(args[2])
        if amount <= 0:
            await message.reply("Сума має бути позитивним числом.")
            logger.warning(f"Admin {sender_id} tried to give non-positive amount: {amount}")
            return
    except ValueError:
        await message.reply("Невірна ID гравця або сума. Будь ласка, введіть числові значення.")
        logger.warning(f"Admin {sender_id} used /give_balance with non-integer arguments: {args[1]}, {args[2]}")
        return

    target_user_data = get_user_data(target_user_id)
    if target_user_data['balance'] == 0 and target_user_data['username'] == 'Error Player': # This is a heuristic for "user not found"
        await message.reply(f"Користувача з ID {target_user_id} не знайдено або сталася помилка при отриманні його даних.")
        logger.warning(f"Admin {sender_id} tried to give balance to non-existent or error user {target_user_id}.")
        return

    new_balance = target_user_data['balance'] + amount
    update_user_data(target_user_id, balance=new_balance)
    updated_target_user_data = get_user_data(target_user_id)

    await message.reply(
        f"🎉 {amount} фантиків успішно додано гравцю {updated_target_user_data['username']} (ID: {target_user_id})! "
        f"Його новий баланс: {updated_target_user_data['balance']} фантиків. 🎉"
    )
    logger.info(f"Admin {sender_id} gave {amount} to user {target_user_id}. New balance: {updated_target_user_data['balance']}.")


@dp.message(Command("get_coins"))
async def get_free_coins_command(message: Message):
    user_id = message.from_user.id
    
    if len(message.text.split()) > 1:
        await message.reply("Ця команда не приймає аргументів. Використання: `/get_coins`")
        logger.warning(f"User {user_id} used /get_coins with unexpected arguments: {message.text}")
        return

    user_data = get_user_data(user_id)
    last_claim_time = user_data['last_free_coins_claim']

    current_time = datetime.now(timezone.utc)
    cooldown_duration = timedelta(hours=COOLDOWN_HOURS)

    if last_claim_time and (current_time - last_claim_time) < cooldown_duration:
        time_left = cooldown_duration - (current_time - last_claim_time)
        hours = int(time_left.total_seconds() // 3600)
        minutes = int((time_left.total_seconds() % 3600) // 60)
        await message.reply(
            f"💰 Ви вже отримували фантики нещодавно. Спробуйте знову через {hours} год {minutes} хв."
        )
        logger.info(f"User {user_id} tried to claim free coins but is on cooldown.")
    else:
        new_balance = user_data['balance'] + FREE_COINS_AMOUNT
        update_user_data(user_id, balance=new_balance, last_free_coins_claim=current_time)
        updated_user_data = get_user_data(user_id)
        await message.reply(
            f"🎉 Вітаємо! Ви отримали {FREE_COINS_AMOUNT} безкоштовних фантиків!\n"
            f"Ваш новий баланс: {updated_user_data['balance']} фантиків. 🎉"
        )
        logger.info(f"User {user_id} claimed {FREE_COINS_AMOUNT} free coins. New balance: {updated_user_data['balance']}.")

@dp.message(lambda msg: msg.web_app_data)
async def web_app_data_handler(message: Message):
    user_id = message.from_user.id
    data_from_webapp = message.web_app_data.data
    
    logger.info(f"Received data from WebApp for user {user_id}: {data_from_webapp}")

    # Обробка логів від фронтенду
    if data_from_webapp.startswith('JS_VERY_FIRST_LOG:'):
        # Це початковий лог, можливо, не потрібно відповідати користувачу, лише логувати
        logger.info(f"WebApp JS_VERY_FIRST_LOG for {user_id}: {data_from_webapp.replace('JS_VERY_FIRST_LOG:', '').strip()}")
    elif data_from_webapp.startswith('JS_LOG:'):
        logger.info(f"WebApp JS_LOG for {user_id}: {data_from_webapp.replace('JS_LOG:', '').strip()}")
    elif data_from_webapp.startswith('JS_DEBUG:'):
        logger.debug(f"WebApp JS_DEBUG for {user_id}: {data_from_webapp.replace('JS_DEBUG:', '').strip()}")
    elif data_from_webapp.startswith('JS_WARN:'):
        logger.warning(f"WebApp JS_WARN for {user_id}: {data_from_webapp.replace('JS_WARN:', '').strip()}")
    elif data_from_webapp.startswith('JS_ERROR:'):
        # На помилки краще відповісти користувачу, щоб він знав
        await message.answer(f"❌ WebApp Error: {data_from_webapp.replace('JS_ERROR:', '').strip()}")
    elif data_from_webapp.startswith('JS_FATAL_REACT_ERROR:'):
        await message.answer(f"❌ Критична помилка WebApp: {data_from_webapp.replace('JS_FATAL_REACT_ERROR:', '').strip()} Будь ласка, спробуйте перезапустити гру.")
    elif data_from_webapp.startswith('JS_FATAL_REACT_MOUNT_ERROR:'):
        await message.answer(f"❌ Критична помилка WebApp (запуск): {data_from_webapp.replace('JS_FATAL_REACT_MOUNT_ERROR:', '').strip()} Зверніться до адміністратора.")
    else:
        # Для інших невідомих даних, просто логуємо
        logger.info(f"WebApp Unhandled Data for {user_id}: {data_from_webapp}")

# --- FastAPI API Endpoints ---
class UserRequest(BaseModel):
    user_id: int | str
    username: Optional[str] = None

class SpinRequest(BaseModel):
    user_id: int | str

class CoinFlipRequest(BaseModel):
    user_id: int | str
    choice: str

class ClaimBonusRequest(BaseModel):
    user_id: int | str

class BlackjackActionRequest(BaseModel):
    user_id: int | str
    room_id: str
    action: str
    amount: Optional[int] = None

@app.post("/api/get_balance")
async def api_get_balance(user_req: UserRequest):
    user_id = user_req.user_id
    username_from_frontend = user_req.username

    user_data = get_user_data(user_id) # Отримати дані користувача
    
    # Оновлення імені користувача, якщо воно змінилося або є "Unnamed Player"
    if username_from_frontend and user_data['username'] != username_from_frontend:
        # Оновлюємо, якщо ім'я з фронтенду не 'Unnamed Player'
        # Або якщо поточне ім'я в БД є 'Unnamed Player' (тобто, вперше встановлюємо реальне ім'я)
        if username_from_frontend != 'Unnamed Player' or user_data['username'] == 'Unnamed Player':
            update_user_data(user_id, username=username_from_frontend)
            user_data['username'] = username_from_frontend # Оновити в локальній змінній для відповіді

    return {
        'username': user_data['username'], 'balance': user_data['balance'],
        'xp': user_data['xp'], 'level': user_data['level'],
        'next_level_xp': get_xp_for_next_level(user_data['level']),
        'last_daily_bonus_claim': user_data['last_daily_bonus_claim'].isoformat() if user_data['last_daily_bonus_claim'] else None,
        'last_quick_bonus_claim': user_data['last_quick_bonus_claim'].isoformat() if user_data['last_quick_bonus_claim'] else None
    }

@app.post("/api/spin")
async def api_spin(spin_req: SpinRequest):
    user_id = spin_req.user_id
    result = spin_slot_logic(user_id)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/api/coin_flip")
async def api_coin_flip(flip_req: CoinFlipRequest):
    user_id = flip_req.user_id
    choice = flip_req.choice
    if choice not in ['heads', 'tails']:
        raise HTTPException(status_code=400, detail="Invalid choice. Must be 'heads' or 'tails'.")
    result = coin_flip_game_logic(user_id, choice)
    if 'error' in result:
        raise HTTPException(status_code=400, detail=result['error'])
    return result

@app.post("/api/claim_daily_bonus")
async def api_claim_daily_bonus(claim_req: ClaimBonusRequest):
    user_id = claim_req.user_id
    user_data = get_user_data(user_id)
    last_claim_time = user_data['last_daily_bonus_claim']
    current_time = datetime.now(timezone.utc)
    cooldown_duration = timedelta(hours=DAILY_BONUS_COOLDOWN_HOURS)
    if last_claim_time and (current_time - last_claim_time) < cooldown_duration:
        time_left = cooldown_duration - (current_time - last_claim_time)
        hours = int(time_left.total_seconds() // 3600)
        minutes = int((time_left.total_seconds() % 3600) // 60)
        seconds = int(time_left.total_seconds() % 60)
        raise HTTPException(
            status_code=403, detail=f"Будь ласка, зачекайте {hours:02d}:{minutes:02d}:{seconds:02d} до наступного бонусу."
        )
    else:
        new_balance = user_data['balance'] + DAILY_BONUS_AMOUNT
        new_xp = user_data['xp'] + 20 # Додаємо XP за щоденний бонус
        new_level = get_level_from_xp(new_xp)
        update_user_data(user_id, balance=new_balance, last_daily_bonus_claim=current_time, xp=new_xp, level=new_level)
        updated_user_data = get_user_data(user_id) # Отримати оновлені дані після збереження
        return {
            'message': 'Бонус успішно отримано!', 'amount': DAILY_BONUS_AMOUNT,
            'balance': updated_user_data['balance'], 'xp': updated_user_data['xp'],
            'level': updated_user_data['level'], 'next_level_xp': get_xp_for_next_level(updated_user_data['level'])
        }

@app.post("/api/claim_quick_bonus")
async def api_claim_quick_bonus(claim_req: ClaimBonusRequest):
    user_id = claim_req.user_id
    user_data = get_user_data(user_id)
    last_claim_time = user_data['last_quick_bonus_claim']
    current_time = datetime.now(timezone.utc)
    cooldown_duration = timedelta(minutes=QUICK_BONUS_COOLDOWN_MINUTES)
    if last_claim_time and (current_time - last_claim_time) < cooldown_duration:
        time_left = cooldown_duration - (current_time - last_claim_time)
        minutes = int(time_left.total_seconds() // 60)
        seconds = int(time_left.total_seconds() % 60)
        raise HTTPException(
            status_code=403, detail=f"Будь ласка, зачекайте {minutes:02d}:{seconds:02d} до наступного швидкого бонусу."
        )
    else:
        new_balance = user_data['balance'] + QUICK_BONUS_AMOUNT
        new_xp = user_data['xp'] + 5 # Додаємо XP за швидкий бонус
        new_level = get_level_from_xp(new_xp)
        update_user_data(user_id, balance=new_balance, last_quick_bonus_claim=current_time, xp=new_xp, level=new_level)
        updated_user_data = get_user_data(user_id) # Отримати оновлені дані після збереження
        return {
            'message': 'Швидкий бонус успішно отримано!', 'amount': QUICK_BONUS_AMOUNT,
            'balance': updated_user_data['balance'], 'xp': updated_user_data['xp'],
            'level': updated_user_data['level'], 'next_level_xp': get_xp_for_next_level(updated_user_data['level'])
        }

@app.post("/api/get_leaderboard")
async def api_get_leaderboard():
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT user_id, username, balance, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 100;'
        )
        leaderboard_raw = cursor.fetchall()
        leaderboard_entries = []
        for row in leaderboard_raw:
            leaderboard_entries.append({
                "user_id": row[0], "username": row[1], "balance": row[2], "xp": row[3], "level": row[4]
            })
        return {"leaderboard": leaderboard_entries}
    except Exception as e:
        logger.error(f"Error fetching leaderboard from PostgreSQL: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail="Error fetching leaderboard data.")
    finally:
        if conn:
            conn.close()

# --- Blackjack Game Logic (Server-side) ---
class Card:
    def __init__(self, suit: str, rank: str):
        self.suit = suit
        self.rank = rank
    def __str__(self): return f"{self.rank}{self.suit}"
    def value(self) -> int:
        if self.rank in ['J', 'Q', 'K']: return 10
        elif self.rank == 'A': return 11 # Початкове значення для туза
        else: return int(self.rank)

class Deck:
    def __init__(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        self.cards: List[Card] = [Card(suit, rank) for suit in suits for rank in ranks]
        random.shuffle(self.cards)
    def deal_card(self) -> Card:
        if not self.cards: 
            self.__init__() # Перетасувати колоду, якщо вона порожня
            logger.info("Reshuffling deck!")
        return self.cards.pop()

class Hand:
    def __init__(self):
        self.cards: List[Card] = []
        self.value: int = 0
        self.aces: int = 0
    
    def add_card(self, card: Card):
        self.cards.append(card)
        self.value += card.value()
        if card.rank == 'A': self.aces += 1
        # Обробка тузів (зміна 11 на 1, якщо перебір)
        while self.value > 21 and self.aces:
            self.value -= 10
            self.aces -= 1
    
    def to_json(self) -> List[str]: 
        return [str(card) for card in self.cards]

class BlackjackPlayer:
    def __init__(self, user_id: int, username: str, websocket: WebSocket):
        self.user_id = user_id
        self.username = username
        self.websocket = websocket
        self.hand = Hand()
        self.bet = 0
        self.is_playing = True # Чи гравець ще в раунді (не перебрав, не зупинився)
        self.has_bet = False # Чи зробив гравець ставку в поточному раунді
    
    def reset_for_round(self):
        self.hand = Hand()
        self.bet = 0
        self.is_playing = True
        self.has_bet = False

class BlackjackRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[int, BlackjackPlayer] = {}
        self.status = "waiting" # waiting, starting_timer, betting, playing, round_end
        self.deck = Deck()
        self.current_turn_index = 0 # Індекс гравця, чий зараз хід
        self.min_players = 2 
        self.max_players = 5
        self.game_start_timer: Optional[asyncio.Task] = None # Таймер для початку гри
        self.betting_timer: Optional[asyncio.Task] = None # Таймер для фази ставок
        self.action_timer: Optional[asyncio.Task] = None # Таймер для ходу гравця
        self.timer_countdown: int = 0 # Загальний лічильник для відображення на фронтенді
        self.round_in_progress = False 
        self.ping_task: Optional[asyncio.Task] = None 

        logger.info(f"BlackjackRoom {self.room_id} created.")

    async def _start_game_after_delay(self, room_id: str, delay: int):
        """Внутрішня функція для запуску таймера гри (від очікування до ставок)."""
        room = blackjack_room_manager.rooms.get(room_id)
        if not room:
            logger.warning(f"Attempted to start timer for non-existent room {room_id}.")
            return
        
        logger.info(f"Room {room_id}: Game start timer countdown started from {delay} seconds. Status: {room.status}")
        for i in range(delay, 0, -1):
            room.timer_countdown = i
            # Перевіряємо умови виходу з таймера: статус змінився або гравців стало менше
            if room.status != "starting_timer" or len(room.players) < room.min_players:
                logger.info(f"Room {room_id} timer cancelled/interrupted. Status: {room.status}, Players: {len(room.players)}")
                if len(room.players) < room.min_players:
                    room.status = "waiting" # Повертаємося до очікування, якщо гравців стало менше
                room.timer_countdown = 0
                await room.send_room_state_to_all()
                return
            await room.send_room_state_to_all()
            await asyncio.sleep(1)
        
        # Таймер завершено
        if room.status == "starting_timer" and len(room.players) >= room.min_players:
            logger.info(f"Room {room_id}: Timer finished, moving to betting phase.")
            room.status = "betting"
            room.timer_countdown = 0 # Скидаємо таймер
            await room.send_room_state_to_all()
            # Запускаємо таймер для фази ставок
            room.betting_timer = asyncio.create_task(room._start_betting_timer(room_id, 20)) # 20 секунд на ставки
        else:
            logger.info(f"Room {room_id}: Timer finished but conditions not met for betting. Status: {room.status}, Players: {len(room.players)}")
            room.status = "waiting" # Повертаємося до очікування, якщо умови не виконані
            room.timer_countdown = 0
            await room.send_room_state_to_all()

    async def _start_betting_timer(self, room_id: str, delay: int):
        """Таймер для фази ставок."""
        room = blackjack_room_manager.rooms.get(room_id)
        if not room:
            logger.warning(f"Attempted to start betting timer for non-existent room {room_id}.")
            return
        
        logger.info(f"Room {room_id}: Betting timer countdown started from {delay} seconds. Status: {room.status}")
        room.timer_countdown = delay # Встановлюємо таймер для відображення
        await room.send_room_state_to_all()

        for i in range(delay, 0, -1):
            # Якщо всі гравці вже зробили ставки, або статус змінився, скасовуємо таймер
            if room.status != "betting" or all(p.has_bet for p in room.players.values()):
                logger.info(f"Room {room_id}: Betting timer cancelled, status changed to {room.status} or all players bet.")
                room.timer_countdown = 0
                return
            room.timer_countdown = i
            await room.send_room_state_to_all()
            await asyncio.sleep(1)
        
        # Таймер ставок завершено
        if room.status == "betting":
            logger.info(f"Room {room_id}: Betting timer finished. Forcing check for round start.")
            # Позначаємо гравців, які не поставили, як not playing
            for player in room.players.values():
                if not player.has_bet:
                    player.is_playing = False
                    player.has_bet = True # Щоб вони не блокували перехід до наступного раунду
                    logger.info(f"Player {player.user_id} did not bet in time, marked as not playing for this round.")
            await room.send_room_state_to_all() # Оновити стан після примусового завершення ставок
            room._check_and_start_round_if_ready()
        room.timer_countdown = 0 # Скидаємо таймер після завершення

    async def _start_action_timer(self, room_id: str, delay: int, user_id: int):
        """Таймер для ходу поточного гравця (hit/stand)."""
        room = blackjack_room_manager.rooms.get(room_id)
        if not room:
            logger.warning(f"Attempted to start action timer for non-existent room {room_id}.")
            return
        
        logger.info(f"Room {room_id}: Action timer for player {user_id} started from {delay} seconds. Status: {room.status}")
        room.timer_countdown = delay
        await room.send_room_state_to_all()

        for i in range(delay, 0, -1):
            # Перевіряємо, чи хід все ще за цим гравцем і гра в стані "playing"
            if room.status != "playing" or room.get_current_player() is None or room.get_current_player().user_id != user_id:
                logger.info(f"Room {room_id}: Action timer for {user_id} cancelled/interrupted. Status: {room.status}, Current player: {room.get_current_player().user_id if room.get_current_player() else 'None'}")
                room.timer_countdown = 0
                return
            room.timer_countdown = i
            await room.send_room_state_to_all()
            await asyncio.sleep(1)
        
        # Таймер ходу гравця завершено
        if room.status == "playing" and room.get_current_player() and room.get_current_player().user_id == user_id:
            logger.info(f"Room {room_id}: Action timer for player {user_id} finished. Forcing stand.")
            await room.handle_action(user_id, "stand") # Автоматично "stand"
        room.timer_countdown = 0 # Скидаємо таймер

    async def add_player(self, user_id: int, username: str, websocket: WebSocket):
        if len(self.players) >= self.max_players:
            return False, "Кімната повна."
        
        if user_id in self.players:
            # Якщо гравець вже в кімнаті, оновлюємо його WebSocket
            self.players[user_id].websocket = websocket
            # Якщо гравець був позначений як "не грає" в попередньому раунді, повертаємо його в активний стан
            self.players[user_id].is_playing = True
            logger.info(f"Player {user_id} reconnected to room {self.room_id}")
            await self.send_room_state_to_all()
            return True, "Ви успішно перепідключились до кімнати."

        player = BlackjackPlayer(user_id, username, websocket)
        self.players[user_id] = player
        logger.info(f"Player {user_id} ({username}) added to room {self.room_id}. Current players: {len(self.players)}")
        
        await self.send_room_state_to_all()

        # Якщо досягнуто мінімальної кількості гравців і гра не йде, запускаємо таймер
        if len(self.players) >= self.min_players and self.status == "waiting" and not self.round_in_progress:
            self.status = "starting_timer"
            if self.game_start_timer and not self.game_start_timer.done():
                self.game_start_timer.cancel()
            self.timer_countdown = 20 # Таймер на 20 секунд
            self.game_start_timer = asyncio.create_task(self._start_game_after_delay(self.room_id, self.timer_countdown))
            logger.info(f"Room {self.room_id}: Game start timer initiated for {self.timer_countdown} seconds.")
            await self.send_room_state_to_all() # Оновити стан з таймером
        
        # Запускаємо ping-pong для підтримки з'єднання, якщо ще не запущено
        if not self.ping_task or (self.ping_task and self.ping_task.done()):
            self.ping_task = asyncio.create_task(self._ping_players())
            logger.info(f"Room {self.room_id}: Ping task started.")

        return True, "Приєднано до кімнати успішно."

    async def remove_player(self, user_id: int):
        if user_id in self.players:
            username = self.players[user_id].username
            del self.players[user_id]
            # Видаляємо з мапінгу player_to_room
            if user_id in blackjack_room_manager.player_to_room:
                del blackjack_room_manager.player_to_room[user_id]

            logger.info(f"Player {username} ({user_id}) removed from room {self.room_id}")
            
            if not self.players:
                # Якщо кімната порожня, скасувати всі таймери і видалити кімнату
                if self.game_start_timer and not self.game_start_timer.done():
                    self.game_start_timer.cancel()
                if self.betting_timer and not self.betting_timer.done():
                    self.betting_timer.cancel()
                if self.action_timer and not self.action_timer.done():
                    self.action_timer.cancel()
                if self.ping_task and not self.ping_task.done():
                    self.ping_task.cancel()
                    logger.info(f"Room {self.room_id}: Ping task cancelled.")
                if self.room_id in blackjack_room_manager.rooms:
                    del blackjack_room_manager.rooms[self.room_id]
                logger.info(f"Room {self.room_id} is empty and removed.")
            else:
                # Якщо гравець, що вийшов, був поточним гравцем або гравець був останнім активним
                if self.status == "playing":
                    active_players_after_removal = [p for p in self.players.values() if p.is_playing]
                    if not active_players_after_removal: # Всі активні гравці вийшли
                        logger.info(f"Room {self.room_id}: All active players left, ending round.")
                        await self.end_round() # Завершити раунд, якщо немає активних гравців
                    elif self.get_current_player() is None or self.get_current_player().user_id == user_id: # Якщо поточний гравець вийшов
                        logger.info(f"Room {self.room_id}: Current player {user_id} left, moving to next turn.")
                        await self.next_turn() # Передати хід наступному
                elif self.status == "betting":
                    # Якщо гравець вийшов під час ставок, перевірити, чи можна почати раунд
                    logger.info(f"Room {self.room_id}: Player {user_id} left during betting. Re-checking round start conditions.")
                    self._check_and_start_round_if_ready() # Перевірити, чи всі інші вже поставили
                
                await self.send_room_state_to_all() # Оновити стан для решти гравців
        else:
            logger.warning(f"Player {user_id} not found in room {self.room_id} for removal.")

    async def _ping_players(self):
        """Надсилає ping-повідомлення для підтримки WebSocket-з'єднання."""
        while True:
            await asyncio.sleep(15) # Надсилати ping кожні 15 секунд
            if not self.players:
                break # Вийти, якщо немає гравців
            
            players_to_ping = list(self.players.values()) 
            for player in players_to_ping:
                try:
                    await player.websocket.send_json({"type": "ping"})
                    logger.debug(f"Sent ping to player {player.user_id} in room {self.room_id}.")
                except WebSocketDisconnect:
                    logger.warning(f"Player {player.user_id} in room {self.room_id} already disconnected during ping. Removing.")
                    if player.user_id in self.players:
                        await self.remove_player(player.user_id)
                except Exception as e:
                    logger.warning(f"Failed to send ping to player {player.user_id} in room {self.room_id}: {e}. Removing player.")
                    if player.user_id in self.players: 
                        await self.remove_player(player.user_id)

    async def send_room_state_to_all(self):
        state = self.get_current_state()
        players_to_notify = list(self.players.values()) 
        for player in players_to_notify:
            try:
                # Дилера немає, тому ці поля завжди порожні/нульові
                # Але важливо, щоб фронтенд їх очікував
                player_state = state.copy()
                player_state["dealer_hand"] = []
                player_state["dealer_score"] = 0
                await player.websocket.send_json(player_state)
                logger.info(f"Sent room state to player {player.user_id} in room {self.room_id}. State: {player_state['status']}, Timer: {player_state['timer']}") # ДОДАНО ЛОГ
            except WebSocketDisconnect:
                logger.warning(f"Player {player.user_id} in room {self.room_id} disconnected during state send. Removing.")
                if player.user_id in self.players:
                    await self.remove_player(player.user_id)
            except Exception as e:
                logger.error(f"Error sending state to player {player.user_id} in room {self.room_id}: {e}")
                if player.user_id in self.players:
                    await self.remove_player(player.user_id)

    def get_current_state(self):
        players_data = []
        for p_id, p in self.players.items():
            players_data.append({
                "user_id": p.user_id,
                "username": p.username,
                "hand": p.hand.to_json(),
                "score": p.hand.value,
                "bet": p.bet,
                "is_playing": p.is_playing,
                "has_bet": p.has_bet
            })
        
        current_player_id = None
        # Визначаємо поточного гравця лише якщо статус "playing"
        if self.status == "playing":
            active_players = [p for p in self.players.values() if p.is_playing]
            if active_players:
                active_players.sort(key=lambda p: p.user_id) # Сортуємо для стабільного порядку ходу
                # Забезпечуємо дійсний індекс, якщо гравці виходять
                if self.current_turn_index >= len(active_players):
                    self.current_turn_index = 0 
                current_player_id = active_players[self.current_turn_index].user_id
        
        return {
            "room_id": self.room_id,
            "status": self.status,
            "dealer_hand": [], # Дилера немає
            "dealer_score": 0, # Дилера немає
            "players": players_data,
            "current_player_turn": current_player_id,
            "player_count": len(self.players),
            "min_players": self.min_players,
            "max_players": self.max_players,
            "timer": self.timer_countdown # Передаємо поточний відлік таймера
        }

    async def handle_bet(self, user_id: int, amount: int):
        player = self.players.get(user_id)
        if not player: 
            logger.warning(f"handle_bet: Player {user_id} not found in room {self.room_id}.")
            return

        if self.status != "betting":
            logger.warning(f"handle_bet: Player {user_id} tried to bet outside 'betting' phase (status: {self.status}).")
            try:
                await player.websocket.send_json({"type": "error", "message": "Ставки приймаються лише на етапі 'betting'."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_bet - wrong phase).")
                await self.remove_player(user_id)
            return

        user_data = get_user_data(user_id)
        if not user_data:
            logger.error(f"handle_bet: User data not found for {user_id}.")
            try:
                await player.websocket.send_json({"type": "error", "message": "Помилка: не вдалося отримати дані користувача."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_bet - user data error).")
                await self.remove_player(user_id)
            return

        if player.has_bet: # Перевірка, чи гравець вже зробив ставку
            logger.warning(f"handle_bet: Player {user_id} already bet in this round.")
            try:
                await player.websocket.send_json({"type": "error", "message": "Ви вже зробили ставку в цьому раунді."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_bet - already bet).")
                await self.remove_player(user_id)
            return

        if amount <= 0:
            logger.warning(f"handle_bet: Player {user_id} tried to bet invalid amount ({amount}).")
            try:
                await player.websocket.send_json({"type": "error", "message": "Ставка має бути позитивним числом."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_bet - invalid amount).")
                await self.remove_player(user_id)
            return

        if user_data["balance"] < amount:
            logger.info(f"handle_bet: Player {user_id} has insufficient balance ({user_data['balance']}) to bet {amount}. Marking as not playing for this round.")
            try:
                await player.websocket.send_json({"type": "error", "message": "Недостатньо фантиків для ставки. Ви не берете участь у цьому раунді."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_bet - insufficient balance).")
                await self.remove_player(user_id)
            
            player.is_playing = False # Гравець не бере участі в цьому раунді
            player.has_bet = True # Він "завершив" фазу ставок, хоч і не зробив її
            await self.send_room_state_to_all() # Оновити стан, щоб інші бачили, що цей гравець не грає
            
            # Після того, як гравець позначений як "не грає", перевіряємо, чи можна почати раунд
            self._check_and_start_round_if_ready()
            return
            
        player.bet = amount
        new_balance = user_data["balance"] - amount
        update_user_data(user_id, balance=new_balance)
        player.has_bet = True
        logger.info(f"handle_bet: Player {user_id} successfully bet {amount}. New balance: {new_balance}")
        
        await self.send_room_state_to_all() # Оновити стан, щоб показати, що гравець зробив ставку

        current_player_bets_status = {p.user_id: p.has_bet for p in self.players.values()}
        logger.info(f"handle_bet: After player {user_id} bet, players' has_bet status: {current_player_bets_status}")

        self._check_and_start_round_if_ready()

    def _check_and_start_round_if_ready(self):
        """Перевіряє, чи всі гравці завершили фазу ставок, і запускає раунд."""
        # Всі гравці, які були в кімнаті на початку фази ставок, повинні або зробити ставку,
        # або бути позначені як is_playing=False (наприклад, через недостатність коштів або таймер).
        # Тобто, ми перевіряємо, чи всі гравці мають has_bet = True.
        
        # Важливо: перевіряємо гравців, які були в кімнаті на момент переходу в статус "betting"
        # Для простоти, зараз перевіряємо всіх поточних гравців.
        all_players_finished_betting = all(p.has_bet for p in self.players.values())
        
        player_bet_statuses = {p.user_id: {'has_bet': p.has_bet, 'is_playing': p.is_playing} for p in self.players.values()}
        logger.info(f"_check_and_start_round_if_ready: Room {self.room_id}. Player statuses: {player_bet_statuses}. All finished betting: {all_players_finished_betting}. Current players in room: {len(self.players)}. Min players: {self.min_players}. Round in progress: {self.round_in_progress}")

        # Перевіряємо, чи є достатньо гравців, які зробили ставку і грають
        players_who_bet_and_play = [p for p in self.players.values() if p.has_bet and p.is_playing]

        if all_players_finished_betting and len(players_who_bet_and_play) >= 1: # Змінено на 1, щоб гра могла початися навіть з 1 гравцем
            if not self.round_in_progress: # Запобігаємо повторному запуску
                self.round_in_progress = True
                self.status = "playing" # Змінюємо статус на "playing"
                if self.betting_timer and not self.betting_timer.done():
                    self.betting_timer.cancel() # Скасувати таймер ставок, якщо він ще працює
                    logger.info(f"Room {self.room_id}: Betting timer cancelled as all players finished betting.")
                logger.info(f"Room {self.room_id}: All players finished betting. Starting round. Initiating start_round task.")
                asyncio.create_task(self.start_round()) # Запускаємо як асинхронну задачу
            else:
                logger.info(f"Room {self.room_id}: All players finished betting, but round already in progress. Skipping start_round.")
        else:
            logger.info(f"Room {self.room_id}: Not all players finished betting or not enough players who bet. Conditions for starting round not met.")
            # Якщо таймер ставок вже закінчився, і гравців, що зробили ставку, менше 1, скасувати раунд
            if self.betting_timer and self.betting_timer.done() and len(players_who_bet_and_play) < 1:
                logger.info(f"Room {self.room_id}: Betting timer finished and not enough players who bet. Cancelling round.")
                asyncio.create_task(self.cancel_round("Недостатньо гравців, які зробили ставку. Раунд скасовано."))
                


    async def handle_action(self, user_id: int, action: str):
        player = self.players.get(user_id)
        if not player: 
            logger.warning(f"handle_action: Player {user_id} not found in room {self.room_id}.")
            return

        if not player.is_playing:
            logger.warning(f"handle_action: Player {user_id} tried to act but is not playing (status: {self.status}, is_playing: {player.is_playing}).")
            try:
                await player.websocket.send_json({"type": "error", "message": "Ви не берете участь у поточному раунді."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_action - not playing).")
                await self.remove_player(user_id)
            return
        
        current_player = self.get_current_player()
        if not current_player or player.user_id != current_player.user_id:
            logger.warning(f"handle_action: Player {user_id} tried to act but it's not their turn (current: {current_player.user_id if current_player else 'None'}).")
            try:
                await player.websocket.send_json({"type": "error", "message": "Зараз хід іншого гравця."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_action - not their turn).")
                await self.remove_player(user_id)
            return

        if action == "hit":
            player.hand.add_card(self.deck.deal_card())
            logger.info(f"Player {user_id} hits. New hand: {player.hand.to_json()}, Score: {player.hand.value}")
            await self.send_room_state_to_all() # Оновлюємо стан після взяття карти
            
            if player.hand.value > 21:
                player.is_playing = False # Гравець перебрав
                try:
                    await player.websocket.send_json({"type": "game_message", "message": "Ви перебрали! 💥"})
                except WebSocketDisconnect:
                    logger.warning(f"Player {user_id} disconnected during game_message send (busted).")
                    await self.remove_player(user_id)
                logger.info(f"Player {user_id} busted with score {player.hand.value}.")
                await asyncio.sleep(1) # Невелика затримка для відображення повідомлення
                await self.next_turn()
            elif player.hand.value == 21:
                player.is_playing = False # Автоматичний stand на 21
                try:
                    await player.websocket.send_json({"type": "game_message", "message": "21! Ви зупинились."})
                except WebSocketDisconnect:
                    logger.warning(f"Player {user_id} disconnected during game_message send (21).")
                    await self.remove_player(user_id)
                logger.info(f"Player {user_id} got 21. Auto-standing.")
                await asyncio.sleep(1)
                await self.next_turn()
            else:
                # Якщо не перебрав, гравець може взяти ще або зупинитись. Залишаємо поточний хід.
                # Перезапускаємо таймер ходу для цього гравця
                if self.action_timer and not self.action_timer.done():
                    self.action_timer.cancel()
                self.action_timer = asyncio.create_task(self._start_action_timer(self.room_id, 20, user_id)) # 20 секунд на наступну дію
                await self.send_room_state_to_all() # Оновити стан з новим таймером
        elif action == "stand":
            player.is_playing = False
            try:
                await player.websocket.send_json({"type": "game_message", "message": "Ви зупинились."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during game_message send (stand).")
                await self.remove_player(user_id)
            logger.info(f"Player {user_id} stands with score {player.hand.value}.")
            await asyncio.sleep(0.5) # Невелика затримка для відображення повідомлення
            await self.next_turn()
        else:
            logger.warning(f"handle_action: Player {user_id} sent unknown action: {action}.")
            try:
                await player.websocket.send_json({"type": "error", "message": "Невідома дія."})
            except WebSocketDisconnect:
                logger.warning(f"Player {user_id} disconnected during error send (handle_action - unknown action).")
                await self.remove_player(user_id)

    def get_current_player(self) -> Optional[BlackjackPlayer]:
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players:
            return None
        active_players.sort(key=lambda p: p.user_id) # Сортуємо для стабільного порядку ходу
        # Забезпечуємо, що current_turn_index не виходить за межі списку активних гравців
        # Якщо індекс виходить за межі, скидаємо його на 0 (початок списку)
        if self.current_turn_index >= len(active_players):
            self.current_turn_index = 0
        return active_players[self.current_turn_index]

    async def next_turn(self):
        # Скасувати поточний таймер ходу, якщо він є
        if self.action_timer and not self.action_timer.done():
            self.action_timer.cancel()
            self.action_timer = None
            self.timer_countdown = 0 # Скинути таймер на фронтенді
        
        active_players = [p for p in self.players.values() if p.is_playing]
        
        if not active_players:
            # Всі гравці завершили хід (перебрали або зупинились)
            logger.info(f"Room {self.room_id}: All players finished their turns. Ending round.")
            await self.end_round()
            return

        # Знайти поточний індекс гравця
        current_player_id = self.get_current_player().user_id if self.get_current_player() else None
        if current_player_id:
            try:
                self.current_turn_index = active_players.index(self.players[current_player_id]) + 1
            except ValueError: # Якщо поточного гравця вже немає в активних (наприклад, відключився)
                self.current_turn_index = 0 # Почати з першого активного
        else:
            self.current_turn_index = 0 # Почати з першого активного, якщо не було поточного

        # Передаємо хід наступному активному гравцю
        next_player = self.get_current_player()
        
        if next_player:
            logger.info(f"Room {self.room_id}: Moving to next player's turn: {next_player.user_id}. Current turn index: {self.current_turn_index}, total active: {len(active_players)}")
            await self.send_room_state_to_all() # Оновити стан для нового поточного гравця
            # Запускаємо таймер ходу для нового гравця
            self.action_timer = asyncio.create_task(self._start_action_timer(self.room_id, 20, next_player.user_id))
        else:
            # Це означає, що active_players порожній, що має бути оброблено вище
            logger.warning(f"Room {self.room_id}: next_turn called but no next player found. Calling end_round.")
            await self.end_round()


    async def start_round(self):
        logger.info(f"Room {self.room_id}: Starting new round. Status: {self.status}")
        self.deck = Deck()
        self.current_turn_index = 0 # Скидаємо індекс ходу

        # Скидаємо стан гравців та роздаємо карти тим, хто бере участь
        for player in self.players.values():
            player.reset_for_round() # Скидаємо стан для нового раунду
            # Важливо: has_bet вже встановлено в handle_bet (або в _start_betting_timer, якщо таймер вийшов)
            # is_playing також встановлюється в handle_bet, якщо недостатньо коштів.
            # Тут ми роздаємо карти тільки тим, хто залишився is_playing = True.
            if player.has_bet and player.is_playing: # Тільки якщо гравець зробив ставку і може грати
                player.hand.add_card(self.deck.deal_card())
                player.hand.add_card(self.deck.deal_card())
                logger.info(f"Player {player.user_id} dealt: {player.hand.to_json()}")
            else:
                logger.info(f"Player {player.user_id} is not playing this round (no bet/insufficient funds/timeout).")

        self.status = "playing"
        await self.send_room_state_to_all() # Відправляємо початковий стан гри

        # Перевірка на миттєвий блекджек у гравців
        players_with_blackjack = []
        for player in self.players.values():
            if player.is_playing and player.hand.value == 21 and len(player.hand.cards) == 2:
                player.is_playing = False # Гравець з блекджеком зупиняється
                players_with_blackjack.append(player.user_id)
                try:
                    await player.websocket.send_json({"type": "game_message", "message": "У вас Блекджек! 🎉"})
                except WebSocketDisconnect:
                    logger.warning(f"Player {player.user_id} disconnected during game_message send (blackjack).")
                    await self.remove_player(player.user_id)
                logger.info(f"Player {player.user_id} has Blackjack!")
                await asyncio.sleep(0.5) # Невелика затримка для відображення
        
        active_players_after_blackjack_check = [p for p in self.players.values() if p.is_playing]
        if not active_players_after_blackjack_check:
            logger.info("No active players left after initial deal/blackjack check. Ending round.")
            await self.end_round() 
        else:
            logger.info(f"Room {self.room_id}: First player's turn.")
            await self.send_room_state_to_all() # Відправити стан з оновленим списком гравців
            # Запускаємо таймер ходу для першого гравця
            first_player = self.get_current_player()
            if first_player:
                self.action_timer = asyncio.create_task(self._start_action_timer(self.room_id, 20, first_player.user_id))


    async def end_round(self):
        logger.info(f"Room {self.room_id}: Ending round. Calculating results.")
        self.status = "round_end"
        self.timer_countdown = 0 # Скидаємо таймер
        if self.action_timer and not self.action_timer.done():
            self.action_timer.cancel() # Скасувати будь-який активний таймер ходу
        self.action_timer = None

        # Збираємо результати всіх гравців для порівняння
        player_results = []
        for player_id, player in self.players.items():
            # Включаємо в результати лише тих, хто брав участь у раунді (зробив ставку)
            if player.bet > 0: # Перевіряємо, чи гравець зробив ставку
                player_results.append({
                    "user_id": player.user_id,
                    "username": player.username,
                    "hand": player.hand.to_json(), # Додаємо руку гравця для відображення на фронтенді
                    "score": player.hand.value,
                    "bet": player.bet,
                    "is_busted": player.hand.value > 21
                })
            else:
                # Гравець не зробив ставку, йому просто повідомляємо, що раунд завершено
                try:
                    await player.websocket.send_json({
                        "type": "round_result",
                        "message": "Раунд завершено. Ви не брали участь.",
                        "winnings": 0,
                        "balance": get_user_data(player.user_id)["balance"], # Актуальний баланс
                        "xp": get_user_data(player.user_id)["xp"],
                        "level": get_user_data(player.user_id)["level"],
                        "next_level_xp": get_xp_for_next_level(get_user_data(player.user_id)["level"]),
                        "final_player_score": 0, # Немає рахунку, бо не грав
                        "final_player_hand": [], # Немає руки, бо не грав
                        "final_dealer_score": 0 # Дилера немає
                    })
                except WebSocketDisconnect:
                    logger.warning(f"Player {player.user_id} disconnected during round_result send (no participation).")
                    await self.remove_player(player.user_id)
                player.reset_for_round() # Скидаємо стан для наступного раунду
                continue # Переходимо до наступного гравця

        # Визначаємо переможців серед тих, хто не перебрав
        valid_players = [p for p in player_results if not p["is_busted"]]
        
        if not valid_players: # Всі активні гравці перебрали
            logger.info(f"Room {self.room_id}: All active players busted. No winners.")
            for player_data in player_results: # Обробляємо тих, хто брав участь
                player = self.players.get(player_data["user_id"])
                if not player: continue # Можливо, гравець вже відключився

                user_data = get_user_data(player.user_id)
                new_balance = user_data["balance"] # Гроші вже списані, повертати нічого
                new_xp = user_data["xp"] + 1 # XP за участь
                new_level = get_level_from_xp(new_xp)
                
                # Вирівнювання XP
                if new_level == user_data["level"] and new_xp >= get_xp_for_next_level(user_data["level"]):
                    new_xp = get_xp_for_next_level(user_data["level"]) - 1

                update_user_data(player.user_id, balance=new_balance, xp=new_xp, level=new_level)
                updated_user_data_for_response = get_user_data(player.user_id) # Отримати оновлені дані
                try:
                    await player.websocket.send_json({
                        "type": "round_result",
                        "message": "Всі перебрали! Ніхто не виграв.",
                        "winnings": 0,
                        "balance": updated_user_data_for_response["balance"],
                        "xp": updated_user_data_for_response["xp"],
                        "level": updated_user_data_for_response["level"],
                        "next_level_xp": get_xp_for_next_level(updated_user_data_for_response["level"]),
                        "final_player_score": player.hand.value,
                        "final_player_hand": player.hand.to_json(), # Додаємо руку гравця
                        "final_dealer_score": 0 # Дилера немає
                    })
                except WebSocketDisconnect:
                    logger.warning(f"Player {player.user_id} disconnected during round_result send (all busted).")
                    await self.remove_player(player.user_id)
                if player: player.reset_for_round() # Скидаємо стан для наступного раунду
        else:
            # Знаходимо максимальний рахунок серед тих, хто не перебрав
            max_score = max(p["score"] for p in valid_players)
            winners = [p for p in valid_players if p["score"] == max_score]
            logger.info(f"Room {self.room_id}: Winners found: {[w['username'] for w in winners]} with score {max_score}.")

            for player_data in player_results: # Обробляємо тих, хто брав участь
                player = self.players.get(player_data["user_id"])
                if not player: continue # Можливо, гравець вже відключився

                user_data = get_user_data(player.user_id)
                winnings = 0
                message = ""
                xp_gain = 0

                if player.user_id in [w["user_id"] for w in winners]:
                    winnings = player.bet * 2 # Подвоюємо ставку
                    message = "Ви виграли! 🎉"
                    xp_gain = 10 # XP за перемогу
                elif player.hand.value > 21:
                    message = "Ви перебрали! Програш."
                    xp_gain = 1 # XP за участь
                else:
                    message = "Ви програли."
                    xp_gain = 1 # XP за участь
                
                new_balance = user_data["balance"] + winnings
                new_xp = user_data["xp"] + xp_gain
                new_level = get_level_from_xp(new_xp)
                
                # Вирівнювання XP
                if new_level == user_data["level"] and new_xp >= get_xp_for_next_level(user_data["level"]):
                    new_xp = get_xp_for_next_level(user_data["level"]) - 1

                update_user_data(player.user_id, balance=new_balance, xp=new_xp, level=new_level)
                updated_user_data_for_response = get_user_data(player.user_id) # Отримати оновлені дані

                if new_level > user_data["level"]:
                    try:
                        await player.websocket.send_json({"type": "level_up", "level": new_level})
                    except WebSocketDisconnect:
                        logger.warning(f"Player {player.user_id} disconnected during level_up send.")
                        await self.remove_player(player.user_id)
                    logger.info(f"Player {player.user_id} leveled up to {new_level}!")

                try:
                    await player.websocket.send_json({
                        "type": "round_result",
                        "message": message,
                        "winnings": winnings,
                        "balance": updated_user_data_for_response["balance"],
                        "xp": updated_user_data_for_response["xp"],
                        "level": updated_user_data_for_response["level"],
                        "next_level_xp": get_xp_for_next_level(updated_user_data_for_response["level"]),
                        "final_player_score": player.hand.value,
                        "final_player_hand": player.hand.to_json(), # Додаємо руку гравця
                        "final_dealer_score": 0 # Дилера немає
                    })
                except WebSocketDisconnect:
                    logger.warning(f"Player {player.user_id} disconnected during round_result send.")
                    await self.remove_player(player.user_id)
                
                if player: player.reset_for_round() # Скидаємо стан гравця для наступного раунду
        
        self.round_in_progress = False # Раунд завершено
        self.status = "waiting" # Повертаємося до очікування нових гравців/раунду
        
        await self.send_room_state_to_all() # Відправити фінальний стан кімнати
        
        # Після невеликої паузи переходимо до фази ставок, якщо є достатньо гравців
        await asyncio.sleep(3) 
        if len(self.players) >= self.min_players:
            self.status = "starting_timer" # Перехід до стартового таймера перед ставками
            if self.game_start_timer and not self.game_start_timer.done():
                self.game_start_timer.cancel()
            self.timer_countdown = 20 # Таймер на 20 секунд
            self.game_start_timer = asyncio.create_task(self._start_game_after_delay(self.room_id, self.timer_countdown))
            logger.info(f"Room {self.room_id}: Transitioned to starting_timer phase after round end.")
        else:
            logger.info(f"Room {self.room_id}: Not enough players for new round, staying in waiting.")


class BlackjackRoomManager:
    def __init__(self):
        self.rooms: Dict[str, BlackjackRoom] = {}
        self.player_to_room: Dict[int, str] = {} # Додано для швидкого пошуку кімнати гравця

    async def create_or_join_room(self, user_id: int, username: str, websocket: WebSocket):
        # Якщо гравець вже в кімнаті, просто оновити його WebSocket
        if user_id in self.player_to_room:
            room_id = self.player_to_room[user_id]
            room = self.rooms.get(room_id)
            if room:
                success, msg = await room.add_player(user_id, username, websocket) # Це оновлює WebSocket
                return room_id
            else:
                # Кімната не знайдена, але гравець був зареєстрований. Очищаємо і створюємо нову.
                del blackjack_room_manager.player_to_room[user_id] 
                logger.warning(f"Player {user_id} was mapped to non-existent room {room_id}. Cleaning up.")

        # Пріоритет: шукаємо кімнату з 1 гравцем, яка чекає на другого
        for room_id, room in self.rooms.items():
            if len(room.players) == 1 and room.status in ["waiting", "starting_timer", "betting"] and room.min_players == 2:
                success, msg = await room.add_player(user_id, username, websocket)
                if success:
                    self.player_to_room[user_id] = room_id
                    logger.info(f"Player {user_id} joined existing room {room_id} (filling 1/2).")
                    return room_id

        # Далі: шукаємо кімнату з менше ніж max_players, яка не в активній грі
        for room_id, room in self.rooms.items():
            if len(room.players) < room.max_players and room.status in ["waiting", "starting_timer", "betting"]:
                success, msg = await room.add_player(user_id, username, websocket)
                if success:
                    self.player_to_room[user_id] = room_id
                    logger.info(f"Player {user_id} joined existing room {room_id}.")
                    return room_id

        # Якщо не знайдено, створюємо нову кімнату
        new_room_id = str(uuid.uuid4())[:8]
        new_room = BlackjackRoom(new_room_id)
        self.rooms[new_room_id] = new_room
        success, msg = await new_room.add_player(user_id, username, websocket)
        if success:
            self.player_to_room[user_id] = new_room_id
            logger.info(f"Player {user_id} created and joined new room {new_room_id}")
            return new_room_id
        
        return None # Якщо з якоїсь причини не вдалося приєднатися або створити

blackjack_room_manager = BlackjackRoomManager()

# --- WebSocket Endpoint ---
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    user_id_int = int(user_id)
    user_data_db = get_user_data(user_id_int)
    username = user_data_db.get("username", f"Гравець {str(user_id_int)[-4:]}")
    
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for user {user_id_int}.")

    room_id = await blackjack_room_manager.create_or_join_room(user_id_int, username, websocket)
    
    if not room_id:
        logger.error(f"Failed to join/create room for user {user_id_int}. Closing websocket.")
        await websocket.close(code=1008, reason="Could not join/create room.")
        return

    room = blackjack_room_manager.rooms.get(room_id)
    if room and user_id_int in room.players:
        room.players[user_id_int].websocket = websocket # Переконатися, що WebSocket оновлений
    else:
        logger.error(f"Room {room_id} or player {user_id_int} not found after create_or_join_room. This should not happen.")
        await websocket.close(code=1008, reason="Internal error: Room/player not found.")
        return

    try:
        while True:
            message_text = await websocket.receive_text()
            try:
                message = json.loads(message_text)
                action = message.get("action")
                
                logger.info(f"WS: Received message from {user_id_int} in room {room_id}: {message}")

                # Перевірка, чи гравець все ще належить цій кімнаті
                if not room or room.room_id != room_id or user_id_int not in room.players: 
                    logger.warning(f"Room mismatch or player not in room for {user_id_int}. Expected {room_id}, actual {room.room_id if room else 'None'}. Closing WS.")
                    await websocket.send_json({"type": "error", "message": "Кімната гри була оновлена або видалена. Будь ласка, перепідключіться."})
                    break # Вийти з циклу, щоб закрити WebSocket
                
                if action == "bet":
                    amount = message.get("amount")
                    if amount is not None:
                        await room.handle_bet(user_id_int, amount)
                    else:
                        await websocket.send_json({"type": "error", "message": "Сума ставки не вказана."})
                elif action in ["hit", "stand"]:
                    await room.handle_action(user_id_int, action)
                elif action == "request_state":
                    await room.send_room_state_to_all()
                elif action == "pong": # Обробка pong-повідомлень від клієнта
                    logger.debug(f"Received pong from {user_id_int}.")
                    # Не потрібно нічого робити, просто підтримує з'єднання
                else:
                    await websocket.send_json({"type": "error", "message": "Невідома дія."})
            except json.JSONDecodeError:
                logger.warning(f"Received non-JSON message from {user_id_int}: {message_text}")
                try:
                    await websocket.send_json({"type": "error", "message": "Неправильний формат повідомлення (очікується JSON)."})
                except RuntimeError: # WebSocket може бути вже закритий
                    pass
            except Exception as e:
                logger.error(f"Error handling WebSocket message from {user_id_int} in room {room_id}: {e}", exc_info=True)
                try:
                    await websocket.send_json({"type": "error", "message": f"Помилка сервера: {str(e)}"})
                except RuntimeError:
                    pass # WebSocket може бути вже закритий
    except WebSocketDisconnect:
        logger.info(f"Client {user_id_int} disconnected from room {room_id}.")
        room = blackjack_room_manager.rooms.get(room_id)
        if room:
            await room.remove_player(user_id_int)
        else:
            logger.warning(f"Room {room_id} not found on disconnect for player {user_id_int}.")
    except Exception as e:
        logger.critical(f"Unexpected error in WebSocket endpoint for {user_id_int}: {e}", exc_info=True)
        try:
            await websocket.send_json({"type": "error", "message": f"Критична помилка сервера: {str(e)}"})
        except RuntimeError:
            pass # WebSocket може бути вже закритий

        
# --- Serve the main HTML file ---
@app.get("/")
async def get_root():
    index_html_path = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(index_html_path):
        logger.error(f"index.html not found at {index_html_path}")
        raise HTTPException(status_code=404, detail="index.html not found")
    with open(index_html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    return HTMLResponse(content=html_content)

# --- Telegram Webhook Endpoint ---
@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    logger.info("Received webhook update from Telegram.") # Лог на початку обробки вебхука
    try:
        update_json = await request.json()
        update = types.Update.model_validate(update_json, context={"bot": bot})
        await dp.feed_update(bot, update) 
        logger.info(f"Webhook update successfully processed. Update ID: {update.update_id}")
    except Exception as e:
        logger.error(f"Error processing webhook update: {e}", exc_info=True)
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    return {"ok": True}

# --- On startup: set webhook for Telegram Bot and initialize DB ---
@app.on_event("startup")
async def on_startup():
    print("Application startup event triggered.")
    init_db()
    print("Database initialization attempted.")
    external_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not external_hostname:
        logger.warning("RENDER_EXTERNAL_HOSTNAME environment variable not set. Assuming localhost for webhook setup.")
        external_hostname = "localhost:8000" # Це для локальної розробки
    global WEBHOOK_URL
    WEBHOOK_URL = f"https://{external_hostname}{WEBHOOK_PATH}" # Використовуємо HTTPS для Render
    global WEB_APP_FRONTEND_URL
    if WEB_APP_FRONTEND_URL and not WEB_APP_FRONTEND_URL.startswith("https://"):
        WEB_APP_FRONTEND_URL = f"https://{WEB_APP_FRONTEND_URL}" # Переконатися, що WebApp URL використовує HTTPS
    
    if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
        try:
            webhook_info = await bot.get_webhook_info()
            if webhook_info.url != WEBHOOK_URL:
                await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
                logger.info(f"Telegram webhook set to: {WEBHOOK_URL}")
            else: logger.info(f"Telegram webhook already set to: {WEBHOOK_URL}")
        except Exception as e:
            logger.error(f"Failed to set Telegram webhook: {e}")
            logger.error("Hint: Is BOT_TOKEN correctly set as an environment variable and valid?")
    else: logger.warning("Skipping Telegram webhook setup because BOT_TOKEN is not set or is a dummy value.")

@app.on_event("shutdown")
async def on_shutdown():
    print("Application shutdown event triggered.")
    if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
        try:
            await bot.delete_webhook()
            logger.info("Telegram webhook deleted.")
        except Exception as e:
            logger.error(f"Failed to delete Telegram webhook on shutdown: {e}")
    logger.info("Closing dispatcher storage and bot session.")
    await dp.storage.close() 
    await bot.session.close() 
    logger.info("Bot session closed.")

# Запуск сервера (зазвичай через `uvicorn main:app --reload`)
if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
