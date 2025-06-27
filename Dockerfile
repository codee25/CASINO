# Використовуємо образ Python 3.11 (це зафіксує версію)
# python:3.11-slim-buster - це легкий образ, що базується на Debian Buster
FROM python:3.11-slim-buster

# Встановлюємо системні залежності, необхідні для компіляції Python-пакетів:
# build-essential: для компіляції C-розширень (наприклад, aiohttp, psycopg2)
# libpq-dev: необхідний для psycopg2 (драйвер PostgreSQL)
# gcc: компілятор, який потрібен деяким бібліотекам
# --no-install-recommends: зменшує розмір образу, не встановлюючи рекомендовані пакети
# && rm -rf /var/lib/apt/lists/*: очищаємо кеш пакетів після встановлення
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    libpq-dev \
    gcc \
    && rm -rf /var/lib/apt/lists/*

# Встановлюємо робочу директорію всередині контейнера
# Всі наступні команди будуть виконуватися відносно цієї директорії
WORKDIR /app

# Копіюємо файл requirements.txt до робочої директорії
# Це потрібно робити окремо, щоб Docker міг кешувати цей шар.
# Якщо requirements.txt не змінюється, Docker не буде перевстановлювати залежності.
COPY requirements.txt .

# Встановлюємо Python-залежності з requirements.txt
# --no-cache-dir: не зберігати кеш pip, щоб зменшити розмір образу
RUN pip install --no-cache-dir -r requirements.txt

# Копіюємо решту коду вашого додатка до робочої директорії контейнера
# '.' означає "все з поточної локальної директорії"
COPY . .

# Визначаємо змінну оточення PORT, яка буде використовуватися вашим Gunicorn.
# Render автоматично надає порт через цю змінну оточення.
ENV PORT 10000

# Команда, яка буде виконуватися при запуску контейнера
# Вона запускатиме ваш start.sh скрипт, який, у свою чергу, запускає Gunicorn.
CMD ["bash", "start.sh"]