const spinButton = document.getElementById('spinButton');
const userBalanceSpan = document.getElementById('userBalance');
const reel1 = document.getElementById('reel1');
const reel2 = document.getElementById('reel2');
const reel3 = document.getElementById('reel3');
const messageDiv = document.getElementById('message');

// Отримуємо user_id з Telegram WebApp
// Цей ID використовується для ідентифікації користувача на бекенді
const userId = Telegram.WebApp.initDataUnsafe.user.id;

// Функція для оновлення балансу на екрані
async function updateBalanceDisplay() {
    try {
        const response = await fetch('/api/get_balance', {
            method: 'POST', // Використовуємо POST для надсилання user_id в тілі запиту
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
            // Додаємо клас для анімації
            reel.classList.add('spinning');

            // Симулюємо "зупинку" барабана через деякий час
            // Кожен барабан зупиняється трохи пізніше, створюючи ефект "розкручування"
            setTimeout(() => {
                reel.classList.remove('spinning');
                reel.textContent = finalSymbols[index];
                completedAnimations++;
                if (completedAnimations === reels.length) {
                    resolve(); // Всі анімації завершено
                }
            }, 1000 + index * 200); 
        });
    });
}


// Обробник натискання кнопки "Крутити!"
spinButton.addEventListener('click', async () => {
    spinButton.disabled = true; // Вимикаємо кнопку під час спіна
    messageDiv.textContent = ''; // Очищаємо повідомлення

    // Починаємо анімацію (можна просто змінити текст на ?)
    reel1.textContent = '?';
    reel2.textContent = '?';
    reel3.textContent = '?';
    
    // Запускаємо анімацію
    const reels = [reel1, reel2, reel3];
    reels.forEach(reel => reel.classList.add('spinning'));

    try {
        // Відправляємо запит на спін до нашого Python-бота (бекенду)
        const response = await fetch('/api/spin', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId }) // Надсилаємо ID користувача
        });
        const data = await response.json(); // Отримуємо JSON відповідь від бота

        if (response.ok) {
            // Зупиняємо анімацію і показуємо результат
            await animateReels(reels, data.symbols);

            userBalanceSpan.textContent = data.new_balance; // Оновлюємо баланс
            if (data.winnings > 0) {
                messageDiv.textContent = `🎉 Ви виграли ${data.winnings} фантиків! 🎉`;
                messageDiv.className = 'message win-message';
            } else {
                messageDiv.textContent = 'Спробуйте ще раз!';
                messageDiv.className = 'message lose-message';
            }
        } else {
            // Обробка помилок від сервера (наприклад, недостатньо коштів)
            messageDiv.textContent = `Помилка: ${data.error}`;
            messageDiv.className = 'message lose-message';
            reels.forEach(reel => reel.classList.remove('spinning')); // Зупиняємо анімацію при помилці
        }
    } catch (error) {
        console.error('Помилка при спіні:', error);
        messageDiv.textContent = 'Не вдалося підключитися до сервера для спіна.';
        messageDiv.className = 'message lose-message';
        reels.forEach(reel => reel.classList.remove('spinning')); // Зупиняємо анімацію при помилці
    } finally {
        spinButton.disabled = false; // Вмикаємо кнопку назад
    }
});

// Завантажуємо баланс при завантаженні Web App (коли сторінка відкривається)
window.onload = updateBalanceDisplay;