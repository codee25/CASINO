from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json
import os
import random
import asyncio
import uuid # For generating unique room IDs
from datetime import datetime, timedelta
from typing import Dict, List, Optional # Ensure these are imported for type hints

# Aiogram imports
from aiogram import Bot, Dispatcher, types
from aiogram.enums import ParseMode
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.client.default import DefaultBotProperties

# --- Config and Setup ---
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")
app = FastAPI()
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# --- Environment Variables ---
# IMPORTANT: You MUST set these environment variables in Render for your Web Service:
# BOT_TOKEN = <YOUR_TELEGRAM_BOT_TOKEN> (Отримайте від BotFather)
# WEB_APP_FRONTEND_URL = <YOUR_RENDER_STATIC_SITE_URL> (Наприклад, https://my-casino-app.onrender.com)
# RENDER_EXTERNAL_HOSTNAME = <YOUR_RENDER_WEB_SERVICE_URL_WITHOUT_TRAILING_SLASH> (Наприклад, https://my-bot-backend.onrender.com)

BOT_TOKEN = os.getenv("BOT_TOKEN")
WEB_APP_FRONTEND_URL = os.getenv("WEB_APP_FRONTEND_URL", "https://your-static-site-name.onrender.com") # Default for local testing
WEBHOOK_PATH = "/webhook" # Path where Telegram will send updates

# Initialize a global variable for WEBHOOK_URL. It will be set on startup.
WEBHOOK_URL: Optional[str] = None

# --- Aiogram Bot Setup ---
if not BOT_TOKEN:
    print("CRITICAL ERROR: BOT_TOKEN environment variable not set. Telegram bot will not work.")
    # You might want to raise an exception or exit in a production environment
    # For now, we'll proceed, but bot operations will fail.

default_props = DefaultBotProperties(parse_mode=ParseMode.HTML)
bot = Bot(token=BOT_TOKEN if BOT_TOKEN else "DUMMY_TOKEN", default=default_props) # Use dummy if not set, but bot won't work
dp = Dispatcher(bot)

# Mock database (in a real application, use a proper database like PostgreSQL, MongoDB, or Firestore)
# Using a simple dictionary for demonstration purposes. This will reset on server restart.
users_db: Dict[str, dict] = {} # user_id: {username, balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim}

# Default XP progression
LEVEL_XP_REQUIREMENTS = {
    1: 0, 2: 100, 3: 300, 4: 600, 5: 1000, 6: 1500, 7: 2100, 8: 2800, 9: 3600, 10: 4500
}
MAX_LEVEL = max(LEVEL_XP_REQUIREMENTS.keys())

# --- Telegram Bot Handlers ---
@dp.message(commands=['start'])
async def start_command_handler(message: types.Message):
    # Get user_id and username
    user_id = str(message.from_user.id)
    username = message.from_user.full_name or f"Гравець {user_id[-4:]}"

    # Initialize user in DB if not exists (or fetch from real DB)
    if user_id not in users_db:
        users_db[user_id] = {
            "username": username,
            "balance": 10000, # Starting bonus
            "xp": 0,
            "level": 1,
            "next_level_xp": LEVEL_XP_REQUIREMENTS.get(2, 100),
            "last_daily_bonus_claim": None,
            "last_quick_bonus_claim": None,
        }
        print(f"New user initialized on /start: {user_id} - {username}")

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [
            InlineKeyboardButton(
                text="🚀 Запустити Імперію Слота",
                web_app=WebAppInfo(url=WEB_APP_FRONTEND_URL)
            )
        ]
    ])
    await message.answer(
        "Привіт! Ласкаво просимо до Імперії Слота! Натисніть кнопку нижче, щоб запустити Web App.",
        reply_markup=keyboard
    )

# --- FastAPI API Endpoints ---
class UserData(BaseModel):
    user_id: str
    username: str = "Unnamed Player"

class SpinData(BaseModel):
    user_id: str

class CoinFlipData(BaseModel):
    user_id: str
    choice: str # 'heads' or 'tails'

class BlackjackAction(BaseModel):
    user_id: str
    room_id: str
    action: str # 'bet', 'hit', 'stand'
    amount: Optional[int] = None # For 'bet' action

@app.post("/api/get_balance")
async def get_balance(user_data: UserData):
    user_id = user_data.user_id
    username = user_data.username

    if user_id not in users_db:
        # This block might be redundant if start_command_handler always initializes,
        # but good for direct access or testing.
        users_db[user_id] = {
            "username": username,
            "balance": 10000, # Starting bonus
            "xp": 0,
            "level": 1,
            "next_level_xp": LEVEL_XP_REQUIREMENTS.get(2, 100),
            "last_daily_bonus_claim": None,
            "last_quick_bonus_claim": None,
        }
        print(f"New user initialized via get_balance: {user_id} - {username}")
    else:
        # Update username if it changed (e.g., user set a Telegram username later)
        users_db[user_id]["username"] = username
        print(f"User {user_id} ({username}) data retrieved.")

    user = users_db[user_id]
    return {
        "user_id": user_id,
        "username": user["username"],
        "balance": user["balance"],
        "xp": user["xp"],
        "level": user["level"],
        "next_level_xp": user["next_level_xp"],
        "last_daily_bonus_claim": user["last_daily_bonus_claim"].isoformat() if user["last_daily_bonus_claim"] else None,
        "last_quick_bonus_claim": user["last_quick_bonus_claim"].isoformat() if user["last_quick_bonus_claim"] else None,
    }

@app.post("/api/spin")
async def spin_slot(spin_data: SpinData):
    user_id = spin_data.user_id
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")

    user = users_db[user_id]
    bet_amount = 100 # Fixed bet amount for slots

    if user["balance"] < bet_amount:
        raise HTTPException(status_code=400, detail="Not enough balance")

    user["balance"] -= bet_amount
    symbols = [random.choice(['�', '🍋', '🍊', '🍇', '🔔', '💎', '🍀', '⭐', '💰']) for _ in range(3)]

    winnings = 0
    message = "Спробуйте ще раз!"

    # Simplified winning logic
    if symbols[0] == symbols[1] == symbols[2]:
        if symbols[0] == '⭐': # Triple Wild
            winnings = bet_amount * 20
            message = "🤯 МЕГА-ВИГРАШ! ТРИ ЗІРКИ! 🤯"
        elif symbols[0] == '💰': # Triple Scatter
            winnings = bet_amount * 15
            message = "💰 ДЖЕКПОТ! ТРИ МІШКИ! 💰"
        elif symbols[0] == '💎': # Triple Diamond
            winnings = bet_amount * 10
            message = "💎 ВЕЛИКИЙ ВИГРАШ! ТРИ ДІАМАНТИ! 💎"
        else:
            winnings = bet_amount * 5
            message = f"🎉 ТРИ {symbols[0]}! Виграш! 🎉"
    elif symbols[0] == symbols[1] or symbols[1] == symbols[2]:
        winnings = bet_amount * 1.5
        message = "✨ Два однакових! ✨"
    elif '⭐' in symbols: # Wild symbol anywhere
        winnings = bet_amount * 2
        message = "⭐ WILD! +2X!"

    user["balance"] += winnings
    user["xp"] += 10 # Gain XP for each spin

    # Level Up Check
    while user["level"] < MAX_LEVEL and user["xp"] >= user["next_level_xp"]:
        user["level"] += 1
        user["xp"] = user["xp"] - user["next_level_xp"] # Carry over excess XP
        user["next_level_xp"] = LEVEL_XP_REQUIREMENTS.get(user["level"] + 1, user["next_level_xp"] * 2) # Simple progression or double
        message += f" 🎉 НОВИЙ РІВЕНЬ: {user['level']}! 🎉"

    return {"symbols": symbols, "winnings": winnings, "balance": user["balance"], "xp": user["xp"], "level": user["level"], "next_level_xp": user["next_level_xp"], "message": message}

@app.post("/api/coin_flip")
async def coin_flip(flip_data: CoinFlipData):
    user_id = flip_data.user_id
    choice = flip_data.choice # 'heads' or 'tails'

    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")

    user = users_db[user_id]
    bet_amount = 50 # Fixed bet for coin flip

    if user["balance"] < bet_amount:
        raise HTTPException(status_code=400, detail="Not enough balance")

    user["balance"] -= bet_amount

    result = random.choice(['heads', 'tails'])
    winnings = 0
    message = ""

    if result == choice:
        winnings = bet_amount * 2
        user["balance"] += winnings
        message = f"🎉 Вітаємо! Випало {result == 'heads' and 'ОРЕЛ' or 'РЕШКА'}! Ви виграли {winnings} фантиків!"
    else:
        message = f"😢 На жаль! Випало {result == 'heads' and 'ОРЕЛ' or 'РЕШКА'}. Спробуйте ще раз!"

    user["xp"] += 5 # XP for coin flip

    # Level Up Check (simplified, can be moved to a helper)
    while user["level"] < MAX_LEVEL and user["xp"] >= user["next_level_xp"]:
        user["level"] += 1
        user["xp"] = user["xp"] - user["next_level_xp"]
        user["next_level_xp"] = LEVEL_XP_REQUIREMENTS.get(user["level"] + 1, user["next_level_xp"] * 2)
        message += f" 🎉 НОВИЙ РІВЕНЬ: {user['level']}! 🎉"

    return {"result": result, "winnings": winnings, "balance": user["balance"], "xp": user["xp"], "level": user["level"], "next_level_xp": user["next_level_xp"], "message": message}

@app.post("/api/claim_daily_bonus")
async def claim_daily_bonus(user_data: UserData):
    user_id = user_data.user_id
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")

    user = users_db[user_id]
    now = datetime.now()
    
    # Define time zones to avoid issues
    # Using a simple comparison; in production, consider pytz for robust timezone handling
    
    if user["last_daily_bonus_claim"]:
        last_claim = user["last_daily_bonus_claim"]
        time_since_last_claim = now - last_claim
        if time_since_last_claim < timedelta(hours=24):
            remaining_time = timedelta(hours=24) - time_since_last_claim
            hours, remainder = divmod(remaining_time.total_seconds(), 3600)
            minutes, seconds = divmod(remainder, 60)
            raise HTTPException(status_code=400, detail=f"Бонус вже отримано. Спробуйте через {int(hours)} год {int(minutes)} хв.")

    bonus_amount = 500
    user["balance"] += bonus_amount
    user["last_daily_bonus_claim"] = now
    user["xp"] += 20 # XP for claiming bonus

    # Level Up Check
    while user["level"] < MAX_LEVEL and user["xp"] >= user["next_level_xp"]:
        user["level"] += 1
        user["xp"] = user["xp"] - user["next_level_xp"]
        user["next_level_xp"] = LEVEL_XP_REQUIREMENTS.get(user["level"] + 1, user["next_level_xp"] * 2)

    return {"amount": bonus_amount, "balance": user["balance"], "xp": user["xp"], "level": user["level"], "next_level_xp": user["next_level_xp"]}

@app.post("/api/claim_quick_bonus")
async def claim_quick_bonus(user_data: UserData):
    user_id = user_data.user_id
    if user_id not in users_db:
        raise HTTPException(status_code=404, detail="User not found")

    user = users_db[user_id]
    now = datetime.now()
    
    if user["last_quick_bonus_claim"]:
        last_claim = user["last_quick_bonus_claim"]
        time_since_last_claim = now - last_claim
        if time_since_last_claim < timedelta(minutes=15):
            remaining_time = timedelta(minutes=15) - time_since_last_claim
            minutes, seconds = divmod(remaining_time.total_seconds(), 60)
            raise HTTPException(status_code=400, detail=f"Бонус вже отримано. Спробуйте через {int(minutes)} хв {int(seconds)} сек.")

    bonus_amount = 100
    user["balance"] += bonus_amount
    user["last_quick_bonus_claim"] = now
    user["xp"] += 5 # XP for claiming bonus

    # Level Up Check
    while user["level"] < MAX_LEVEL and user["xp"] >= user["next_level_xp"]:
        user["level"] += 1
        user["xp"] = user["xp"] - user["next_level_xp"]
        user["next_level_xp"] = LEVEL_XP_REQUIREMENTS.get(user["level"] + 1, user["next_level_xp"] * 2)

    return {"amount": bonus_amount, "balance": user["balance"], "xp": user["xp"], "level": user["level"], "next_level_xp": user["next_level_xp"]}


@app.post("/api/get_leaderboard")
async def get_leaderboard():
    # Convert users_db to a list and sort for leaderboard
    leaderboard_entries = []
    for user_id, data in users_db.items():
        leaderboard_entries.append({
            "user_id": user_id,
            "username": data["username"],
            "balance": data["balance"], # Keep balance for sorting purposes
            "xp": data["xp"],
            "level": data["level"]
        })
    
    # Sort by level (descending), then by XP (descending)
    leaderboard_entries.sort(key=lambda x: (x["level"], x["xp"]), reverse=True)
    
    # Return top 100 or fewer if less than 100 users
    return {"leaderboard": leaderboard_entries[:100]}


# --- Blackjack Game Logic (Server-side) ---

class Card:
    def __init__(self, suit: str, rank: str):
        self.suit = suit
        self.rank = rank

    def __str__(self):
        return f"{self.rank}{self.suit}" # e.g., "K♠", "A♥"

    def value(self) -> int:
        if self.rank in ['J', 'Q', 'K']:
            return 10
        elif self.rank == 'A':
            return 11 # Ace can be 1 or 11, handled in Hand
        else:
            return int(self.rank)

class Deck:
    def __init__(self):
        suits = ['♠', '♥', '♦', '♣']
        ranks = ['2', '3', '4', '5', '6', '7', '8', '9', '10', 'J', 'Q', 'K', 'A']
        self.cards: List[Card] = [Card(suit, rank) for suit in suits for rank in ranks]
        random.shuffle(self.cards)

    def deal_card(self) -> Card:
        if not self.cards:
            self.__init__() # Reshuffle if deck is empty
            print("Reshuffling deck!")
        return self.cards.pop()

class Hand:
    def __init__(self):
        self.cards: List[Card] = []
        self.value: int = 0
        self.aces: int = 0

    def add_card(self, card: Card):
        self.cards.append(card)
        self.value += card.value()
        if card.rank == 'A':
            self.aces += 1
        
        # Adjust for Aces
        while self.value > 21 and self.aces:
            self.value -= 10
            self.aces -= 1

    def __str__(self):
        return ", ".join(str(card) for card in self.cards)
    
    def to_json(self, hide_first: bool = False) -> List[str]:
        if hide_first and self.cards:
            return ["Hidden", str(self.cards[1])] + [str(card) for card in self.cards[2:]] # Hide first card, show second as string
        return [str(card) for card in self.cards]


class BlackjackPlayer:
    def __init__(self, user_id: str, username: str, websocket: WebSocket):
        self.user_id = user_id
        self.username = username
        self.websocket = websocket
        self.hand = Hand()
        self.bet = 0
        self.is_ready = False
        self.is_playing = True # True if still in round (not busted/stood)
        self.has_bet = False # Flag to ensure player has bet before starting

    def reset_for_round(self):
        self.hand = Hand()
        self.bet = 0
        self.is_ready = False
        self.is_playing = True
        self.has_bet = False

class BlackjackRoom:
    def __init__(self, room_id: str):
        self.room_id = room_id
        self.players: Dict[str, BlackjackPlayer] = {} # user_id: BlackjackPlayer
        self.status = "waiting" # waiting, starting_timer, betting, playing, dealer_turn, round_end
        self.deck = Deck()
        self.dealer_hand = Hand()
        self.current_turn_index = 0
        self.min_players = 2
        self.max_players = 5
        self.game_start_timer: Optional[asyncio.Task] = None # Stores the asyncio task for the timer
        self.timer_countdown: int = 0 # Current countdown value

    async def add_player(self, user_id: str, username: str, websocket: WebSocket):
        if len(self.players) >= self.max_players:
            return False, "Room is full."
        if user_id in self.players:
            return False, "Player already in room."
        
        player = BlackjackPlayer(user_id, username, websocket)
        self.players[user_id] = player
        
        # Notify existing players
        await self.send_room_state_to_all()
        return True, "Joined room successfully."

    async def remove_player(self, user_id: str):
        if user_id in self.players:
            del self.players[user_id]
            print(f"Player {user_id} removed from room {self.room_id}")
            if not self.players:
                # If room empty, cancel timer and remove room
                if self.game_start_timer and not self.game_start_timer.done():
                    self.game_start_timer.cancel()
                del blackjack_room_manager.rooms[self.room_id]
                print(f"Room {self.room_id} is empty and removed.")
            else:
                # If game was in progress and current player left, advance turn
                if self.status == "playing" and not self.players[user_id].is_playing: # If current player left mid-turn
                     active_players_after_removal = [p for p in self.players.values() if p.is_playing]
                     if not active_players_after_removal: # All players done, go to dealer
                         await self.next_turn()
                     elif self.current_turn_index >= len(active_players_after_removal): # Index out of bounds, reset
                         self.current_turn_index = 0
                     
                await self.send_room_state_to_all()
        else:
            print(f"Player {user_id} not found in room {self.room_id}")

    async def send_room_state_to_all(self):
        state = self.get_current_state()
        for player in self.players.values():
            try:
                # Customize state for each player (e.g., hide other player's hidden cards in poker, but not blackjack)
                player_state = state.copy()
                # If not dealer's turn and dealer has 2 cards, hide the second one for players
                if self.status not in ["dealer_turn", "round_end"] and len(self.dealer_hand.cards) > 1:
                    player_state["dealer_hand"] = [str(self.dealer_hand.cards[0]), "Hidden"]
                    player_state["dealer_score"] = self.dealer_hand.cards[0].value() # Show only first card's value
                else: # Reveal dealer's full hand and score
                    player_state["dealer_hand"] = self.dealer_hand.to_json()
                    player_state["dealer_score"] = self.dealer_hand.value

                await player.websocket.send_json(player_state)
            except Exception as e:
                print(f"Error sending state to {player.user_id}: {e}")
                # Consider disconnecting player if send fails

    def get_current_state(self):
        players_data = []
        for p_id, p in self.players.items():
            players_data.append({
                "user_id": p.user_id,
                "username": p.username,
                "hand": p.hand.to_json(),
                "score": p.hand.value,
                "bet": p.bet,
                "is_ready": p.is_ready,
                "is_playing": p.is_playing,
                "has_bet": p.has_bet
            })
        
        # Determine current player's user_id
        current_player_id = None
        if self.status == "playing":
            active_players = [p for p in self.players.values() if p.is_playing]
            if active_players:
                current_player_id = active_players[self.current_turn_index % len(active_players)].user_id


        return {
            "room_id": self.room_id,
            "status": self.status,
            "dealer_hand": [], # Placeholder, will be filled in send_room_state_to_all based on visibility
            "dealer_score": 0, # Placeholder
            "players": players_data,
            "current_player_turn": current_player_id,
            "player_count": len(self.players),
            "min_players": self.min_players,
            "max_players": self.max_players,
            "timer": self.timer_countdown # Send current timer value
        }

    async def handle_bet(self, user_id: str, amount: int):
        player = self.players.get(user_id)
        if not player:
            return

        if self.status != "betting":
            await player.websocket.send_json({"type": "error", "message": "Ставки приймаються лише на етапі 'betting'."})
            return

        user_in_db = users_db.get(user_id)
        if not user_in_db or user_in_db["balance"] < amount:
            await player.websocket.send_json({"type": "error", "message": "Недостатньо фантиків для ставки."})
            return
        if amount <= 0:
            await player.websocket.send_json({"type": "error", "message": "Ставка має бути позитивним числом."})
            return
        if player.has_bet:
            await player.websocket.send_json({"type": "error", "message": "Ви вже зробили ставку в цьому раунді."})
            return

        player.bet = amount
        user_in_db["balance"] -= amount # Deduct bet from balance
        player.has_bet = True
        print(f"Player {user_id} bet {amount}")

        # Check if all players have bet that are connected
        all_bet = all(p.has_bet for p in self.players.values())
        if all_bet and len(self.players) >= self.min_players:
            self.status = "playing"
            await self.start_round()
        else:
            await self.send_room_state_to_all() # Update all clients to show who has bet


    async def handle_action(self, user_id: str, action: str):
        player = self.players.get(user_id)
        if not player or not player.is_playing:
            await player.websocket.send_json({"type": "error", "message": "Зараз не ваш хід або ви не граєте."})
            return
        
        current_player = self.get_current_player()
        if not current_player or player.user_id != current_player.user_id:
             await player.websocket.send_json({"type": "error", "message": "Зараз хід іншого гравця."})
             return

        if action == "hit":
            player.hand.add_card(self.deck.deal_card())
            await self.send_room_state_to_all() # Send update after card is added
            if player.hand.value > 21:
                player.is_playing = False # Busted
                await player.websocket.send_json({"type": "game_message", "message": "Ви перебрали! 💥"})
                await asyncio.sleep(1) # Small delay for message to be seen
                await self.next_turn()
            # If not busted, player can still hit, so don't advance turn yet
        elif action == "stand":
            player.is_playing = False
            await player.websocket.send_json({"type": "game_message", "message": "Ви зупинились."})
            await asyncio.sleep(0.5)
            await self.next_turn()
        else:
            await player.websocket.send_json({"type": "error", "message": "Невідома дія."})

    def get_current_player(self) -> Optional[BlackjackPlayer]:
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players:
            return None
        # Ensure current_turn_index wraps around
        return active_players[self.current_turn_index % len(active_players)]

    async def next_turn(self):
        self.current_turn_index += 1
        active_players = [p for p in self.players.values() if p.is_playing]

        if not active_players: # All players finished their turns (stood or busted), dealer's turn
            self.status = "dealer_turn"
            await self.send_room_state_to_all() # Show dealer's second card
            await asyncio.sleep(1) # Pause before dealer plays
            await self.dealer_play()
        else:
            await self.send_room_state_to_all() # Update whose turn it is

    async def start_round(self):
        print(f"Room {self.room_id}: Starting new round.")
        self.deck = Deck() # New shuffled deck for each round
        self.dealer_hand = Hand()
        self.current_turn_index = 0 # Reset turn index for new round
        
        # Reset players and deal initial cards
        for player in self.players.values():
            player.reset_for_round() # Reset player state from previous round
            player.hand.add_card(self.deck.deal_card()) # First card
            player.hand.add_card(self.deck.deal_card()) # Second card
        
        self.dealer_hand.add_card(self.deck.deal_card()) # Dealer's first card (visible)
        self.dealer_hand.add_card(self.deck.deal_card()) # Dealer's second card (hidden initially)

        self.status = "playing"
        await self.send_room_state_to_all() # Send initial hands

        # Check for immediate Blackjacks and process players who get it
        for player in self.players.values():
            if player.hand.value == 21 and len(player.hand.cards) == 2:
                player.is_playing = False # Player has Blackjack, no more turns
                await player.websocket.send_json({"type": "game_message", "message": "У вас Блекджек! 🎉"})
                await asyncio.sleep(1) # Small delay for message to be seen

        # If any players are still active, start their turns
        active_players_after_blackjack_check = [p for p in self.players.values() if p.is_playing]
        if not active_players_after_blackjack_check:
            # Everyone got blackjack or busted already, proceed to dealer
            await self.next_turn() 
        else:
            # Ensure the current_turn_index points to the first active player
            # It's already 0, but if first players had blackjack, get_current_player() will skip them.
            await self.send_room_state_to_all() 


    async def dealer_play(self):
        print(f"Room {self.room_id}: Dealer's turn.")
        self.status = "dealer_turn"
        await self.send_room_state_to_all() # Reveal dealer's second card

        await asyncio.sleep(1) # Pause for players to see dealer's revealed card

        while self.dealer_hand.value < 17:
            self.dealer_hand.add_card(self.deck.deal_card())
            await self.send_room_state_to_all()
            await asyncio.sleep(1) # Slow down dealer's play

        await self.end_round()

    async def end_round(self):
        print(f"Room {self.room_id}: Ending round.")
        self.status = "round_end"
        dealer_score = self.dealer_hand.value

        for player in self.players.values():
            user_in_db = users_db.get(player.user_id)
            if not user_in_db: continue

            player_score = player.hand.value
            winnings = 0
            message = ""
            xp_gain = 0

            # Determine outcomes
            if player_score > 21: # Player busted (already handled, but safety check)
                message = "Ви перебрали! Програш."
                xp_gain = 1 # Small XP for playing
            elif dealer_score > 21: # Dealer busted
                winnings = player.bet * 2
                message = "Дилер перебрав! Ви виграли!"
                xp_gain = 10
            elif player_score > dealer_score:
                winnings = player.bet * 2
                message = "Ви виграли!"
                xp_gain = 10
            elif player_score < dealer_score:
                message = "Ви програли."
                xp_gain = 1 # Small XP for playing
            else: # Push
                winnings = player.bet # Return bet
                message = "Нічия!"
                xp_gain = 2 # Some XP for push

            user_in_db["balance"] += winnings
            user_in_db["xp"] += xp_gain

            # Level Up Check
            while user_in_db["level"] < MAX_LEVEL and user_in_db["xp"] >= user_in_db["next_level_xp"]:
                user_in_db["level"] += 1
                user_in_db["xp"] = user_in_db["xp"] - user_in_db["next_level_xp"]
                user_in_db["next_level_xp"] = LEVEL_XP_REQUIREMENTS.get(user_in_db["level"] + 1, user_in_db["next_level_xp"] * 2)
                await player.websocket.send_json({"type": "level_up", "level": user_in_db["level"]})

            await player.websocket.send_json({
                "type": "round_result",
                "message": message,
                "winnings": winnings,
                "balance": user_in_db["balance"],
                "xp": user_in_db["xp"],
                "level": user_in_db["level"],
                "next_level_xp": user_in_db["next_level_xp"],
                "final_player_score": player_score,
                "final_dealer_score": dealer_score # Send final dealer score for clear display
            })

            player.reset_for_round() # Reset player for next round

        # After all players are processed, set status back to waiting for next round
        # Or transition to betting phase if we want to immediately start next round
        self.status = "waiting" # Go back to waiting for next game start
        self.dealer_hand = Hand() # Clear dealer hand
        await self.send_room_state_to_all() # Send reset state
        await asyncio.sleep(2) # Pause before moving to betting phase
        self.status = "betting" # Immediately allow betting for next round
        await self.send_room_state_to_all() # Tell clients to open betting UI


class BlackjackRoomManager:
    def __init__(self):
        self.rooms: Dict[str, BlackjackRoom] = {} # room_id: BlackjackRoom

    async def create_or_join_room(self, user_id: str, username: str, websocket: WebSocket):
        # Try to find an existing 'waiting' or 'betting' room that's not full
        for room_id, room in self.rooms.items():
            if room.status in ["waiting", "betting"] and len(room.players) < room.max_players:
                success, msg = await room.add_player(user_id, username, websocket)
                if success:
                    print(f"Player {user_id} joined existing room {room_id}. Current players: {len(room.players)}")
                    # If minimum players reached, start timer
                    if len(room.players) >= room.min_players and room.status == "waiting":
                        room.status = "starting_timer"
                        # Cancel existing timer if any, and start a new one
                        if room.game_start_timer and not room.game_start_timer.done():
                            room.game_start_timer.cancel()
                        room.timer_countdown = 20 # Set initial timer value for client
                        room.game_start_timer = asyncio.create_task(self._start_game_after_delay(room_id, 20))
                        print(f"Room {room_id}: Game start timer initiated for 20 seconds.")
                    await room.send_room_state_to_all() # Send initial state on join/create
                    return room_id
        
        # No suitable room found, create a new one
        new_room_id = str(uuid.uuid4())[:8] # Short unique ID
        new_room = BlackjackRoom(new_room_id)
        self.rooms[new_room_id] = new_room
        success, msg = await new_room.add_player(user_id, username, websocket)
        if success:
            print(f"Player {user_id} created and joined new room {new_room_id}")
            await new_room.send_room_state_to_all()
            return new_room_id
        return None # Should not happen if add_player succeeds

    async def _start_game_after_delay(self, room_id: str, delay: int):
        room = self.rooms.get(room_id)
        if not room:
            return

        for i in range(delay, 0, -1):
            room.timer_countdown = i # Update timer countdown in room state
            if room.status != "starting_timer" or len(room.players) < room.min_players:
                print(f"Room {room_id} timer cancelled/interrupted.")
                # If player count drops below minimum, reset status
                if len(room.players) < room.min_players:
                    room.status = "waiting"
                room.timer_countdown = 0 # Reset timer on cancel
                await room.send_room_state_to_all() # Inform clients about status change
                return
            await room.send_room_state_to_all() # Send state to update timer countdown on clients
            await asyncio.sleep(1)
        
        if room.status == "starting_timer" and len(room.players) >= room.min_players:
            print(f"Room {room_id}: Timer finished, moving to betting phase.")
            room.status = "betting" # Transition to betting phase
            room.timer_countdown = 0 # Reset timer
            await room.send_room_state_to_all()


blackjack_room_manager = BlackjackRoomManager()


# --- WebSocket Endpoint ---
# This endpoint handles real-time game interactions for Blackjack.
@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    # Retrieve user's username. This should be consistent with how users are handled elsewhere.
    username = users_db.get(user_id, {}).get("username", f"Гравець {user_id[-4:]}")
    
    room_id = await blackjack_room_manager.create_or_join_room(user_id, username, websocket)
    if not room_id:
        await websocket.close(code=1008, reason="Could not join/create room.")
        return

    try:
        # Initial state is already sent by create_or_join_room
        while True:
            data = await websocket.receive_text()
            try:
                message = json.loads(data)
                action = message.get("action")
                
                room = blackjack_room_manager.rooms.get(room_id)
                if not room:
                    await websocket.send_json({"type": "error", "message": "Кімната не знайдена."})
                    continue

                if action == "bet":
                    amount = message.get("amount")
                    if amount is not None:
                        await room.handle_bet(user_id, amount)
                    else:
                        await websocket.send_json({"type": "error", "message": "Сума ставки не вказана."})
                elif action in ["hit", "stand"]:
                    await room.handle_action(user_id, action)
                elif action == "request_state": # For new players to get current state
                    await room.send_room_state_to_all() # Will send to all including requester
                else:
                    await websocket.send_json({"type": "error", "message": "Невідома дія."})

            except json.JSONDecodeError:
                print(f"Received non-JSON message from {user_id}: {data}")
                await websocket.send_json({"type": "error", "message": "Неправильний формат повідомлення (очікується JSON)."})
            except Exception as e:
                print(f"Error handling WebSocket message from {user_id}: {e}")
                await websocket.send_json({"type": "error", "message": f"Помилка сервера: {str(e)}"})

    except WebSocketDisconnect:
        print(f"Client {user_id} disconnected from room {room_id}.")
        room = blackjack_room_manager.rooms.get(room_id)
        if room:
            await room.remove_player(user_id)
            # If players remain, notify them
            if room.players:
                await room.send_room_state_to_all()
        
    except Exception as e:
        print(f"Unexpected error in WebSocket endpoint for {user_id}: {e}")
        # Consider more robust error handling / logging
        
# --- Serve the main HTML file ---
# This serves your React frontend (index.html)
@app.get("/")
async def get_root():
    # Read index.html content
    index_html_path = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(index_html_path):
        raise HTTPException(status_code=404, detail="index.html not found")
    
    with open(index_html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    return HTMLResponse(content=html_content)

# --- Telegram Webhook Endpoint ---
# This endpoint receives updates from Telegram and passes them to aiogram dispatcher.
@app.post(WEBHOOK_PATH)
async def bot_webhook(request: Request):
    update_json = await request.json()
    update = types.Update.model_validate(update_json, context={"bot": bot})
    await dp.feed_update(update)
    return {"ok": True}

# --- On startup: set webhook for Telegram Bot ---
# This function runs when your FastAPI application starts.
# It sets the Telegram webhook URL so Telegram knows where to send updates.
@app.on_event("startup")
async def on_startup():
    # Render automatically sets RENDER_EXTERNAL_HOSTNAME to your service's external URL.
    # We use this to construct the webhook URL.
    external_hostname = os.getenv("RENDER_EXTERNAL_HOSTNAME")
    if not external_hostname:
        print("WARN: RENDER_EXTERNAL_HOSTNAME environment variable not set. Assuming localhost for webhook setup.")
        external_hostname = "http://localhost:8000" # Fallback for local testing

    global WEBHOOK_URL
    WEBHOOK_URL = f"{external_hostname}{WEBHOOK_PATH}"

    # Set webhook
    try:
        webhook_info = await bot.get_webhook_info()
        if webhook_info.url != WEBHOOK_URL:
            await bot.set_webhook(WEBHOOK_URL, drop_pending_updates=True) # drop_pending_updates=True clears old updates
            print(f"Telegram webhook set to: {WEBHOOK_URL}")
        else:
            print(f"Telegram webhook already set to: {WEBHOOK_URL}")
    except Exception as e:
        print(f"ERROR: Failed to set Telegram webhook: {e}")
        if BOT_TOKEN == "DUMMY_TOKEN":
            print("Hint: Is BOT_TOKEN correctly set as an environment variable?")

@app.on_event("shutdown")
async def on_shutdown():
    # Delete webhook and close bot session when the app shuts down.
    try:
        await bot.delete_webhook()
        print("Telegram webhook deleted.")
    except Exception as e:
        print(f"ERROR: Failed to delete Telegram webhook on shutdown: {e}")
    finally:
        await dp.storage.close() # Close dispatcher storage if used
        await bot.session.close() # Close aiohttp session
        print("Bot session closed.")

