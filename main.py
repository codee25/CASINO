import logging
import os
import json
import random
import urllib.parse
import asyncio
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

# НОВИЙ ІМПОРТ: Для CORS
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

# ДОДАНО: Налаштування CORS
# Це дозволяє вашому фронтенду (навіть якщо він на іншому домені/порті)
# надсилати запити до вашого FastAPI бекенду.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # Дозволити всі джерела. У продакшені краще вказати конкретні домени.
    allow_credentials=True,
    allow_methods=["*"],  # Дозволити всі HTTP методи (GET, POST, etc.)
    allow_headers=["*"],  # Дозволити всі заголовки
)

# --- Database Configuration ---
DATABASE_URL = os.getenv("DATABASE_URL")

def get_db_connection():
    try:
        conn = psycopg2.connect(DATABASE_URL)
        return conn
    except Exception as e:
        logger.error(f"Failed to connect to database: {e}")
        return None

# --- Telegram Bot Setup ---
if API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
    bot = Bot(token=API_TOKEN, default=DefaultBotProperties(parse_mode=ParseMode.HTML))
    dp = Dispatcher()
else:
    logger.warning("BOT_TOKEN is not set or is a dummy value. Telegram bot features will be disabled.")
    bot = None
    dp = None

# --- WebApp Static Files ---
app.mount("/webapp", StaticFiles(directory=WEBAPP_DIR), name="webapp")

@app.get("/", response_class=HTMLResponse)
async def read_root():
    # Serve your index.html from the webapp directory
    with open(os.path.join(WEBAPP_DIR, "index.html"), "r", encoding="utf-8") as f:
        return HTMLResponse(content=f.read())

# --- User Management Endpoints ---
class UserRequest(BaseModel):
    user_id: int
    username: str

class BalanceResponse(BaseModel):
    user_id: int
    username: str
    balance: int
    xp: int
    level: int
    next_level_xp: int
    last_daily_bonus_claim: Optional[datetime]
    last_quick_bonus_claim: Optional[datetime]

@app.post("/api/get_balance", response_model=BalanceResponse)
async def get_balance(user_req: UserRequest):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")
    
    try:
        with conn.cursor() as cur:
            # Check if user exists, if not, create them
            cur.execute(sql.SQL("""
                INSERT INTO users (user_id, username, balance, xp, level, next_level_xp)
                VALUES (%s, %s, 1000, 0, 1, 100)
                ON CONFLICT (user_id) DO UPDATE SET
                    username = EXCLUDED.username
                RETURNING user_id, username, balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim;
            """), (user_req.user_id, user_req.username))
            user_data = cur.fetchone()
            conn.commit()

            if user_data:
                user_id, username, balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim = user_data
                logger.info(f"User {user_id} ({username}) balance: {balance}, XP: {xp}, Level: {level}")
                return BalanceResponse(
                    user_id=user_id,
                    username=username,
                    balance=balance,
                    xp=xp,
                    level=level,
                    next_level_xp=next_level_xp,
                    last_daily_bonus_claim=last_daily_bonus_claim,
                    last_quick_bonus_claim=last_quick_bonus_claim
                )
            else:
                raise HTTPException(status_code=404, detail="User not found after creation attempt.")
    except Exception as e:
        conn.rollback()
        logger.error(f"Error getting/creating user balance: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

class ClaimBonusRequest(BaseModel):
    user_id: int

@app.post("/api/claim_daily_bonus")
async def claim_daily_bonus(req: ClaimBonusRequest):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")
    
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT balance, last_daily_bonus_claim FROM users WHERE user_id = %s"), (req.user_id,))
            user_data = cur.fetchone()

            if not user_data:
                raise HTTPException(status_code=404, detail="User not found.")

            balance, last_claim_str = user_data
            last_claim = last_claim_str.replace(tzinfo=timezone.utc) if last_claim_str else None # Ensure timezone-aware

            now_utc = datetime.now(timezone.utc)
            
            if last_claim and (now_utc - last_claim) < timedelta(hours=24):
                time_left = timedelta(hours=24) - (now_utc - last_claim)
                hours, remainder = divmod(time_left.total_seconds(), 3600)
                minutes, seconds = divmod(remainder, 60)
                raise HTTPException(status_code=400, detail=f"Щоденний бонус вже отримано. Спробуйте через {int(hours)} год {int(minutes)} хв.")

            bonus_amount = 300
            new_balance = balance + bonus_amount
            
            cur.execute(sql.SQL("UPDATE users SET balance = %s, last_daily_bonus_claim = %s WHERE user_id = %s RETURNING balance;"),
                        (new_balance, now_utc, req.user_id))
            conn.commit()
            logger.info(f"User {req.user_id} claimed daily bonus: +{bonus_amount}")
            return {"message": f"Ви отримали {bonus_amount} фантиків!", "amount": bonus_amount, "new_balance": new_balance}
    except HTTPException as e:
        conn.rollback()
        raise e
    except Exception as e:
        conn.rollback()
        logger.error(f"Error claiming daily bonus for user {req.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

@app.post("/api/claim_quick_bonus")
async def claim_quick_bonus(req: ClaimBonusRequest):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")
    
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT balance, last_quick_bonus_claim FROM users WHERE user_id = %s"), (req.user_id,))
            user_data = cur.fetchone()

            if not user_data:
                raise HTTPException(status_code=404, detail="User not found.")

            balance, last_claim_str = user_data
            last_claim = last_claim_str.replace(tzinfo=timezone.utc) if last_claim_str else None # Ensure timezone-aware

            now_utc = datetime.now(timezone.utc)
            
            if last_claim and (now_utc - last_claim) < timedelta(minutes=15):
                time_left = timedelta(minutes=15) - (now_utc - last_claim)
                minutes, seconds = divmod(time_left.total_seconds(), 60)
                raise HTTPException(status_code=400, detail=f"Швидкий бонус вже отримано. Спробуйте через {int(minutes)} хв {int(seconds)} сек.")

            bonus_amount = 100
            new_balance = balance + bonus_amount
            
            cur.execute(sql.SQL("UPDATE users SET balance = %s, last_quick_bonus_claim = %s WHERE user_id = %s RETURNING balance;"),
                        (new_balance, now_utc, req.user_id))
            conn.commit()
            logger.info(f"User {req.user_id} claimed quick bonus: +{bonus_amount}")
            return {"message": f"Ви отримали {bonus_amount} фантиків!", "amount": bonus_amount, "new_balance": new_balance}
    except HTTPException as e:
        conn.rollback()
        raise e
    except Exception as e:
        conn.rollback()
        logger.error(f"Error claiming quick bonus for user {req.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

class SpinRequest(BaseModel):
    user_id: int

@app.post("/api/spin")
async def spin_slot(req: SpinRequest):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    BET_AMOUNT = 100
    XP_PER_SPIN = 10

    try:
        with conn.cursor() as cur:
            # Get user balance and XP
            cur.execute(sql.SQL("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE;"), (req.user_id,))
            user_data = cur.fetchone()

            if not user_data:
                raise HTTPException(status_code=404, detail="User not found.")

            current_balance, current_xp, current_level, next_level_xp = user_data

            if current_balance < BET_AMOUNT:
                raise HTTPException(status_code=400, detail="Недостатньо фантиків для спіна!")

            # Deduct bet
            new_balance = current_balance - BET_AMOUNT
            cur.execute(sql.SQL("UPDATE users SET balance = %s WHERE user_id = %s;"), (new_balance, req.user_id))
            conn.commit() # Commit balance deduction immediately

            # Spin logic
            symbols_pool = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀']
            wild_symbol = '⭐'
            scatter_symbol = '💰'
            all_symbols = symbols_pool + [wild_symbol, scatter_symbol]

            # Generate random symbols for each reel
            reel1 = random.choice(all_symbols)
            reel2 = random.choice(all_symbols)
            reel3 = random.choice(all_symbols)
            
            result_symbols = [reel1, reel2, reel3]
            winnings = 0
            message = "Немає виграшу."

            # Check for winnings
            if reel1 == reel2 == reel3:
                if reel1 == '💎':
                    winnings = BET_AMOUNT * 10
                    message = "ДЖЕКПОТ! 💎💎💎"
                elif reel1 == wild_symbol:
                    winnings = BET_AMOUNT * 7
                    message = "Супер виграш! ⭐⭐⭐"
                elif reel1 == scatter_symbol:
                    winnings = BET_AMOUNT * 5
                    message = "Великий виграш! 💰💰💰"
                else:
                    winnings = BET_AMOUNT * 3
                    message = f"Виграш! {reel1}{reel1}{reel1}"
            elif len(set(result_symbols)) == 2 and (wild_symbol in result_symbols or scatter_symbol in result_symbols):
                # Check for 2 matching symbols + wild/scatter
                counts = {s: result_symbols.count(s) for s in set(result_symbols)}
                
                # Check for two same symbols and a wild
                for s in symbols_pool:
                    if counts.get(s, 0) == 2 and wild_symbol in result_symbols:
                        winnings = BET_AMOUNT * 1.5
                        message = "Виграш з WILD! " + "".join(result_symbols)
                        break
                # Check for two same symbols and a scatter (less common, but possible)
                if winnings == 0: # Only if not already won by wild
                    for s in symbols_pool:
                        if counts.get(s, 0) == 2 and scatter_symbol in result_symbols:
                            winnings = BET_AMOUNT * 1.2
                            message = "Виграш з SCATTER! " + "".join(result_symbols)
                            break
            elif result_symbols.count(scatter_symbol) >= 2:
                winnings = BET_AMOUNT * 0.8
                message = "Виграш з SCATTER!"
            elif result_symbols.count(wild_symbol) >= 2:
                winnings = BET_AMOUNT * 0.7
                message = "Виграш з WILD!"
            
            # Update balance with winnings
            new_balance += winnings
            cur.execute(sql.SQL("UPDATE users SET balance = %s WHERE user_id = %s;"), (new_balance, req.user_id))

            # Update XP and check for level up
            new_xp = current_xp + XP_PER_SPIN
            
            # Recalculate next_level_xp based on new_level
            if new_xp >= next_level_xp:
                new_level = current_level + 1
                new_next_level_xp = new_level * 100 + (new_level - 1) * 50 # Example: Level 2 needs 200 + 50 = 250 XP
                message += f" 🎉 Новий рівень: {new_level}!"
                logger.info(f"User {req.user_id} leveled up to {new_level}. New XP: {new_xp}, Next Level XP: {new_next_level_xp}")
            else:
                new_level = current_level
                new_next_level_xp = next_level_xp # Keep the same if not leveled up

            cur.execute(sql.SQL("UPDATE users SET xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s;"),
                        (new_xp, new_level, new_next_level_xp, req.user_id))
            
            conn.commit()

            logger.info(f"User {req.user_id} spun: {result_symbols}. Winnings: {winnings}. New Balance: {new_balance}. New XP: {new_xp}. New Level: {new_level}")
            return {"symbols": result_symbols, "winnings": winnings, "message": message, "new_balance": new_balance, "xp": new_xp, "level": new_level, "next_level_xp": new_next_level_xp}

    except HTTPException as e:
        conn.rollback()
        raise e
    except Exception as e:
        conn.rollback()
        logger.error(f"Error during slot spin for user {req.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

class CoinFlipRequest(BaseModel):
    user_id: int
    choice: str # 'heads' or 'tails'

@app.post("/api/coin_flip")
async def coin_flip(req: CoinFlipRequest):
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")

    BET_AMOUNT = 50
    XP_PER_FLIP = 5

    if req.choice not in ['heads', 'tails']:
        raise HTTPException(status_code=400, detail="Невірний вибір. Оберіть 'heads' або 'tails'.")

    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE;"), (req.user_id,))
            user_data = cur.fetchone()

            if not user_data:
                raise HTTPException(status_code=404, detail="User not found.")

            current_balance, current_xp, current_level, next_level_xp = user_data

            if current_balance < BET_AMOUNT:
                raise HTTPException(status_code=400, detail="Недостатньо фантиків для підкидання монетки!")

            # Deduct bet
            new_balance = current_balance - BET_AMOUNT
            cur.execute(sql.SQL("UPDATE users SET balance = %s WHERE user_id = %s;"), (new_balance, req.user_id))
            conn.commit() # Commit balance deduction immediately

            # Flip logic
            result = random.choice(['heads', 'tails'])
            winnings = 0
            message = ""

            if result == req.choice:
                winnings = BET_AMOUNT * 2 # Double the bet
                message = f"Вітаємо! Ви вгадали! Випало {result}. Ви виграли {winnings} фантиків!"
            else:
                message = f"На жаль, ви програли. Випало {result}. Спробуйте ще раз!"
            
            # Update balance with winnings (if any)
            new_balance += winnings
            cur.execute(sql.SQL("UPDATE users SET balance = %s WHERE user_id = %s;"), (new_balance, req.user_id))

            # Update XP and check for level up
            new_xp = current_xp + XP_PER_FLIP
            
            # Recalculate next_level_xp based on new_level
            if new_xp >= next_level_xp:
                new_level = current_level + 1
                new_next_level_xp = new_level * 100 + (new_level - 1) * 50
                message += f" 🎉 Новий рівень: {new_level}!"
                logger.info(f"User {req.user_id} leveled up to {new_level}. New XP: {new_xp}, Next Level XP: {new_next_level_xp}")
            else:
                new_level = current_level
                new_next_level_xp = next_level_xp

            cur.execute(sql.SQL("UPDATE users SET xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s;"),
                        (new_xp, new_level, new_next_level_xp, req.user_id))
            
            conn.commit()

            logger.info(f"User {req.user_id} flipped coin. Choice: {req.choice}, Result: {result}, Winnings: {winnings}. New Balance: {new_balance}. New XP: {new_xp}. New Level: {new_level}")
            return {"result": result, "winnings": winnings, "message": message, "new_balance": new_balance, "xp": new_xp, "level": new_level, "next_level_xp": new_next_level_xp}

    except HTTPException as e:
        conn.rollback()
        raise e
    except Exception as e:
        conn.rollback()
        logger.error(f"Error during coin flip for user {req.user_id}: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

@app.post("/api/get_leaderboard")
async def get_leaderboard():
    conn = get_db_connection()
    if conn is None:
        raise HTTPException(status_code=500, detail="Database connection failed.")
    
    try:
        with conn.cursor() as cur:
            # Order by level descending, then by XP descending, then by balance descending
            cur.execute(sql.SQL("SELECT user_id, username, balance, xp, level FROM users ORDER BY level DESC, xp DESC, balance DESC LIMIT 100;"))
            leaderboard_data = cur.fetchall()

            leaderboard = []
            for user_id, username, balance, xp, level in leaderboard_data:
                leaderboard.append({
                    "user_id": user_id,
                    "username": username,
                    "balance": balance,
                    "xp": xp,
                    "level": level
                })
            logger.info("Leaderboard fetched successfully.")
            return {"leaderboard": leaderboard}
    except Exception as e:
        logger.error(f"Error fetching leaderboard: {e}")
        raise HTTPException(status_code=500, detail=f"Internal server error: {e}")
    finally:
        conn.close()

# --- Blackjack Game Logic (Multiplayer with WebSockets) ---
class Card:
    def __init__(self, rank, suit):
        self.rank = rank
        self.suit = suit
        self.value = self._get_value()

    def _get_value(self):
        if self.rank in ['K', 'Q', 'J', 'T']:
            return 10
        elif self.rank == 'A':
            return 11 # Ace value is handled dynamically in Hand
        else:
            return int(self.rank)

    def __str__(self):
        return f"{self.rank}{self.suit}" # e.g., "A♠"

    def to_dict(self):
        return {"rank": self.rank, "suit": self.suit, "value": self.value}

class Deck:
    def __init__(self):
        ranks = ['A', '2', '3', '4', '5', '6', '7', '8', '9', 'T', 'J', 'Q', 'K']
        suits = ['♠', '♥', '♦', '♣']
        self.cards = [Card(rank, suit) for rank in ranks for suit in suits]
        random.shuffle(self.cards)

    def deal_card(self):
        if not self.cards:
            # Reshuffle if deck is empty (simple for now)
            self.__init__()
            logger.warning("Deck empty, reshuffling.")
        return self.cards.pop()

class Hand:
    def __init__(self):
        self.cards: List[Card] = []
        self.score = 0
        self.aces = 0

    def add_card(self, card: Card):
        self.cards.append(card)
        if card.rank == 'A':
            self.aces += 1
        self._calculate_score()

    def _calculate_score(self):
        self.score = sum(card.value for card in self.cards)
        # Adjust for Aces
        while self.aces > 0 and self.score > 21:
            self.score -= 10
            self.aces -= 1

    def is_blackjack(self):
        return len(self.cards) == 2 and self.score == 21

    def is_busted(self):
        return self.score > 21
    
    def to_list_str(self, hide_first=False):
        if hide_first and len(self.cards) > 0:
            return ["Hidden"] + [str(card) for card in self.cards[1:]]
        return [str(card) for card in self.cards]

class Player:
    def __init__(self, user_id: int, username: str, websocket: WebSocket):
        self.user_id = user_id
        self.username = username
        self.websocket = websocket
        self.hand = Hand()
        self.bet = 0
        self.is_playing = True # True if still in round (not busted, stood, or finished turn)
        self.has_bet = False # True if player has placed a bet for the current round

    def to_dict(self, hide_dealer_first_card=False):
        return {
            "user_id": self.user_id,
            "username": self.username,
            "hand": self.hand.to_list_str(hide_first=hide_dealer_first_card and self.user_id == -1), # -1 is dealer
            "score": self.hand.score,
            "bet": self.bet,
            "is_busted": self.hand.is_busted(),
            "is_playing": self.is_playing,
            "has_bet": self.has_bet
        }

class Room:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[int, Player] = {} # {user_id: Player object}
        self.deck = Deck()
        self.dealer = Player(user_id=-1, username="Dealer", websocket=None) # Dealer is a special player
        self.status = "waiting" # waiting, starting_timer, betting, playing, round_end
        self.current_player_turn: Optional[int] = None
        self.player_count = 0
        self.min_players = 2
        self.max_players = 5
        self.timer_task: Optional[asyncio.Task] = None
        self.timer_seconds = 0 # Current countdown for timers
        self.STARTING_TIMER_DURATION = 20 # Seconds
        self.BETTING_TIMER_DURATION = 30 # Seconds
        self.ACTION_TIMER_DURATION = 20 # Seconds for hit/stand

    def add_player(self, player: Player):
        if player.user_id not in self.players:
            self.players[player.user_id] = player
            self.player_count = len(self.players)
            logger.info(f"Player {player.username} ({player.user_id}) joined room {self.room_id}. Current players: {self.player_count}")
            self._start_timer_if_ready()
        else:
            # Update existing player's websocket if they reconnected
            self.players[player.user_id].websocket = player.websocket
            logger.info(f"Player {player.username} ({player.user_id}) reconnected to room {self.room_id}.")
        
    def remove_player(self, user_id: int):
        if user_id in self.players:
            del self.players[user_id]
            self.player_count = len(self.players)
            logger.info(f"Player {user_id} left room {self.room_id}. Current players: {self.player_count}")
            if self.player_count < self.min_players and self.status != "waiting":
                logger.warning(f"Room {self.room_id} has too few players. Resetting game.")
                self._reset_game()
            self._send_room_state_to_all_players()

    def _reset_game(self):
        logger.info(f"Resetting game for room {self.room_id}.")
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None
        self.status = "waiting"
        self.deck = Deck()
        self.dealer = Player(user_id=-1, username="Dealer", websocket=None)
        self.current_player_turn = None
        self.timer_seconds = 0
        for player in self.players.values():
            player.hand = Hand()
            player.bet = 0
            player.is_playing = True
            player.has_bet = False
        self._send_room_state_to_all_players()
        self._start_timer_if_ready() # Try to start timer again if enough players

    async def _run_timer(self, duration: int, next_status: str):
        self.timer_seconds = duration
        while self.timer_seconds > 0:
            self._send_room_state_to_all_players() # Send state with updated timer
            await asyncio.sleep(1)
            self.timer_seconds -= 1
        
        self.status = next_status
        self.timer_seconds = 0 # Reset timer after it finishes
        logger.info(f"Room {self.room_id} timer finished. Transitioning to status: {self.status}")
        self._send_room_state_to_all_players() # Send final state after timer

        if next_status == "betting":
            self.timer_task = asyncio.create_task(self._run_timer(self.BETTING_TIMER_DURATION, "playing"))
        elif next_status == "playing":
            await self._start_round() # This is where the game truly begins after betting
        elif next_status == "round_end":
            await asyncio.sleep(5) # Show results for 5 seconds
            self._reset_game() # Automatically reset after round end

    def _start_timer_if_ready(self):
        if self.status == "waiting" and self.player_count >= self.min_players and not self.timer_task:
            self.status = "starting_timer"
            logger.info(f"Room {self.room_id}: Starting game countdown ({self.STARTING_TIMER_DURATION}s).")
            self.timer_task = asyncio.create_task(self._run_timer(self.STARTING_TIMER_DURATION, "betting"))
            self._send_room_state_to_all_players() # Send immediate update for timer start

    async def _start_round(self):
        logger.info(f"Room {self.room_id}: Starting new round.")
        self.status = "playing"
        self.deck = Deck() # New deck for each round
        self.dealer.hand = Hand()
        self.current_player_turn = None # Reset current player turn

        # Reset player hands and playing status for the new round
        active_players_in_round = []
        for player_id, player in list(self.players.items()): # Iterate over a copy
            if player.has_bet: # Only players who bet participate
                player.hand = Hand()
                player.is_playing = True
                active_players_in_round.append(player_id)
            else:
                player.is_playing = False # Player did not bet, not playing this round
                player.bet = 0 # Reset bet if they didn't participate
        
        if not active_players_in_round:
            logger.warning(f"Room {self.room_id}: No active players for the round. Resetting game.")
            self._send_room_state_to_all_players(game_message="Ніхто не зробив ставку. Раунд скасовано.")
            await asyncio.sleep(3)
            self._reset_game()
            return

        # Deal initial cards (two to each player, two to dealer)
        for _ in range(2):
            for player_id in active_players_in_round:
                self.players[player_id].hand.add_card(self.deck.deal_card())
            self.dealer.hand.add_card(self.deck.deal_card())
        
        # Hide dealer's first card
        self.dealer.hand_hidden = True # Custom attribute to track hidden card state
        
        logger.info(f"Room {self.room_id}: Cards dealt. Dealer hand (hidden): {self.dealer.hand.to_list_str(hide_first=True)}")
        for player_id in active_players_in_round:
            logger.info(f"Room {self.room_id}: Player {self.players[player_id].username} hand: {self.players[player_id].hand.to_list_str()}")

        # Check for immediate Blackjacks
        for player_id in active_players_in_round:
            player = self.players[player_id]
            if player.hand.is_blackjack():
                player.is_playing = False # Player stands on Blackjack
                logger.info(f"Player {player.username} has Blackjack!")
                self._send_room_state_to_all_players(game_message=f"{player.username} має Блекджек!")

        # Determine first player
        self.current_player_turn = active_players_in_round[0]
        logger.info(f"Room {self.room_id}: First player turn: {self.players[self.current_player_turn].username}")
        self._send_room_state_to_all_players(game_message=f"Гра почалася! Хід гравця {self.players[self.current_player_turn].username}.")
        
        # Start action timer for the first player
        self.timer_task = asyncio.create_task(self._run_action_timer())

    async def _run_action_timer(self):
        # This timer is for player actions (hit/stand)
        self.timer_seconds = self.ACTION_TIMER_DURATION
        while self.timer_seconds > 0:
            if self.status != "playing": # Stop timer if game state changes
                logger.debug(f"Room {self.room_id}: Action timer stopped due to status change to {self.status}.")
                self.timer_seconds = 0
                return
            self._send_room_state_to_all_players()
            await asyncio.sleep(1)
            self.timer_seconds -= 1
        
        logger.info(f"Room {self.room_id}: Action timer for {self.players[self.current_player_turn].username} expired. Auto-standing.")
        # If timer expires, automatically stand
        await self._handle_stand(self.current_player_turn)


    async def _next_player_turn(self):
        active_players_in_round = [p_id for p_id, p in self.players.items() if p.is_playing and p.has_bet]
        
        if not active_players_in_round:
            logger.info(f"Room {self.room_id}: No more active players. Dealer's turn.")
            await self._dealer_turn()
            return

        current_player_index = active_players_in_round.index(self.current_player_turn)
        next_index = (current_player_index + 1) % len(active_players_in_round)
        self.current_player_turn = active_players_in_round[next_index]
        
        logger.info(f"Room {self.room_id}: Next player turn: {self.players[self.current_player_turn].username}")
        self._send_room_state_to_all_players(game_message=f"Хід гравця {self.players[self.current_player_turn].username}.")
        
        # Restart action timer for the next player
        if self.timer_task:
            self.timer_task.cancel()
        self.timer_task = asyncio.create_task(self._run_action_timer())


    async def _dealer_turn(self):
        logger.info(f"Room {self.room_id}: Dealer's turn begins.")
        self.status = "dealer_playing" # New status for dealer's turn
        self.current_player_turn = self.dealer.user_id # Set turn to dealer
        self.dealer.hand_hidden = False # Reveal dealer's hidden card
        self._send_room_state_to_all_players(game_message="Хід дилера. Розкриваю карти.")
        await asyncio.sleep(2) # Pause to show revealed card

        while self.dealer.hand.score < 17:
            card = self.deck.deal_card()
            self.dealer.hand.add_card(card)
            logger.info(f"Room {self.room_id}: Dealer hits. Card: {card}. Dealer hand: {self.dealer.hand.to_list_str()}, Score: {self.dealer.hand.score}")
            self._send_room_state_to_all_players(game_message=f"Дилер бере карту: {card}")
            await asyncio.sleep(1.5) # Pause for visual effect

        logger.info(f"Room {self.room_id}: Dealer stands or busts. Final dealer hand: {self.dealer.hand.to_list_str()}, Score: {self.dealer.hand.score}")
        self._end_round()


    def _end_round(self):
        logger.info(f"Room {self.room_id}: Round ending. Calculating results.")
        self.status = "round_end"
        self.current_player_turn = None # No one's turn
        self.timer_seconds = 0 # Ensure timer is off
        if self.timer_task:
            self.timer_task.cancel()
            self.timer_task = None

        dealer_score = self.dealer.hand.score
        dealer_busted = self.dealer.hand.is_busted()

        results_messages = []

        conn = get_db_connection()
        if conn is None:
            logger.error("Database connection failed during round end.")
            self._send_room_state_to_all_players(game_message="Помилка збереження результатів раунду (DB).")
            return

        try:
            with conn.cursor() as cur:
                for player_id, player in self.players.items():
                    if not player.has_bet: # Players who didn't bet are skipped
                        continue

                    player_score = player.hand.score
                    player_busted = player.hand.is_busted()
                    winnings = 0
                    message = ""
                    xp_gained = 0
                    
                    # Log player's final state for debugging
                    logger.info(f"Player {player.username} ({player.user_id}) final hand: {player.hand.to_list_str()}, score: {player_score}, busted: {player_busted}")

                    if player_busted:
                        message = "Ви перебрали! Програш."
                        winnings = -player.bet
                        xp_gained = 2 # Small XP for participation
                    elif dealer_busted:
                        message = "Дилер перебрав! Ви виграли!"
                        winnings = player.bet
                        xp_gained = 20
                    elif player_score > dealer_score:
                        message = "Ви виграли!"
                        winnings = player.bet
                        xp_gained = 20
                    elif player_score < dealer_score:
                        message = "Ви програли."
                        winnings = -player.bet
                        xp_gained = 2
                    else:
                        message = "Нічия!"
                        winnings = 0
                        xp_gained = 5 # Small XP for draw

                    # Update user balance and XP in DB
                    cur.execute(sql.SQL("SELECT balance, xp, level, next_level_xp FROM users WHERE user_id = %s FOR UPDATE;"), (player_id,))
                    user_db_data = cur.fetchone()
                    if user_db_data:
                        current_balance, current_xp, current_level, next_level_xp = user_db_data
                        new_balance = current_balance + winnings
                        new_xp = current_xp + xp_gained
                        new_level = current_level
                        new_next_level_xp = next_level_xp

                        if new_xp >= next_level_xp:
                            new_level += 1
                            new_next_level_xp = new_level * 100 + (new_level - 1) * 50
                            logger.info(f"User {player_id} leveled up to {new_level} from Blackjack. New XP: {new_xp}, Next Level XP: {new_next_level_xp}")

                        cur.execute(sql.SQL("UPDATE users SET balance = %s, xp = %s, level = %s, next_level_xp = %s WHERE user_id = %s;"),
                                    (new_balance, new_xp, new_level, new_next_level_xp, player_id))
                        conn.commit() # Commit changes for each player

                        # Send individual round result to player
                        player_websocket = self.players[player_id].websocket
                        if player_websocket:
                            asyncio.create_task(player_websocket.send_json({
                                "type": "round_result",
                                "message": message,
                                "winnings": winnings,
                                "balance": new_balance,
                                "xp": new_xp,
                                "level": new_level,
                                "next_level_xp": new_next_level_xp,
                                "final_player_score": player_score,
                                "final_dealer_score": dealer_score # Include dealer score for client display
                            }))
                            logger.info(f"Sent round_result to player {player.username}: {message}, Winnings: {winnings}")
                    else:
                        logger.error(f"User {player_id} not found in DB during round end processing.")
                
                # After all player results are processed, send a final room state update
                # This will trigger the frontend to reset or show "new round soon"
                self._send_room_state_to_all_players(game_message="Раунд завершено! Новий раунд незабаром...")
                
                # Start timer for next round
                self.timer_task = asyncio.create_task(self._run_timer(5, "waiting")) # 5 seconds to reset

        except Exception as e:
            conn.rollback()
            logger.error(f"Error during round end processing for room {self.room_id}: {e}")
            self._send_room_state_to_all_players(game_message="Помилка при завершенні раунду.")
        finally:
            conn.close()


    async def _send_room_state_to_all_players(self, game_message: Optional[str] = None):
        state = {
            "room_id": self.room_id,
            "status": self.status,
            "dealer_hand": self.dealer.hand.to_list_str(hide_first=self.dealer.hand_hidden if hasattr(self.dealer, 'hand_hidden') else False),
            "dealer_score": self.dealer.hand.score if not (hasattr(self.dealer, 'hand_hidden') and self.dealer.hand_hidden) else self.dealer.hand.cards[1].value if len(self.dealer.hand.cards) > 1 else 0, # Show real score only if not hidden
            "players": [p.to_dict(hide_dealer_first_card=True) for p in self.players.values()], # Pass hide_dealer_first_card=True for all players
            "current_player_turn": self.current_player_turn,
            "player_count": self.player_count,
            "min_players": self.min_players,
            "max_players": self.max_players,
            "timer": self.timer_seconds,
            "message": game_message # Include a general game message
        }
        for player in self.players.values():
            try:
                # For the player themselves, ensure their own hand is always fully visible
                player_specific_state = state.copy()
                player_specific_state["players"] = [
                    p.to_dict(hide_dealer_first_card=False) if p.user_id == player.user_id else p.to_dict(hide_dealer_first_card=True)
                    for p in self.players.values()
                ]
                # Ensure dealer's first card is hidden for players during 'playing' phase
                if self.status == "playing" and hasattr(self.dealer, 'hand_hidden') and self.dealer.hand_hidden:
                    player_specific_state["dealer_hand"] = self.dealer.hand.to_list_str(hide_first=True)
                    player_specific_state["dealer_score"] = self.dealer.hand.cards[1].value if len(self.dealer.hand.cards) > 1 else 0
                else:
                    player_specific_state["dealer_hand"] = self.dealer.hand.to_list_str(hide_first=False)
                    player_specific_state["dealer_score"] = self.dealer.hand.score

                await player.websocket.send_json(player_specific_state)
            except WebSocketDisconnect:
                logger.warning(f"WebSocket disconnected for user {player.user_id} during state send.")
                self.remove_player(player.user_id)
            except Exception as e:
                logger.error(f"Error sending state to user {player.user_id}: {e}")

# Global dictionary to hold active rooms
active_rooms: Dict[str, Room] = {}
room_counter = 0

async def get_or_create_room():
    global room_counter
    # Try to find a room that is waiting for players
    for room in active_rooms.values():
        if room.status == "waiting" and room.player_count < room.max_players:
            return room
    
    # If no waiting room, create a new one
    room_id = f"room_{room_counter}"
    room_counter += 1
    new_room = Room(room_id)
    active_rooms[room_id] = new_room
    logger.info(f"Created new room: {room_id}")
    return new_room

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    logger.info(f"WebSocket accepted for user_id: {user_id}")

    conn = get_db_connection()
    if conn is None:
        logger.error(f"DB connection failed for WebSocket user {user_id}.")
        await websocket.close(code=1011, reason="DB Error")
        return

    username = f"Player{user_id}"
    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT username FROM users WHERE user_id = %s;"), (user_id,))
            user_data = cur.fetchone()
            if user_data:
                username = user_data[0]
            else:
                # If user doesn't exist, create a dummy entry for WS purposes
                # This should ideally be handled by /api/get_balance first
                logger.warning(f"User {user_id} not found in DB when connecting to WS. Using default username.")
                pass # Let the frontend handle user creation via /api/get_balance
    except Exception as e:
        logger.error(f"Error fetching username for WS {user_id}: {e}")
    finally:
        conn.close()

    player = Player(user_id, username, websocket)
    room = await get_or_create_room()
    room.add_player(player)

    # Initial state send
    await room._send_room_state_to_all_players(game_message="Приєднано до кімнати. Очікування гравців...")

    try:
        while True:
            data = await websocket.receive_json()
            action = data.get("action")
            logger.info(f"Received action '{action}' from user {user_id} in room {room.room_id}")

            if action == "bet":
                amount = data.get("amount")
                await _handle_bet(room, user_id, amount)
            elif action == "hit":
                await _handle_hit(room, user_id)
            elif action == "stand":
                await _handle_stand(room, user_id)
            elif action == "request_state":
                logger.info(f"User {user_id} requested state for room {room.room_id}.")
                await room._send_room_state_to_all_players() # Send current state
            elif data.get("type") == "pong":
                logger.debug(f"Received pong from user {user_id}.")
                # No action needed, just keep connection alive
            else:
                logger.warning(f"Unknown action '{action}' from user {user_id}.")
                await websocket.send_json({"type": "error", "message": "Невідома дія."})

    except WebSocketDisconnect:
        logger.info(f"User {user_id} disconnected from WebSocket.")
        room.remove_player(user_id)
    except Exception as e:
        logger.error(f"Error in WebSocket for user {user_id}: {e}")
        room.remove_player(user_id)
        # Attempt to send an error message before closing
        try:
            await websocket.send_json({"type": "error", "message": f"Помилка сервера: {e}"})
        except Exception as send_e:
            logger.error(f"Failed to send error message to {user_id}: {send_e}")
    finally:
        # Ensure player is removed from room if not already
        if user_id in room.players:
            room.remove_player(user_id)
        logger.info(f"WebSocket connection closed for user {user_id}.")


async def _handle_bet(room: Room, user_id: int, amount: int):
    conn = get_db_connection()
    if conn is None:
        logger.error("DB connection failed for _handle_bet.")
        await room.players[user_id].websocket.send_json({"type": "error", "message": "Помилка бази даних."})
        return

    player = room.players.get(user_id)
    if not player:
        logger.warning(f"Player {user_id} not found in room {room.room_id} during bet.")
        return # Should not happen if player is in room.players

    if room.status != "betting":
        logger.warning(f"Player {user_id} tried to bet outside betting phase (status: {room.status}).")
        await player.websocket.send_json({"type": "error", "message": "Зараз не час для ставок."})
        return

    if player.has_bet:
        logger.warning(f"Player {user_id} already placed a bet.")
        await player.websocket.send_json({"type": "error", "message": "Ви вже зробили ставку."})
        return

    try:
        with conn.cursor() as cur:
            cur.execute(sql.SQL("SELECT balance FROM users WHERE user_id = %s FOR UPDATE;"), (user_id,))
            current_balance = cur.fetchone()[0]

            if current_balance < amount:
                logger.warning(f"Player {user_id} has insufficient balance ({current_balance}) for bet ({amount}).")
                await player.websocket.send_json({"type": "error", "message": "Недостатньо фантиків для ставки."})
                return

            new_balance = current_balance - amount
            cur.execute(sql.SQL("UPDATE users SET balance = %s WHERE user_id = %s;"), (new_balance, user_id))
            conn.commit()

            player.bet = amount
            player.has_bet = True
            logger.info(f"Player {user_id} placed bet: {amount}. New balance: {new_balance}.")
            await player.websocket.send_json({"type": "game_message", "message": f"Ваша ставка {amount} прийнята. Баланс: {new_balance}"})

            # Check if all active players have placed their bets
            all_bets_placed = True
            for p in room.players.values():
                if p.is_playing and not p.has_bet: # Only check players who are 'playing' in the current round
                    all_bets_placed = False
                    break
            
            # If all players have bet, start the round
            if all_bets_placed:
                logger.info(f"Room {room.room_id}: All players have placed bets. Starting round.")
                if room.timer_task:
                    room.timer_task.cancel() # Cancel betting timer
                await room._start_round()
            else:
                logger.info(f"Room {room.room_id}: Waiting for other players to bet.")
                await room._send_room_state_to_all_players(game_message="Очікуємо інших гравців на ставку...")

    except Exception as e:
        conn.rollback()
        logger.error(f"Error handling bet for user {user_id}: {e}")
        await player.websocket.send_json({"type": "error", "message": f"Помилка при ставці: {e}"})
    finally:
        conn.close()


async def _handle_hit(room: Room, user_id: int):
    player = room.players.get(user_id)
    if not player or room.status != "playing" or room.current_player_turn != user_id or not player.is_playing:
        logger.warning(f"Player {user_id} tried to hit out of turn/phase or not playing. Status: {room.status}, Turn: {room.current_player_turn}, IsPlaying: {player.is_playing if player else 'N/A'}")
        await player.websocket.send_json({"type": "error", "message": "Зараз не ваш хід або неможливо взяти карту."})
        return

    player.hand.add_card(room.deck.deal_card())
    logger.info(f"Player {user_id} hit. New hand: {player.hand.to_list_str()}, Score: {player.hand.score}")
    await room._send_room_state_to_all_players(game_message=f"{player.username} взяв карту.")

    if player.hand.is_busted():
        player.is_playing = False
        logger.info(f"Player {user_id} busted with score {player.hand.score}.")
        await room._send_room_state_to_all_players(game_message=f"{player.username} перебрав!")
        # Move to next player or dealer turn
        await room._next_player_turn()
    else:
        # If not busted, player can hit again or stand. Keep current turn.
        # Reset timer for current player
        if room.timer_task:
            room.timer_task.cancel()
        room.timer_task = asyncio.create_task(room._run_action_timer())
        await room._send_room_state_to_all_players(game_message=f"Ваш хід! (Рахунок: {player.hand.score})")


async def _handle_stand(room: Room, user_id: int):
    player = room.players.get(user_id)
    if not player or room.status != "playing" or room.current_player_turn != user_id or not player.is_playing:
        logger.warning(f"Player {user_id} tried to stand out of turn/phase or not playing. Status: {room.status}, Turn: {room.current_player_turn}, IsPlaying: {player.is_playing if player else 'N/A'}")
        await player.websocket.send_json({"type": "error", "message": "Зараз не ваш хід або неможливо зупинитись."})
        return

    player.is_playing = False
    logger.info(f"Player {user_id} stood with score {player.hand.score}.")
    await room._send_room_state_to_all_players(game_message=f"{player.username} зупинився.")
    
    # Move to next player or dealer turn
    await room._next_player_turn()


# --- Telegram Bot Handlers ---
if dp:
    @dp.message(CommandStart())
    async def command_start_handler(message: Message) -> None:
        if WEB_APP_FRONTEND_URL:
            # Create a unique start_param for each user if needed, or just use user_id
            start_param = f"user_id={message.from_user.id}"
            web_app_url = f"{WEB_APP_FRONTEND_URL}?{start_param}"
            
            keyboard = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="Відкрити Імперію Слота", web_app=WebAppInfo(url=web_app_url))]
            ])
            
            await message.answer(
                f"Привіт, {message.from_user.full_name}! Ласкаво просимо до Імперії Слота! Натисніть кнопку нижче, щоб почати грати.",
                reply_markup=keyboard
            )
            logger.info(f"Sent start message to user {message.from_user.id} with WebApp URL: {web_app_url}")
        else:
            await message.answer("Вибачте, WebApp URL не налаштовано.")
            logger.warning("WEB_APP_FRONTEND_URL is not set.")

    @dp.message(Command("help"))
    async def command_help_handler(message: Message) -> None:
        await message.answer("Це віртуальне казино 'Імперія Слота'. Ви можете грати в слоти, підкидати монетку та блекджек. Використовуйте /start, щоб відкрити гру.")

    @dp.message()
    async def echo_message(message: types.Message):
        # This handler catches all other messages and logs them,
        # but doesn't reply to avoid spamming users.
        logger.info(f"Received message from {message.from_user.id}: {message.text}")


# --- FastAPI Startup/Shutdown Events ---
@app.on_event("startup")
async def on_startup():
    logger.info("Application startup event triggered.")
    # Initialize database schema if it doesn't exist
    conn = get_db_connection()
    if conn:
        try:
            with conn.cursor() as cur:
                cur.execute("""
                    CREATE TABLE IF NOT EXISTS users (
                        user_id BIGINT PRIMARY KEY,
                        username VARCHAR(255) NOT NULL,
                        balance INTEGER DEFAULT 1000,
                        xp INTEGER DEFAULT 0,
                        level INTEGER DEFAULT 1,
                        next_level_xp INTEGER DEFAULT 100,
                        last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL,
                        last_quick_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL
                    );
                """)
                conn.commit()
            logger.info("Database schema checked/created successfully.")
        except Exception as e:
            conn.rollback()
            logger.error(f"Error during database schema initialization: {e}")
        finally:
            conn.close()
    else:
        logger.error("Skipping database schema initialization due to connection failure.")

    # Set up Telegram webhook
    if bot and dp and API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
        if WEBHOOK_HOST:
            external_hostname = WEBHOOK_HOST
        else:
            # Fallback for local development if RENDER_EXTERNAL_HOSTNAME is not set
            # In production on Render, RENDER_EXTERNAL_HOSTNAME will be set automatically
            logger.warning("RENDER_EXTERNAL_HOSTNAME is not set. Assuming local development or testing.")
            external_hostname = "localhost:8000" # Default for local FastAPI

        WEBHOOK_PATH = f"/telegram-webhook/{API_TOKEN}"
        WEBHOOK_URL = f"https://{external_hostname}{WEBHOOK_PATH}" # Use https for Render

        global WEB_APP_FRONTEND_URL
        if WEB_APP_FRONTEND_URL and not WEB_APP_FRONTEND_URL.startswith("https://"):
            WEB_APP_FRONTEND_URL = f"https://{WEB_APP_FRONTEND_URL}" # Ensure HTTPS for WebApp URL

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
        logger.warning("Skipping Telegram webhook setup because BOT_TOKEN is not set or is a dummy value.")

    # Start polling for Telegram updates if not using webhook (e.g., local dev)
    # For Render, webhooks are preferred. This block is mostly for local testing without ngrok.
    if bot and dp and not WEBHOOK_HOST:
        logger.info("Starting Telegram bot polling (no webhook host detected).")
        asyncio.create_task(dp.start_polling(bot))

@app.on_event("shutdown")
async def on_shutdown():
    print("Application shutdown event triggered.")
    # Delete webhook and close bot session when the app shuts down.
    if bot and API_TOKEN and API_TOKEN != "DUMMY_TOKEN":
        try:
            await bot.delete_webhook()
            logger.info("Telegram webhook deleted.")
        except Exception as e:
            logger.error(f"Failed to delete Telegram webhook on shutdown: {e}")
    
    logger.info("Closing dispatcher storage and bot session.")
    if dp:
        await dp.storage.close() 
    if bot:
        await bot.session.close()

# --- Telegram Webhook Endpoint ---
if dp:
    @app.post(f"/telegram-webhook/{{token}}")
    async def telegram_webhook(token: str, request: Request):
        if token != API_TOKEN:
            logger.warning(f"Received webhook with invalid token: {token}")
            raise HTTPException(status_code=403, detail="Invalid bot token")
        
        update = types.Update.model_validate(await request.json(), context={"bot": bot})
        await dp.feed_update(bot, update)
        return {"ok": True}
