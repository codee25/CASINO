from fastapi import FastAPI, WebSocket, WebSocketDisconnect, HTTPException
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import json
import os
import random
import asyncio
from datetime import datetime, timedelta

app = FastAPI()

# --- Config and Setup ---
# In a real app, use environment variables for secrets and better configuration
# For Render, we expect to be running in the /opt/render/project/src directory
WEBAPP_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "webapp")

# Mount static files for your web app
app.mount("/static", StaticFiles(directory=WEBAPP_DIR), name="static")

# Mock database (in a real application, use a proper database like PostgreSQL, MongoDB, or Firestore)
# Using a simple dictionary for demonstration purposes. This will reset on server restart.
users_db = {} # user_id: {username, balance, xp, level, next_level_xp, last_daily_bonus_claim, last_quick_bonus_claim}

# Default XP progression
LEVEL_XP_REQUIREMENTS = {
    1: 0,
    2: 100,
    3: 300,
    4: 600,
    5: 1000,
    6: 1500,
    7: 2100,
    8: 2800,
    9: 3600,
    10: 4500
}
MAX_LEVEL = max(LEVEL_XP_REQUIREMENTS.keys())

# --- User Management API Endpoints ---
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
    amount: int = None # For 'bet' action

@app.post("/api/get_balance")
async def get_balance(user_data: UserData):
    user_id = user_data.user_id
    username = user_data.username

    if user_id not in users_db:
        # Initialize new user with default values
        users_db[user_id] = {
            "username": username,
            "balance": 10000, # Starting bonus
            "xp": 0,
            "level": 1,
            "next_level_xp": LEVEL_XP_REQUIREMENTS.get(2, 100),
            "last_daily_bonus_claim": None,
            "last_quick_bonus_claim": None,
        }
        print(f"New user initialized: {user_id} - {username}")
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
    symbols = [random.choice(['🍒', '🍋', '�', '🍇', '🔔', '💎', '🍀', '⭐', '💰']) for _ in range(3)]

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
            "balance": data["balance"],
            "xp": data["xp"],
            "level": data["level"]
        })
    
    # Sort by level (descending), then by XP (descending)
    leaderboard_entries.sort(key=lambda x: (x["level"], x["xp"]), reverse=True)
    
    # Return top 100 or fewer if less than 100 users
    return {"leaderboard": leaderboard_entries[:100]}


# --- Blackjack Game Logic (Server-side) ---

class Card:
    def __init__(self, suit, rank):
        self.suit = suit
        self.rank = rank

    def __str__(self):
        return f"{self.rank}{self.suit}" # e.g., "K♠", "A♥"

    def value(self):
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
        self.cards = [Card(suit, rank) for suit in suits for rank in ranks]
        random.shuffle(self.cards)

    def deal_card(self):
        if not self.cards:
            self.__init__() # Reshuffle if deck is empty
            print("Reshuffling deck!")
        return self.cards.pop()

class Hand:
    def __init__(self):
        self.cards = []
        self.value = 0
        self.aces = 0

    def add_card(self, card):
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
    
    def to_json(self, hide_first=False):
        if hide_first and self.cards:
            return [str(self.cards[0]), "Hidden"] + [str(card) for card in self.cards[1:]]
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
        self.status = "waiting" # waiting, betting, playing, dealer_turn, round_end
        self.deck = Deck()
        self.dealer_hand = Hand()
        self.current_turn_index = 0
        self.min_players = 2
        self.max_players = 5
        self.game_start_timer: asyncio.Task = None # Stores the asyncio task for the timer

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
                await self.send_room_state_to_all()
        else:
            print(f"Player {user_id} not found in room {self.room_id}")

    async def send_room_state_to_all(self):
        state = self.get_current_state()
        for player in self.players.values():
            try:
                # Customize state for each player (e.g., hide other player's hidden cards in poker, but not blackjack)
                # For Blackjack, dealer's hidden card is the main thing to control
                player_state = state.copy()
                if self.status in ["betting", "waiting", "starting_timer"]:
                     # Dealer's hand should be hidden before actual play
                    player_state["dealer_hand"] = [str(self.dealer_hand.cards[0]), "Hidden"] if len(self.dealer_hand.cards) > 1 else self.dealer_hand.to_json()
                    player_state["dealer_score"] = self.dealer_hand.cards[0].value() if len(self.dealer_hand.cards) > 1 else self.dealer_hand.value
                
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
            "dealer_hand": self.dealer_hand.to_json(hide_first=(self.status in ["betting", "waiting", "starting_timer"] and len(self.dealer_hand.cards) > 1)),
            "dealer_score": self.dealer_hand.cards[0].value() if (self.status in ["betting", "waiting", "starting_timer"] and len(self.dealer_hand.cards) > 1) else self.dealer_hand.value,
            "players": players_data,
            "current_player_turn": current_player_id,
            "player_count": len(self.players),
            "min_players": self.min_players,
            "max_players": self.max_players,
        }

    async def handle_bet(self, user_id: str, amount: int):
        if self.status != "betting":
            await self.players[user_id].websocket.send_json({"type": "error", "message": "Ставки приймаються лише на етапі 'betting'."})
            return

        player = self.players.get(user_id)
        if not player:
            return

        user_in_db = users_db.get(user_id)
        if not user_in_db or user_in_db["balance"] < amount:
            await player.websocket.send_json({"type": "error", "message": "Недостатньо фантиків для ставки."})
            return
        if amount <= 0:
            await player.websocket.send_json({"type": "error", "message": "Ставка має бути позитивним числом."})
            return

        player.bet = amount
        user_in_db["balance"] -= amount
        player.has_bet = True
        print(f"Player {user_id} bet {amount}")

        # Check if all players have bet
        all_bet = all(p.has_bet for p in self.players.values())
        if all_bet and len(self.players) >= self.min_players:
            self.status = "playing"
            await self.start_round()
        else:
            await self.send_room_state_to_all()


    async def handle_action(self, user_id: str, action: str):
        player = self.players.get(user_id)
        if not player or player.user_id != self.get_current_player().user_id or not player.is_playing:
            await player.websocket.send_json({"type": "error", "message": "Зараз не ваш хід або ви не граєте."})
            return

        if action == "hit":
            player.hand.add_card(self.deck.deal_card())
            if player.hand.value > 21:
                player.is_playing = False # Busted
                await self.send_room_state_to_all()
                await player.websocket.send_json({"type": "game_message", "message": "Ви перебрали! 💥"})
                await asyncio.sleep(1) # Small delay for message to be seen
                await self.next_turn()
            else:
                await self.send_room_state_to_all() # Update all players with new card
        elif action == "stand":
            player.is_playing = False
            await player.websocket.send_json({"type": "game_message", "message": "Ви зупинились."})
            await asyncio.sleep(0.5)
            await self.next_turn()
        else:
            await player.websocket.send_json({"type": "error", "message": "Невідома дія."})

    def get_current_player(self):
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players:
            return None
        return active_players[self.current_turn_index % len(active_players)]

    async def next_turn(self):
        self.current_turn_index += 1
        active_players = [p for p in self.players.values() if p.is_playing]

        if not active_players: # All players finished, dealer's turn
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
        for player in self.players.values():
            player.reset_for_round()
            player.hand.add_card(self.deck.deal_card()) # First card
            player.hand.add_card(self.deck.deal_card()) # Second card
        
        self.dealer_hand.add_card(self.deck.deal_card()) # Dealer's first card (visible)
        self.dealer_hand.add_card(self.deck.deal_card()) # Dealer's second card (hidden initially)

        self.status = "playing"
        self.current_turn_index = 0 # Reset turn index for new round
        await self.send_room_state_to_all() # Send initial hands

        # Check for immediate Blackjack
        for player in self.players.values():
            if player.hand.value == 21 and len(player.hand.cards) == 2:
                player.is_playing = False
                await player.websocket.send_json({"type": "game_message", "message": "Блекджек! 🎉"})
                await asyncio.sleep(1)
        
        # If any players are still active, start their turns
        active_players = [p for p in self.players.values() if p.is_playing]
        if not active_players:
            # Everyone got blackjack or busted already, proceed to dealer
            await self.next_turn() 
        else:
             await self.send_room_state_to_all() # Ensure current_player_turn is set correctly

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
        self.status = "waiting" # Or "betting" to immediately start betting
        self.dealer_hand = Hand() # Clear dealer hand
        await self.send_room_state_to_all() # Send reset state
        await asyncio.sleep(2) # Pause before moving to betting phase
        self.status = "betting"
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
                        await room.send_room_state_to_all() # Update UI with timer status
                        # Cancel existing timer if any, and start a new one
                        if room.game_start_timer and not room.game_start_timer.done():
                            room.game_start_timer.cancel()
                        room.game_start_timer = asyncio.create_task(self._start_game_after_delay(room_id, 20))
                        print(f"Room {room_id}: Game start timer initiated for 20 seconds.")
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
            if room.status != "starting_timer" or len(room.players) < room.min_players:
                print(f"Room {room_id} timer cancelled/interrupted.")
                # If player count drops below minimum, reset status
                if len(room.players) < room.min_players:
                    room.status = "waiting"
                await room.send_room_state_to_all() # Inform clients about status change
                return
            await room.send_room_state_to_all() # Send state to update timer countdown on clients
            await asyncio.sleep(1)
        
        if room.status == "starting_timer" and len(room.players) >= room.min_players:
            print(f"Room {room_id}: Timer finished, starting game.")
            room.status = "betting" # Transition to betting phase
            await room.send_room_state_to_all()
            # No need to call start_round here, betting phase starts, then betting leads to start_round


blackjack_room_manager = BlackjackRoomManager()


# --- WebSocket Endpoint ---

class WebSocketMessage(BaseModel):
    action: str
    amount: int = None # For 'bet'
    # Other fields as needed for game actions

@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: str):
    # Retrieve user's username (e.g., from users_db or initDataUnsafe if available)
    # For now, let's assume it's passed or defaults. In a real app, you'd verify auth.
    username = users_db.get(user_id, {}).get("username", f"Гравець {user_id[-4:]}")
    
    room_id = await blackjack_room_manager.create_or_join_room(user_id, username, websocket)
    if not room_id:
        await websocket.close(code=1008, reason="Could not join/create room.")
        return

    try:
        # Send initial state to the newly connected player
        room = blackjack_room_manager.rooms.get(room_id)
        if room:
            await room.send_room_state_to_all() # Broadcast to all after a new player joins/creates

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
                elif action == "ready":
                    player = room.players.get(user_id)
                    if player:
                        player.is_ready = True
                        await room.send_room_state_to_all()
                        # If all players are ready and min players, start betting phase
                        if all(p.is_ready for p in room.players.values()) and len(room.players) >= room.min_players and room.status == "waiting":
                             if room.game_start_timer and not room.game_start_timer.done(): # Cancel existing if any
                                 room.game_start_timer.cancel()
                             room.status = "starting_timer"
                             room.game_start_timer = asyncio.create_task(blackjack_room_manager._start_game_after_delay(room.room_id, 20))
                             await room.send_room_state_to_all() # Update UI to show timer
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
@app.get("/")
async def get_root():
    # Read index.html content
    index_html_path = os.path.join(WEBAPP_DIR, "index.html")
    if not os.path.exists(index_html_path):
        raise HTTPException(status_code=404, detail="index.html not found")
    
    with open(index_html_path, "r", encoding="utf-8") as f:
        html_content = f.read()
    
    return HTMLResponse(content=html_content)