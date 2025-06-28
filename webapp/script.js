// =================================================================
// 🚀 Початкові логи та перевірка WebApp
// =================================================================
console.log("[DEBUG-INIT] Script loaded.");
if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
    Telegram.WebApp.sendData('JS_DEBUG: Script loaded and Telegram.WebApp detected.');
} else {
    console.error("Telegram.WebApp is not detected at script start.");
    // No Telegram.WebApp.sendData if Telegram.WebApp is completely missing
}


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
const leaderboardButton = document.getElementById('leaderboardButton'); // Змінна для кнопки лідерів
const leaderboardModal = document.getElementById('leaderboardModal');
const leaderboardTableBody = document.getElementById('leaderboardTableBody');
const leaderboardLoading = document.getElementById('leaderboardLoading');
const leaderboardError = document.getElementById('leaderboardError');

// Перевірка, чи знайдено елементи DOM, і логування цього
console.log(`[DEBUG-DOM] spinButton: ${!!spinButton}`);
console.log(`[DEBUG-DOM] leaderboardButton: ${!!leaderboardButton}`);
console.log(`[DEBUG-DOM] leaderboardModal: ${!!leaderboardModal}`);

if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
    Telegram.WebApp.sendData(`JS_DEBUG: DOM Elements found - LBBtn:${!!leaderboardButton}, LBModal:${!!leaderboardModal}`);
}


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
const REEL_HEIGHT_PX = 90;
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
    telegramUsername = Telegram.WebApp.initDataUnsafe.user.username || Telegram.WebApp.initDataUnsafe.user.first_name || `Гравець ${String(userId).slice(-4)}`;
    console.log(`[WebApp Init] Telegram User ID: ${userId}, Username: ${telegramUsername}`);
    Telegram.WebApp.expand();
    Telegram.WebApp.sendData(`JS_LOG: WebApp Initialized for User: ${userId}`);
} else {
    console.warn('[WebApp Init] Telegram WebApp not found or testing outside Telegram.');
    messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
    messageDiv.className = 'text-yellow-400 font-bold';
    spinButton.disabled = true;
    spinButton.classList.remove('pulsing');
    dailyBonusButton.disabled = true;
    quickBonusButton.disabled = true;
    if (leaderboardButton) leaderboardButton.disabled = true;
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) {
        Telegram.WebApp.sendData('JS_LOG: WebApp NOT Initialized - user ID missing.');
    } else {
        console.error("Telegram.WebApp object is completely missing. Cannot send logs.");
    }
}

// 🌐 URL бекенду (твій актуальний Render URL)
const API_BASE_URL = 'https://casino-0h0l.onrender.com'; // <<<< ОНОВІТЬ ЦЕЙ URL!


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
            console.log("[Audio] AudioContext is running.");
            audioPrompt.style.display = 'none';
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: AudioContext started.');
        } catch (e) {
            console.error("[Audio] Помилка ініціалізації аудіо:", e);
            audioPrompt.style.display = 'flex';
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Audio init failed: ${e.message}`);
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
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Showing modal: ${title} - ${msg.substring(0, Math.min(msg.length, 50))}`);
}
customModal.querySelector('.close-button').addEventListener('click', () => {
    customModal.classList.remove('active');
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Custom modal closed.');
});
customModal.querySelector('.modal-content button').addEventListener('click', () => {
    customModal.classList.remove('active');
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Custom modal closed (OK button).');
});


// =================================================================
// 📟 Оновлення Балансу та Прогресу Користувача
// =================================================================
async function updateBalanceAndProgressDisplay() {
    if (!userId) {
        console.warn('[Balance Update] Cannot update balance and progress: userId is null. Skipping API call.');
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Balance update skipped - no user ID.');
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
            console.error('[Balance Update] API Error:', errData);
            showCustomModal(`Помилка: ${errData.error || 'Невідома помилка при отриманні даних.'}`, "Помилка завантаження");
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Balance update API failed: ${errData.error || 'Unknown error'}`);
            return;
        }

        const data = await response.json();
        const currentBalance = data.balance;
        const currentXP = data.xp || 0;
        const currentLevel = data.level || 1;
        const lastDailyClaim = data.last_daily_bonus_claim ? new Date(data.last_daily_bonus_claim) : null;
        const lastQuickClaim = data.last_quick_bonus_claim ? new Date(data.last_quick_bonus_claim) : null;

        if (currentBalance !== lastKnownUserBalance) {
            userBalanceSpan.classList.remove('animate-pulse-balance');
            void userBalanceSpan.offsetWidth;
            userBalanceSpan.classList.add('animate-pulse-balance');
            userBalanceSpan.textContent = currentBalance;
            lastKnownUserBalance = currentBalance;
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Balance updated to ${currentBalance}`);
        }

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
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Level Up! New Level: ${currentLevel}`);
        }
        lastKnownUserXP = currentXP;
        lastKnownUserLevel = currentLevel;
        
        updateDailyBonusButton(lastDailyClaim); 
        updateQuickBonusButton(lastQuickClaim);

        messageDiv.textContent = '';
        messageDiv.className = 'text-white';
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Balance and progress display updated successfully.');
    } catch (error) {
        console.error('[Balance Update] Помилка при отриманні балансу та прогресу:', error);
        showCustomModal('🚫 Помилка звʼязку з сервером. Перевірте зʼєднання.', "Помилка");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Balance update network error: ${error.message}`);
    }
}

// =================================================================
// 🎁 Логіка Бонусних Кнопок
// =================================================================

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
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Daily Bonus clicked - no user ID.');
        return;
    }
    if (dailyBonusButton.disabled) {
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Daily Bonus clicked - button disabled.');
        return;
    }

    dailyBonusButton.disabled = true;
    dailyBonusButton.classList.remove('pulsing');
    messageDiv.textContent = 'Отримуємо щоденну винагороду...';
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Attempting to claim daily bonus...');

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
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Daily Bonus claimed: ${data.amount}`);
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Винагороди");
            messageDiv.className = 'text-red-500 font-bold';
            updateBalanceAndProgressDisplay();
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Daily Bonus API failed: ${data.error || 'Unknown'}`);
        }
    } catch (error) {
        console.error('[Daily Bonus] Помилка при отриманні щоденної винагороди:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером для винагороди.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        dailyBonusButton.disabled = false;
        dailyBonusButton.classList.add('pulsing');
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Daily Bonus network error: ${error.message}`);
    }
});


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
        quickBonusButton.classList.remove('active-countdown');
        quickBonusCooldownSpan.textContent = '';
    } else {
        quickBonusButton.disabled = true;
        quickBonusButton.classList.remove('pulsing');
        quickBonusButton.classList.add('active-countdown');
        
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
            quickBonusCooldownSpan.textContent = formatTime(timeLeft);
        };

        updateCountdown();
        quickBonusCountdownInterval = setInterval(updateCountdown, 1000);
    }
}

quickBonusButton.addEventListener('click', async () => {
    if (!userId) {
        showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Quick Bonus clicked - no user ID.');
        return;
    }
    if (quickBonusButton.disabled) {
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Quick Bonus clicked - button disabled.');
        return;
    }

    quickBonusButton.disabled = true;
    quickBonusButton.classList.remove('pulsing');
    quickBonusButton.classList.remove('active-countdown');
    quickBonusCooldownSpan.textContent = '';

    messageDiv.textContent = 'Отримуємо швидкий бонус...';
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Attempting to claim quick bonus...');

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
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Quick Bonus claimed: ${data.amount}`);
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Бонусу");
            messageDiv.className = 'text-red-500 font-bold';
            updateBalanceAndProgressDisplay();
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Quick Bonus API failed: ${data.error || 'Unknown'}`);
        }
    } catch (error) {
        console.error('[Quick Bonus] Помилка при отриманні швидкого бонусу:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером для швидкого бонусу.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        quickBonusButton.disabled = false;
        quickBonusButton.classList.add('pulsing');
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Quick Bonus network error: ${error.message}`);
    }
});


// =================================================================
// 🏆 Логіка Дошки Лідерів
// =================================================================

// Додамо перевірку на існування елементу перед додаванням слухача подій
if (leaderboardButton) {
    console.log("[Leaderboard] Leaderboard button element found. Attaching event listener.");
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard button found. Attaching listener.');

    leaderboardButton.addEventListener('click', async () => {
        console.log("[Leaderboard] Leaderboard button clicked.");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard button click event fired.');

        // Забезпечуємо, що модалка існує, перш ніж звертатися до її властивостей
        if (!leaderboardModal) {
            console.error("[Leaderboard] Leaderboard modal element not found!");
            showCustomModal('🚫 Помилка: елемент модального вікна лідерів не знайдено.', "Помилка UI");
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_ERROR: Leaderboard modal element missing.');
            return;
        }

        leaderboardTableBody.innerHTML = ''; // Очистити попередні дані
        leaderboardLoading.classList.remove('hidden'); // Показати завантаження
        leaderboardError.classList.add('hidden'); // Приховати помилку
        leaderboardModal.classList.add('active'); // Показати модалку (додаємо клас 'active')
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard modal activated, fetching data...');

        try {
            const response = await fetch(`${API_BASE_URL}/api/get_leaderboard`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            console.log("[Leaderboard] API response status:", response.status);
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Leaderboard API response status: ${response.status}`);

            if (!response.ok) {
                const errData = await response.json();
                console.error("[Leaderboard] API error data:", errData);
                leaderboardError.textContent = `Помилка: ${errData.error || 'Невідома помилка при завантаженні лідерів.'}`;
                leaderboardError.classList.remove('hidden');
                if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Leaderboard API failed: ${errData.error || 'Unknown'}`);
                return;
            }

            const data = await response.json();
            console.log("[Leaderboard] data received:", data);
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Leaderboard data received (count: ${data.leaderboard ? data.leaderboard.length : 0})`);

            if (data.leaderboard && data.leaderboard.length > 0) {
                data.leaderboard.sort((a, b) => {
                    if (b.level !== a.level) {
                        return b.level - a.level; // Sort by level descending
                    }
                    return b.xp - a.xp; // Then by XP descending
                });
                
                data.leaderboard.forEach((player, index) => {
                    const row = `
                        <tr class="${(index % 2 === 0) ? 'bg-gray-800' : 'bg-gray-700'}">
                            <td class="py-2 px-3 font-bold">${index + 1}</td>
                            <td class="py-2 px-3">${player.username}</td>
                            <td class="py-2 px-3 text-right">${player.level}</td>
                            <td class="py-2 px-3 text-right">${player.balance}</td>
                            <td class="py-2 px-3 text-right">${player.xp}</td>
                        </tr>
                    `;
                    leaderboardTableBody.insertAdjacentHTML('beforeend', row);
                });
                if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard table populated.');
            } else {
                leaderboardTableBody.innerHTML = '<tr><td colspan="5" class="py-4 text-center text-gray-400">Наразі немає лідерів. Будь першим!</td></tr>';
                if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard is empty.');
            }
        } catch (error) {
            console.error('[Leaderboard] Помилка при завантаженні дошки лідерів:', error);
            leaderboardError.textContent = '🚫 Не вдалося зʼєднатись із сервером для завантаження дошки лідерів.';
            leaderboardError.classList.remove('hidden');
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Leaderboard network error: ${error.message}`);
        } finally {
            leaderboardLoading.classList.add('hidden');
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard fetch complete.');
        }
    });

    // Закриття модалки дошки лідерів
    if (leaderboardModal && leaderboardModal.querySelector('.close-button')) {
        leaderboardModal.querySelector('.close-button').addEventListener('click', () => {
            console.log("[Leaderboard] Leaderboard modal close button clicked.");
            leaderboardModal.classList.remove('active');
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Leaderboard modal closed.');
        });
    } else {
        console.error("[Leaderboard] Leaderboard modal or its close button not found for close listener.");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_ERROR: Leaderboard modal close button missing.');
    }

} else {
    console.error("[Leaderboard] Leaderboard button element not found! Ensure ID 'leaderboardButton' is correct in index.html.");
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_ERROR: Leaderboard button HTML element not found.');
}


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
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Spin clicked - no user ID.');
        return;
    }

    spinButton.disabled = true;
    spinButton.classList.remove('pulsing');
    messageDiv.textContent = '';
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Spin button clicked, starting spin process.');

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
                    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Big Win! ${data.winnings} coins.`);
                } else {
                    messageDiv.className = 'message win-message';
                    playWinSoundEffect();
                    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_LOG: Win! ${data.winnings} coins.`);
                }
            } else {
                messageDiv.textContent = '😢 Спробуйте ще раз!';
                messageDiv.className = 'message lose-message text-red-400';
                playLoseSoundEffect();
                if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Lose on spin.');
            }
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка сервера.'}`, "Помилка Спіна");
            messageDiv.className = 'text-red-500 font-bold';
            playLoseSoundEffect();
            if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Spin API failed: ${data.error || 'Unknown'}`);
        }
    } catch (error) {
        console.error('[Spin] Помилка при спіні:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером. Перевірте зʼєднання.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        playLoseSoundEffect();
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData(`JS_ERROR: Spin network error: ${error.message}`);
    } finally {
        spinButton.disabled = false;
        spinButton.classList.add('pulsing');
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Spin process finished.');
    }
});

// =================================================================
// 🚀 Початкове завантаження та перевірка аудіо
// =================================================================
window.onload = () => {
    console.log("[Init] Window loaded.");
    if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Window loaded, starting init checks.');

    // Включаємо аудіо контекст, якщо він ще не запущений
    if (Tone.context.state !== 'running') {
        audioPrompt.style.display = 'flex';
        console.log("[Init] AudioContext not running, showing prompt.");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: AudioContext prompt visible.');
    } else {
        audioPrompt.style.display = 'none';
        console.log("[Init] AudioContext already running, hiding prompt.");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: AudioContext already running.');
    }

    // Завантажуємо дані користувача тільки якщо userId вже доступний
    if (userId) {
        console.log("[Init] User ID available, updating balance and progress display.");
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: Fetching initial user data...');
        updateBalanceAndProgressDisplay();
    } else {
        console.warn("[Init] User ID not available, displaying Telegram launch message.");
        messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
        messageDiv.className = 'text-yellow-400';
        if (typeof Telegram !== 'undefined' && Telegram.WebApp) Telegram.WebApp.sendData('JS_LOG: No User ID, app needs Telegram launch.');
    }
};
