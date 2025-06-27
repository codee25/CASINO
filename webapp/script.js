const spinButton = document.getElementById('spinButton');
const userBalanceSpan = document.getElementById('userBalance');
const reel1 = document.getElementById('reel1');
const reel2 = document.getElementById('reel2');
const reel3 = document.getElementById('reel3');
const messageDiv = document.getElementById('message');

// Отримуємо user_id з Telegram WebApp
// Цей ID використовується для ідентифікації користувача на бекенді
// Цей рядок може видавати TypeError, якщо Web App відкривається не через Telegram.
// Це нормально, якщо ви тестуєте його напряму, але для роботи потрібен Telegram.
const userId = Telegram.WebApp.initDataUnsafe?.user?.id; // Додано оператор ?. для безпечного доступу

// =================================================================
// ПОЧАТОК: ДОДАНО БАЗОВИЙ URL ВАШОГО БОТА (API БЕКЕНДУ)
// =================================================================
// Важливо: замініть 'https://my-slot-bot.onrender.com' на АКТУАЛЬНИЙ URL ВАШОГО БОТА!
// Цей URL - це WEBHOOK_HOST, який ви налаштували для вашого бота на Render.com.
const API_BASE_URL = 'https://my-slot-bot.onrender.com';
// =================================================================
// КІНЕЦЬ: ДОДАНО БАЗОВИЙ URL ВАШОГО БОТА
// =================================================================


// Функція для оновлення балансу на екрані
async function updateBalanceDisplay() {
    try {
        const response = await fetch(`${API_BASE_URL}/api/get_balance`, { // Змінено шлях
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });
        const data = await response.json();
        if (response.ok) {
            userBalanceSpan.textContent = data.balance;
        } else {
            messageDiv.textContent = `Помилка: ${data.error}`;
            messageDiv.className = 'message lose-message';
        }
    } catch (error) {
        console.error('Помилка при отриманні балансу:', error);
        messageDiv.textContent = 'Не вдалося підключитися до сервера для отримання балансу.';
        messageDiv.className = 'message lose-message';
    }
}

// Функція для анімації барабанів
function animateReels(reels, finalSymbols) {
    return new Promise(resolve => {
        let completedAnimations = 0;
        reels.forEach((reel, index) => {
            reel.classList.add('spinning');
            setTimeout(() => {
                reel.classList.remove('spinning');
                reel.textContent = finalSymbols[index];
                completedAnimations++;
                if (completedAnimations === reels.length) {
                    resolve();
                }
            }, 1000 + index * 200); 
        });
    });
}

// Обробник натискання кнопки "Крутити!"
spinButton.addEventListener('click', async () => {
    spinButton.disabled = true;
    messageDiv.textContent = '';

    reel1.textContent = '?';
    reel2.textContent = '?';
    reel3.textContent = '?';
    
    const reels = [reel1, reel2, reel3];
    reels.forEach(reel => reel.classList.add('spinning'));

    try {
        const response = await fetch(`${API_BASE_URL}/api/spin`, { // Змінено шлях
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });
        const data = await response.json();

        if (response.ok) {
            await animateReels(reels, data.symbols);

            userBalanceSpan.textContent = data.new_balance;
            if (data.winnings > 0) {
                messageDiv.textContent = `🎉 Ви виграли ${data.winnings} фантиків! 🎉`;
                messageDiv.className = 'message win-message';
            } else {
                messageDiv.textContent = 'Спробуйте ще раз!';
                messageDiv.className = 'message lose-message';
            }
        } else {
            messageDiv.textContent = `Помилка: ${data.error}`;
            messageDiv.className = 'message lose-message';
            reels.forEach(reel => reel.classList.remove('spinning'));
        }
    } catch (error) {
        console.error('Помилка при спіні:', error);
        messageDiv.textContent = 'Не вдалося підключитися до сервера для спіна.';
        messageDiv.className = 'message lose-message';
        reels.forEach(reel => reel.classList.remove('spinning'));
    } finally {
        spinButton.disabled = false;
    }
});

// Завантажуємо баланс при завантаженні Web App (коли сторінка відкривається)
window.onload = updateBalanceDisplay;
