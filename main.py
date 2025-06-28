import logging
import os
import json
import random
import urllib.parse
from datetime import datetime, timedelta, timezone

import psycopg2
from psycopg2 import sql

from aiogram import Bot, Dispatcher
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton, Message
from aiogram.filters import CommandStart, Command
from aiogram.webhook.aiohttp_server import SimpleRequestHandler

from aiohttp.web import Application, json_response, Request
import aiohttp_cors

# --- Налаштування логування ---
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# --- Змінні середовища ---
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
except ValueError:
    logger.error(f"Invalid ADMIN_ID provided: '{ADMIN_ID_STR}'. It must be an integer.")

# --- Налаштування бази даних PostgreSQL ---
DATABASE_URL = os.getenv('DATABASE_URL')

bot = Bot(token=API_TOKEN)
dp = Dispatcher()

# --- Конфігурація гри (збігається з JS фронтендом) ---
SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '�']
WILD_SYMBOL = '⭐'
SCATTER_SYMBOL = '💰'
ALL_REEL_SYMBOLS = SYMBOLS + [WILD_SYMBOL, SCATTER_SYMBOL]

BET_AMOUNT = 100
FREE_COINS_AMOUNT = 500 # Кількість фантиків для /get_coins
COOLDOWN_HOURS = 24 # Затримка в годинах для /get_coins

# XP та Рівні
XP_PER_SPIN = 10
XP_PER_WIN_MULTIPLIER = 2 
LEVEL_THRESHOLDS = [
    0,    # Level 1: 0 XP
    100,  # Level 2: 100 XP
    300,  # Level 3: 300 XP
    600,  # Level 4: 600 XP
    1000, # Level 5: 1000 XP
    1500, # Level 6: 1500 XP
    2200, # Level 7: 2200 XP
    3000, # Level 8: 3000 XP
    4000, # Level 9: 4000 XP
    5500, # Level 10: 5500 XP
    7500, # Level 11: 7500 XP
    10000 # Level 12: 10000 XP (and beyond)
]

def get_level_from_xp(xp):
    """Визначає рівень користувача на основі досвіду."""
    for i, threshold in enumerate(LEVEL_THRESHOLDS):
        if xp < threshold:
            return i + 1 # Рівні починаються з 1, індекси з 0
    return len(LEVEL_THRESHOLDS) # Максимальний рівень, якщо XP перевищує всі пороги

def get_xp_for_next_level(level):
    """Повертає XP, необхідний для наступного рівня (або для поточного, якщо це останній)."""
    if level < len(LEVEL_THRESHOLDS):
        return LEVEL_THRESHOLDS[level] # Порог для наступного рівня
    return LEVEL_THRESHOLDS[-1] # Якщо максимальний рівень, повертає останній поріг

PAYOUTS = {
    # Три однакові (включаючи Wild, що діє як замінник)
    ('🍒', '🍒', '🍒'): 1000,
    ('🍋', '🍋', '🍋'): 800,
    ('🍊', '🍊', '🍊'): 600,
    ('🍇', '🍇', '🍇'): 400,
    ('🔔', '🔔', '🔔'): 300,
    ('💎', '💎', '💎'): 200,
    ('🍀', '🍀', '🍀'): 150,
    ('⭐', '⭐', '⭐'): 2000, # Високий виграш за три Wild
    
    # Дві однакові (включаючи Wild) - WILD може бути другим або третім
    ('🍒', '🍒'): 100,
    ('🍋', '🍋'): 80,

    # Scatter виграші (не залежать від позиції)
    ('💰', '💰'): 200, # За 2 Scatter
    ('💰', '💰', '💰'): 500, # За 3 Scatter
}

# --- Функції для роботи з базою даних ---

def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL за URL."""
    conn = None
    try:
        url = urllib.parse.urlparse(DATABASE_URL)
        conn = psycopg2.connect(
            database=url.path[1:],
            user=url.username,
            password=url.password,
            host=url.hostname,
            port=url.port,
            sslmode='require',
            # Важливо для довготривалих з'єднань
            keepalives=1, 
            keepalives_idle=30,
            keepalives_interval=10,
            keepalives_count=5
        )
        return conn
    except psycopg2.Error as err:
        logger.error(f"DB connection error: {err}")
        raise
    except Exception as e:
        logger.error(f"Unexpected error during DB connection: {e}")
        raise

def init_db():
    """Ініціалізує таблиці та виконує міграції для бази даних PostgreSQL."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        # 1. Створення таблиці users, якщо вона не існує (без last_daily_bonus_claim)
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                user_id BIGINT PRIMARY KEY,
                balance INTEGER DEFAULT 1000,
                xp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL
            )
        ''')
        conn.commit()
        logger.info("Table 'users' initialized or already exists.")

        # 2. Міграції для додавання стовпців, якщо вони вже існують у старих версіях
        migrations = [
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS xp INTEGER DEFAULT 0;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS level INTEGER DEFAULT 1;",
            "ALTER TABLE users ADD COLUMN IF NOT EXISTS last_free_coins_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL;"
            # ALTER TABLE users ADD COLUMN IF NOT EXISTS last_daily_bonus_claim TIMESTAMP WITH TIME ZONE DEFAULT NULL; - ВИДАЛЕНО
        ]

        for mig_sql in migrations:
            try:
                cursor.execute(mig_sql)
                conn.commit()
                logger.info(f"Migration applied: {mig_sql.strip()}")
            except psycopg2.ProgrammingError as e:
                logger.warning(f"Migration failed (might already exist or specific DB error): {e} -> {mig_sql}")
                conn.rollback()

        # Додатково: видалити стовпець last_daily_bonus_claim, якщо він існує (якщо був доданий раніше)
        try:
            cursor.execute('''
                ALTER TABLE users DROP COLUMN IF EXISTS last_daily_bonus_claim;
            ''')
            conn.commit()
            logger.info("Column 'last_daily_bonus_claim' removed if existed.")
        except psycopg2.ProgrammingError as e:
            logger.warning(f"Failed to drop column 'last_daily_bonus_claim': {e}")
            conn.rollback()


        logger.info("DB schema migration checked.")

    except Exception as e:
        logger.error(f"DB init/migration error: {e}")
    finally:
        if conn:
            conn.close()

def get_user_data(user_id):
    """Отримує всі дані користувача (баланс, XP, рівень, час останніх бонусів) з БД."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            'SELECT balance, xp, level, last_free_coins_claim FROM users WHERE user_id = %s', 
            (user_id,)
        )
        result = cursor.fetchone()
        if result:
            return {
                'balance': result[0],
                'xp': result[1],
                'level': result[2],
                'last_free_coins_claim': result[3]
            }
        else:
            # Якщо користувача немає, створити його з початковими значеннями
            cursor.execute(
                'INSERT INTO users (user_id, balance, xp, level, last_free_coins_claim) VALUES (%s, %s, %s, %s, %s)', 
                (user_id, 1000, 0, 1, None)
            )
            conn.commit()
            return {
                'balance': 1000,
                'xp': 0,
                'level': 1,
                'last_free_coins_claim': None
            }
    except Exception as e:
        logger.error(f"Error getting user data from PostgreSQL for {user_id}: {e}")
        return {'balance': 0, 'xp': 0, 'level': 1, 'last_free_coins_claim': None}
    finally:
        if conn:
            conn.close()

def update_user_data(user_id, balance=None, xp=None, level=None, last_free_coins_claim=None):
    """Оновлює дані користувача в базі даних PostgreSQL. Приймає АБСОЛЮТНІ ЗНАЧЕННЯ."""
    conn = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        update_fields = []
        update_values = []

        # Отримуємо поточні дані, щоб зберегти ті, які не оновлюються
        current_data_from_db = get_user_data(user_id) # Отримуємо актуальні дані з бази

        if balance is not None:
            update_fields.append("balance = %s")
            update_values.append(balance)
        else:
            update_values.append(current_data_from_db['balance']) # Якщо не надано, використовуємо поточне з БД

        if xp is not None:
            update_fields.append("xp = %s")
            update_values.append(xp)
        else:
            update_values.append(current_data_from_db['xp'])

        if level is not None:
            update_fields.append("level = %s")
            update_values.append(level)
        else:
            update_values.append(current_data_from_db['level'])

        if last_free_coins_claim is not None:
            update_fields.append("last_free_coins_claim = %s")
            update_values.append(last_free_coins_claim)
        else:
            update_values.append(current_data_from_db['last_free_coins_claim'])

        if not update_fields: # Якщо немає полів для оновлення, нічого не робимо
            logger.info(f"No fields to update for user {user_id}.")
            return

        update_query = sql.SQL('''
            UPDATE users SET {fields} WHERE user_id = %s
        ''').format(fields=sql.SQL(', ').join(map(sql.SQL, update_fields)))

        update_values.append(user_id) # Додаємо user_id в кінець для WHERE

        cursor.execute(update_query, update_values)
        conn.commit()
        logger.info(f"User {user_id} data updated.")
    except Exception as e:
        logger.error(f"Error updating user data in PostgreSQL for {user_id}: {e}")
    finally:
        if conn:
            conn.close()


def check_win_conditions(symbols):
    """Перевіряє виграшні комбінації, враховуючи Wild та Scatter."""
    winnings = 0
    s1, s2, s3 = symbols

    # --- Перевірка Scatter виграшів (пріоритет над іншими) ---
    scatter_count = symbols.count(SCATTER_SYMBOL)
    if scatter_count >= 2:
        return PAYOUTS.get(tuple([SCATTER_SYMBOL] * scatter_count), 0)

    # --- Перевірка виграшів для 3 однакових символів (з Wild) ---
    # Спочатку перевіряємо чи всі символи однакові або Wild
    if (s1 == s2 == s3) or \
       (s1 == WILD_SYMBOL and s2 == s3) or \
       (s2 == WILD_SYMBOL and s1 == s3) or \
       (s3 == WILD_SYMBOL and s1 == s2) or \
       (s1 == WILD_SYMBOL and s2 == WILD_SYMBOL and s3 in SYMBOLS) or \
       (s1 == WILD_SYMBOL and s3 == WILD_SYMBOL and s2 in SYMBOLS) or \
       (s2 == WILD_SYMBOL and s3 == WILD_SYMBOL and s1 in SYMBOLS) or \
       (s1 == WILD_SYMBOL and s2 == WILD_SYMBOL and s3 == WILD_SYMBOL):
       
        # Визначаємо "основний" символ, який Wild замінив, або сам Wild
        effective_symbol = ''
        if s1 != WILD_SYMBOL and s1 != SCATTER_SYMBOL: effective_symbol = s1
        elif s2 != WILD_SYMBOL and s2 != SCATTER_SYMBOL: effective_symbol = s2
        elif s3 != WILD_SYMBOL and s3 != SCATTER_SYMBOL: effective_symbol = s3
        else: effective_symbol = WILD_SYMBOL # Якщо всі Wild, то виграш за Wild

        return PAYOUTS.get((effective_symbol, effective_symbol, effective_symbol), 0)

    # --- Перевірка виграшів для 2 однакових символів (лише перші два, з Wild) ---
    # Це спрощена перевірка, яка враховує лише s1 та s2.
    # Якщо потрібно складнішу логіку (s2,s3 або s1,s3), її потрібно додати окремо.
    for main_symbol in SYMBOLS:
        if ((s1 == main_symbol or s1 == WILD_SYMBOL) and 
            (s2 == main_symbol or s2 == WILD_SYMBOL) and
            (s3 != main_symbol and s3 != WILD_SYMBOL and s3 != SCATTER_SYMBOL)): # Третій символ НЕ має бути частиною виграшу
            return PAYOUTS.get((main_symbol, main_symbol), 0)
    
    return winnings


def spin_slot(user_id):
    user_data = get_user_data(user_id)
    current_balance = user_data['balance']
    current_xp = user_data['xp']

    if current_balance < BET_AMOUNT:
        logger.info(f"User {user_id} tried to spin with insufficient balance: {current_balance}.")
        return {'error': 'Недостатньо коштів для спіна!'}, current_balance

    result_symbols = [random.choice(ALL_REEL_SYMBOLS) for _ in range(3)]
    winnings = check_win_conditions(result_symbols) # Використовуємо нову функцію перевірки

    # Розрахунок нового балансу та XP
    new_balance = current_balance - BET_AMOUNT + winnings
    xp_gained = XP_PER_SPIN
    if winnings > 0:
        xp_gained += (XP_PER_SPIN * XP_PER_WIN_MULTIPLIER)
        logger.info(f"User {user_id} won {winnings}. Symbols: {result_symbols}")
    else:
        logger.info(f"User {user_id} lost on spin. Symbols: {result_symbols}")
    
    new_xp = current_xp + xp_gained
    new_level = get_level_from_xp(new_xp)

    # Оновлюємо дані користувача в БД
    update_user_data(user_id, balance=new_balance, xp=new_xp, level=new_level)

    final_user_data = get_user_data(user_id) # Отримуємо оновлені дані після спіна
    
    return {
        'symbols': result_symbols,
        'winnings': winnings,
        'new_balance': final_user_data['balance'],
        'xp': final_user_data['xp'],
        'level': final_user_data['level'],
        'next_level_xp': get_xp_for_next_level(final_user_data['level']) # Додаємо для фронтенду
    }, final_user_data['balance']


# --- Обробники Telegram-бота (aiogram v3 синтаксис) ---

@dp.message(CommandStart())
async def send_welcome(message: Message):
    user_id = message.from_user.id
    init_db() # Ініціалізуємо БД при першому старті (безпечно викликати багато разів)
    user_data = get_user_data(user_id) # Отримуємо або створюємо користувача

    keyboard = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎰 Відкрити Слот-Казино 🎰", web_app=WebAppInfo(url=WEB_APP_URL))]
    ])

    await message.reply(
        f"Привіт, {message.from_user.first_name}!\n"
        f"Ласкаво просимо до віртуального Слот-Казино!\n"
        f"Ваш поточний баланс: {user_data['balance']} фантиків.\n"
        f"Натисніть кнопку нижче, щоб почати грати!",
        reply_markup=keyboard
    )

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


# --- Обробка запитів від Web App (через aiohttp.web) ---

async def api_get_balance(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        logger.warning("api_get_balance: User ID is missing in request.")
        return json_response({'error': 'User ID is required'}, status=400)
    
    user_data = get_user_data(user_id) # Повертаємо всі дані
    return json_response({
        'balance': user_data['balance'],
        'xp': user_data['xp'],
        'level': user_data['level'],
        'next_level_xp': get_xp_for_next_level(user_data['level']),
        'last_free_coins_claim': user_data['last_free_coins_claim'].isoformat() if user_data['last_free_coins_claim'] else None
    })

async def api_spin(request: Request):
    data = await request.json()
    user_id = data.get('user_id')
    if not user_id:
        logger.warning("api_spin: User ID is missing in request.")
        return json_response({'error': 'User ID is required'}, status=400)
    
    result, final_balance = spin_slot(user_id)
    if 'error' in result:
        return json_response(result, status=400)
    
    return json_response(result)


# api_claim_daily_bonus - ВИДАЛЕНО

# --- Функції для запуску бота та веб-сервера ---

async def on_startup_webhook(web_app: Application):
    """Викликається при запуску Aiohttp веб-сервера."""
    logger.warning('Starting bot and webhook...')
    init_db()
    await bot.set_webhook(WEBHOOK_URL)
    logger.info(f"Webhook set to: {WEBHOOK_URL}")

async def on_shutdown_webhook(web_app: Application):
    """Викликається при завершенні роботи Aiohttp веб-сервера."""
    logger.warning('Shutting down bot and webhook...')
    await bot.delete_webhook()

app_aiohttp = Application()

# Реєстрація API ендпоінтів для Web App (ПЕРЕД CORS)
app_aiohttp.router.add_post('/api/get_balance', api_get_balance, name='api_get_balance')
app_aiohttp.router.add_post('/api/spin', api_spin, name='api_spin')
# app_aiohttp.router.add_post('/api/claim_daily_bonus', api_claim_daily_bonus, name='api_claim_daily_bonus') # ВИДАЛЕНО

# Налаштовуємо CORS для дозволу запитів з Web App URL
cors = aiohttp_cors.setup(app_aiohttp, defaults={
    WEB_APP_URL: aiohttp_cors.ResourceOptions(
        allow_credentials=True,
        expose_headers="*",
        allow_headers="*",
        allow_methods="*",
    )
})

# Застосовуємо CORS до ваших API-маршрутів
for route in list(app_aiohttp.router.routes()):
    # Перевіряємо, чи це наші API маршрути, включаючи новий
    if route.resource and route.resource.name in ['api_get_balance', 'api_spin']: # ВИДАЛЕНО 'api_claim_daily_bonus'
        cors.add(route)

# Реєстрація хендлера для Telegram webhook
SimpleRequestHandler(
    dispatcher=dp,
    bot=bot,
).register(app_aiohttp, WEBHOOK_PATH)

# Додаємо функції запуску/зупинки до Aiohttp додатка
app_aiohttp.on_startup.append(on_startup_webhook)
app_aiohttp.on_shutdown.append(on_shutdown_webhook)

if __name__ == '__main__':
    pass
