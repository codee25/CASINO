import logging
import os
import json
import random

import psycopg2 # Імпортуємо PostgreSQL конектор
from psycopg2 import sql # Для безпечного формування запитів

from aiogram import Bot, Dispatcher, types
from aiogram.types import WebAppInfo, InlineKeyboardMarkup, InlineKeyboardButton
from flask import Flask, request, jsonify

# ... (Налаштування логування, змінних середовища API_TOKEN, WEB_APP_URL, WEBHOOK_HOST, WEBHOOK_PATH, WEBHOOK_URL - залишаються без змін) ...

# --- Ініціалізація бота та диспетчера ---
bot = Bot(token=API_TOKEN)
dp = Dispatcher(bot)

# --- Налаштування Flask ---
app = Flask(__name__)

# --- Налаштування бази даних PostgreSQL ---
# Параметри підключення до бази даних PostgreSQL
# Ці змінні середовища ви отримаєте від Render.com (Postgres service)
PG_HOST = os.getenv('PG_HOST')
PG_USER = os.getenv('PG_USER')
PG_PASSWORD = os.getenv('PG_PASSWORD')
PG_DATABASE = os.getenv('PG_DATABASE')
PG_PORT = os.getenv('PG_PORT', 5432) # Порт PostgreSQL, зазвичай 5432

def get_db_connection():
    """Створює та повертає з'єднання до бази даних PostgreSQL."""
    try:
        conn = psycopg2.connect(
            host=PG_HOST,
            user=PG_USER,
            password=PG_PASSWORD,
            dbname=PG_DATABASE,
            port=PG_PORT,
            sslmode='require' # Важливо для Render.com, щоб використовувати SSL
        )
        return conn
    except psycopg2.Error as err:
        logger.error(f"Error connecting to PostgreSQL: {err}")
        raise

def init_db():
    """Ініціалізує таблиці в базі даних PostgreSQL."""
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
            update_user_balance(user_id, 1000) # Додаємо початковий баланс
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

# ... (Логіка Слот-машини SYMBOLS, BET_AMOUNT, PAYOUTS, spin_slot - залишаються без змін) ...

# ... (Обробники Telegram-бота send_welcome - залишаються без змін) ...

# ... (Обробка запитів від Web App API_get_balance, API_spin - залишаються без змін) ...

# ... (Функції для запуску бота: telegram_webhook, setup_webhook_on_start - залишаються без змін) ...

# --- Запускаємо Flask-додаток ---
if __name__ == '__main__':
    pass # Gunicorn запустить 'app'