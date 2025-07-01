import logging
import os
import json
import random
import urllib.parse
import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional, Any

import psycopg2
from psycopg2 import sql

from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

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
DATABASE_URL = os.getenv('DATABASE_URL')

WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

# --- FastAPI App Setup ---
app = FastAPI()

origins = [
    WEB_APP_FRONTEND_URL,
    "http://localhost:3000",  # For local development
    "http://localhost:5173"   # For local Vite development
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Allow all origins for now, consider restricting in production
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Serve static files from the 'webapp' directory
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# --- Telegram Bot Setup ---
bot = None
dp = None

if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
else:
    logger.warning("BOT_TOKEN is not set or is a dummy value. Telegram bot features will be disabled.")

# --- Database Connection ---
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
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username TEXT DEFAULT 'Unnamed Player',
                balance INTEGER DEFAULT 1000,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL
            );
        """)
        logger.info("Table 'users' initialized or already exists.")

        # Apply migrations
        migrations = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS username TEXT DEFAULT 'Unnamed Player';",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;"
        ]
        for migration in migrations:
            try:
                cur.execute(migration)
                logger.info(f"Migration applied: {migration}")
            except Exception as e:
                logger.warning(f"Migration failed (possibly already applied): {migration} - {e}")
        
        conn.commit()
        logger.info("DB schema migration checked.")
    except Exception as e:
        logger.error(f"Error initializing database: {e}")
    finally:
        if conn:
            conn.close()

# Initialize DB on startup
init_db()

# --- User Data Operations ---
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

        current_data_from_db = get_user_data(user_id_int) 
        logger.info(f"Before update for {user_id_int}: {current_data_from_db.get('balance', 'N/A')} balance, {current_data_from_db.get('xp', 'N/A')} xp, {current_data_from_db.get('level', 'N/A')} level.")

        update_fields_parts = []
        update_values = []

        fields_to_update = {
            'username': kwargs.get('username', current_data_from_db.get('username', 'Unnamed Player')),
            'balance': kwargs.get('balance', current_data_from_db.get('balance', 0)),
            'xp': kwargs.get('xp', current_data_from_db.get('xp', 0)),
            'level': kwargs.get('level', current_data_from_db.get('level', 1)),
            'last_free_coins_claim': kwargs.get('last_free_coins_claim', current_data_from_db.get('last_free_coins_claim')),
            'last_daily_bonus_claim': kwargs.get('last_daily_bonus_claim', current_data_from_db.get('last_daily_bonus_claim')),
            'last_quick_bonus_claim': kwargs.get('last_quick_bonus_claim', current_data_from_db.get('last_quick_bonus_claim'))
        }
        
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

# --- XP and Leveling System ---
LEVEL_THRESHOLDS = {
    1: 0,
    2: 100,
    3: 250,
    4: 500,
    5: 800,
    6: 1200,
    7: 1700,
    8: 2300,
    9: 3000,
    10: 4000,
    11: 5500,
    12: 7000,
    13: 9000,
    14: 11500,
    15: 14500,
    16: 18000,
    17: 22000,
    18: 26500,
    19: 31500,
    20: 37000
}

def get_next_level_xp(current_level: int) -> int:
    next_level = current_level + 1
    return LEVEL_THRESHOLDS.get(next_level, LEVEL_THRESHOLDS.get(max(LEVEL_THRESHOLDS.keys()))) # Return max if beyond defined levels

def calculate_level_and_xp(current_xp: int, current_level: int) -> tuple[int, int]:
    new_level = current_level
    while new_level + 1 in LEVEL_THRESHOLDS and current_xp >= LEVEL_THRESHOLDS[new_level + 1]:
        new_level += 1
    return new_level, current_xp

# --- Telegram Bot Handlers ---
@dp.message(CommandStart())
async def command_start_handler(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or f"Гравець {str(user_id)[-4:]}"
    
    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        logger.info(f"CommandStart: User {user_id} fetched data: {user_data}")
        
        keyboard = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🎮 Відкрити гру", web_app=WebAppInfo(url=WEB_APP_FRONTEND_URL))]
        ])
        
        await message.answer(
            f"Вітаємо, {username}!\n\n"
            f"Ваш баланс: {user_data['balance']} фантиків.\n"
            f"Ваш рівень: {user_data['level']} (XP: {user_data['xp']}/{get_next_level_xp(user_data['level'])})\n\n"
            "Натисніть кнопку нижче, щоб відкрити віртуальне казино!",
            reply_markup=keyboard
        )
        logger.info(f"User {user_id} ({username}) started the bot. Balance: {user_data['balance']}.")
    except Exception as e:
        logger.error(f"Error in command_start_handler for user {user_id}: {e}")
        await message.answer("Вибачте, виникла помилка при запуску гри. Будь ласка, спробуйте пізніше.")

@dp.message(Command("balance"))
async def command_balance_handler(message: Message) -> None:
    user_id = message.from_user.id
    username = message.from_user.username or message.from_user.first_name or f"Гравець {str(user_id)[-4:]}"
    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        await message.answer(
            f"Ваш баланс: {user_data['balance']} фантиків.\n"
            f"Ваш рівень: {user_data['level']} (XP: {user_data['xp']}/{get_next_level_xp(user_data['level'])})"
        )
    except Exception as e:
        logger.error(f"Error in command_balance_handler for user {user_id}: {e}")
        await message.answer("Вибачте, не вдалося отримати ваш баланс.")

# --- API Endpoints for WebApp ---
class UserRequest(BaseModel):
    user_id: int
    username: Optional[str] = 'Unnamed Player'

class SpinRequest(UserRequest):
    pass

class CoinFlipRequest(UserRequest):
    choice: str # 'heads' or 'tails'

class BetRequest(UserRequest):
    amount: int
    room_id: str # For Blackjack

class HitStandRequest(UserRequest):
    room_id: str

@app.post("/api/get_balance")
async def get_balance(request: UserRequest):
    try:
        user_data = get_user_data(request.user_id) # Use the single get_user_data
        user_data['next_level_xp'] = get_next_level_xp(user_data['level'])
        return user_data
    except Exception as e:
        logger.error(f"API Error /api/get_balance for user {request.user_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "Failed to retrieve balance", "message": str(e)})

@app.post("/api/spin")
async def spin_slot(request: SpinRequest):
    user_id = request.user_id
    username = request.username
    SPIN_COST = 100
    
    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        if user_data["balance"] < SPIN_COST:
            raise HTTPException(status_code=400, detail={"error": "Insufficient funds"})

        # Deduct cost
        user_data["balance"] -= SPIN_COST
        
        # Spin logic
        symbols = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀']
        wild_symbol = '⭐'
        scatter_symbol = '💰'
        
        reel1 = random.choice(symbols)
        reel2 = random.choice(symbols)
        reel3 = random.choice(symbols)

        # For testing purposes, uncomment to force a win
        # reel1 = '🍒'
        # reel2 = '🍒'
        # reel3 = '🍒'

        # Check for winnings
        winnings = 0
        xp_gain = 0
        
        # 3 matching symbols
        if reel1 == reel2 == reel3:
            if reel1 == '💎': winnings = 1000; xp_gain = 20
            elif reel1 == '🔔': winnings = 750; xp_gain = 15
            elif reel1 == '🍀': winnings = 500; xp_gain = 10
            elif reel1 == '🍒': winnings = 400; xp_gain = 8
            elif reel1 == '🍊': winnings = 200; xp_gain = 4
            elif reel1 == '�': winnings = 150; xp_gain = 3
            elif reel1 == '🍇': winnings = 300; xp_gain = 6
            else: winnings = 100; xp_gain = 2 # Should not happen with current symbols
        
        # Wild symbol logic (simplified: replaces any single symbol to make a match)
        # This is a basic example, real slots have complex wild logic
        if wild_symbol in [reel1, reel2, reel3]:
            # If two symbols match and one is wild
            if (reel1 == reel2 and reel3 == wild_symbol) or \
               (reel1 == reel3 and reel2 == wild_symbol) or \
               (reel2 == reel3 and reel1 == wild_symbol):
                if winnings == 0: # Only apply if no 3-match win already
                    winnings += 50 # Small bonus for wild match
                    xp_gain += 1

        # Scatter symbol (e.g., bonus game trigger, not implemented here)
        scatter_count = [reel1, reel2, reel3].count(scatter_symbol)
        if scatter_count >= 2: # Example: 2 scatters give small win
            if winnings == 0:
                winnings += 20 # Small bonus for scatters
                xp_gain += 1

        user_data["balance"] += winnings
        user_data["xp"] += xp_gain
        
        new_level, new_xp = calculate_level_and_xp(user_data["xp"], user_data["level"])
        user_data["level"] = new_level
        user_data["xp"] = new_xp # XP might not change if level up consumes it, but here it just accumulates

        update_user_data(user_id, balance=user_data["balance"], xp=user_data["xp"], level=user_data["level"]) # Use the single update_user_data

        return {
            "symbols": [reel1, reel2, reel3],
            "winnings": winnings,
            "balance": user_data["balance"],
            "xp": user_data["xp"],
            "level": user_data["level"],
            "next_level_xp": get_next_level_xp(user_data["level"])
        }
    except HTTPException:
        raise # Re-raise HTTP exceptions
    except Exception as e:
        logger.error(f"API Error /api/spin for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "Spin failed", "message": str(e)})

@app.post("/api/coin_flip")
async def coin_flip(request: CoinFlipRequest):
    user_id = request.user_id
    username = request.username
    choice = request.choice
    FLIP_COST = 50

    if choice not in ['heads', 'tails']:
        raise HTTPException(status_code=400, detail={"error": "Invalid choice. Must be 'heads' or 'tails'."})

    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        if user_data["balance"] < FLIP_COST:
            raise HTTPException(status_code=400, detail={"error": "Insufficient funds"})

        user_data["balance"] -= FLIP_COST

        result = random.choice(['heads', 'tails'])
        winnings = 0
        xp_gain = 0
        message = ""

        if result == choice:
            winnings = FLIP_COST * 2 # Double the bet
            user_data["balance"] += winnings
            xp_gain = 5
            message = f"🎉 Вітаємо! Ви вгадали! Ви виграли {winnings} фантиків!"
        else:
            xp_gain = 1 # Small XP even on loss
            message = "😢 На жаль, ви не вгадали. Спробуйте ще раз!"

        user_data["xp"] += xp_gain
        new_level, new_xp = calculate_level_and_xp(user_data["xp"], user_data["level"])
        user_data["level"] = new_level
        user_data["xp"] = new_xp

        update_user_data(user_id, balance=user_data["balance"], xp=user_data["xp"], level=user_data["level"]) # Use the single update_user_data

        return {
            "result": result,
            "winnings": winnings,
            "balance": user_data["balance"],
            "xp": user_data["xp"],
            "level": user_data["level"],
            "next_level_xp": get_next_level_xp(user_data["level"]),
            "message": message
        }

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API Error /api/coin_flip for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "Coin flip failed", "message": str(e)})

@app.post("/api/claim_daily_bonus")
async def claim_daily_bonus(request: UserRequest):
    user_id = request.user_id
    username = request.username
    BONUS_AMOUNT = 500
    COOLDOWN_HOURS = 24

    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        last_claim = user_data.get("last_daily_bonus_claim")
        now = datetime.now(timezone.utc)

        if last_claim and (now - last_claim) < timedelta(hours=COOLDOWN_HOURS):
            remaining_time = timedelta(hours=COOLDOWN_HOURS) - (now - last_claim)
            raise HTTPException(status_code=429, detail={
                "error": "Cooldown active",
                "message": f"Ви вже отримали щоденну винагороду. Спробуйте через {int(remaining_time.total_seconds() // 3600)} год {int((remaining_time.total_seconds() % 3600) // 60)} хв."
            })

        user_data["balance"] += BONUS_AMOUNT
        user_data["xp"] += 10 # Small XP for claiming bonus
        user_data["last_daily_bonus_claim"] = now
        
        new_level, new_xp = calculate_level_and_xp(user_data["xp"], user_data["level"])
        user_data["level"] = new_level
        user_data["xp"] = new_xp

        update_user_data(
            user_id,
            balance=user_data["balance"],
            xp=user_data["xp"],
            level=user_data["level"],
            last_daily_bonus_claim=now
        ) # Use the single update_user_data
        return {"message": f"Ви успішно отримали {BONUS_AMOUNT} фантиків!", "amount": BONUS_AMOUNT}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API Error /api/claim_daily_bonus for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "Failed to claim daily bonus", "message": str(e)})

@app.post("/api/claim_quick_bonus")
async def claim_quick_bonus(request: UserRequest):
    user_id = request.user_id
    username = request.username
    BONUS_AMOUNT = 50
    COOLDOWN_MINUTES = 15

    try:
        user_data = get_user_data(user_id) # Use the single get_user_data
        last_claim = user_data.get("last_quick_bonus_claim")
        now = datetime.now(timezone.utc)

        if last_claim and (now - last_claim) < timedelta(minutes=COOLDOWN_MINUTES):
            remaining_time = timedelta(minutes=COOLDOWN_MINUTES) - (now - last_claim)
            raise HTTPException(status_code=429, detail={
                "error": "Cooldown active",
                "message": f"Ви вже отримали швидкий бонус. Спробуйте через {int(remaining_time.total_seconds() // 60)} хв {int(remaining_time.total_seconds() % 60)} сек."
            })

        user_data["balance"] += BONUS_AMOUNT
        user_data["xp"] += 2 # Very small XP for quick bonus
        user_data["last_quick_bonus_claim"] = now
        
        new_level, new_xp = calculate_level_and_xp(user_data["xp"], user_data["level"])
        user_data["level"] = new_level
        user_data["xp"] = new_xp

        update_user_data(
            user_id,
            balance=user_data["balance"],
            xp=user_data["xp"],
            level=user_data["level"],
            last_quick_bonus_claim=now
        ) # Use the single update_user_data
        return {"message": f"Ви успішно отримали {BONUS_AMOUNT} фантиків!", "amount": BONUS_AMOUNT}

    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"API Error /api/claim_quick_bonus for user {user_id}: {e}")
        raise HTTPException(status_code=500, detail={"error": "Failed to claim quick bonus", "message": str(e)})

@app.post("/api/get_leaderboard")
async def get_leaderboard():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        # Order by level descending, then xp descending
        cur.execute("SELECT username, balance, xp, level FROM users ORDER BY level DESC, xp DESC LIMIT 100")
        leaderboard_data = cur.fetchall()
        
        result = []
        for row in leaderboard_data:
            result.append({
                "username": row[0],
                "balance": row[1],
                "xp": row[2],
                "level": row[3]
            })
        return {"leaderboard": result}
    except Exception as e:
        logger.error(f"API Error /api/get_leaderboard: {e}")
        raise HTTPException(status_code=500, detail={"error": "Failed to retrieve leaderboard", "message": str(e)})
    finally:
        if conn:
            conn.close()

# --- Blackjack Game Logic (Multiplayer with WebSockets) ---

class Card:
    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = self._get_value()

    def _get_value(self):
        if self.rank in ['J', 'Q', 'K']:
            return 10
        elif self.rank == 'A':
            return 11 # Ace can be 1 or 11, handle in hand logic
        else:
            return int(self.rank)

    def __str__(self):
        return f"{self.rank}{self.suit}"

    def __repr__(self):
        return self.__str__()

class Deck:
    def __init__(self):
        self.cards = []
        self.reset()

    def reset(self):
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        suits = ['♠', '♦', '♥', '♣']
        self.cards = [Card(rank, suit) for rank in ranks for suit in suits]
        random.shuffle(self.cards)

    def deal_card(self):
        if not self.cards:
            self.reset() # Reshuffle if deck is empty
            logger.info("Deck reshuffled.")
        return self.cards.pop()

class BlackjackPlayer:
    def __init__(self, user_id: int, username: str):
        self.user_id = user_id
        self.username = username
        self.hand: List[Card] = []
        self.score = 0
        self.bet = 0
        self.is_playing = True # True if player is active in the current round
        self.has_bet = False # True if player has placed a bet for the current round

    def add_card(self, card: Card):
        self.hand.append(card)
        self._calculate_score()

    def _calculate_score(self):
        self.score = sum(card.value for card in self.hand)
        num_aces = sum(1 for card in self.hand if card.rank == 'A')
        while self.score > 21 and num_aces > 0:
            self.score -= 10 # Change Ace from 11 to 1
            num_aces -= 1

    def clear_hand(self):
        self.hand = []
        self.score = 0
        self.bet = 0
        self.is_playing = True # Reset for next round
        self.has_bet = False # Reset for next round

    def to_dict(self, hide_dealer_card=False):
        hand_display = [str(card) for card in self.hand]
        if hide_dealer_card and self.username == "Dealer" and len(hand_display) > 1:
            hand_display[0] = "Hidden" # Hide first card for dealer
        return {
            "user_id": self.user_id,
            "username": self.username,
            "hand": hand_display,
            "score": self.score,
            "bet": self.bet,
            "is_playing": self.is_playing,
            "has_bet": self.has_bet
        }

class BlackjackRoom:
    def __init__(self, room_id: str, min_players: int = 2, max_players: int = 4):
        self.room_id = room_id
        self.players: Dict[int, BlackjackPlayer] = {} # user_id -> BlackjackPlayer
        self.connections: Dict[int, WebSocket] = {} # user_id -> WebSocket
        self.deck = Deck()
        self.dealer = BlackjackPlayer(user_id=0, username="Dealer") # Dealer is a special player
        self.status = "waiting" # waiting, starting_timer, betting, playing, round_end
        self.min_players = min_players
        self.max_players = max_players
        self.current_player_turn: Optional[int] = None
        self.timer_task: Optional[asyncio.Task] = None
        self.timer_seconds = 0
        self.round_in_progress = False
        self.ping_task: Optional[asyncio.Task] = None
        logger.info(f"Room {self.room_id} created with min_players={min_players}, max_players={max_players}")

    async def add_player(self, user_id: int, username: str, websocket: WebSocket):
        if user_id not in self.players:
            if len(self.players) >= self.max_players:
                await websocket.send_json({"type": "error", "message": "Кімната повна."})
                return False
            self.players[user_id] = BlackjackPlayer(user_id, username)
            logger.info(f"Player {user_id} ({username}) added to room {self.room_id}. Current players: {len(self.players)}")
        else:
            # Player is rejoining, update username and set is_playing to True
            self.players[user_id].username = username
            self.players[user_id].is_playing = True
            logger.info(f"Player {user_id} ({username}) re-joined room {self.room_id}.")

        self.connections[user_id] = websocket
        await self.broadcast_room_state()
        self._check_and_start_game_if_ready()
        return True

    async def remove_player(self, user_id: int):
        if user_id in self.players:
            player_username = self.players[user_id].username
            del self.players[user_id]
            if user_id in self.connections:
                del self.connections[user_id]
            logger.info(f"Player {user_id} removed from room {self.room_id}")

            # If player left during their turn, advance turn
            if self.status == "playing" and self.current_player_turn == user_id:
                await self._advance_turn()
            
            # If player left during betting and they were the last one to bet, check if round can start
            if self.status == "betting":
                logger.info(f"Room {self.room_id}: Player {user_id} left during betting. Re-checking round start conditions.")
                asyncio.create_task(self._check_and_start_round_if_ready()) # Use asyncio.create_task for non-blocking call
            
            await self.broadcast_room_state()
            self._check_and_end_game_if_empty()
        
    def _check_and_start_game_if_ready(self):
        if len(self.players) >= self.min_players and self.status == "waiting":
            self.status = "starting_timer"
            self.timer_seconds = 20 # Start countdown for game start
            logger.info(f"Room {self.room_id}: Game start timer initiated for {self.timer_seconds} seconds.")
            self.timer_task = asyncio.create_task(self._countdown_timer("betting"))

    async def _check_and_start_round_if_ready(self):
        if self.round_in_progress:
            logger.info(f"Room {self.room_id}: Round already in progress, skipping start check.")
            return

        active_players = [p for p in self.players.values() if p.is_playing]
        
        # Log player has_bet statuses for debugging
        player_has_bet_status = {p.user_id: p.has_bet for p in self.players.values()}
        player_is_playing_status = {p.user_id: p.is_playing for p in self.players.values()}
        logger.info(f"Room {self.room_id}. Player statuses: {player_is_playing_status}. All finished betting: {all(p.has_bet for p in active_players)}. Current players in room: {len(self.players)}. Min players: {self.min_players}. Round in progress: {self.round_in_progress}")

        if len(active_players) >= self.min_players and all(p.has_bet for p in active_players):
            logger.info(f"Room {self.room_id}: All active players have bet. Starting round.")
            self.round_in_progress = True
            await self._start_round()
        else:
            logger.info(f"Room {self.room_id}: Not all players finished betting or not enough players. Conditions for starting round not met.")
            # If we are in betting phase and some players haven't bet, reset their is_playing status
            if self.status == "betting":
                for player in self.players.values():
                    if not player.has_bet:
                        player.is_playing = False # Mark them as not playing this round
                        logger.info(f"Player {player.user_id} did not bet, marked as not playing this round.")
                await self.broadcast_room_state() # Update clients with player.is_playing status

    def _check_and_end_game_if_empty(self):
        if not self.players:
            logger.info(f"Room {self.room_id} is empty and removed.")
            if self.timer_task:
                self.timer_task.cancel()
            if self.ping_task:
                self.ping_task.cancel()
            del rooms[self.room_id] # Remove room from global dict

    async def _countdown_timer(self, next_status: str):
        try:
            while self.timer_seconds > 0:
                await self.broadcast_room_state()
                await asyncio.sleep(1)
                self.timer_seconds -= 1
            
            self.status = next_status
            self.timer_seconds = 0
            logger.info(f"Room {self.room_id}: Timer finished, moving to {next_status} phase.")
            
            if next_status == "betting":
                # Start betting timer
                self.timer_seconds = 20 # 20 seconds for betting
                self.timer_task = asyncio.create_task(self._countdown_timer("playing")) # Next phase after betting is playing
                await self.broadcast_room_state()
            elif next_status == "playing":
                # If betting timer finished, and not all players bet, mark them as not playing
                for player in self.players.values():
                    if player.is_playing and not player.has_bet:
                        player.is_playing = False
                        logger.info(f"Player {player.user_id} did not bet in time, marked as not playing this round.")
                await self.broadcast_room_state() # Send updated player statuses
                asyncio.create_task(self._check_and_start_round_if_ready()) # Check if round can start now
            elif next_status == "round_end":
                await self._end_round()
        except asyncio.CancelledError:
            logger.info(f"Room {self.room_id}: Timer task cancelled.")
        except Exception as e:
            logger.error(f"Error in room {self.room_id} timer: {e}")

    async def _start_round(self):
        logger.info(f"Room {self.room_id}: Starting new round.")
        self.status = "playing"
        self.round_in_progress = True

        # Clear hands and reset for all active players
        for player in self.players.values():
            player.clear_hand()
        self.dealer.clear_hand()
        self.deck.reset()

        # Initial deal
        for _ in range(2):
            for player in self.players.values():
                if player.is_playing: # Only deal to active players
                    player.add_card(self.deck.deal_card())
            self.dealer.add_card(self.deck.deal_card())
        
        # Determine first player's turn
        self.current_player_turn = None
        active_player_ids = [p.user_id for p in self.players.values() if p.is_playing]
        if active_player_ids:
            self.current_player_turn = active_player_ids[0]
            self.timer_seconds = 15 # Timer for player turn
            self.timer_task = asyncio.create_task(self._countdown_timer_for_turn(self.current_player_turn))
        else:
            logger.warning(f"Room {self.room_id}: No active players to start round with after betting phase.")
            await self._end_round() # End round if no players are active

        await self.broadcast_room_state()

    async def _countdown_timer_for_turn(self, player_id: int):
        try:
            while self.timer_seconds > 0 and self.current_player_turn == player_id:
                await self.broadcast_room_state() # Send state to update timer on client
                await asyncio.sleep(1)
                self.timer_seconds -= 1
            
            if self.current_player_turn == player_id: # If timer ran out for current player
                logger.info(f"Player {player_id}'s turn timed out. Automatically standing.")
                await self.handle_stand(player_id) # Auto-stand
        except asyncio.CancelledError:
            logger.info(f"Room {self.room_id}: Player turn timer task cancelled for {player_id}.")
        except Exception as e:
            logger.error(f"Error in player turn timer for {player_id} in room {self.room_id}: {e}")

    async def _advance_turn(self):
        active_player_ids = [p.user_id for p in self.players.values() if p.is_playing]
        if not active_player_ids:
            logger.info(f"Room {self.room_id}: No active players left. Ending round.")
            await self._end_round()
            return

        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None

        try:
            current_player_index = active_player_ids.index(self.current_player_turn)
            next_player_index = (current_player_index + 1) % len(active_player_ids)
            self.current_player_turn = active_player_ids[next_player_index]
            self.timer_seconds = 15 # Reset timer for next player
            self.timer_task = asyncio.create_task(self._countdown_timer_for_turn(self.current_player_turn))
            logger.info(f"Room {self.room_id}: Advanced turn to {self.current_player_turn}.")
        except ValueError: # Current player not found, likely left
            logger.warning(f"Room {self.room_id}: Current player {self.current_player_turn} not found in active players. Finding next.")
            self.current_player_turn = active_player_ids[0] # Just pick first active player
            self.timer_seconds = 15
            self.timer_task = asyncio.create_task(self._countdown_timer_for_turn(self.current_player_turn))
        
        await self.broadcast_room_state()


    async def handle_bet(self, user_id: int, amount: int):
        player = self.players.get(user_id)
        if not player or self.status != "betting" or player.has_bet:
            await self.connections[user_id].send_json({"type": "error", "message": "Неправильний стан для ставки або ставка вже зроблена."})
            return

        user_data = get_user_data(user_id) # Fetch current balance
        if user_data["balance"] < amount:
            await self.connections[user_id].send_json({"type": "error", "message": "Недостатньо фантиків для ставки."})
            return

        player.bet = amount
        player.has_bet = True
        user_data["balance"] -= amount # Deduct bet immediately
        update_user_data(user_id, balance=user_data["balance"], xp=user_data["xp"], level=user_data["level"]) # Update DB
        logger.info(f"handle_bet: Player {user_id} successfully bet {amount}. New balance: {user_data['balance']}")

        # Log player has_bet statuses for debugging
        player_has_bet_status = {p.user_id: p.has_bet for p in self.players.values()}
        logger.info(f"handle_bet: After player {user_id} bet, players' has_bet status: {player_has_bet_status}")
        
        await self.broadcast_room_state() # Update all clients with new bet status
        asyncio.create_task(self._check_and_start_round_if_ready()) # Check if all players have bet and round can start

    async def handle_hit(self, user_id: int):
        player = self.players.get(user_id)
        if not player or self.status != "playing" or self.current_player_turn != user_id:
            await self.connections[user_id].send_json({"type": "error", "message": "Зараз не ваш хід або гра не в стані 'playing'."})
            return

        player.add_card(self.deck.deal_card())
        
        if player.score > 21:
            logger.info(f"Player {user_id} went bust with score {player.score}.")
            player.is_playing = False # Player is out for this round
            await self.connections[user_id].send_json({"type": "game_message", "message": "Перебір! Ваш рахунок більше 21."})
            await self.broadcast_room_state()
            await self._advance_turn()
        else:
            await self.broadcast_room_state()

    async def handle_stand(self, user_id: int):
        player = self.players.get(user_id)
        if not player or self.status != "playing" or self.current_player_turn != user_id:
            await self.connections[user_id].send_json({"type": "error", "message": "Зараз не ваш хід або гра не в стані 'playing'."})
            return
        
        player.is_playing = False # Player decided to stand
        logger.info(f"Player {user_id} stood with score {player.score}.")
        await self.connections[user_id].send_json({"type": "game_message", "message": "Ви зупинились."})
        await self.broadcast_room_state()
        await self._advance_turn()

    async def _dealer_play(self):
        logger.info(f"Room {self.room_id}: Dealer's turn. Initial hand: {self.dealer.hand}, score: {self.dealer.score}")
        while self.dealer.score < 17:
            self.dealer.add_card(self.deck.deal_card())
            logger.info(f"Room {self.room_id}: Dealer hits. New hand: {self.dealer.hand}, score: {self.dealer.score}")
            await self.broadcast_room_state(show_dealer_card=True) # Reveal dealer's hidden card during play
            await asyncio.sleep(1) # Small delay for animation effect
        logger.info(f"Room {self.room_id}: Dealer stands with score {self.dealer.score}.")


    async def _end_round(self):
        logger.info(f"Room {self.room_id}: Round ending. Calculating results.")
        self.status = "round_end"
        self.round_in_progress = False
        self.current_player_turn = None
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None

        # Reveal dealer's hand and play
        await self.broadcast_room_state(show_dealer_card=True) # Reveal dealer's hidden card
        await asyncio.sleep(1) # Small delay before dealer plays
        await self._dealer_play()
        await self.broadcast_room_state(show_dealer_card=True) # Final dealer hand

        results = {}
        for user_id, player in list(self.players.items()): # Iterate over a copy in case players leave
            if not player.is_playing:
                # If player was not playing (e.g., didn't bet or went bust), they lose their bet
                results[user_id] = {"message": "Ви не брали участь у раунді або перебрали.", "winnings": 0, "final_player_score": player.score}
                # No need to deduct balance again, it was deducted on bet
                continue

            user_data = get_user_data(user_id) # Fetch latest user data
            winnings = 0
            xp_gain = 0
            message = ""

            if player.score > 21: # Already handled by handle_hit, but double check
                message = "Перебір! Ви програли."
                # Winnings is 0, bet already deducted
            elif self.dealer.score > 21:
                message = "Дилер перебрав! Ви виграли!"
                winnings = player.bet * 2
                xp_gain = 10
            elif player.score > self.dealer.score:
                message = "Ви виграли у дилера!"
                winnings = player.bet * 2
                xp_gain = 10
            elif player.score < self.dealer.score:
                message = "Ви програли дилеру."
                # Winnings is 0, bet already deducted
                xp_gain = 2 # Small XP for participation
            else:
                message = "Нічия! Ваша ставка повернена."
                winnings = player.bet # Return bet
                xp_gain = 5

            user_data["balance"] += winnings
            user_data["xp"] += xp_gain
            
            new_level, new_xp = calculate_level_and_xp(user_data["xp"], user_data["level"])
            
            # Check for level up notification
            if new_level > user_data["level"]:
                await self.connections[user_id].send_json({"type": "level_up", "level": new_level})
                logger.info(f"Player {user_id} leveled up to {new_level}!")

            user_data["level"] = new_level
            user_data["xp"] = new_xp

            update_user_data(user_id, balance=user_data["balance"], xp=user_data["xp"], level=user_data["level"])
            
            results[user_id] = {
                "message": message,
                "winnings": winnings,
                "balance": user_data["balance"],
                "xp": user_data["xp"],
                "level": user_data["level"],
                "next_level_xp": get_next_level_xp(user_data["level"]),
                "final_player_score": player.score
            }
            logger.info(f"Player {user_id} round result: {results[user_id]}")

        # Send individual results to players
        for user_id, result_data in results.items():
            if user_id in self.connections:
                await self.connections[user_id].send_json({"type": "round_result", **result_data})
        
        # Reset players for next round
        for player in self.players.values():
            player.clear_hand()
        self.dealer.clear_hand()

        await asyncio.sleep(5) # Pause before starting next round
        self.status = "waiting" # Reset to waiting for next round
        await self.broadcast_room_state() # Notify clients of reset
        self._check_and_start_game_if_ready() # Check if enough players to start next game

    async def broadcast_room_state(self, show_dealer_card: bool = False):
        state = {
            "room_id": self.room_id,
            "status": self.status,
            "dealer_hand": [str(card) for card in self.dealer.hand] if show_dealer_card else [str(self.dealer.hand[0]), "Hidden"] if len(self.dealer.hand) > 1 else [str(self.dealer.hand[0])] if self.dealer.hand else [],
            "dealer_score": self.dealer.score if show_dealer_card or len(self.dealer.hand) == 0 else self.dealer.hand[0].value, # Only show first card value if hidden
            "players": [p.to_dict() for p in self.players.values()],
            "current_player_turn": self.current_player_turn,
            "player_count": len(self.players),
            "min_players": self.min_players,
            "max_players": self.max_players,
            "timer": self.timer_seconds
        }
        for user_id, ws in list(self.connections.items()):
            try:
                await ws.send_json(state)
            except RuntimeError as e:
                logger.error(f"Error broadcasting to {user_id} in room {self.room_id}: {e}")
                # This player's websocket is likely closed, remove them
                asyncio.create_task(self.remove_player(user_id))
            except Exception as e:
                logger.error(f"Unexpected error broadcasting to {user_id} in room {self.room_id}: {e}")
                asyncio.create_task(self.remove_player(user_id))

    async def send_ping(self):
        try:
            while True:
                await asyncio.sleep(10) # Send ping every 10 seconds
                for user_id, ws in list(self.connections.items()):
                    try:
                        await ws.send_json({"type": "ping"})
                        # logger.debug(f"Sent ping to {user_id} in room {self.room_id}")
                    except RuntimeError as e:
                        logger.warning(f"Failed to send ping to {user_id} in room {self.room_id}: {e}")
                        asyncio.create_task(self.remove_player(user_id))
        except asyncio.CancelledError:
            logger.info(f"Room {self.room_id}: Ping task cancelled.")
        except Exception as e:
            logger.error(f"Error in room {self.room_id} ping task: {e}")


rooms: Dict[str, BlackjackRoom] = {} # room_id -> BlackjackRoom
player_room_map: Dict[int, str] = {} # user_id -> room_id

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for user {user_id}.")

    username = f"Гравець {str(user_id)[-4:]}" # Default username
    try:
        user_data = get_user_data(user_id) # Fetch to get actual username if exists
        username = user_data.get('username', username)
    except Exception as e:
        logger.warning(f"Could not fetch username for {user_id} during WS connection: {e}")

    room_id = player_room_map.get(user_id)
    current_room = None

    if room_id and room_id in rooms:
        current_room = rooms[room_id]
        if await current_room.add_player(user_id, username, websocket):
            logger.info(f"Player {user_id} joined existing room {room_id} (filling {len(current_room.players)}/{current_room.max_players}).")
        else:
            # If add_player returned False (e.g., room full), it means player couldn't join
            await websocket.close(code=4000, reason="Failed to join room.")
            return
    else:
        # Try to find an existing room that is not full and in 'waiting' status
        found_room = None
        for r_id, room in rooms.items():
            if len(room.players) < room.max_players and room.status == "waiting":
                found_room = room
                break
        
        if found_room:
            current_room = found_room
            player_room_map[user_id] = current_room.room_id
            if await current_room.add_player(user_id, username, websocket):
                logger.info(f"Player {user_id} joined existing room {current_room.room_id} (filling {len(current_room.players)}/{current_room.max_players}).")
            else:
                await websocket.close(code=4000, reason="Failed to join room.")
                return
        else:
            # Create a new room
            new_room_id = str(uuid.uuid4().hex)[:8]
            current_room = BlackjackRoom(new_room_id)
            rooms[new_room_id] = current_room
            player_room_map[user_id] = new_room_id
            await current_room.add_player(user_id, username, websocket)
            logger.info(f"Player {user_id} created and joined new room {new_room_id}")
            current_room.ping_task = asyncio.create_task(current_room.send_ping()) # Start ping task for new room

    if current_room:
        # Initial state broadcast
        await current_room.broadcast_room_state()

    try:
        while True:
            message_text = await websocket.receive_text()
            message = json.loads(message_text)
            logger.info(f"WS: Received message from {user_id} in room {current_room.room_id}: {message}")

            action = message.get("action")
            
            if action == "bet":
                amount = message.get("amount")
                if amount:
                    await current_room.handle_bet(user_id, amount)
            elif action == "hit":
                await current_room.handle_hit(user_id)
            elif action == "stand":
                await current_room.handle_stand(user_id)
            elif action == "leave_room":
                # Client explicitly requested to leave
                await current_room.remove_player(user_id)
                if user_id in player_room_map:
                    del player_room_map[user_id]
                await websocket.send_json({"type": "game_message", "message": "Ви покинули кімнату."})
                await websocket.close(code=1000, reason="User left room.")
                break # Exit the loop as connection is closing
            elif action == "request_state":
                await current_room.broadcast_room_state() # Send current state to requesting client
            elif message.get("type") == "pong":
                # Handle pong, no specific action needed other than keeping connection alive
                pass
            else:
                logger.warning(f"Unknown action received: {message}")
                await websocket.send_json({"type": "error", "message": "Невідома дія."})

    except WebSocketDisconnect as e:
        logger.info(f"Client {user_id} disconnected from room {current_room.room_id}. Code: {e.code}")
        await current_room.remove_player(user_id)
        if user_id in player_room_map:
            del player_room_map[user_id]
    except Exception as e:
        logger.critical(f"Unexpected error in WebSocket endpoint for {user_id}: {e}", exc_info=True)
        if current_room:
            await current_room.remove_player(user_id)
        if user_id in player_room_map:
            del player_room_map[user_id]
        try:
            await websocket.close(code=1011, reason=f"Server error: {e}")
        except RuntimeError:
            logger.warning(f"Could not close websocket for {user_id}, already closed.")

# --- Root endpoint to serve the React app ---
@app.get("/", response_class=HTMLResponse)
async def read_root():
    with open(os.path.join(WEBAPP_DIR, "index.html"), "r", encoding="utf-8") as f:
        return f.read()

# --- Telegram Webhook ---
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL: Optional[str] = None # Initialize as None

@app.post(WEBHOOK_PATH)
async def bot_webhook(update: dict):
    if not dp:
        logger.warning("Telegram Dispatcher is not initialized. Skipping webhook update.")
        return {"status": "error", "message": "Bot not configured."}
    
    telegram_update = types.Update(**update)
    logger.info(f"Received webhook update from Telegram. Update ID: {telegram_update.update_id}")
    try:
        await dp.feed_update(bot, telegram_update)
        logger.info(f"Webhook update successfully processed. Update ID: {telegram_update.update_id}")
    except Exception as e:
        logger.error(f"Error processing webhook update {telegram_update.update_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Error processing update: {e}")
    return {"ok": True}

# --- On startup: set webhook for Telegram Bot and initialize DB ---
@app.on_event("startup")
async def on_startup():
    print("Application startup event triggered.")
    init_db() # Call init_db here
    print("Database initialization attempted.")
    
    # Declare globals at the top of the function
    global WEBHOOK_URL
    global WEB_APP_FRONTEND_URL

    external_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not external_hostname:
        logger.warning("RENDER_EXTERNAL_HOSTNAME environment variable not set. Assuming localhost for webhook setup.")
        external_hostname = "localhost:8000" 
    
    WEBHOOK_URL = f"https://{external_hostname}{WEBHOOK_PATH}" 
    
    if WEB_APP_FRONTEND_URL and not WEB_APP_FRONTEND_URL.startswith("https://"):
        WEB_APP_FRONTEND_URL = f"https://{WEB_APP_FRONTEND_URL}"
    
    if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
        # Register the telegram_router with the main dispatcher
        dp.include_router(telegram_router)
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
    await bot.session.close() 
    logger.info("Bot session closed.")
