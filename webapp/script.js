const spinButton = document.getElementById('spinButton');
const userBalanceSpan = document.getElementById('userBalance');
const reel1 = document.getElementById('reel1');
const reel2 = document.getElementById('reel2');
const reel3 = document.getElementById('reel3');
const messageDiv = document.getElementById('message');

// 🧠 Безпечна ініціалізація Telegram WebApp
let userId = null;

if (typeof Telegram !== 'undefined' && Telegram.WebApp && Telegram.WebApp.initDataUnsafe?.user?.id) {
    userId = Telegram.WebApp.initDataUnsafe.user.id;
} else {
    console.warn('Telegram WebApp не знайдено або ви тестуєте не через Telegram.');
    messageDiv.textContent = '⚠️ Увійдіть через Telegram для гри.';
    spinButton.disabled = true;
}

// 🌐 URL бекенду (твій актуальний Render URL)
const API_BASE_URL = 'https://casino-0h0l.onrender.com';

// 📟 Оновлення балансу
async function updateBalanceDisplay() {
    if (!userId) return;

    try {
        const response = await fetch(`${API_BASE_URL}/api/get_balance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        if (!response.ok) {
            const errData = await response.json();
            messageDiv.textContent = `Помилка: ${errData.error}`;
            messageDiv.className = 'message lose-message';
            return;
        }

        const data = await response.json();
        userBalanceSpan.textContent = data.balance;
    } catch (error) {
        console.error('Помилка при отриманні балансу:', error);
        messageDiv.textContent = '🚫 Помилка звʼязку з сервером.';
        messageDiv.className = 'message lose-message';
    }
}

// 🎞️ Анімація барабанів
function animateReels(reels, finalSymbols) {
    return new Promise(resolve => {
        let completed = 0;
        reels.forEach((reel, index) => {
            reel.classList.add('spinning');
            setTimeout(() => {
                reel.classList.remove('spinning');
                reel.textContent = finalSymbols[index];
                completed++;
                if (completed === reels.length) resolve();
            }, 1000 + index * 200);
        });
    });
}

// 🎰 Обробка спіна
spinButton.addEventListener('click', async () => {
    if (!userId) return;

    spinButton.disabled = true;
    messageDiv.textContent = '';
    const reels = [reel1, reel2, reel3];

    reels.forEach((r) => {
        r.textContent = '?';
        r.classList.add('spinning');
    });

    try {
        const response = await fetch(`${API_BASE_URL}/api/spin`, {
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
                messageDiv.textContent = '😢 Спробуйте ще раз!';
                messageDiv.className = 'message lose-message';
            }
        } else {
            messageDiv.textContent = `❌ Помилка: ${data.error}`;
            messageDiv.className = 'message lose-message';
            reels.forEach(r => r.classList.remove('spinning'));
        }
    } catch (error) {
        console.error('Помилка при спіні:', error);
        messageDiv.textContent = '🚫 Не вдалося зʼєднатись із сервером.';
        messageDiv.className = 'message lose-message';
        reels.forEach(r => r.classList.remove('spinning'));
    } finally {
        spinButton.disabled = false;
    }
});

// 🚀 Початкове завантаження балансу
window.onload = () => {
    if (userId) {
        updateBalanceDisplay();
    }
};
