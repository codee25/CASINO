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
const WILD_SYMBOL = '⭐'; // Wild символ
const SCATTER_SYMBOL = '💰'; // Scatter символ (для фріспінів або бонусів)

const ALL_REEL_SYMBOLS = [...SYMBOLS, WILD_SYMBOL, SCATTER_SYMBOL]; // Всі символи на барабанах

const BET_AMOUNT = 100;
const REEL_HEIGHT_PX = 100; // Висота одного символу на барабані у пікселях (з CSS)
const REEL_SPIN_CYCLES = 5; // Кількість повних прокруток барабанів перед зупинкою
const REEL_SPIN_DURATION_BASE = 0.8; // Базова тривалість обертання одного барабана
const REEL_STOP_DURATION = 1.0; // Тривалість зупинки від верхньої точки до фінального символу
const REEL_STOP_EASE = "power2.out"; // Ефект плавного зупинення
const REEL_STOP_STAGGER = 0.2; // Затримка між зупинками барабанів (перший, потім другий, потім третій)

// XP та Рівні (Мають збігатися з бекендом main.py!)
const LEVEL_THRESHOLDS = [
    0,    // Level 1: 0 XP
    100,  // Level 2: 100 XP
    300,  // Level 3: 300 XP
    600,  // Level 4: 600 XP
    1000, // Level 5: 1000 XP
    1500, // Level 6: 1500 XP
    2200, // Level 7: 2200 XP
    3000, // Level 8: 3000 XP
    4000, // Level 9: 4000 XP
    5500, // Level 10: 5500 XP
    7500, // Level 11: 7500 XP
    10000 // Level 12: 10000 XP
];

const DAILY_BONUS_AMOUNT = 300; // Ця сума має збігатися з бекендом
const DAILY_BONUS_COOLDOWN_HOURS = 24; // Цей кулдаун має збігатися з бекендом

// =================================================================
// 🧠 Ініціалізація Telegram WebApp
// =================================================================
let userId = null; // ID користувача Telegram
let lastKnownUserBalance = 0; // Для оптимізації оновлення балансу
let lastKnownUserXP = 0; // Для оптимізації оновлення XP
let lastKnownUserLevel = 1; // Для оптимізації оновлення рівня

if (typeof Telegram !== 'undefined' && Telegram.WebApp && Telegram.WebApp.initDataUnsafe?.user?.id) {
    userId = Telegram.WebApp.initDataUnsafe.user.id;
    console.log(`Telegram User ID: ${userId}`);
    Telegram.WebApp.expand(); // Розгортаємо WebApp на весь екран для кращого досвіду
} else {
    console.warn('Telegram WebApp не знайдено або ви тестуєте не через Telegram. Деякі функції можуть не працювати.');
    messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
    messageDiv.className = 'text-yellow-400 font-bold';
    spinButton.disabled = true;
    spinButton.classList.remove('pulsing');
    dailyBonusButton.disabled = true;
}

// 🌐 URL бекенду (твій актуальний Render URL)
// ОБОВ'ЯЗКОВО ПЕРЕВІРТЕ І ОНОВІТЬ ЦЕЙ URL НА АКТУАЛЬНИЙ URL ВАШОГО БОТА НА RENDER.COM
const API_BASE_URL = 'https://casino-0h0l.onrender.com';

// =================================================================
// 🔊 Ініціалізація звукових ефектів (Tone.js)
// =================================================================
let spinStartSound, reelStopSound, winSound, bigWinSound, loseSound, levelUpSound, dailyBonusSound;

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
            audioPrompt.style.display = 'none'; // Hide prompt if audio started
        } catch (e) {
            console.error("Помилка ініціалізації аудіо:", e);
            audioPrompt.style.display = 'flex'; // Show prompt again if failed
            return; 
        }
    } else {
        audioPrompt.style.display = 'none'; // Hide prompt if already running
    }

    spinStartSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.01, decay: 0.1, sustain: 0.05, release: 0.1 } });
    reelStopSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.005, decay: 0.05, sustain: 0.01, release: 0.1 } });
    winSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sine" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
    bigWinSound = createSynthSound({ type: "PolySynth", oscillator: { type: "triangle" }, envelope: { attack: 0.05, decay: 0.5, sustain: 0.2, release: 1.0 } });
    loseSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "square" }, envelope: { attack: 0.01, decay: 0.3, sustain: 0.1, release: 0.4 } });
    levelUpSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sawtooth" }, envelope: { attack: 0.02, decay: 0.3, sustain: 0.2, release: 0.8 } });
    dailyBonusSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "triangle" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
}

// Запускаємо ініціалізацію звуків після першої взаємодії користувача
window.addEventListener('click', () => setupSounds(), { once: true });
window.addEventListener('touchstart', () => setupSounds(), { once: true }); // For mobile devices

// Активація аудіо через кнопку (якщо автозапуск заблоковано)
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


// =================================================================
// 💬 Кастомне модальне вікно для сповіщень
// =================================================================
function showCustomModal(msg, title = "Повідомлення") {
    modalMessage.innerHTML = `<h3 class="text-xl font-bold mb-2">${title}</h3><p>${msg}</p>`;
    customModal.classList.add('active'); // Використовуємо клас для анімації
}
// Додаємо обробник для кнопки закриття модального вікна
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
            body: JSON.stringify({ user_id: userId })
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

        // Анімація зміни балансу
        if (currentBalance !== lastKnownUserBalance) {
            userBalanceSpan.classList.remove('animate-pulse-balance');
            void userBalanceSpan.offsetWidth; // Trigger reflow
            userBalanceSpan.classList.add('animate-pulse-balance');
            userBalanceSpan.textContent = currentBalance;
            lastKnownUserBalance = currentBalance;
        }

        // Оновлення XP та Рівня
        const nextLevelThreshold = LEVEL_THRESHOLDS[currentLevel] || LEVEL_THRESHOLDS[LEVEL_THRESHOLDS.length - 1]; // Забезпечуємо поріг для останнього рівня
        
        userLevelSpan.textContent = currentLevel;
        userXpSpan.textContent = currentXP;
        nextLevelXpSpan.textContent = nextLevelThreshold;
        
        const xpProgress = Math.min(100, (currentXP / nextLevelThreshold) * 100);
        xpProgressBar.style.width = `${xpProgress}%`;

        if (currentLevel > lastKnownUserLevel && lastKnownUserLevel !== 0) { // Перевірка на підвищення рівня, не при першому завантаженні
            playLevelUpSound();
            showCustomModal(`🎉 Ви досягли Рівня ${currentLevel}! 🎉`, "Підвищення Рівня!");
        }
        lastKnownUserXP = currentXP;
        lastKnownUserLevel = currentLevel;
        

        // Оновлення стану кнопки щоденного бонусу
        updateDailyBonusButton(lastDailyClaim);

        messageDiv.textContent = ''; // Очистити попередні повідомлення
        messageDiv.className = 'text-white'; // Скинути стиль повідомлень
    } catch (error) {
        console.error('Помилка при отриманні балансу та прогресу:', error);
        showCustomModal('🚫 Помилка звʼязку з сервером. Перевірте зʼєднання.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
    }
}

// Логіка для щоденного бонусу
function updateDailyBonusButton(lastClaimTime) {
    const now = new Date();
    const cooldownDuration = DAILY_BONUS_COOLDOWN_HOURS * 60 * 60 * 1000; // у мілісекундах

    if (!lastClaimTime || (now - lastClaimTime) >= cooldownDuration) {
        dailyBonusButton.disabled = false;
        dailyBonusButton.classList.add('pulsing');
        dailyBonusCooldownSpan.textContent = '';
    } else {
        dailyBonusButton.disabled = true;
        dailyBonusButton.classList.remove('pulsing');
        const timeLeft = cooldownDuration - (now - lastClaimTime);
        const hours = Math.floor(timeLeft / (1000 * 60 * 60));
        const minutes = Math.floor((timeLeft % (1000 * 60 * 60)) / (1000 * 60));
        dailyBonusCooldownSpan.textContent = `(${hours}год ${minutes}хв)`;
        // Оновлювати таймер кожну хвилину
        setTimeout(() => updateDailyBonusButton(lastClaimTime), (minutes % 1 === 0 ? 60 : (minutes % 1) * 60) * 1000); 
    }
}

dailyBonusButton.addEventListener('click', async () => {
    if (!userId) {
        showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
        return;
    }
    if (dailyBonusButton.disabled) return; // Запобігти подвійному кліку

    dailyBonusButton.disabled = true;
    dailyBonusButton.classList.remove('pulsing');
    messageDiv.textContent = 'Отримуємо бонус...';

    try {
        const response = await fetch(`${API_BASE_URL}/api/claim_daily_bonus`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        const data = await response.json();

        if (response.ok) {
            playDailyBonusSound();
            showCustomModal(`🎉 Ви отримали ${data.amount} фантиків!`, "Щоденний Бонус!");
            updateBalanceAndProgressDisplay(); // Оновити баланс і стан кнопки
        } else {
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Бонусу");
            messageDiv.className = 'text-red-500 font-bold';
            // Не вмикаємо кнопку одразу, щоб кулдаун спрацював
            updateBalanceAndProgressDisplay(); // Оновити стан кнопки з врахуванням кулдауну
        }
    } catch (error) {
        console.error('Помилка при отриманні щоденного бонусу:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером для бонусу.', "Помилка");
        messageDiv.className = 'text-red-500 font-bold';
        dailyBonusButton.disabled = false; // Вмикаємо кнопку лише при справжній помилці мережі
        dailyBonusButton.classList.add('pulsing');
    }
});


// =================================================================
// 🎞️ Анімація Барабанів (використання GSAP)
// =================================================================
function animateReels(reels, finalSymbols) {
    return new Promise(resolve => {
        const masterTimeline = gsap.timeline({ onComplete: resolve }); // Головна timeline для контролю всієї анімації спіна

        reels.forEach((reel, index) => {
            const reelContent = reel.querySelector('.reel-content');
            
            // Створюємо послідовність символів для анімації
            const numSpinSymbols = REEL_SPIN_CYCLES * ALL_REEL_SYMBOLS.length;
            let animationSymbols = [];
            for (let i = 0; i < numSpinSymbols; i++) {
                animationSymbols.push(ALL_REEL_SYMBOLS[Math.floor(Math.random() * ALL_REEL_SYMBOLS.length)]);
            }
            animationSymbols.push(finalSymbols[index]); // Додаємо фінальний символ в кінець

            reelContent.innerHTML = animationSymbols.map(s => `<div class="reel-symbol">${s}</div>`).join('');
            
            // Встановлюємо початкове положення
            gsap.set(reelContent, { y: 0 });
            reel.classList.add('spinning'); // Додати клас для blur ефекту

            // Анімація прокрутки для кожного барабана
            masterTimeline.to(reelContent, {
                y: -(animationSymbols.length - 1) * REEL_HEIGHT_PX, // Прокрутка до кінця анімаційних символів
                duration: REEL_SPIN_DURATION_BASE + (index * REEL_STOP_STAGGER), // Додаємо затримку до тривалості для послідовної зупинки
                ease: "linear", // Лінійна швидкість під час прокрутки
                onStart: () => {
                    if (index === 0) playSpinStartSound(); // Звук початку спіна лише для першого барабана
                },
                onUpdate: function() {
                    // Частий звук під час спіна, якщо аудіо активне
                    if (this.progress() > 0.1 && this.progress() < 0.95 && Tone.context.state === 'running') {
                        playReelStopSound("C4");
                    }
                }
            }, 0); // Всі барабани починають "обертатися" одночасно

            // Фінальна анімація зупинки на потрібному символі
            masterTimeline.to(reelContent, {
                y: -((animationSymbols.length - 1) * REEL_HEIGHT_PX), // Точна позиція для зупинки
                duration: REEL_STOP_DURATION,
                ease: REEL_STOP_EASE, // Плавне сповільнення
                overwrite: true, // Гарантує, що попередня анімація буде замінена
                onComplete: () => {
                    reel.classList.remove('spinning'); // Видалити клас blur
                    reelContent.innerHTML = `<div class="reel-symbol">${finalSymbols[index]}</div>`; // Встановити тільки фінальний символ
                    gsap.set(reelContent, { y: 0 }); // Скинути позицію для коректного відображення
                    playReelStopSound("G4"); // Звук зупинки барабана
                }
            }, `<${index * REEL_STOP_STAGGER}`); // Починаємо зупинку послідовно з затримкою
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

    spinButton.disabled = true; // Вимикаємо кнопку під час спіна
    spinButton.classList.remove('pulsing'); // Вимкнути анімацію пульсації
    messageDiv.textContent = ''; // Очищаємо повідомлення

    // Заповнюємо барабани початковим символом (?) і готуємо їх до анімації
    reelElements.forEach((reel) => {
        const reelContent = reel.querySelector('.reel-content');
        reelContent.innerHTML = `<div class="reel-symbol">?</div>`; 
        gsap.set(reelContent, { y: 0 }); // Скидаємо будь-які попередні трансформації
        reel.classList.remove('spinning'); // Гарантуємо, що blur немає до старту спіна
    });

    try {
        const response = await fetch(`${API_BASE_URL}/api/spin`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ user_id: userId })
        });

        const data = await response.json(); // Отримуємо JSON відповідь

        if (response.ok) {
            await animateReels(reelElements, data.symbols); // Анімуємо барабани до фінальних символів
            updateBalanceAndProgressDisplay(); // Оновлюємо баланс та прогрес

            if (data.winnings > 0) {
                messageDiv.textContent = `🎉 Ви виграли ${data.winnings} фантиків! 🎉`;
                if (data.winnings >= 500) { // Приклад: великий виграш
                    messageDiv.className = 'message big-win-message'; // Клас для великого виграшу
                    playBigWinSoundEffect();
                } else {
                    messageDiv.className = 'message win-message'; // Клас для звичайного виграшу
                    playWinSoundEffect();
                }
            } else {
                messageDiv.textContent = '😢 Спробуйте ще раз!';
                messageDiv.className = 'message lose-message text-red-400';
                playLoseSoundEffect();
            }
        } else {
            // Обробка помилок від сервера (наприклад, недостатньо коштів)
            showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка сервера.'}`, "Помилка Спіна");
            messageDiv.className = 'message lose-message text-red-500 font-bold';
            playLoseSoundEffect();
        }
    } catch (error) {
        console.error('Помилка при спіні:', error);
        showCustomModal('🚫 Не вдалося зʼєднатись із сервером. Перевірте зʼєднання.', "Помилка");
        messageDiv.className = 'message lose-message text-red-500 font-bold';
        playLoseSoundEffect();
    } finally {
        spinButton.disabled = false; // Вмикаємо кнопку
        spinButton.classList.add('pulsing'); // Знову вмикаємо анімацію пульсації
    }
});

// =================================================================
// 🚀 Початкове завантаження та перевірка аудіо
// =================================================================
window.onload = () => {
    // Спочатку перевіряємо, чи потрібно показати підказку для аудіо
    if (Tone.context.state !== 'running') {
        audioPrompt.style.display = 'flex';
    } else {
        audioPrompt.style.display = 'none';
    }

    if (userId) {
        updateBalanceAndProgressDisplay(); // Оновлюємо баланс та прогрес
    } else {
        messageDiv.textContent = '⚠️ Будь ласка, запустіть гру через Telegram.';
        messageDiv.className = 'text-yellow-400';
    }
};

