// =================================================================
// 🎮 Елементи DOM
// =================================================================
const spinButton = document.getElementById('spinButton');
const userBalanceSpan = document.getElementById('userBalance');
const userLevelSpan = document.getElementById('userLevel');
const userXpSpan = document.getElementById('userXp');
const nextLevelXpSpan = document.getElementById('nextLevelXp');
const xpProgressBar = document.getElementById('xpProgressBar');
const dailyBonusButton = document.getElementById('dailyBonusButton');
const dailyBonusCooldownSpan = document.getElementById('dailyBonusCooldown');
const quickBonusButton = document.getElementById('quickBonusButton');
const quickBonusCooldownSpan = document.getElementById('quickBonusCooldown');
const leaderboardButton = document.getElementById('leaderboardButton');
const leaderboardModal = document.getElementById('leaderboardModal');
const leaderboardTableBody = document.getElementById('leaderboardTableBody');
const leaderboardLoading = document.getElementById('leaderboardLoading');
const leaderboardError = document.getElementById('leaderboardError');

const reelElements = [
    document.getElementById('reel1'),
    document.getElementById('reel2'),
    document.getElementById('reel3')
];
const messageDiv = document.getElementById('message');
const customModal = document.getElementById('customModal');
const modalMessage = document.getElementById('modalMessage');
const activateAudioButton = document.getElementById('activateAudioButton');
const audioPrompt = document.getElementById('audioPrompt');

// =================================================================
// ⚙️ Глобальні Налаштування Гри (Мають збігатися з бекендом main.py!)
// =================================================================
const SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀'];
const WILD_SYMBOL = '⭐';
const SCATTER_SYMBOL = '💰';

const ALL_REEL_SYMBOLS = [...SYMBOLS, WILD_SYMBOL, SCATTER_SYMBOL];

const BET_AMOUNT = 100;
const REEL_HEIGHT_PX = 90; // Змінено для кращого вмісту
const REEL_SPIN_CYCLES = 5;
const REEL_SPIN_DURATION_BASE = 0.8;
const REEL_STOP_DURATION = 1.0;
const REEL_STOP_EASE = "power2.out";
const REEL_STOP_STAGGER = 0.2;

// XP та Рівні
const LEVEL_THRESHOLDS = [
    0, 100, 300, 600, 1000, 1500, 2200, 3000, 4000, 5500, 7500, 10000
];

const DAILY_BONUS_AMOUNT = 300;
const DAILY_BONUS_COOLDOWN_HOURS = 24;

const QUICK_BONUS_AMOUNT = 100;
const QUICK_BONUS_COOLDOWN_MINUTES = 15;

// =================================================================
// 🧠 Ініціалізація Telegram WebApp
// =================================================================
let userId = null;
let telegramUsername = null;
let lastKnownUserBalance = 0;
let lastKnownUserXP = 0;
let lastKnownUserLevel = 1;
let dailyBonusCountdownInterval = null;
let quickBonusCountdownInterval = null;

if (typeof Telegram !== 'undefined' && Telegram.WebApp && Telegram.WebApp.initDataUnsafe?.user?.id) {
    userId = Telegram.WebApp.initDataUnsafe.user.id;
    // Намагаємося отримати username, інакше first_name, інакше заглушка
    telegramUsername = Telegram.WebApp.initDataUnsafe.user.username || Telegram.WebApp.initDataUnsafe.user.first_name || `Гравець ${String(userId).slice(-4)}`;
    console.log(`Telegram User ID: ${userId}, Username: ${telegramUsername}`);
    Telegram.WebApp.expand();
} else {
    console.warn('Telegram WebApp не знайдено або ви тестуєте не через Telegram. Деякі функції можуть не працювати.');
    messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
    messageDiv.className = 'text-yellow-400 font-bold';
    spinButton.disabled = true;
    spinButton.classList.remove('pulsing');
    dailyBonusButton.disabled = true;
    quickBonusButton.disabled = true;
    leaderboardButton.disabled = true;
}

// 🌐 URL бекенду (твій актуальний Render URL)
const API_BASE_URL = 'https://casino-0h0l.onrender.com';

// =================================================================
// 🔊 Ініціалізація звукових ефектів (Tone.js)
// =================================================================
let spinStartSound, reelStopSound, winSound, bigWinSound, loseSound, levelUpSound, dailyBonusSound, quickBonusSound;

function createSynthSound(options = {}) {
    const defaultOptions = {
        type: "MembraneSynth",
        envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 },
        oscillator: { type: "sine" }
    };
    const finalOptions = { ...defaultOptions, ...options };

    let synth;
    if (finalOptions.type === "PolySynth") {
        synth = new Tone.PolySynth(Tone.Synth).toDestination();
    } else if (finalOptions.type === "NoiseSynth") {
        synth = new Tone.NoiseSynth().toDestination();
    } else {
        synth = new Tone.MembraneSynth().toDestination();
    }

    synth.set({
        oscillator: finalOptions.oscillator,
        envelope: finalOptions.envelope
    });
    return synth;
}

async function setupSounds() {
    if (Tone.context.state !== 'running') {
        try {
            await Tone.start();
            console.log("AudioContext is running.");
            audioPrompt.style.display = 'none';
        } catch (e) {
            console.error("Помилка ініціалізації аудіо:", e);
            audioPrompt.style.display = 'flex';
            return; 
        }
    } else {
        audioPrompt.style.display = 'none';
    }

    spinStartSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.01, decay: 0.1, sustain: 0.05, release: 0.1 } });
    reelStopSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.005, decay: 0.05, sustain: 0.01, release: 0.1 } });
    winSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sine" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
    bigWinSound = createSynthSound({ type: "PolySynth", oscillator: { type: "triangle" }, envelope: { attack: 0.05, decay: 0.5, sustain: 0.2, release: 1.0 } });
    loseSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "square" }, envelope: { attack: 0.01, decay: 0.3, sustain: 0.1, release: 0.4 } });
    levelUpSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sawtooth" }, envelope: { attack: 0.02, decay: 0.3, sustain: 0.2, release: 0.8 } });
    dailyBonusSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "triangle" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
    quickBonusSound = createSynthSound({ type: "PolySynth", oscillator: { type: "triangle" }, envelope: { attack: 0.01, decay: 0.1, sustain: 0.05, release: 0.3 } });
}

window.addEventListener('click', () => setupSounds(), { once: true });
window.addEventListener('touchstart', () => setupSounds(), { once: true });

activateAudioButton.addEventListener('click', async () => {
    await setupSounds();
});

function playSpinStartSound() { if (spinStartSound && Tone.context.state === 'running') spinStartSound.triggerAttackRelease("C4", "8n"); }
function playReelStopSound(note = "D4") { if (reelStopSound && Tone.context.state === 'running') reelStopSound.triggerAttackRelease(note, "16n"); }
function playWinSoundEffect() { if (winSound && Tone.context.state === 'running') winSound.triggerAttackRelease(["C5", "E5", "G5"], "4n"); }
function playBigWinSoundEffect() { if (bigWinSound && Tone.context.state === 'running') bigWinSound.triggerAttackRelease(["C5", "G5", "C6"], "1n"); }
function playLoseSoundEffect() { if (loseSound && Tone.context.state === 'running') loseSound.triggerAttackRelease("C3", "4n"); }
function playLevelUpSound() { if (levelUpSound && Tone.context.state === 'running') levelUpSound.triggerAttackRelease(["E4", "G4", "C5"], "0.8n"); }
function playDailyBonusSound() { if (dailyBonusSound && Tone.context.state === 'running') dailyBonusSound.triggerAttackRelease("G4", "0.5n"); }
function playQuickBonusSound() { if (quickBonusSound && Tone.context.state === 'running') quickBonusSound.triggerAttackRelease("A4", "0.2n"); }


// =================================================================
// 💬 Кастомне модальне вікно для сповіщень
// =================================================================
function showCustomModal(msg, title = "Повідомлення") {
    modalMessage.innerHTML = `<h3 class="text-xl font-bold mb-2">${title}</h3><p>${msg}</p>`;
    customModal.classList.add('active');
}
customModal.querySelector('.close-button').addEventListener('click', () => {
    customModal.classList.remove('active');
});
customModal.querySelector('.modal-content button').addEventListener('click', () => {
    customModal.classList.remove('active');
});


// =================================================================
// 📟 Оновлення Балансу та Прогресу Користувача
// =================================================================
async function updateBalanceAndProgressDisplay() {
    if (!userId) {
        console.warn('Cannot update balance and progress: userId is null. Skipping API call.');
        return;
    }

    try {
        const response = await fetch(`${API_BASE_URL}/api/get_balance`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId, username: telegramUsername })
        });

        if (!response.ok) {
            const errData = await response.json();
            showCustomModal(`Помилка: ${errData.error || 'Невідома помилка при отриманні даних.'}`, "Помилка завантаження");
            messageDiv.className = 'text-red-500 font-bold';
            return;
        }

        const data = await response.json();
        const currentBalance = data.balance;
        const currentXP = data.xp || 0;
        const currentLevel = data.level || 1;
        const lastDailyClaim = data.last_daily_bonus_claim ? new Date(data.last_daily_bonus_claim) : null;
        const lastQuickClaim = data.last_quick_bonus_claim ? new Date(data.last_quick_bonus_claim) : null;

        // Анімація зміни балансу
        if (currentBalance !== lastKnownUserBalance) {
            userBalanceSpan.classList.remove('animate-pulse-balance');
            void userBalanceSpan.offsetWidth;
            userBalanceSpan.classList.add('animate-pulse-balance');
            userBalanceSpan.textContent = currentBalance;
            lastKnownUserBalance = currentBalance;
        }

        // Оновлення XP та Рівня
        const nextLevelIndex = currentLevel < LEVEL_THRESHOLDS.length ? currentLevel : LEVEL_THRESHOLDS.length -1;
        const nextLevelThreshold = LEVEL_THRESHOLDS[nextLevelIndex];
        
        userLevelSpan.textContent = currentLevel;
        userXpSpan.textContent = currentXP;
        nextLevelXpSpan.textContent = nextLevelThreshold;
        
        const xpProgress = Math.min(100, (currentXP / nextLevelThreshold) * 100);
        xpProgressBar.style.width = `${xpProgress}%`;

        if (currentLevel > lastKnownUserLevel && lastKnownUserLevel !== 0) {
            playLevelUpSound();
            showCustomModal(`🎉 Ви досягли Рівня ${currentLevel}! 🎉`, "Підвищення Рівня!");
        }
        lastKnownUserXP = currentXP;
        lastKnownUserLevel = currentLevel;
        
        // Оновлення стану кнопок бонусів
        updateDailyBonusButton(lastDailyClaim); 
        updateQuickBonusButton(lastQuickClaim);

        messageDiv.textContent = '';
        messageDiv.className = 'text-white';
    } catch (error) {
        console.error('Помилка при отриманні балансу та прогресу:', error);
        showCustomModal('🚫 Помилка звʼязку з сервером. Перевірте зʼєднання.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
    }
}

// =================================================================
// 🎁 Логіка Бонусних Кнопок
// =================================================================

// Допоміжна функція для форматування часу (ММ:СС або ЧЧ:ММ:СС)
function formatTime(ms) {
    const totalSeconds = Math.floor(ms / 1000);
    const hours = Math.floor(totalSeconds / 3600);
    const minutes = Math.floor((totalSeconds % 3600) / 60);
    const seconds = totalSeconds % 60;
    
    if (hours > 0) {
        return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    } else {
        return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
    }
}

// Логіка для щоденного бонусу
function updateDailyBonusButton(lastClaimTime) {
    const now = new Date();
    const cooldownDuration = DAILY_BONUS_COOLDOWN_HOURS * 60 * 60 * 1000;

    if (dailyBonusCountdownInterval) {
        clearInterval(dailyBonusCountdownInterval);
        dailyBonusCountdownInterval = null;
    }

    if (!lastClaimTime || (now.getTime() - lastClaimTime.getTime()) >= cooldownDuration) {
        dailyBonusButton.disabled = false;
        dailyBonusButton.classList.add('pulsing');
        dailyBonusCooldownSpan.textContent = '';
    } else {
        dailyBonusButton.disabled = true;
        dailyBonusButton.classList.remove('pulsing');
        
        const updateCountdown = () => {
            const nowInner = new Date();
            const timeLeft = cooldownDuration - (nowInner.getTime() - lastClaimTime.getTime());

            if (timeLeft <= 0) {
                dailyBonusButton.disabled = false;
                dailyBonusButton.classList.add('pulsing');
                dailyBonusCooldownSpan.textContent = '';
                clearInterval(dailyBonusCountdownInterval);
                dailyBonusCountdownInterval = null;
                return;
            }
            dailyBonusCooldownSpan.textContent = `(${formatTime(timeLeft)})`;
        };

        updateCountdown();
        dailyBonusCountdownInterval = setInterval(updateCountdown, 1000);
    }
}

dailyBonusButton.addEventListener('click', async () => {
    if (!userId) {
        showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
        return;
    }
    if (dailyBonusButton.disabled) return;

    dailyBonusButton.disabled = true;
    dailyBonusButton.classList.remove('pulsing');
    messageDiv.textContent = 'Отримуємо щоденну винагороду...';

    try {
        const response = await fetch(`${API_BASE_URL}/api/claim_daily_bonus`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        const data = await response.json();

        if (response.ok) {
            playDailyBonusSound();
            showCustomModal(`🎉 Ви отримали ${data.amount} фантиків!`, "Щоденна Винагорода!");
            updateBalanceAndProgressDisplay();
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Винагороди");
            messageDiv.className = 'text-red-500 font-bold';
            updateBalanceAndProgressDisplay();
        }
    } catch (error) {
        console.error('Помилка при отриманні щоденної винагороди:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером для винагороди.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        dailyBonusButton.disabled = false;
        dailyBonusButton.classList.add('pulsing');
    }
});


// Логіка для швидкого бонусу (15 хвилин)
function updateQuickBonusButton(lastClaimTime) {
    const now = new Date();
    const cooldownDuration = QUICK_BONUS_COOLDOWN_MINUTES * 60 * 1000;

    if (quickBonusCountdownInterval) {
        clearInterval(quickBonusCountdownInterval);
        quickBonusCountdownInterval = null;
    }

    if (!lastClaimTime || (now.getTime() - lastClaimTime.getTime()) >= cooldownDuration) {
        quickBonusButton.disabled = false;
        quickBonusButton.classList.add('pulsing');
        quickBonusButton.classList.remove('active-countdown'); // Приховати таймер
        quickBonusCooldownSpan.textContent = '';
    } else {
        quickBonusButton.disabled = true;
        quickBonusButton.classList.remove('pulsing');
        quickBonusButton.classList.add('active-countdown'); // Показати таймер
        
        const updateCountdown = () => {
            const nowInner = new Date();
            const timeLeft = cooldownDuration - (nowInner.getTime() - lastClaimTime.getTime());

            if (timeLeft <= 0) {
                quickBonusButton.disabled = false;
                quickBonusButton.classList.add('pulsing');
                quickBonusButton.classList.remove('active-countdown');
                quickBonusCooldownSpan.textContent = '';
                clearInterval(quickBonusCountdownInterval);
                quickBonusCountdownInterval = null;
                return;
            }
            quickBonusCooldownSpan.textContent = formatTime(timeLeft); // Формат ММ:СС
        };

        updateCountdown();
        quickBonusCountdownInterval = setInterval(updateCountdown, 1000);
    }
}

quickBonusButton.addEventListener('click', async () => {
    if (!userId) {
        showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
        return;
    }
    if (quickBonusButton.disabled) return;

    quickBonusButton.disabled = true;
    quickBonusButton.classList.remove('pulsing');
    quickBonusButton.classList.remove('active-countdown');
    quickBonusCooldownSpan.textContent = '';

    messageDiv.textContent = 'Отримуємо швидкий бонус...';

    try {
        const response = await fetch(`${API_BASE_URL}/api/claim_quick_bonus`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        const data = await response.json();

        if (response.ok) {
            playQuickBonusSound();
            showCustomModal(`💰 Ви отримали ${data.amount} фантиків!`, "Швидкий Бонус!");
            updateBalanceAndProgressDisplay();
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Бонусу");
            messageDiv.className = 'text-red-500 font-bold';
            updateBalanceAndProgressDisplay();
        }
    } catch (error) {
        console.error('Помилка при отриманні швидкого бонусу:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером для швидкого бонусу.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        quickBonusButton.disabled = false;
        quickBonusButton.classList.add('pulsing');
    }
});


// =================================================================
// 🏆 Логіка Дошки Лідерів
// =================================================================
leaderboardButton.addEventListener('click', async () => {
    leaderboardTableBody.innerHTML = ''; // Очистити попередні дані
    leaderboardLoading.classList.remove('hidden'); // Показати завантаження
    leaderboardError.classList.add('hidden'); // Приховати помилку
    leaderboardModal.classList.add('active'); // Показати модалку

    try {
        const response = await fetch(`${API_BASE_URL}/api/get_leaderboard`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({}) // Немає необхідності передавати user_id для дошки лідерів
        });

        if (!response.ok) {
            const errData = await response.json();
            leaderboardError.textContent = `Помилка: ${errData.error || 'Невідома помилка при завантаженні лідерів.'}`;
            leaderboardError.classList.remove('hidden');
            return;
        }

        const data = await response.json();
        if (data.leaderboard && data.leaderboard.length > 0) {
            data.leaderboard.forEach((player, index) => {
                const row = `
                    <tr class="${(index % 2 === 0) ? 'bg-gray-800' : 'bg-gray-700'}">
                        <td class="py-2 px-3">${index + 1}</td>
                        <td class="py-2 px-3">${player.username || `Гравець ${player.user_id}`}</td>
                        <td class="py-2 px-3 text-right">${player.level}</td>
                        <td class="py-2 px-3 text-right">${player.balance}</td>
                        <td class="py-2 px-3 text-right">${player.xp}</td>
                    </tr>
                `;
                leaderboardTableBody.insertAdjacentHTML('beforeend', row);
            });
        } else {
            leaderboardTableBody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-gray-400">Наразі немає лідерів. Будь першим!</td></tr>';
        }
    } catch (error) {
        console.error('Помилка при завантаженні дошки лідерів:', error);
        leaderboardError.textContent = '🚫 Не вдалося зʼєднатись із сервером для завантаження дошки лідерів.';
        leaderboardError.classList.remove('hidden');
    } finally {
        leaderboardLoading.classList.add('hidden');
    }
});


// =================================================================
// 🎞️ Анімація Барабанів (використання GSAP)
// =================================================================
function animateReels(reels, finalSymbols) {
    return new Promise(resolve => {
        const masterTimeline = gsap.timeline({ onComplete: resolve });

        reels.forEach((reel, index) => {
            const reelContent = reel.querySelector('.reel-content');
            
            const numSpinSymbols = REEL_SPIN_CYCLES * ALL_REEL_SYMBOLS.length;
            let animationSymbols = [];
            for (let i = 0; i < numSpinSymbols; i++) {
                animationSymbols.push(ALL_REEL_SYMBOLS[Math.floor(Math.random() * ALL_REEL_SYMBOLS.length)]);
            }
            animationSymbols.push(finalSymbols[index]);

            reelContent.innerHTML = animationSymbols.map(s => `<div class="reel-symbol">${s}</div>`).join('');
            
            gsap.set(reelContent, { y: 0 });
            reel.classList.add('spinning');

            masterTimeline.to(reelContent, {
                y: -(animationSymbols.length - 1) * REEL_HEIGHT_PX,
                duration: REEL_SPIN_DURATION_BASE + (index * REEL_STOP_STAGGER),
                ease: "linear",
                onStart: () => {
                    if (index === 0) playSpinStartSound();
                },
                onUpdate: function() {
                    if (this.progress() > 0.1 && this.progress() < 0.95 && Tone.context.state === 'running') {
                        playReelStopSound("C4");
                    }
                }
            }, 0);

            masterTimeline.to(reelContent, {
                y: -((animationSymbols.length - 1) * REEL_HEIGHT_PX),
                duration: REEL_STOP_DURATION,
                ease: REEL_STOP_EASE,
                overwrite: true,
                onComplete: () => {
                    reel.classList.remove('spinning');
                    reelContent.innerHTML = `<div class="reel-symbol">${finalSymbols[index]}</div>`;
                    gsap.set(reelContent, { y: 0 });
                    playReelStopSound("G4");
                }
            }, `<${index * REEL_STOP_STAGGER}`);
        });
    });
}

// =================================================================
// 🎰 Обробка Спіна
// =================================================================
spinButton.addEventListener('click', async () => {
    if (!userId) {
        showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
        return;
    }

    spinButton.disabled = true;
    spinButton.classList.remove('pulsing');
    messageDiv.textContent = '';

    reelElements.forEach((reel) => {
        const reelContent = reel.querySelector('.reel-content');
        reelContent.innerHTML = `<div class="reel-symbol">?</div>`; 
        gsap.set(reelContent, { y: 0 });
        reel.classList.remove('spinning');
    });

    try {
        const response = await fetch(`${API_BASE_URL}/api/spin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        const data = await response.json();

        if (response.ok) {
            await animateReels(reelElements, data.symbols);
            updateBalanceAndProgressDisplay();

            if (data.winnings > 0) {
                messageDiv.textContent = `🎉 Ви виграли ${data.winnings} фантиків! 🎉`;
                if (data.winnings >= 500) {
                    messageDiv.className = 'message big-win-message';
                    playBigWinSoundEffect();
                } else {
                    messageDiv.className = 'message win-message';
                    playWinSoundEffect();
                }
            } else {
                messageDiv.textContent = '😢 Спробуйте ще раз!';
                messageDiv.className = 'message lose-message text-red-400';
                playLoseSoundEffect();
            }
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка сервера.'}`, "Помилка Спіна");
            messageDiv.className = 'text-red-500 font-bold';
            playLoseSoundEffect();
        }
    } catch (error) {
        console.error('Помилка при спіні:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером. Перевірте зʼєднання.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        playLoseSoundEffect();
    } finally {
        spinButton.disabled = false;
        spinButton.classList.add('pulsing');
    }
});

// =================================================================
// 🚀 Початкове завантаження та перевірка аудіо
// =================================================================
window.onload = () => {
    // Включаємо аудіо контекст, якщо він ще не запущений
    if (Tone.context.state !== 'running') {
        audioPrompt.style.display = 'flex';
    } else {
        audioPrompt.style.display = 'none';
    }

    // Завантажуємо дані користувача тільки якщо userId вже доступний
    if (userId) {
        updateBalanceAndProgressDisplay();
    } else {
        messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
        messageDiv.className = 'text-yellow-400';
    }
};