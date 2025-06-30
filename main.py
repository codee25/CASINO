import logging
import os
import json
import random
import urllib.parse
import asyncio
import uuid # For generating unique room IDs for Blackjack
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional # For type hinting

import psycopg2
from psycopg2 import sql # For safe SQL query composition

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel # For FastAPI request models

from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart, Command # For aiogram v3 filters
from aiogram.client.default import DefaultBotProperties # For bot default settings

# NEW IMPORT: For CORS middleware
from fastapi.middleware.cors import CORSMiddleware

# --- Logging Configuration ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Environment Variables ---
# IMPORTANT: Set these environment variables on Render.com for your Web Service
API_TOKEN = os.getenv('BOT_TOKEN') # Telegram Bot Token
WEB_APP_FRONTEND_URL = os.getenv('WEB_APP_FRONTEND_URL') # URL of your Render Static Site
# RENDER_EXTERNAL_HOSTNAME is automatically set by Render.com. It's the URL of your FastAPI backend.
WEBHOOK_HOST = os.getenv('RENDER_EXTERNAL_HOSTNAME') 

# Path to the frontend (webapp) folder
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

# --- FastAPI App Setup ---
app = FastAPI()

# ADDED: CORS Configuration
# Temporarily allowing all origins to ensure CORS is not the issue during debugging.
# In production, it's safer to specify exact frontend URLs.
origins = [
    "*", 
    # Uncomment and use specific URLs in production:
    # WEB_APP_FRONTEND_URL, 
    # f"https://{WEBHOOK_HOST}", 
    # "http://localhost",
    # "http://localhost:3000",
    # "http://localhost:5173",
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"], # Allow all HTTP methods (GET, POST, PUT, DELETE, etc.)
    allow_headers=["*"], # Allow all headers
)

app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# --- Telegram Bot Webhook Configuration ---
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL: Optional[str] = None # Will be set during startup

ADMIN_ID_STR = os.getenv('ADMIN_ID')
ADMIN_ID: Optional[int] = None
try:
    if ADMIN_ID_STR:
        ADMIN_ID = int(ADMIN_ID_STR)
except ValueError:
    logger.error(f"Invalid ADMIN_ID provided: '{ADMIN_ID_STR}'. It must be an integer.")

# --- PostgreSQL Database Configuration ---
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    logger.critical("DATABASE_URL environment variable is not set. The bot will not be able to connect to the database.")
    # In a production application, you might want to raise an exception here to prevent startup

# --- Aiogram Bot Setup ---
if not API_TOKEN:
    logger.critical("API_TOKEN (BOT_TOKEN) environment variable not set. Telegram bot will not work.")
    bot = Bot(token="DUMMY_TOKEN", default=DefaultBotProperties(parse_mode=ParseMode.HTML))
else:
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))

dp = Dispatcher()

# --- Game Configuration (Matches JS Frontend) ---
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀']
WILD_SYMBOL = '⭐'
SCATTER_SYMBOL = '💰'
ALL_REEL_SYMBOLS = SYMBOLS + [WILD_SYMBOL, SCATTER_SYMBOL]

BET_AMOUNT = 100 # Bet for slots
COIN_FLIP_BET_AMOUNT = 50 # Bet for coin flip

FREE_COINS_AMOUNT = 500 # Amount of free coins for /get_coins
COOLDOWN_HOURS = 24 # Cooldown in hours for /get_coins

DAILY_BONUS_AMOUNT = 300 # Daily bonus via Web App
DAILY_BONUS_COOLDOWN_HOURS = 24

QUICK_BONUS_AMOUNT = 100 # Quick bonus via Web App
QUICK_BONUS_COOLDOWN_MINUTES = 15

# XP and Levels
XP_PER_SPIN = 10
XP_PER_COIN_FLIP = 5 # XP for coin flip
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
    """Determines user level based on XP (1-based)."""
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp < threshold:
            return i + 1 # Levels start from 1, indexes from 0
    return len(LEVEL_THRESHOLDS) # Max level

def get_xp_for_next_level(level: int) -> int:
    """Returns XP needed for the next level (or for the current if it's the last)."""
    # Level is 1-based. To get threshold for next level, use current level as index (e.g., Level 1 -> index 1 for Level 2 threshold)
    if level >= len(LEVEL_THRESHOLDS): # If current level is already max or beyond
        return LEVEL_THRESHOLDS[-1] # Return the highest threshold
    return LEVEL_THRESHOLDS[level] # Return threshold for the next level (e.g., for Level 1, returns LEVEL_THRESHOLDS[1] which is 100 XP for Level 2)


PAYOUTS = {
    ('🍒', '🍒', '🍒'): 1000, ('🍋', '🍋', '🍋'): 800, ('🍊', '�', '🍊'): 600,
    ('🍇', '🍇', '🍇'): 400, ('🔔', '🔔', '🔔'): 300, ('💎', '💎', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150, ('⭐', '⭐', '⭐'): 2000, 
    ('🍒', '🍒'): 100, ('🍋', '🍋'): 80, ('🍊', '🍊'): 60,
    ('🍇', '🍇'): 40, ('🔔', '🔔'): 30, ('💎', '💎'): 20,
    ('🍀', '🍀'): 10,
    ('💰', '💰'): 200, ('💰', '💰', '💰'): 500,
}

# --- Database Functions ---

def get_db_connection():
    """Establishes and returns a PostgreSQL database connection using the URL."""
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
        return conn
    except psycopg2.Error as err:
        logger.error(f"DB connection error: {err}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during DB connection: {e}")
        raise

def init_db():
    """Initializes tables and performs migrations for the PostgreSQL database."""
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
        logger.error(f"DB init/migration error: {e}")
    finally:
        if conn:
            conn.close()

def get_user_data(user_id: int | str) -> dict:
    """Retrieves all user data from DB. Creates a new user if not exists."""
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
            # Ensure datetime objects are timezone-aware UTC
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
        return {
            'username': 'Error Player', 'balance': 0, 'xp': 0, 'level': 1, 
            'last_free_coins_claim': None, 'last_daily_bonus_claim': None, 'last_quick_bonus_claim': None
        }
    finally:
        if conn:
            conn.close()

def update_user_data(user_id: int | str, **kwargs):
    """Updates user data in the PostgreSQL database. Accepts keyword arguments for update."""
    user_id_int = int(user_id)
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        current_data_from_db = get_user_data(user_id_int) 
        logger.info(f"Before update for {user_id_int}: {current_data_from_db.get('balance', 'N/A')} balance, {current_data_from_db.get('xp', 'N/A')} xp, {current_data_from_db.get('level', 'N/A')} level.")

        update_fields_parts = []
        update_values = []

        # Populate fields_to_update with current DB values first, then override with kwargs
        fields_to_update = {
            'username': kwargs.get('username', current_data_from_db.get('username', 'Unnamed Player')),
            'balance': kwargs.get('balance', current_data_from_db.get('balance', 0)),
            'xp': kwargs.get('xp', current_data_from_db.get('xp', 0)),
            'level': kwargs.get('level', current_data_from_db.get('level', 1)),
            'last_free_coins_claim': kwargs.get('last_free_coins_claim', current_data_from_db.get('last_free_coins_claim')),
            'last_daily_bonus_claim': kwargs.get('last_daily_bonus_claim', current_data_from_db.get('last_daily_bonus_claim')),
            'last_quick_bonus_claim': kwargs.get('last_quick_bonus_claim', current_data_from_db.get('last_quick_bonus_claim'))
        }
        
        # Ensure datetime objects are timezone-aware UTC before saving
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
    """Checks winning combinations for a 3-reel slot, considering Wild and Scatter."""
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

    level_up_message = ""
    if new_level > current_level:
        level_up_message = f" 🎉 НОВИЙ РІВЕНЬ: {new_level}! 🎉"

    update_user_data(user_id, balance=new_balance, xp=new_xp, level=new_level)

    final_user_data = get_user_data(user_id) # Get updated data for response
    
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
        winnings = COIN_FLIP_BET_AMOUNT * 2 
        new_balance += winnings 
        message = f"🎉 Вітаємо! Монета показала {coin_result == 'heads' and 'Орла' or 'Решку'}! Ви виграли {winnings} фантиків!"
        xp_gained += (XP_PER_COIN_FLIP * XP_PER_WIN_MULTIPLIER) 
        logger.info(f"User {user_id} won coin flip. Result: {coin_result}. Winnings: {winnings}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    else:
        message = f"😢 На жаль, монета показала {coin_result == 'heads' and 'Орла' or 'Решку'}. Спробуйте ще раз!"
        logger.info(f"User {user_id} lost coin flip. Result: {coin_result}. Gained {xp_gained} XP. New balance would be {new_balance}.")
    
    new_xp = current_xp + xp_gained
    new_level = get_level_from_xp(new_xp)

    level_up_message = ""
    if new_level > current_level:
        level_up_message = f" 🎉 НОВИЙ РІВЕНЬ: {new_level}! 🎉"

    update_user_data(user_id, balance=new_balance, xp=new_xp, level=new_level)

    final_user_data = get_user_data(user_id) 

    return {
        'result': coin_result, 'winnings': winnings, 'balance': final_user_data['balance'],
        'message': message + level_up_message, 
        'xp': final_user_data['xp'], 'level': final_user_data['level'],
        'next_level_xp': get_xp_for_next_level(final_user_data['level'])
    }

# --- Telegram Bot Handlers (aiogram v3 syntax) ---
@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db()
    
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
    
    user_data = get_user_data(user_id) 
    
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

    if data_from_webapp.startswith('JS_VERY_FIRST_LOG:'):
        await message.answer(f"✅ WebApp Core Log: {data_from_webapp.replace('JS_VERY_FIRST_LOG:', '').strip()}")
    elif data_from_webapp.startswith('JS_LOG:'):
        logger.info(f"WebApp JS_LOG for {user_id}: {data_from_webapp.replace('JS_LOG:', '').strip()}")
    elif data_from_webapp.startswith('JS_DEBUG:'):
        logger.debug(f"WebApp JS_DEBUG for {user_id}: {data_from_webapp.replace('JS_DEBUG:', '').strip()}")
    elif data_from_webapp.startswith('JS_WARN:'):
        logger.warning(f"WebApp JS_WARN for {user_id}: {data_from_webapp.replace('JS_WARN:', '').strip()}")
    elif data_from_webapp.startswith('JS_ERROR:'):
        await message.answer(f"❌ WebApp Error: {data_from_webapp.replace('JS_ERROR:', '').strip()}")
    else:
        pass 

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

    user_data = get_user_data(user_id) 
    
    if username_from_frontend and user_data['username'] != username_from_frontend:
        if username_from_frontend != 'Unnamed Player' or user_data['username'] == 'Unnamed Player':
            update_user_data(user_id, username=username_from_frontend)
            user_data['username'] = username_from_frontend 

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
        new_xp = user_data['xp'] + 20
        new_level = get_level_from_xp(new_xp)
        update_user_data(user_id, balance=new_balance, last_daily_bonus_claim=current_time, xp=new_xp, level=new_level)
        updated_user_data = get_user_data(user_id)
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
        new_xp = user_data['xp'] + 5
        new_level = get_level_from_xp(new_xp)
        update_user_data(user_id, balance=new_balance, last_quick_bonus_claim=current_time, xp=new_xp, level=new_level)
        updated_user_data = get_user_data(user_id)
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
        elif self.rank == 'A': return 11
        else: return int(self.rank)

class Deck:
    def __init__(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        self.cards: List[Card] = [Card(suit, rank) for suit in suits for rank in ranks]
        random.shuffle(self.cards)
    def deal_card(self) -> Card:
        if not self.cards: self.__init__(); print("Reshuffling deck!")
        return self.cards.pop()

class Hand:
    def __init__(self):
        self.cards: List[Card] = []; self.value: int = 0; self.aces: int = 0
    def add_card(self, card: Card):
        self.cards.append(card); self.value += card.value()
        if card.rank == 'A': self.aces += 1
        while self.value > 21 and self.aces: self.value -= 10; self.aces -= 1
    def __str__(self): return ", ".join(str(card) for card in self.cards)
    def to_json(self, hide_first: bool = False) -> List[str]:
        if hide_first and self.cards:
            if len(self.cards) > 1: return [str(self.cards[0]), "Hidden"]
            else: return [str(self.cards[0])]
        return [str(card) for card in self.cards]

class BlackjackPlayer:
    def __init__(self, user_id: int, username: str, websocket: WebSocket):
        self.user_id = user_id; self.username = username; self.websocket = websocket
        self.hand = Hand(); self.bet = 0; self.is_ready = False; self.is_playing = True; self.has_bet = False
    def reset_for_round(self):
        self.hand = Hand(); self.bet = 0; self.is_ready = False; self.is_playing = True; self.has_bet = False

class BlackjackRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id; self.players: Dict[int, BlackjackPlayer] = {}
        self.status = "waiting"; self.deck = Deck(); self.dealer_hand = Hand()
        self.current_turn_index = 0; self.min_players = 2; self.max_players = 5
        self.game_start_timer: Optional[asyncio.Task] = None; self.timer_countdown: int = 0
    async def add_player(self, user_id: int, username: str, websocket: WebSocket):
        if len(self.players) >= self.max_players: return False, "Room is full."
        if user_id in self.players: return False, "Player already in room."
        player = BlackjackPlayer(user_id, username, websocket); self.players[user_id] = player
        await self.send_room_state_to_all(); return True, "Joined room successfully."
    async def remove_player(self, user_id: int):
        if user_id in self.players:
            del self.players[user_id]; print(f"Player {user_id} removed from room {self.room_id}")
            if not self.players:
                if self.game_start_timer and not self.game_start_timer.done(): self.game_start_timer.cancel()
                del blackjack_room_manager.rooms[self.room_id]; print(f"Room {self.room_id} is empty and removed.")
            else:
                if self.status == "playing":
                     active_players_after_removal = [p for p in self.players.values() if p.is_playing]
                     if not active_players_after_removal: await self.next_turn()
                     elif self.current_turn_index >= len(active_players_after_removal): self.current_turn_index = 0
                await self.send_room_state_to_all()
        else: print(f"Player {user_id} not found in room {self.room_id}")
    async def send_room_state_to_all(self):
        state = self.get_current_state()
        for player in self.players.values():
            try:
                player_state = state.copy()
                if self.status not in ["dealer_turn", "round_end"] and len(self.dealer_hand.cards) > 1:
                    player_state["dealer_hand"] = [str(self.dealer_hand.cards[0]), "Hidden"]
                    player_state["dealer_score"] = self.dealer_hand.cards[0].value() 
                else: 
                    player_state["dealer_hand"] = self.dealer_hand.to_json()
                    player_state["dealer_score"] = self.dealer_hand.value

                await player.websocket.send_json(player_state)
            except Exception as e:
                print(f"Error sending state to {player.user_id}: {e}")

    def get_current_state(self):
        players_data = []
        for p_id, p in self.players.items():
            players_data.append({
                "user_id": p.user_id, "username": p.username, "hand": p.hand.to_json(),
                "score": p.hand.value, "bet": p.bet, "is_ready": p.is_ready,
                "is_playing": p.is_playing, "has_bet": p.has_bet
            })
        current_player_id = None
        if self.status == "playing":
            active_players = [p for p in self.players.values() if p.is_playing]
            if active_players: current_player_id = active_players[self.current_turn_index % len(active_players)].user_id
        return {
            "room_id": self.room_id, "status": self.status, "dealer_hand": [], "dealer_score": 0,
            "players": players_data, "current_player_turn": current_player_id,
            "player_count": len(self.players), "min_players": self.min_players,
            "max_players": self.max_players, "timer": self.timer_countdown
        }
    async def handle_bet(self, user_id: int, amount: int):
        player = self.players.get(user_id)
        if not player: return
        if self.status != "betting":
            await player.websocket.send_json({"type": "error", "message": "Ставки приймаються лише на етапі 'betting'."}); return
        user_data = get_user_data(user_id)
        if not user_data or user_data["balance"] < amount:
            await player.websocket.send_json({"type": "error", "message": "Недостатньо фантиків для ставки."}); return
        if amount <= 0:
            await player.websocket.send_json({"type": "error", "message": "Ставка має бути позитивним числом."}); return
        if player.has_bet:
            await player.websocket.send_json({"type": "error", "message": "Ви вже зробили ставку в цьому раунді."}); return
        player.bet = amount
        new_balance = user_data["balance"] - amount
        update_user_data(user_id, balance=new_balance); player.has_bet = True
        print(f"Player {user_id} bet {amount}")
        all_bet = all(p.has_bet for p in self.players.values())
        if all_bet and len(self.players) >= self.min_players:
            self.status = "playing"; await self.start_round()
        else: await self.send_room_state_to_all()
    async def handle_action(self, user_id: int, action: str):
        player = self.players.get(user_id)
        if not player or not player.is_playing:
            await player.websocket.send_json({"type": "error", "message": "Зараз не ваш хід або ви не граєте."}); return
        current_player = self.get_current_player()
        if not current_player or player.user_id != current_player.user_id:
             await player.websocket.send_json({"type": "error", "message": "Зараз хід іншого гравця."}); return
        if action == "hit":
            player.hand.add_card(self.deck.deal_card()); await self.send_room_state_to_all()
            if player.hand.value > 21:
                player.is_playing = False; await player.websocket.send_json({"type": "game_message", "message": "Ви перебрали! 💥"})
                await asyncio.sleep(1); await self.next_turn()
        elif action == "stand":
            player.is_playing = False; await player.websocket.send_json({"type": "game_message", "message": "Ви зупинились."})
            await asyncio.sleep(0.5); await self.next_turn()
        else: await player.websocket.send_json({"type": "error", "message": "Невідома дія."})
    def get_current_player(self) -> Optional[BlackjackPlayer]:
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players: return None
        return active_players[self.current_turn_index % len(active_players)]
    async def next_turn(self):
        self.current_turn_index += 1
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players: 
            self.status = "dealer_turn"; await self.send_room_state_to_all()
            await asyncio.sleep(1); await self.dealer_play()
        else: await self.send_room_state_to_all()
    async def start_round(self):
        print(f"Room {self.room_id}: Starting new round.")
        self.deck = Deck(); self.dealer_hand = Hand(); self.current_turn_index = 0
        for player in self.players.values():
            player.reset_for_round(); player.hand.add_card(self.deck.deal_card()); player.hand.add_card(self.deck.deal_card())
        self.dealer_hand.add_card(self.deck.deal_card()); self.dealer_hand.add_card(self.deck.deal_card())
        self.status = "playing"; await self.send_room_state_to_all()
        for player in self.players.values():
            if player.hand.value == 21 and len(player.hand.cards) == 2:
                player.is_playing = False; await player.websocket.send_json({"type": "game_message", "message": "У вас Блекджек! 🎉"})
                await asyncio.sleep(1)
        active_players_after_blackjack_check = [p for p in self.players.values() if p.is_playing]
        if not active_players_after_blackjack_check: await self.next_turn() 
        else: await self.send_room_state_to_all() 
    async def dealer_play(self):
        print(f"Room {self.room_id}: Dealer's turn.")
        self.status = "dealer_turn"; await self.send_room_state_to_all()
        await asyncio.sleep(1)
        while self.dealer_hand.value < 17:
            self.dealer_hand.add_card(self.deck.deal_card()); await self.send_room_state_to_all()
            await asyncio.sleep(1)
        await self.end_round()
    async def end_round(self):
        print(f"Room {self.room_id}: Ending round.")
        self.status = "round_end"; dealer_score = self.dealer_hand.value

        # Process results for each player and update database
        for player in self.players.values():
            user_data = get_user_data(player.user_id) # Get actual data from DB
            if not user_data: continue # Skip if user not found (should not happen)

            player_score = player.hand.value
            winnings = 0
            message = ""
            xp_gain = 0

            # Determine game results
            if player_score > 21: # Player busted
                message = "Ви перебрали! Програш."
                xp_gain = 1 # Small XP for participation
            elif dealer_score > 21: # Dealer busted
                winnings = player.bet * 2
                message = "Дилер перебрав! Ви виграли!"
                xp_gain = 10
            elif player_score > dealer_score: # Player won
                winnings = player.bet * 2
                message = "Ви виграли!"
                xp_gain = 10
            elif player_score < dealer_score: # Player lost
                message = "Ви програли."
                xp_gain = 1 # Small XP for participation
            else: # Push (Tie)
                winnings = player.bet # Return bet
                message = "Нічия!"
                xp_gain = 2 # Some XP for a tie

            new_balance = user_data["balance"] + winnings
            new_xp = user_data["xp"] + xp_gain
            new_level = get_level_from_xp(new_xp)

            update_user_data(player.user_id, balance=new_balance, xp=new_xp, level=new_level) # Update DB
            
            # Get updated data from DB for response, to ensure consistency
            updated_user_data_for_response = get_user_data(player.user_id)

            # Check if level up occurred and send appropriate message
            if new_level > user_data["level"]:
                await player.websocket.send_json({"type": "level_up", "level": new_level})

            # Send round result to player
            await player.websocket.send_json({
                "type": "round_result",
                "message": message,
                "winnings": winnings,
                "balance": updated_user_data_for_response["balance"],
                "xp": updated_user_data_for_response["xp"],
                "level": updated_user_data_for_response["level"],
                "next_level_xp": get_xp_for_next_level(updated_user_data_for_response["level"]),
                "final_player_score": player_score,
                "final_dealer_score": dealer_score # Send final dealer score for display
            })

            player.reset_for_round() # Reset player state for next round

        # After processing all players, return room to waiting state
        self.status = "waiting" 
        self.dealer_hand = Hand() # Clear dealer's hand
        await self.send_room_state_to_all() # Send reset state
        await asyncio.sleep(2) # Pause before transitioning to betting phase
        self.status = "betting" # Immediately allow betting for next round
        await self.send_room_state_to_all() # Notify clients to enable betting UI

class BlackjackRoomManager:
    def __init__(self): self.rooms: Dict[str, BlackjackRoom] = {}
    async def create_or_join_room(self, user_id: int, username: str, websocket: WebSocket):
        for room_id, room in self.rooms.items():
            if room.status in ["waiting", "betting"] and len(room.players) < room.max_players:
                success, msg = await room.add_player(user_id, username, websocket)
                if success:
                    print(f"Player {user_id} joined existing room {room_id}. Current players: {len(room.players)}")
                    if len(room.players) >= room.min_players and room.status == "waiting":
                        room.status = "starting_timer"
                        if room.game_start_timer and not room.game_start_timer.done(): room.game_start_timer.cancel()
                        room.timer_countdown = 20
                        room.game_start_timer = asyncio.create_task(self._start_game_after_delay(room_id, 20))
                        print(f"Room {room_id}: Game start timer initiated for 20 seconds.")
                    await room.send_room_state_to_all(); return room_id
        new_room_id = str(uuid.uuid4())[:8]; new_room = BlackjackRoom(new_room_id)
        self.rooms[new_room_id] = new_room
        success, msg = await new_room.add_player(user_id, username, websocket)
        if success: print(f"Player {user_id} created and joined new room {new_room_id}"); await new_room.send_room_state_to_all(); return new_room_id
        return None
    async def _start_game_after_delay(self, room_id: str, delay: int):
        room = self.rooms.get(room_id)
        if not room: return
        for i in range(delay, 0, -1):
            room.timer_countdown = i
            if room.status != "starting_timer" or len(room.players) < room.min_players:
                print(f"Room {room_id} timer cancelled/interrupted.")
                if len(room.players) < room.min_players: room.status = "waiting"
                room.timer_countdown = 0; await room.send_room_state_to_all(); return
            await room.send_room_state_to_all(); await asyncio.sleep(1)
        if room.status == "starting_timer" and len(room.players) >= room.min_players:
            print(f"Room {room_id}: Timer finished, moving to betting phase."); room.status = "betting"
            room.timer_countdown = 0; await room.send_room_state_to_all()

blackjack_room_manager = BlackjackRoomManager()

# --- WebSocket Endpoint ---
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str): # user_id as str initially from path
    user_id_int = int(user_id) # Convert to int for internal use

    user_data_db = get_user_data(user_id_int)
    username = user_data_db.get("username", f"Гравець {str(user_id_int)[-4:]}")
    
    room_id = await blackjack_room_manager.create_or_join_room(user_id_int, username, websocket)
    if not room_id: await websocket.close(code=1008, reason="Could not join/create room."); return
    try:
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data); action = message.get("action")
                room = blackjack_room_manager.rooms.get(room_id)
                if not room: await websocket.send_json({"type": "error", "message": "Кімната не знайдена."}); continue
                if action == "bet":
                    amount = message.get("amount")
                    if amount is not None: await room.handle_bet(user_id_int, amount)
                    else: await websocket.send_json({"type": "error", "message": "Сума ставки не вказана."})
                elif action in ["hit", "stand"]: await room.handle_action(user_id_int, action)
                elif action == "request_state": await room.send_room_state_to_all()
                else: await websocket.send_json({"type": "error", "message": "Невідома дія."})
            except json.JSONDecodeError:
                print(f"Received non-JSON message from {user_id_int}: {data}")
                await websocket.send_json({"type": "error", "message": "Неправильний формат повідомлення (очікується JSON)."})
            except Exception as e:
                print(f"Error handling WebSocket message from {user_id_int}: {e}")
                await websocket.send_json({"type": "error", "message": f"Помилка сервера: {str(e)}"})
    except WebSocketDisconnect:
        print(f"Client {user_id_int} disconnected from room {room_id}.")
        room = blackjack_room_manager.rooms.get(room_id)
        if room:
            await room.remove_player(user_id_int)
            if room.players: await room.send_room_state_to_all()
    except Exception as e: print(f"Unexpected error in WebSocket endpoint for {user_id_int}: {e}")
        
# --- Serve the main HTML file ---
@app.get("/")
async def get_root():
    index_html_path = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(index_html_path): raise HTTPException(status_code=404, detail="index.html not found")
    with open(index_html_path, "r", encoding="utf-8") as f: html_content = f.read()
    return HTMLResponse(content=html_content)

# --- Telegram Webhook Endpoint ---
@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    update_json = await request.json()
    update = types.Update.model_validate(update_json, context={"bot": bot})
    await dp.feed_update(bot, update) 
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
        external_hostname = "localhost:8000" # Fallback for local testing
    global WEBHOOK_URL
    WEBHOOK_URL = f"https://{external_hostname}{WEBHOOK_PATH}" 
    global WEB_APP_FRONTEND_URL
    if WEB_APP_FRONTEND_URL and not WEB_APP_FRONTEND_URL.startswith("https://"):
        WEB_APP_FRONTEND_URL = f"https://{WEB_APP_FRONTEND_URL}"
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
