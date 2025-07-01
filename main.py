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

origins = [
    WEB_APP_FRONTEND_URL,
    "https://web.telegram.org",
    "https://oauth.telegram.org",
    "https://casino-0h0l.onrender.com", # Додайте ваш Render URL
    "http://localhost:3000", # Для локальної розробки React
    "http://localhost:5173", # Для Vite dev server
    "http://127.0.0.1:8000" # Для локального FastAPI
]

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Дозволити всі джерела, або налаштувати `origins` вище
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# --- Database Connection ---
DATABASE_URL = os.getenv('DATABASE_URL')

def get_db_connection():
    conn = psycopg2.connect(DATABASE_URL)
    return conn

# --- DB Schema Migration (Simplified) ---
def apply_db_migrations():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Check if users table exists, create if not
        cur.execute("""
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                username VARCHAR(255) NOT NULL,
                balance INTEGER DEFAULT 1000,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL
            );
        """)
        
        # Add next_level_xp column if it doesn't exist
        cur.execute("""
            DO $$
            BEGIN
                IF NOT EXISTS (SELECT 1 FROM pg_attribute WHERE attrelid = 'users'::regclass AND attname = 'next_level_xp') THEN
                    ALTER TABLE users ADD COLUMN next_level_xp INTEGER DEFAULT 100;
                END IF;
            END
            $$;
        """)
        
        conn.commit()
        logger.info("DB schema migration checked.")
    except Exception as e:
        logger.error(f"Error applying DB migrations: {e}")
    finally:
        if conn:
            conn.close()

# Apply migrations on startup
apply_db_migrations()

# --- Telegram Bot Setup ---
if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
else:
    bot = None
    dp = None
    logger.warning("BOT_TOKEN is not set or is a dummy value. Telegram bot functionality will be disabled.")

# --- Game Logic ---
class UserData(BaseModel):
    user_id: int
    username: Optional[str] = None

class SpinRequest(BaseModel):
    user_id: int

class CoinFlipRequest(BaseModel):
    user_id: int
    choice: str

@app.post("/api/get_balance")
async def get_balance(user_data: UserData):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim FROM users WHERE user_id = %s", (user_data.user_id,))
        user_record = cur.fetchone()

        if user_record:
            balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim = user_record
            logger.info(f"Retrieved user {user_data.user_id} data: balance={balance}, xp={xp}, level={level}")
        else:
            # Create new user
            username_to_set = user_data.username if user_data.username else f"Player{user_data.user_id}"
            cur.execute(
                "INSERT INTO users (user_id, username, balance, xp, level, next_level_xp) VALUES (%s, %s, %s, %s, %s, %s) RETURNING balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim",
                (user_data.user_id, username_to_set, 1000, 0, 1, 100) # Initial balance 1000, 0 XP, Level 1, 100 XP for next level
            )
            balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim = cur.fetchone()
            conn.commit()
            logger.info(f"Created new user {user_data.user_id} with balance {balance}")

        return {
            "user_id": user_data.user_id,
            "username": user_data.username,
            "balance": balance,
            "xp": xp,
            "level": level,
            "next_level_xp": next_level_xp,
            "last_daily_bonus_claim": last_daily_bonus_claim.isoformat() if last_daily_bonus_claim else None,
            "last_quick_bonus_claim": last_quick_bonus_claim.isoformat() if last_quick_bonus_claim else None
        }
    except Exception as e:
        logger.error(f"Error fetching balance for user {user_data.user_id}: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/spin")
async def spin(request: SpinRequest):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE", (request.user_id,))
        user_record = cur.fetchone()
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found.")
        
        balance, xp, level, next_level_xp = user_record
        
        BET_AMOUNT = 100
        if balance < BET_AMOUNT:
            raise HTTPException(status_code=400, detail="Insufficient balance.")
        
        balance -= BET_AMOUNT

        SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎']
        WILD_SYMBOL = '⭐'
        SCATTER_SYMBOL = '💰'
        ALL_REEL_SYMBOLS = SYMBOLS + [WILD_SYMBOL, SCATTER_SYMBOL]

        # Generate random symbols for 3 reels
        # For demonstration, let's make it sometimes win
        symbols = [random.choice(ALL_REEL_SYMBOLS) for _ in range(3)]
        winnings = 0

        # Simple winning logic
        if symbols[0] == symbols[1] == symbols[2]:
            if symbols[0] == '💎': winnings = BET_AMOUNT * 10
            elif symbols[0] == '🔔': winnings = BET_AMOUNT * 5
            elif symbols[0] == WILD_SYMBOL: winnings = BET_AMOUNT * 7
            elif symbols[0] == SCATTER_SYMBOL: winnings = BET_AMOUNT * 8
            else: winnings = BET_AMOUNT * 3
        elif symbols.count(WILD_SYMBOL) >= 2: # Two or more wilds
            winnings = BET_AMOUNT * 4
        elif symbols.count(SCATTER_SYMBOL) >= 2: # Two or more scatters
            winnings = BET_AMOUNT * 6
        elif symbols[0] == symbols[1] or symbols[1] == symbols[2]:
            winnings = BET_AMOUNT * 0.5 # Small win for two in a row

        balance += winnings
        
        # XP gain logic
        xp_gain = 10 if winnings > 0 else 5
        xp += xp_gain

        # Level up logic
        while xp >= next_level_xp:
            level += 1
            xp -= next_level_xp # Carry over remaining XP
            next_level_xp = level * 100 + (level - 1) * 50 # Increase XP needed for next level
            logger.info(f"User {request.user_id} leveled up to {level}! New next_level_xp: {next_level_xp}")

        cur.execute(
            "UPDATE users SET balance = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s",
            (balance, xp, level, next_level_xp, request.user_id)
        )
        conn.commit()
        
        return {"symbols": symbols, "winnings": winnings, "balance": balance, "xp": xp, "level": level, "next_level_xp": next_level_xp}
    except HTTPException as e:
        if conn: conn.rollback()
        raise e
    except Exception as e:
        logger.error(f"Error during spin for user {request.user_id}: {e}")
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/coin_flip")
async def coin_flip(request: CoinFlipRequest):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE", (request.user_id,))
        user_record = cur.fetchone()
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found.")
        
        balance, xp, level, next_level_xp = user_record
        
        BET_AMOUNT = 50
        if balance < BET_AMOUNT:
            raise HTTPException(status_code=400, detail="Insufficient balance.")
        
        balance -= BET_AMOUNT

        coin_sides = ['heads', 'tails']
        result = random.choice(coin_sides)
        winnings = 0
        message = ""

        if request.choice == result:
            winnings = BET_AMOUNT * 2 # Double the bet
            balance += winnings
            message = f"Вітаємо! Ви вгадали! Виграш: {winnings} фантиків."
            xp_gain = 15
        else:
            message = f"На жаль, ви не вгадали. Випала {result}. Спробуйте ще раз."
            xp_gain = 5 # Still get some XP for playing

        xp += xp_gain

        while xp >= next_level_xp:
            level += 1
            xp -= next_level_xp
            next_level_xp = level * 100 + (level - 1) * 50
            logger.info(f"User {request.user_id} leveled up to {level}! New next_level_xp: {next_level_xp}")

        cur.execute(
            "UPDATE users SET balance = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s",
            (balance, xp, level, next_level_xp, request.user_id)
        )
        conn.commit()
        
        return {"result": result, "winnings": winnings, "message": message, "balance": balance, "xp": xp, "level": level, "next_level_xp": next_level_xp}
    except HTTPException as e:
        if conn: conn.rollback()
        raise e
    except Exception as e:
        logger.error(f"Error during coin flip for user {request.user_id}: {e}")
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/claim_daily_bonus")
async def claim_daily_bonus(request: UserData):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT balance, last_daily_bonus_claim, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE", (request.user_id,))
        user_record = cur.fetchone()
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found.")
        
        balance, last_daily_bonus_claim, xp, level, next_level_xp = user_record
        
        now = datetime.now(timezone.utc)
        
        if last_daily_bonus_claim and (now - last_daily_bonus_claim) < timedelta(hours=24):
            remaining_time = timedelta(hours=24) - (now - last_daily_bonus_claim)
            hours, remainder = divmod(remaining_time.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            raise HTTPException(status_code=400, detail=f"Щоденний бонус можна отримати раз на 24 години. Залишилось: {int(hours)}год {int(minutes)}хв {int(seconds)}сек.")
        
        bonus_amount = 500
        balance += bonus_amount
        xp_gain = 20

        xp += xp_gain
        while xp >= next_level_xp:
            level += 1
            xp -= next_level_xp
            next_level_xp = level * 100 + (level - 1) * 50
            logger.info(f"User {request.user_id} leveled up to {level}! New next_level_xp: {next_level_xp}")

        cur.execute(
            "UPDATE users SET balance = %s, last_daily_bonus_claim = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s",
            (balance, now, xp, level, next_level_xp, request.user_id)
        )
        conn.commit()
        
        return {"message": "Daily bonus claimed!", "amount": bonus_amount, "balance": balance, "xp": xp, "level": level, "next_level_xp": next_level_xp}
    except HTTPException as e:
        if conn: conn.rollback()
        raise e
    except Exception as e:
        logger.error(f"Error claiming daily bonus for user {request.user_id}: {e}")
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/claim_quick_bonus")
async def claim_quick_bonus(request: UserData):
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        cur.execute("SELECT balance, last_quick_bonus_claim, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE", (request.user_id,))
        user_record = cur.fetchone()
        if not user_record:
            raise HTTPException(status_code=404, detail="User not found.")
        
        balance, last_quick_bonus_claim, xp, level, next_level_xp = user_record
        
        now = datetime.now(timezone.utc)
        
        if last_quick_bonus_claim and (now - last_quick_bonus_claim) < timedelta(minutes=15):
            remaining_time = timedelta(minutes=15) - (now - last_quick_bonus_claim)
            minutes, seconds = divmod(remaining_time.total_seconds(), 60)
            raise HTTPException(status_code=400, detail=f"Швидкий бонус можна отримати раз на 15 хвилин. Залишилось: {int(minutes)}хв {int(seconds)}сек.")
        
        bonus_amount = 100
        balance += bonus_amount
        xp_gain = 5

        xp += xp_gain
        while xp >= next_level_xp:
            level += 1
            xp -= next_level_xp
            next_level_xp = level * 100 + (level - 1) * 50
            logger.info(f"User {request.user_id} leveled up to {level}! New next_level_xp: {next_level_xp}")

        cur.execute(
            "UPDATE users SET balance = %s, last_quick_bonus_claim = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s",
            (balance, now, xp, level, next_level_xp, request.user_id)
        )
        conn.commit()
        
        return {"message": "Quick bonus claimed!", "amount": bonus_amount, "balance": balance, "xp": xp, "level": level, "next_level_xp": next_level_xp}
    except HTTPException as e:
        if conn: conn.rollback()
        raise e
    except Exception as e:
        logger.error(f"Error claiming quick bonus for user {request.user_id}: {e}")
        if conn: conn.rollback()
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

@app.post("/api/get_leaderboard")
async def get_leaderboard():
    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        
        # Order by level (descending) then by XP (descending)
        cur.execute("SELECT username, level, xp FROM users ORDER BY level DESC, xp DESC LIMIT 100")
        leaderboard_records = cur.fetchall()
        
        leaderboard_data = []
        for record in leaderboard_records:
            leaderboard_data.append({
                "username": record[0],
                "level": record[1],
                "xp": record[2]
            })
        
        return {"leaderboard": leaderboard_data}
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    finally:
        if conn:
            conn.close()

# --- Blackjack Game State (in-memory for simplicity, replace with Redis/DB for production) ---
class Card:
    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = self._get_value()

    def _get_value(self):
        if self.rank in ['J', 'Q', 'K']:
            return 10
        elif self.rank == 'A':
            return 11 # Handled dynamically for 1 or 11
        else:
            return int(self.rank)

    def __str__(self):
        return f"{self.rank}{self.suit}"

    def __repr__(self):
        return self.__str__()

class Deck:
    def __init__(self):
        self.cards = []
        self.build()

    def build(self):
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        suits = ['♠', '♦', '♥', '♣']
        for suit in suits:
            for rank in ranks:
                self.cards.append(Card(rank, suit))
        random.shuffle(self.cards)

    def shuffle(self):
        random.shuffle(self.cards)

    def deal(self):
        if not self.cards:
            self.build() # Reshuffle if deck is empty
            self.shuffle()
        return self.cards.pop()

class Player:
    def __init__(self, user_id: int, username: str, balance: int):
        self.user_id = user_id
        self.username = username
        self.hand: List[Card] = []
        self.score = 0
        self.bet = 0
        self.has_bet = False
        self.is_playing = True # True if still in the round (not busted/stood)
        self.balance = balance # Player's current balance, updated from DB
        self.websocket: Optional[WebSocket] = None # Store WebSocket for direct communication

    def add_card(self, card: Card):
        self.hand.append(card)
        self.calculate_score()

    def calculate_score(self):
        self.score = sum(card.value for card in self.hand)
        num_aces = sum(1 for card in self.hand if card.rank == 'A')
        
        while self.score > 21 and num_aces:
            self.score -= 10 # Change Ace from 11 to 1
            num_aces -= 1

    def reset_hand(self):
        self.hand = []
        self.score = 0
        self.bet = 0
        self.has_bet = False
        self.is_playing = True

    def to_dict(self, hide_first_card=False):
        hand_display = [str(card) for card in self.hand]
        if hide_first_card and len(hand_display) > 0:
            hand_display[0] = "Hidden" # Mask dealer's first card
        return {
            "user_id": self.user_id,
            "username": self.username,
            "hand": hand_display,
            "score": self.score,
            "bet": self.bet,
            "has_bet": self.has_bet,
            "is_playing": self.is_playing
        }

class Room:
    def __init__(self, room_id: str, min_players: int = 1, max_players: int = 4):
        self.room_id = room_id
        self.players: Dict[int, Player] = {} # user_id -> Player object
        self.deck = Deck()
        self.dealer_hand: List[Card] = []
        self.dealer_score = 0
        self.status = "waiting" # waiting, starting_timer, betting, playing, round_end
        self.current_player_turn: Optional[int] = None # user_id of current player
        self.min_players = min_players
        self.max_players = max_players
        self.timer_task: Optional[asyncio.Task] = None
        self.timer_seconds = 0
        self.turn_timer_task: Optional[asyncio.Task] = None
        self.ping_task: Optional[asyncio.Task] = None # Task for sending pings
        self.ping_interval = 10 # seconds
        self.last_pong_time: Dict[int, datetime] = {} # Track last pong from each player
        self.disconnect_timeout = 30 # seconds after last pong to consider disconnected

    def add_player(self, user_id: int, username: str, balance: int, websocket: WebSocket):
        if user_id in self.players:
            # Player reconnected, update websocket and balance
            self.players[user_id].websocket = websocket
            self.players[user_id].balance = balance
            logger.info(f"Player {user_id} reconnected to room {self.room_id}")
        else:
            # New player
            if len(self.players) >= self.max_players:
                raise ValueError("Room is full.")
            player = Player(user_id, username, balance)
            player.websocket = websocket
            self.players[user_id] = player
            logger.info(f"Player {user_id} ({username}) added to room {self.room_id}. Current players: {len(self.players)}")
            self.last_pong_time[user_id] = datetime.now(timezone.utc)
            if self.ping_task is None:
                self.ping_task = asyncio.create_task(self._ping_players())

        # If room becomes ready, start timer
        if len(self.players) >= self.min_players and self.status == "waiting":
            self.start_game_timer()

    def remove_player(self, user_id: int):
        if user_id in self.players:
            del self.players[user_id]
            if user_id in self.last_pong_time:
                del self.last_pong_time[user_id]
            logger.info(f"Player {user_id} removed from room {self.room_id}")
            if not self.players:
                self.stop_timer()
                if self.ping_task:
                    self.ping_task.cancel()
                    self.ping_task = None
                    logger.info(f"Room {self.room_id}: Ping task cancelled.")
                logger.info(f"Room {self.room_id} is empty and removed.")
                return True # Room is empty
            
            # If current player leaves, advance turn
            if self.current_player_turn == user_id:
                self.advance_turn()
            
            # If game was starting and now not enough players
            if len(self.players) < self.min_players and self.status in ["starting_timer", "betting", "playing"]:
                self.status = "waiting"
                self.stop_timer()
                logger.info(f"Room {self.room_id}: Not enough players, returning to waiting state.")
                asyncio.create_task(self.broadcast_state()) # Broadcast state change
        return False # Room is not empty

    def _calculate_dealer_score(self):
        self.dealer_score = sum(card.value for card in self.dealer_hand)
        num_aces = sum(1 for card in self.dealer_hand if card.rank == 'A')
        while self.dealer_score > 21 and num_aces:
            self.dealer_score -= 10
            num_aces -= 1

    async def _ping_players(self):
        while True:
            await asyncio.sleep(self.ping_interval)
            now = datetime.now(timezone.utc)
            disconnected_players = []
            for user_id, player in list(self.players.items()): # Iterate over copy
                if player.websocket:
                    try:
                        await player.websocket.send_json({"type": "ping"})
                        if user_id in self.last_pong_time and (now - self.last_pong_time[user_id]).total_seconds() > self.disconnect_timeout:
                            logger.warning(f"Player {user_id} in room {self.room_id} timed out (no pong). Disconnecting.")
                            disconnected_players.append(user_id)
                    except Exception as e:
                        logger.error(f"Error sending ping to {user_id} in room {self.room_id}: {e}")
                        disconnected_players.append(user_id)
            
            for user_id in disconnected_players:
                if user_id in self.players: # Ensure player still exists before removing
                    await self.players[user_id].websocket.close(1001, "Timeout") # Close with abnormal closure
                    self.remove_player(user_id)
            
            if not self.players and self.ping_task: # If room becomes empty, stop pinging
                self.ping_task.cancel()
                self.ping_task = None
                logger.info(f"Room {self.room_id}: Ping task cancelled due to empty room.")
                break


    def start_game_timer(self):
        self.stop_timer() # Ensure any existing timer is stopped
        self.status = "starting_timer"
        self.timer_seconds = 10 # 10 seconds to start game
        self.timer_task = asyncio.create_task(self._game_timer_countdown())
        logger.info(f"Room {self.room_id}: Game start timer initiated for {self.timer_seconds} seconds.")

    async def _game_timer_countdown(self):
        while self.timer_seconds > 0:
            await self.broadcast_state()
            await asyncio.sleep(1)
            self.timer_seconds -= 1
        
        if len(self.players) >= self.min_players:
            await self.start_round()
        else:
            self.status = "waiting"
            logger.info(f"Room {self.room_id}: Game start cancelled, not enough players.")
            await self.broadcast_state()

    def start_turn_timer(self):
        self.stop_turn_timer()
        self.timer_seconds = 20 # 20 seconds per turn
        self.turn_timer_task = asyncio.create_task(self._turn_timer_countdown())
        logger.info(f"Room {self.room_id}: Turn timer started for {self.current_player_turn} for {self.timer_seconds} seconds.")

    def stop_turn_timer(self):
        if self.turn_timer_task:
            self.turn_timer_task.cancel()
            self.turn_timer_task = None
            self.timer_seconds = 0
            logger.info(f"Room {self.room_id}: Turn timer stopped.")

    async def _turn_timer_countdown(self):
        while self.timer_seconds > 0:
            await self.broadcast_state() # Update clients with remaining time
            await asyncio.sleep(1)
            self.timer_seconds -= 1
        
        # If timer runs out, player automatically stands
        if self.current_player_turn and self.current_player_turn in self.players:
            logger.info(f"Player {self.current_player_turn} timed out. Auto-standing.")
            await self.process_game_action(self.current_player_turn, {"action": "stand"})
        else:
            logger.warning(f"Turn timer ended but no current player or player not found: {self.current_player_turn}")
            self.advance_turn() # Just advance if no player or player already gone

    def stop_timer(self):
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None
            self.timer_seconds = 0
        self.stop_turn_timer() # Also stop turn timer if main timer is stopped

    async def start_round(self):
        self.stop_timer()
        self.deck = Deck()
        self.deck.shuffle()
        self.dealer_hand = []
        self.dealer_score = 0
        
        # Reset players and set them to be playing this round
        for player in self.players.values():
            player.reset_hand()
            player.is_playing = True # All active players are playing this round
        
        self.status = "betting"
        self.timer_seconds = 15 # Time for betting
        self.timer_task = asyncio.create_task(self._betting_timer_countdown())
        logger.info(f"Room {self.room_id}: Starting new round. Status: betting.")
        await self.broadcast_state()


    async def _betting_timer_countdown(self):
        while self.timer_seconds > 0:
            await self.broadcast_state()
            await asyncio.sleep(1)
            self.timer_seconds -= 1
        
        # After betting time, proceed to dealing for those who bet
        await self.deal_initial_cards()


    async def deal_initial_cards(self):
        # Filter players who actually placed a bet
        players_in_round = [p for p in self.players.values() if p.has_bet]
        
        if not players_in_round:
            logger.info(f"Room {self.room_id}: No players placed bets. Resetting to waiting.")
            self.status = "waiting"
            await self.broadcast_state()
            return

        # Deal two cards to each player who bet
        for _ in range(2):
            for player in players_in_round:
                player.add_card(self.deck.deal())
            self.dealer_hand.append(self.deck.deal())
        
        self._calculate_dealer_score()
        
        self.status = "playing"
        # Determine initial turn order (players who bet, then dealer)
        self.turn_order = [p.user_id for p in players_in_round]
        
        # Remove players who didn't bet from current round (they won't get turns)
        for player in self.players.values():
            if not player.has_bet:
                player.is_playing = False # Mark as not playing this round
        
        if self.turn_order:
            self.current_player_turn = self.turn_order[0]
            self.start_turn_timer()
        else:
            # If no players are left after filtering, go straight to dealer's turn or end round
            logger.warning(f"Room {self.room_id}: No active players after dealing. Proceeding to dealer's turn.")
            await self.dealer_play()
            return # Dealer play will handle round end

        logger.info(f"Room {self.room_id}: Initial cards dealt. First turn: {self.current_player_turn}")
        await self.broadcast_state()


    async def dealer_play(self):
        self.stop_turn_timer() # Stop any player turn timer
        self.current_player_turn = -1 # Indicate dealer's turn
        await self.broadcast_state(hide_dealer_card=False) # Reveal dealer's card

        # Dealer hits until score is 17 or more
        while self.dealer_score < 17:
            await asyncio.sleep(1) # Small delay for dramatic effect
            self.dealer_hand.append(self.deck.deal())
            self._calculate_dealer_score()
            logger.info(f"Dealer hits. New score: {self.dealer_score}")
            await self.broadcast_state(hide_dealer_card=False)
        
        logger.info(f"Dealer stands with score: {self.dealer_score}")
        await self.end_round()

    async def end_round(self):
        self.status = "round_end"
        self.current_player_turn = None
        
        results_messages = []
        
        for user_id, player in self.players.items():
            if not player.is_playing: # Skip players who didn't bet or left early
                continue

            winnings = 0
            message = ""
            
            player_blackjack = (len(player.hand) == 2 and player.score == 21)
            dealer_blackjack = (len(self.dealer_hand) == 2 and self.dealer_score == 21)

            if player.score > 21:
                message = f"Перебір! Ви програли {player.bet} фантиків."
                winnings = -player.bet
            elif dealer_blackjack and not player_blackjack:
                message = f"Дилер має Блекджек! Ви програли {player.bet} фантиків."
                winnings = -player.bet
            elif player_blackjack and not dealer_blackjack:
                message = f"Блекджек! Ви виграли {int(player.bet * 2.5)} фантиків!" # 1.5x payout for Blackjack
                winnings = int(player.bet * 2.5)
            elif player.score == self.dealer_score:
                message = f"Нічия! Ваша ставка {player.bet} повертається."
                winnings = player.bet # Return bet
            elif player.score > self.dealer_score or self.dealer_score > 21:
                message = f"Ви виграли! Виграш: {player.bet * 2} фантиків."
                winnings = player.bet * 2
            else:
                message = f"Ви програли {player.bet} фантиків."
                winnings = -player.bet
            
            # Update user balance and XP in DB
            conn = None
            try:
                conn = get_db_connection()
                cur = conn.cursor()
                cur.execute("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
                user_db_data = cur.fetchone()
                
                if user_db_data:
                    current_balance, current_xp, current_level, current_next_level_xp = user_db_data
                    new_balance = current_balance + winnings
                    
                    xp_gain = 0
                    if winnings > 0:
                        xp_gain = 30 # More XP for winning blackjack
                    elif winnings == player.bet: # Push
                        xp_gain = 10
                    else: # Lose
                        xp_gain = 5
                    
                    new_xp = current_xp + xp_gain
                    new_level = current_level
                    new_next_level_xp = current_next_level_xp

                    level_up_occurred = False
                    while new_xp >= new_next_level_xp:
                        new_level += 1
                        new_xp -= new_next_level_xp
                        new_next_level_xp = new_level * 100 + (new_level - 1) * 50
                        level_up_occurred = True
                        logger.info(f"User {user_id} leveled up to {new_level}! New next_level_xp: {new_next_level_xp}")
                    
                    cur.execute(
                        "UPDATE users SET balance = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s",
                        (new_balance, new_xp, new_level, new_next_level_xp, user_id)
                    )
                    conn.commit()
                    
                    # Send individual result message to player
                    await self.send_message_to_player(user_id, {
                        "type": "round_result",
                        "message": message,
                        "winnings": winnings,
                        "balance": new_balance,
                        "xp": new_xp,
                        "level": new_level,
                        "next_level_xp": new_next_level_xp,
                        "final_player_score": player.score
                    })
                    if level_up_occurred:
                        await self.send_message_to_player(user_id, {
                            "type": "level_up",
                            "level": new_level
                        })

                else:
                    logger.error(f"User {user_id} not found in DB during round end processing.")
                    await self.send_message_to_player(user_id, {"type": "error", "message": "Помилка: Користувача не знайдено."})
            except Exception as e:
                logger.error(f"Error updating user {user_id} balance/xp after round: {e}")
                if conn: conn.rollback()
                await self.send_message_to_player(user_id, {"type": "error", "message": f"Помилка оновлення даних: {e}"})
            finally:
                if conn: conn.close()
        
        # After all players processed, reset for next round
        await asyncio.sleep(5) # Give players time to see results
        await self.start_round() # Automatically start next round


    def advance_turn(self):
        self.stop_turn_timer()
        if not self.turn_order:
            logger.warning(f"Room {self.room_id}: No players in turn order to advance.")
            # If no players left to play, go to dealer's turn
            asyncio.create_task(self.dealer_play())
            return

        current_idx = self.turn_order.index(self.current_player_turn) if self.current_player_turn else -1
        
        next_player_found = False
        for i in range(current_idx + 1, len(self.turn_order)):
            next_player_id = self.turn_order[i]
            if next_player_id in self.players and self.players[next_player_id].is_playing:
                self.current_player_turn = next_player_id
                next_player_found = True
                self.start_turn_timer()
                logger.info(f"Room {self.room_id}: Advanced turn to {self.current_player_turn}")
                break
        
        if not next_player_found:
            # All players have taken their turn, or stood/busted
            logger.info(f"Room {self.room_id}: All players finished their turns. Dealer's turn.")
            self.current_player_turn = None # No player has turn
            asyncio.create_task(self.dealer_play())
        
        asyncio.create_task(self.broadcast_state())


    async def broadcast_state(self, hide_dealer_card=True):
        state = {
            "room_id": self.room_id,
            "status": self.status,
            "dealer_hand": [str(card) for card in self.dealer_hand],
            "dealer_score": self.dealer_score,
            "players": [p.to_dict() for p in self.players.values()],
            "current_player_turn": self.current_player_turn,
            "player_count": len(self.players),
            "min_players": self.min_players,
            "max_players": self.max_players,
            "timer": self.timer_seconds
        }
        
        if hide_dealer_card and len(state["dealer_hand"]) > 0:
            state["dealer_hand"][0] = "Hidden"
            state["dealer_score"] = self.dealer_hand[1].value if len(self.dealer_hand) > 1 else 0 # Show only visible card score

        for player_id, player in self.players.items():
            if player.websocket:
                try:
                    await player.websocket.send_json(state)
                except Exception as e:
                    logger.error(f"Error broadcasting state to {player_id} in room {self.room_id}: {e}")
                    # Consider player disconnected if send fails
                    asyncio.create_task(self.remove_player(player_id))

    async def send_message_to_player(self, user_id: int, message: dict):
        player = self.players.get(user_id)
        if player and player.websocket:
            try:
                await player.websocket.send_json(message)
                logger.info(f"Sent specific message to {user_id}: {message.get('type')}")
            except Exception as e:
                logger.error(f"Error sending message to player {user_id}: {e}")

    async def process_game_action(self, user_id: int, message: dict):
        player = self.players.get(user_id)
        if not player:
            logger.warning(f"Action from unknown player {user_id} in room {self.room_id}: {message}")
            return

        action = message.get("action")
        logger.info(f"Player {user_id} ({player.username}) in room {self.room_id} sent action: {action}")

        if self.status == "betting":
            if action == "bet":
                amount = message.get("amount")
                if amount != 200: # Fixed bet amount
                    await self.send_message_to_player(user_id, {"type": "error", "message": "Невірна сума ставки. Ставка має бути 200."})
                    return

                if player.has_bet:
                    await self.send_message_to_player(user_id, {"type": "error", "message": "Ви вже зробили ставку."})
                    return

                conn = None
                try:
                    conn = get_db_connection()
                    cur = conn.cursor()
                    cur.execute("SELECT balance FROM users WHERE user_id = %s FOR UPDATE", (user_id,))
                    current_balance = cur.fetchone()[0]
                    
                    if current_balance < amount:
                        await self.send_message_to_player(user_id, {"type": "error", "message": "Недостатньо фантиків для ставки."})
                        conn.rollback()
                        return

                    player.bet = amount
                    player.has_bet = True
                    player.balance -= amount # Deduct from player's in-memory balance immediately
                    cur.execute("UPDATE users SET balance = %s WHERE user_id = %s", (player.balance, user_id))
                    conn.commit()
                    await self.send_message_to_player(user_id, {"type": "game_message", "message": f"Ваша ставка {amount} прийнята."})
                    logger.info(f"Player {user_id} placed bet {amount}. Remaining balance: {player.balance}")
                except Exception as e:
                    logger.error(f"Error processing bet for user {user_id}: {e}")
                    await self.send_message_to_player(user_id, {"type": "error", "message": f"Помилка при обробці ставки: {e}"})
                    if conn: conn.rollback()
                finally:
                    if conn: conn.close()

                # Check if all players have bet
                all_bet = True
                for p in self.players.values():
                    if p.is_playing and not p.has_bet: # Only check players who are active in this round
                        all_bet = False
                        break
                
                if all_bet and self.status == "betting":
                    self.stop_timer() # Stop betting timer
                    await self.deal_initial_cards()
            else:
                await self.send_message_to_player(user_id, {"type": "error", "message": "Зараз час для ставок."})

        elif self.status == "playing" and self.current_player_turn == user_id:
            if action == "hit":
                player.add_card(self.deck.deal())
                if player.score > 21:
                    player.is_playing = False # Player busted
                    await self.send_message_to_player(user_id, {"type": "game_message", "message": "Перебір! Ви програли."})
                    self.advance_turn()
                else:
                    await self.broadcast_state() # Update state with new card
                    self.start_turn_timer() # Reset timer for current player
            elif action == "stand":
                player.is_playing = False # Player stands
                await self.send_message_to_player(user_id, {"type": "game_message", "message": "Ви зупинились."})
                self.advance_turn()
            else:
                await self.send_message_to_player(user_id, {"type": "error", "message": "Невідома дія під час вашого ходу."})
        
        elif action == "leave_room":
            room_id = message.get("room_id")
            if room_id == self.room_id:
                self.remove_player(user_id)
                await self.send_message_to_player(user_id, {"type": "game_message", "message": "Ви покинули кімнату."})
            else:
                await self.send_message_to_player(user_id, {"type": "error", "message": "Ви не в цій кімнаті."})
        else:
            # This is the "Невідома дія" case
            logger.warning(f"Received unknown or invalid action '{action}' from user {user_id} in room {self.room_id} with status {self.status}. Message: {message}")
            await self.send_message_to_player(user_id, {"type": "error", "message": "Невідома дія."})
        
        # Always broadcast state after processing an action, unless it's a specific message
        if action not in ["ping", "request_state", "leave_room"]: # These actions handle their own state updates or don't require full broadcast
            await self.broadcast_state()


# --- WebSocket Room Management ---
active_rooms: Dict[str, Room] = {} # room_id -> Room object
user_room_map: Dict[int, str] = {} # user_id -> room_id

async def get_or_create_room(user_id: int, username: str, balance: int):
    # Try to find an existing room the user is already in
    if user_id in user_room_map:
        room_id = user_room_map[user_id]
        if room_id in active_rooms:
            logger.info(f"User {user_id} already in room {room_id}. Rejoining.")
            return active_rooms[room_id]

    # Try to find an existing room with available slots
    for room_id, room in active_rooms.items():
        if len(room.players) < room.max_players and room.status in ["waiting", "starting_timer"]:
            logger.info(f"Found available room {room_id} for user {user_id}.")
            user_room_map[user_id] = room_id
            return room

    # If no available room, create a new one
    new_room_id = str(uuid.uuid4())[:8] # Short UUID for room ID
    new_room = Room(new_room_id)
    active_rooms[new_room_id] = new_room
    user_room_map[user_id] = new_room_id
    logger.info(f"Created new room {new_room_id} for user {user_id}.")
    return new_room

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    logger.info(f"WebSocket connection accepted for user {user_id}.")

    conn = None
    try:
        conn = get_db_connection()
        cur = conn.cursor()
        cur.execute("SELECT username, balance FROM users WHERE user_id = %s", (user_id,))
        user_record = cur.fetchone()
        if not user_record:
            # This should ideally not happen if get_balance is called first
            logger.error(f"User {user_id} not found in DB during WebSocket connection.")
            await websocket.send_json({"type": "error", "message": "Користувача не знайдено. Перезавантажте гру."})
            await websocket.close()
            return
        
        username, balance = user_record
        
        room = await get_or_create_room(user_id, username, balance)
        
        # Add player to room (or update existing player's websocket)
        room.add_player(user_id, username, balance, websocket)
        
        # Send initial state to the newly connected/reconnected player
        await room.broadcast_state()

        try:
            while True:
                message_str = await websocket.receive_text()
                message = json.loads(message_str)
                
                if message.get("type") == "pong":
                    room.last_pong_time[user_id] = datetime.now(timezone.utc)
                    logger.info(f"WS: Received pong from {user_id} in room {room.room_id}")
                    continue # Do not process pong as a game action

                logger.info(f"WS: Received message from {user_id} in room {room.room_id}: {message}")
                await room.process_game_action(user_id, message)

        except WebSocketDisconnect:
            logger.info(f"Client {user_id} disconnected from room {room.room_id}.")
        except Exception as e:
            logger.error(f"Error in websocket for user {user_id} in room {room.room_id}: {e}")
            # Optionally send an error message to the client before closing
            try:
                await websocket.send_json({"type": "error", "message": f"Помилка сервера: {e}"})
            except:
                pass # Ignore if send fails on a broken connection
    finally:
        # This block runs when the websocket connection is closed or an error occurs
        if user_id in user_room_map:
            room_id = user_room_map[user_id]
            if room_id in active_rooms:
                room = active_rooms[room_id]
                if room.remove_player(user_id): # If room becomes empty, remove it from active_rooms
                    del active_rooms[room_id]
            del user_room_map[user_id]
        if conn:
            conn.close()
        logger.info(f"WebSocket connection closed for user {user_id}.")


# --- Static Files (for your React app) ---
app.mount("/", StaticFiles(directory=WEBAPP_DIR, html=True), name="static")

# --- Telegram Webhook ---
WEBHOOK_PATH = "/webhook"
WEBHOOK_URL = f"https://{WEBHOOK_HOST}{WEBHOOK_PATH}" if WEBHOOK_HOST else None

@app.on_event("startup")
async def on_startup():
    logger.info("Application startup event triggered.")
    if bot and dp:
        dp.include_router(telegram_router) # Include the router

        if WEBHOOK_URL:
            try:
                webhook_info = await bot.get_webhook_info()
                if webhook_info.url != WEBHOOK_URL:
                    await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True)
                    logger.info(f"Telegram webhook set to: {WEBHOOK_URL}")
                else:
                    logger.info(f"Telegram webhook already set to: {WEBHOOK_URL}")
            except Exception as e:
                logger.error(f"Failed to set Telegram webhook: {e}")
                logger.error("Hint: Is BOT_TOKEN correctly set as an environment variable and valid?")
        else:
            logger.warning("WEBHOOK_URL is not set. Skipping Telegram webhook setup.")
    else:
        logger.warning("Skipping Telegram bot startup because BOT_TOKEN is not set or is a dummy value.")

@app.on_event("shutdown")
async def on_shutdown():
    logger.info("Application shutdown event triggered.")
    if bot and dp:
        try:
            await bot.delete_webhook()
            logger.info("Telegram webhook deleted.")
        except Exception as e:
            logger.error(f"Failed to delete Telegram webhook: {e}")
        finally:
            await bot.session.close()
            logger.info("Telegram bot session closed.")

# --- Telegram Bot Handlers ---
if dp:
    telegram_router = dp # Use dp as router directly if no other routers are needed

    @telegram_router.message(CommandStart())
    async def command_start_handler(message: Message) -> None:
        user_id = message.from_user.id
        username = message.from_user.username or message.from_user.first_name

        conn = None
        try:
            conn = get_db_connection()
            cur = conn.cursor()
            
            cur.execute("SELECT balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim FROM users WHERE user_id = %s", (user_id,))
            user_record = cur.fetchone()

            if user_record:
                balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim = user_record
                logger.info(f"CommandStart: User {user_id} fetched data: {{'username': '{username}', 'balance': {balance}, 'xp': {xp}, 'level': {level}, 'last_free_coins_claim': {last_free_coins_claim}, 'last_daily_bonus_claim': {last_daily_bonus_claim}, 'last_quick_bonus_claim': {last_quick_bonus_claim}}}")
            else:
                # Create new user
                cur.execute(
                    "INSERT INTO users (user_id, username, balance, xp, level, next_level_xp) VALUES (%s, %s, %s, %s, %s, %s) RETURNING balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim",
                    (user_id, username, 1000, 0, 1, 100)
                )
                balance, xp, level, last_free_coins_claim, last_daily_bonus_claim, last_quick_bonus_claim = cur.fetchone()
                conn.commit()
                logger.info(f"CommandStart: Created new user {user_id} with balance {balance}")

            markup = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🎮 Відкрити гру 🎮", web_app=WebAppInfo(url=WEB_APP_FRONTEND_URL))]
            ])
            await message.answer(f"Вітаємо, {username}! Ваш баланс: {balance} фантиків. Рівень: {level}. Натисніть кнопку, щоб почати грати!", reply_markup=markup)
            logger.info(f"User {user_id} ({username}) started the bot. Balance: {balance}.")

        except Exception as e:
            logger.error(f"Error in /start handler for user {user_id}: {e}")
            if conn: conn.rollback()
            await message.answer("Виникла помилка при запуску бота. Спробуйте пізніше.")
        finally:
            if conn:
                conn.close()

    @app.post(WEBHOOK_PATH)
    async def bot_webhook(request: Request):
        if not dp:
            logger.warning("Webhook received but Telegram bot functionality is disabled.")
            raise HTTPException(status_code=403, detail="Bot not configured.")
        
        update = types.Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
        logger.info(f"Webhook update successfully processed. Update ID: {update.update_id}")
        return {"ok": True}

