// Use window.React for unpkg.com global React
const React = window.React;
const { useState, useEffect, createContext, useContext, useCallback, useRef } = window.React;

// Ensure Tone is globally available from the CDN script in index.html
const Tone = window.Tone || {
    context: { state: 'suspended', resume: async () => {}, start: async () => {} },
    MembraneSynth: function() { return { toDestination: () => this, set: () => {}, triggerAttackRelease: () => {} }; },
    PolySynth: function() { return { toDestination: () => this, set: () => {}, triggerAttackRelease: () => {} }; },
    NoiseSynth: function() { return { toDestination: () => this, set: () => {}, triggerAttackRelease: () => {} }; }
};


// -----------------------------------------------------------------------------
// Global User Context
// -----------------------------------------------------------------------------
const UserContext = createContext(null);

const UserProvider = ({ children }) => {
    const [user, setUser] = useState({
        userId: null,
        username: 'Unnamed Player',
        balance: 0,
        xp: 0,
        level: 1,
        nextLevelXp: 100, // Default, will be updated from backend
        lastDailyBonusClaim: null,
        lastQuickBonusClaim: null
    });
    const [isLoading, setIsLoading] = useState(true);
    const [error, setError] = useState(null);

    // Backend API URL (ВАШ АКТУАЛЬНИЙ URL!)
    const API_BASE_URL = 'https://casino-0h0l.onrender.com'; // ЗМІНІТЬ ЦЕЙ URL!

    // Helper to send logs to Telegram bot for debugging
    const sendTelegramLog = useCallback((message, type = 'JS_LOG') => {
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.sendData(`${type}: ${message}`);
        } else {
            console.log(`[${type}] ${message}`);
        }
    }, []);

    const fetchUserData = useCallback(async () => {
        setIsLoading(true);
        setError(null);
        let currentUserId = null;
        let currentUsername = 'Unnamed Player';

        if (window.Telegram && window.Telegram.WebApp && window.Telegram.WebApp.initDataUnsafe?.user?.id) {
            currentUserId = window.Telegram.WebApp.initDataUnsafe.user.id;
            currentUsername = window.Telegram.WebApp.initDataUnsafe.user.username || window.Telegram.WebApp.initDataUnsafe.user.first_name || `Гравець ${String(currentUserId).slice(-4)}`;
            sendTelegramLog(`WebApp Init: User ID ${currentUserId}, Username ${currentUsername}`);
            window.Telegram.WebApp.expand();
        } else {
            sendTelegramLog('WebApp NOT Initialized - user ID missing (likely direct access)', 'JS_WARN');
            setError('Будь ласка, запустіть гру через Telegram для доступу до балансу.');
            setIsLoading(false);
            return;
        }

        try {
            const response = await fetch(`${API_BASE_URL}/api/get_balance`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: currentUserId, username: currentUsername })
            });

            if (!response.ok) {
                const errData = await response.json();
                sendTelegramLog(`Balance fetch API failed: ${errData.error || 'Unknown error'}`, 'JS_ERROR');
                throw new Error(errData.error || 'Failed to fetch user data');
            }

            const data = await response.json();
            setUser(prevUser => {
                const newUserState = {
                    userId: currentUserId,
                    username: currentUsername,
                    balance: data.balance,
                    xp: data.xp,
                    level: data.level,
                    nextLevelXp: data.next_level_xp,
                    lastDailyBonusClaim: data.last_daily_bonus_claim ? new Date(data.last_daily_bonus_claim) : null,
                    lastQuickBonusClaim: data.last_quick_bonus_claim ? new Date(data.last_quick_bonus_claim) : null
                };
                // Play level up sound if level increased
                if (prevUser.level !== 1 && newUserState.level > prevUser.level) {
                    playLevelUpSound();
                    showCustomModal(`🎉 Ви досягли Рівня ${newUserState.level}! 🎉`, "Підвищення Рівня!");
                }
                return newUserState;
            });
            sendTelegramLog('User data fetched and updated successfully.');
        } catch (err) {
            console.error('Error fetching user data:', err);
            setError(err.message || 'Помилка звʼязку з сервером. Перевірте зʼєднання.');
            sendTelegramLog(`Error during user data fetch: ${err.message}`, 'JS_ERROR');
        } finally {
            setIsLoading(false);
        }
    }, [sendTelegramLog]);

    useEffect(() => {
        fetchUserData();
        // Set up interval to refresh balance periodically if needed, or rely on game actions
        // const interval = setInterval(fetchUserData, 60000); // refresh every 60 seconds
        // return () => clearInterval(interval);
    }, [fetchUserData]);

    return (
        <UserContext.Provider value={{ user, setUser, fetchUserData, isLoading, error, API_BASE_URL, sendTelegramLog }}>
            {children}
        </UserContext.Provider>
    );
};

const useUser = () => useContext(UserContext);


// -----------------------------------------------------------------------------
// Audio Context Setup (Tone.js)
// -----------------------------------------------------------------------------
let spinStartSound, reelStopSound, winSound, bigWinSound, loseSound, levelUpSound, dailyBonusSound, quickBonusSound, coinFlipSound;

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
            document.getElementById('audioPrompt').style.display = 'none';
        } catch (e) {
            console.error("[Audio] Помилка ініціалізації аудіо:", e);
            document.getElementById('audioPrompt').style.display = 'flex';
            return;
        }
    } else {
        document.getElementById('audioPrompt').style.display = 'none';
    }

    spinStartSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.01, decay: 0.1, sustain: 0.05, release: 0.1 } });
    reelStopSound = createSynthSound({ type: "MembraneSynth", envelope: { attack: 0.005, decay: 0.05, sustain: 0.01, release: 0.1 } });
    winSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sine" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
    bigWinSound = createSynthSound({ type: "PolySynth", oscillator: { type: "triangle" }, envelope: { attack: 0.05, decay: 0.5, sustain: 0.2, release: 1.0 } });
    loseSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "square" }, envelope: { attack: 0.01, decay: 0.3, sustain: 0.1, release: 0.4 } });
    levelUpSound = createSynthSound({ type: "PolySynth", oscillator: { type: "sawtooth" }, envelope: { attack: 0.02, decay: 0.3, sustain: 0.2, release: 0.8 } });
    dailyBonusSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "triangle" }, envelope: { attack: 0.01, decay: 0.2, sustain: 0.1, release: 0.5 } });
    quickBonusSound = createSynthSound({ type: "PolySynth", oscillator: { type: "triangle" }, envelope: { attack: 0.01, decay: 0.1, sustain: 0.05, release: 0.3 } });
    coinFlipSound = createSynthSound({ type: "MembraneSynth", oscillator: { type: "square" }, envelope: { attack: 0.01, decay: 0.15, sustain: 0.05, release: 0.2 } });
}

function playSpinStartSound() { if (spinStartSound && Tone.context.state === 'running') spinStartSound.triggerAttackRelease("C4", "8n"); }
function playReelStopSound(note = "D4") { if (reelStopSound && Tone.context.state === 'running') reelStopSound.triggerAttackRelease(note, "16n"); }
function playWinSoundEffect() { if (winSound && Tone.context.state === 'running') winSound.triggerAttackRelease(["C5", "E5", "G5"], "4n"); }
function playBigWinSoundEffect() { if (bigWinSound && Tone.context.state === 'running') bigWinSound.triggerAttackRelease(["C5", "G5", "C6"], "1n"); }
function playLoseSoundEffect() { if (loseSound && Tone.context.state === 'running') loseSound.triggerAttackRelease("C3", "4n"); }
function playLevelUpSound() { if (levelUpSound && Tone.context.state === 'running') levelUpSound.triggerAttackRelease(["E4", "G4", "C5"], "0.8n"); }
function playDailyBonusSound() { if (dailyBonusSound && Tone.context.state === 'running') dailyBonusSound.triggerAttackRelease("G4", "0.5n"); }
function playQuickBonusSound() { if (quickBonusSound && Tone.context.state === 'running') quickBonusSound.triggerAttackRelease("A4", "0.2n"); }
function playCoinFlipSound() { if (coinFlipSound && Tone.context.state === 'running') coinFlipSound.triggerAttackRelease("C5", "0.1s"); }


// -----------------------------------------------------------------------------
// Custom Modal for Alerts
// -----------------------------------------------------------------------------
function showCustomModal(msg, title = "Повідомлення") {
    const modalElement = document.getElementById('customModal');
    const modalMessageElement = document.getElementById('modalMessage');
    if (modalElement && modalMessageElement) {
        modalMessageElement.innerHTML = `<h3 class="text-xl font-bold mb-2">${title}</h3><p>${msg}</p>`;
        modalElement.classList.add('active');
        if (window.Telegram && window.Telegram.WebApp) {
            window.Telegram.WebApp.sendData(`JS_LOG: Showing modal: ${title} - ${msg.substring(0, Math.min(msg.length, 50))}`);
        }
    }
}
// Add event listeners for modal close buttons outside React for simplicity
document.addEventListener('DOMContentLoaded', () => {
    const customModal = document.getElementById('customModal');
    if (customModal) {
        customModal.querySelector('.close-button').addEventListener('click', () => {
            customModal.classList.remove('active');
        });
        customModal.querySelector('.modal-content button').addEventListener('click', () => {
            customModal.classList.remove('active');
        });
    }
    const audioPrompt = document.getElementById('audioPrompt');
    if (audioPrompt) {
        const activateAudioButton = document.getElementById('activateAudioButton');
        if (activateAudioButton) {
            activateAudioButton.addEventListener('click', async () => {
                await setupSounds();
            });
        }
        // Initial check for audio context state
        if (Tone.context.state !== 'running') {
            audioPrompt.style.display = 'flex';
        } else {
            audioPrompt.style.display = 'none';
        }
    }
});


// -----------------------------------------------------------------------------
// Slot Machine Game Component
// -----------------------------------------------------------------------------
const SYMBOLS = ['🍒', '🍋', '🍊', '🍇', '🔔', '💎', '🍀'];
const WILD_SYMBOL = '⭐';
const SCATTER_SYMBOL = '💰';
const ALL_REEL_SYMBOLS = [...SYMBOLS, WILD_SYMBOL, SCATTER_SYMBOL];
const REEL_HEIGHT_PX = 90;
const REEL_SPIN_CYCLES = 5;
const REEL_SPIN_DURATION_BASE = 0.8;
const REEL_STOP_DURATION = 1.0;
const REEL_STOP_EASE = "power2.out";
const REEL_STOP_STAGGER = 0.2;
const BET_AMOUNT = 100; // Ставка для слотів

const SlotMachine = () => {
    const { user, fetchUserData, API_BASE_URL, sendTelegramLog } = useUser();
    const [message, setMessage] = useState('');
    const [messageClass, setMessageClass] = useState('');
    const [isSpinning, setIsSpinning] = useState(false);
    const reelRefs = [useRef(null), useRef(null), useRef(null)]; // Refs for GSAP animation

    const animateReels = useCallback((finalSymbols) => {
        return new Promise(resolve => {
            const masterTimeline = gsap.timeline({ onComplete: resolve });

            reelRefs.forEach((reelRef, index) => {
                const reelContent = reelRef.current.querySelector('.reel-content');
                
                const numSpinSymbols = REEL_SPIN_CYCLES * ALL_REEL_SYMBOLS.length;
                let animationSymbols = [];
                for (let i = 0; i < numSpinSymbols; i++) {
                    animationSymbols.push(ALL_REEL_SYMBOLS[Math.floor(Math.random() * ALL_REEL_SYMBOLS.length)]);
                }
                animationSymbols.push(finalSymbols[index]); // Ensure final symbol is at the end

                reelContent.innerHTML = animationSymbols.map(s => `<div class="reel-symbol">${s}</div>`).join('');
                
                gsap.set(reelContent, { y: 0 }); // Reset position
                reelRef.current.classList.add('spinning'); // Add spinning class

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
                        reelRef.current.classList.remove('spinning');
                        // Set the exact final symbol
                        reelContent.innerHTML = `<div class="reel-symbol">${finalSymbols[index]}</div>`;
                        gsap.set(reelContent, { y: 0 }); // Ensure it snaps to correct position
                        playReelStopSound("G4");
                    }
                }, `<${index * REEL_STOP_STAGGER}`); // Stagger starts
            });
        });
    }, [reelRefs]);

    const handleSpin = async () => {
        if (!user.userId) {
            showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб грати.', "Недоступно");
            return;
        }
        if (isSpinning || user.balance < BET_AMOUNT) return;

        setIsSpinning(true);
        setMessage('');
        sendTelegramLog('Spin button clicked, starting spin process.');

        try {
            const response = await fetch(`${API_BASE_URL}/api/spin`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: user.userId })
            });

            const data = await response.json();

            if (response.ok) {
                await animateReels(data.symbols); // Animate with actual results
                await fetchUserData(); // Update balance and XP from backend

                if (data.winnings > 0) {
                    setMessage(`🎉 Ви виграли ${data.winnings} фантиків! 🎉`);
                    if (data.winnings >= 500) {
                        setMessageClass('message big-win-message');
                        playBigWinSoundEffect();
                    } else {
                        setMessageClass('message win-message');
                        playWinSoundEffect();
                    }
                    sendTelegramLog(`Win: ${data.winnings} coins.`);
                } else {
                    setMessage('😢 Спробуйте ще раз!');
                    setMessageClass('message lose-message text-red-400');
                    playLoseSoundEffect();
                    sendTelegramLog('Lose on spin.');
                }
            } else {
                showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка сервера.'}`, "Помилка Спіна");
                setMessageClass('text-red-500 font-bold');
                playLoseSoundEffect();
                sendTelegramLog(`Spin API failed: ${data.error || 'Unknown'}`, 'JS_ERROR');
            }
        } catch (error) {
            console.error('Помилка при спіні:', error);
            showCustomModal('🚫 Не вдалося зʼєднатись із сервером. Перевірте зʼєднання.', "Помилка");
            setMessageClass('text-red-500 font-bold');
            playLoseSoundEffect();
            sendTelegramLog(`Spin network error: ${error.message}`, 'JS_ERROR');
        } finally {
            setIsSpinning(false);
        }
    };

    return (
        <div className="flex-grow flex flex-col items-center justify-around p-4 md:p-8 w-full">
            <h1 className="text-3xl md:text-4xl font-extrabold text-yellow-400 mb-4 drop-shadow-lg leading-tight text-center">
                Ігрові Автомати
            </h1>
            
            {/* Slot Machine Reels */}
            <div className="slot-machine flex justify-center gap-2 mb-4 w-full max-w-xs md:max-w-sm mx-auto relative px-1 py-3">
                <div className="absolute -top-3 left-1/2 transform -translate-x-1/2 w-3/4 h-2 bg-gradient-to-r from-yellow-400 to-red-500 rounded-t-lg shadow-lg"></div>
                {/* Reel 1 */}
                <div id="reel1" ref={reelRefs[0]} className="reel relative w-24 h-24 md:w-28 md:h-28 rounded-lg bg-gray-800 border-3 border-yellow-500 overflow-hidden shadow-inner transform-gpu">
                    <div className="reel-content absolute top-0 left-0 w-full h-full"></div>
                </div>
                {/* Reel 2 */}
                <div id="reel2" ref={reelRefs[1]} className="reel relative w-24 h-24 md:w-28 md:h-28 rounded-lg bg-gray-800 border-3 border-yellow-500 overflow-hidden shadow-inner transform-gpu">
                    <div className="reel-content absolute top-0 left-0 w-full h-full"></div>
                </div>
                {/* Reel 3 */}
                <div id="reel3" ref={reelRefs[2]} className="reel relative w-24 h-24 md:w-28 md:h-28 rounded-lg bg-gray-800 border-3 border-yellow-500 overflow-hidden shadow-inner transform-gpu">
                    <div className="reel-content absolute top-0 left-0 w-full h-full"></div>
                </div>
                <div className="absolute -bottom-3 left-1/2 transform -translate-x-1/2 w-3/4 h-2 bg-gradient-to-r from-red-500 to-yellow-400 rounded-b-lg shadow-lg"></div>
            </div>

            {/* Spin Button */}
            <div className="game-controls flex flex-col md:flex-row gap-3 mb-4 w-full max-w-xs md:max-w-sm">
                <button 
                    id="spinButton"
                    onClick={handleSpin}
                    disabled={isSpinning || user.balance < BET_AMOUNT}
                    className={`spin-button bg-gradient-to-r from-green-500 to-emerald-600 hover:from-green-600 hover:to-emerald-700 text-white font-extrabold py-3 px-6 rounded-full text-lg md:text-xl shadow-xl transition-all duration-300 ease-in-out transform hover:scale-105 active:scale-95 w-full uppercase tracking-wider flex items-center justify-center ${!isSpinning && user.balance >= BET_AMOUNT ? 'pulsing' : ''}`}
                >
                    Крутити! (Ставка: {BET_AMOUNT})
                </button>
            </div>
            
            <div id="message" className={`message text-base md:text-lg font-semibold mt-4 min-h-[30px] flex items-center justify-center text-center w-full max-w-sm ${messageClass}`}>
                {message}
            </div>
        </div>
    );
};


// -----------------------------------------------------------------------------
// Coin Flip Game Component (New Game)
// -----------------------------------------------------------------------------
const COIN_FLIP_BET_AMOUNT = 50; // Ставка для монетки

const CoinFlip = () => {
    const { user, fetchUserData, API_BASE_URL, sendTelegramLog } = useUser();
    const [message, setMessage] = useState('');
    const [resultCoin, setResultCoin] = useState(''); // 'heads' or 'tails'
    const [isFlipping, setIsFlipping] = useState(false);
    const [lastChoice, setLastChoice] = useState(null); // To show user's choice

    const handleFlip = async (choice) => {
        if (!user.userId) {
            showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб грати.', "Недоступно");
            return;
        }
        if (isFlipping || user.balance < COIN_FLIP_BET_AMOUNT) return;

        setIsFlipping(true);
        setMessage('');
        setResultCoin('');
        setLastChoice(choice);
        playCoinFlipSound();
        sendTelegramLog(`Coin Flip: User chose ${choice}, starting flip.`);

        try {
            const response = await fetch(`${API_BASE_URL}/api/coin_flip`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: user.userId, choice: choice })
            });

            const data = await response.json();

            if (response.ok) {
                // Animate coin flip UI (simplified for now)
                await new Promise(resolve => setTimeout(resolve, 1500)); // Simulate flip time

                setResultCoin(data.result);
                setMessage(data.message);
                await fetchUserData(); // Update balance and XP from backend
                sendTelegramLog(`Coin Flip Result: ${data.result}, Winnings: ${data.winnings}`);
            } else {
                showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка сервера.'}`, "Помилка Підкидання");
                sendTelegramLog(`Coin Flip API failed: ${data.error || 'Unknown'}`, 'JS_ERROR');
            }
        } catch (error) {
            console.error('Помилка при підкиданні монетки:', error);
            showCustomModal('🚫 Не вдалося зʼєднатись із сервером. Перевірте зʼєднання.', "Помилка");
            sendTelegramLog(`Coin Flip network error: ${error.message}`, 'JS_ERROR');
        } finally {
            setIsFlipping(false);
        }
    };

    return (
        <div className="flex-grow flex flex-col items-center justify-around p-4 md:p-8 w-full">
            <h1 className="text-3xl md:text-4xl font-extrabold text-yellow-400 mb-4 drop-shadow-lg leading-tight text-center">
                Підкидання Монетки
            </h1>

            <div className="coin-flip-area flex flex-col items-center mb-6">
                <div className={`coin-display text-9xl transition-transform duration-500 ease-out ${isFlipping ? 'animate-spin-3d' : ''}`}>
                    {resultCoin === 'heads' && '🪙'}
                    {resultCoin === 'tails' && '💰'} {/* Використаємо '💰' для 'tails' тимчасово */}
                    {!resultCoin && '❓'}
                </div>
                {lastChoice && !isFlipping && <p className="text-gray-300 text-lg mt-2">Ваш вибір: {lastChoice === 'heads' ? 'Орел' : 'Решка'}</p>}
            </div>

            <div className="game-controls flex flex-col md:flex-row gap-3 mb-4 w-full max-w-xs md:max-w-sm">
                <button
                    onClick={() => handleFlip('heads')}
                    disabled={isFlipping || user.balance < COIN_FLIP_BET_AMOUNT}
                    className="spin-button bg-gradient-to-r from-blue-500 to-indigo-600 hover:from-blue-600 hover:to-indigo-700 text-white font-extrabold py-3 px-6 rounded-full text-lg md:text-xl shadow-xl transition-all duration-300 ease-in-out transform hover:scale-105 active:scale-95 w-full uppercase tracking-wider flex items-center justify-center"
                >
                    Орел (Ставка: {COIN_FLIP_BET_AMOUNT})
                </button>
                <button
                    onClick={() => handleFlip('tails')}
                    disabled={isFlipping || user.balance < COIN_FLIP_BET_AMOUNT}
                    className="spin-button bg-gradient-to-r from-purple-500 to-pink-600 hover:from-purple-600 hover:to-pink-700 text-white font-extrabold py-3 px-6 rounded-full text-lg md:text-xl shadow-xl transition-all duration-300 ease-in-out transform hover:scale-105 active:scale-95 w-full uppercase tracking-wider flex items-center justify-center mt-3 md:mt-0"
                >
                    Решка (Ставка: {COIN_FLIP_BET_AMOUNT})
                </button>
            </div>
            
            <div className="message text-base md:text-lg font-semibold mt-4 min-h-[30px] flex items-center justify-center text-center w-full max-w-sm">
                {message}
            </div>
        </div>
    );
};


// -----------------------------------------------------------------------------
// Leaderboard Component
// -----------------------------------------------------------------------------
const Leaderboard = () => {
    const { API_BASE_URL, sendTelegramLog } = useUser();
    const [leaderboardData, setLeaderboardData] = useState([]);
    const [loading, setLoading] = useState(true);
    const [error, setError] = useState(null);

    const fetchLeaderboard = useCallback(async () => {
        setLoading(true);
        setError(null);
        sendTelegramLog('Fetching leaderboard data...');
        try {
            const response = await fetch(`${API_BASE_URL}/api/get_leaderboard`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({})
            });

            if (!response.ok) {
                const errData = await response.json();
                sendTelegramLog(`Leaderboard API error: ${errData.error || 'Unknown'}`, 'JS_ERROR');
                throw new Error(errData.error || 'Failed to fetch leaderboard data');
            }

            const data = await response.json();
            sendTelegramLog(`Leaderboard data received, count: ${data.leaderboard ? data.leaderboard.length : 0}`);
            
            // Sort by level descending, then by XP descending
            if (data.leaderboard && data.leaderboard.length > 0) {
                data.leaderboard.sort((a, b) => {
                    if (b.level !== a.level) {
                        return b.level - a.level;
                    }
                    return b.xp - a.xp;
                });
            }
            setLeaderboardData(data.leaderboard || []);

        } catch (err) {
            console.error('Error fetching leaderboard:', err);
            setError(err.message || 'Помилка завантаження дошки лідерів.');
            sendTelegramLog(`Leaderboard network error: ${err.message}`, 'JS_ERROR');
        } finally {
            setLoading(false);
        }
    }, [API_BASE_URL, sendTelegramLog]);

    useEffect(() => {
        fetchLeaderboard();
    }, [fetchLeaderboard]);

    return (
        <div className="flex-grow flex flex-col items-center justify-start p-4 md:p-8 w-full">
            <h2 className="text-3xl font-extrabold text-yellow-400 mb-6">👑 Дошка Лідерів 👑</h2>
            <div id="leaderboardTableContainer" className="overflow-x-auto w-full max-w-lg bg-gray-800 rounded-xl shadow-2xl p-4 border-2 border-yellow-400">
                {loading && <p className="text-yellow-300 mt-4 text-center">Завантаження...</p>}
                {error && <p className="text-red-500 mt-4 text-center">{error}</p>}
                {!loading && !error && leaderboardData.length === 0 && (
                    <p className="py-4 text-center text-gray-400">Наразі немає лідерів. Будь першим!</p>
                )}
                {!loading && !error && leaderboardData.length > 0 && (
                    <table className="w-full text-left text-sm md:text-base text-gray-300">
                        <thead className="text-xs md:text-sm text-gray-100 uppercase bg-gray-700">
                            <tr>
                                <th scope="col" className="py-2 px-3">#</th>
                                <th scope="col" className="py-2 px-3">Ім'я</th>
                                <th scope="col" className="py-2 px-3 text-right">Рівень</th>
                                <th scope="col" className="py-2 px-3 text-right">Баланс</th>
                                <th scope="col" class="py-2 px-3 text-right">XP</th>
                            </tr>
                        </thead>
                        <tbody>
                            {leaderboardData.map((player, index) => (
                                <tr key={index} className={(index % 2 === 0) ? 'bg-gray-800' : 'bg-gray-700'}>
                                    <td className="py-2 px-3 font-bold">{index + 1}</td>
                                    <td className="py-2 px-3">{player.username}</td>
                                    <td className="py-2 px-3 text-right">{player.level}</td>
                                    <td className="py-2 px-3 text-right">{player.balance}</td>
                                    <td class="py-2 px-3 text-right">{player.xp}</td>
                                </tr>
                            ))}
                        </tbody>
                    </table>
                )}
            </div>
        </div>
    );
};


// -----------------------------------------------------------------------------
// Top Header Component (Balance, XP, Level, Bonuses, Leaderboard)
// -----------------------------------------------------------------------------
const TopHeader = ({ onShowLeaderboard }) => {
    const { user, fetchUserData, API_BASE_URL, sendTelegramLog } = useUser();
    const [dailyBonusCooldownText, setDailyBonusCooldownText] = useState('');
    const [quickBonusCooldownText, setQuickBonusCooldownText] = useState('');

    // Helper for time formatting
    const formatTime = (ms) => {
        const totalSeconds = Math.floor(ms / 1000);
        const hours = Math.floor(totalSeconds / 3600);
        const minutes = Math.floor((totalSeconds % 3600) / 60);
        const seconds = totalSeconds % 60;
        
        if (hours > 0) {
            return `${hours.toString().padStart(2, '0')}:${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        } else {
            return `${minutes.toString().padStart(2, '0')}:${seconds.toString().padStart(2, '0')}`;
        }
    };

    // Daily Bonus Logic
    const updateDailyBonusCountdown = useCallback(() => {
        const now = new Date();
        const cooldownDuration = 24 * 60 * 60 * 1000; // 24 hours in ms
        
        const dailyBonusButtonElement = document.getElementById('dailyBonusButton'); // Access via DOM
        if (!dailyBonusButtonElement) return;

        if (!user.lastDailyBonusClaim || (now.getTime() - user.lastDailyBonusClaim.getTime()) >= cooldownDuration) {
            setDailyBonusCooldownText('');
            dailyBonusButtonElement.disabled = false;
            dailyBonusButtonElement.classList.add('pulsing');
        } else {
            const timeLeft = cooldownDuration - (now.getTime() - user.lastDailyBonusClaim.getTime());
            setDailyBonusCooldownText(`(${formatTime(timeLeft)})`);
            dailyBonusButtonElement.disabled = true;
            dailyBonusButtonElement.classList.remove('pulsing');
        }
    }, [user.lastDailyBonusClaim]);

    useEffect(() => {
        const interval = setInterval(updateDailyBonusCountdown, 1000);
        updateDailyBonusCountdown(); // Initial call
        return () => clearInterval(interval);
    }, [updateDailyBonusCountdown]);

    const handleClaimDailyBonus = async () => {
        if (!user.userId) {
            showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
            return;
        }
        const dailyBonusButtonElement = document.getElementById('dailyBonusButton');
        if (dailyBonusButtonElement && dailyBonusButtonElement.disabled) return;

        if(dailyBonusButtonElement) {
            dailyBonusButtonElement.disabled = true;
            dailyBonusButtonElement.classList.remove('pulsing');
        }
        sendTelegramLog('Attempting to claim daily bonus...');

        try {
            const response = await fetch(`${API_BASE_URL}/api/claim_daily_bonus`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: user.userId })
            });

            const data = await response.json();

            if (response.ok) {
                playDailyBonusSound();
                showCustomModal(`🎉 Ви отримали ${data.amount} фантиків!`, "Щоденна Винагорода!");
                fetchUserData();
                sendTelegramLog(`Daily Bonus claimed: ${data.amount}`);
            } else {
                showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Винагороди");
                fetchUserData(); // Fetch updated data to refresh cooldown
                sendTelegramLog(`Daily Bonus API failed: ${data.error || 'Unknown'}`, 'JS_ERROR');
            }
        } catch (error) {
            console.error('Помилка при отриманні щоденної винагороди:', error);
            showCustomModal('🚫 Не вдалося зʼєднатись із сервером для винагороди.', "Помилка");
            if(dailyBonusButtonElement) {
                dailyBonusButtonElement.disabled = false;
                dailyBonusButtonElement.classList.add('pulsing');
            }
            sendTelegramLog(`Daily Bonus network error: ${error.message}`, 'JS_ERROR');
        }
    };

    // Quick Bonus Logic
    const updateQuickBonusCountdown = useCallback(() => {
        const now = new Date();
        const cooldownDuration = 15 * 60 * 1000; // 15 minutes in ms
        
        const quickBonusButtonElement = document.getElementById('quickBonusButton');
        const quickBonusCooldownElement = document.getElementById('quickBonusCooldown');
        if (!quickBonusButtonElement || !quickBonusCooldownElement) return;


        if (!user.lastQuickBonusClaim || (now.getTime() - user.lastQuickBonusClaim.getTime()) >= cooldownDuration) {
            setQuickBonusCooldownText('');
            quickBonusButtonElement.disabled = false;
            quickBonusButtonElement.classList.add('pulsing');
            quickBonusButtonElement.classList.remove('active-countdown'); // Hide timer
        } else {
            const timeLeft = cooldownDuration - (now.getTime() - user.lastQuickBonusClaim.getTime());
            setQuickBonusCooldownText(formatTime(timeLeft));
            quickBonusButtonElement.disabled = true;
            quickBonusButtonElement.classList.remove('pulsing');
            quickBonusButtonElement.classList.add('active-countdown'); // Show timer
        }
    }, [user.lastQuickBonusClaim]);

    useEffect(() => {
        const interval = setInterval(updateQuickBonusCountdown, 1000);
        updateQuickBonusCountdown(); // Initial call
        return () => clearInterval(interval);
    }, [updateQuickBonusCountdown]);

    const handleClaimQuickBonus = async () => {
        if (!user.userId) {
            showCustomModal('⚠️ Будь ласка, запустіть гру через Telegram, щоб отримати User ID.', "Недоступно");
            return;
        }
        const quickBonusButtonElement = document.getElementById('quickBonusButton');
        if (quickBonusButtonElement && quickBonusButtonElement.disabled) return;

        if(quickBonusButtonElement) {
            quickBonusButtonElement.disabled = true;
            quickBonusButtonElement.classList.remove('pulsing');
            quickBonusButtonElement.classList.remove('active-countdown');
            setQuickBonusCooldownText('');
        }
        sendTelegramLog('Attempting to claim quick bonus...');

        try {
            const response = await fetch(`${API_BASE_URL}/api/claim_quick_bonus`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ user_id: user.userId })
            });

            const data = await response.json();

            if (response.ok) {
                playQuickBonusSound();
                showCustomModal(`💰 Ви отримали ${data.amount} фантиків!`, "Швидкий Бонус!");
                fetchUserData();
                sendTelegramLog(`Quick Bonus claimed: ${data.amount}`);
            } else {
                showCustomModal(`❌ Помилка: ${data.error || 'Невідома помилка.'}`, "Помилка Бонусу");
                fetchUserData(); // Fetch updated data to refresh cooldown
                sendTelegramLog(`Quick Bonus API failed: ${data.error || 'Unknown'}`, 'JS_ERROR');
            }
        } catch (error) {
            console.error('Помилка при отриманні швидкого бонусу:', error);
            showCustomModal('🚫 Не вдалося зʼєднатись із сервером для швидкого бонусу.', "Помилка");
            if(quickBonusButtonElement) {
                quickBonusButtonElement.disabled = false;
                quickBonusButtonElement.classList.add('pulsing');
            }
            sendTelegramLog(`Quick Bonus network error: ${error.message}`, 'JS_ERROR');
        }
    };


    const xpProgress = Math.min(100, (user.xp / user.nextLevelXp) * 100);

    return (
        <div className="app-header w-full h-16 bg-gradient-to-b from-yellow-700 to-yellow-900 shadow-lg flex items-center justify-center text-xl font-bold text-gray-900 uppercase tracking-widest z-10 flex-shrink-0 relative">
            Імперія Слота
            {/* Quick Bonus Button */}
            <button 
                id="quickBonusButton" 
                onClick={handleClaimQuickBonus}
                className="quick-bonus-button absolute left-2 md:left-4 top-1/2 -translate-y-1/2 w-12 h-12 rounded-full bg-blue-600 hover:bg-blue-700 text-white flex items-center justify-center text-lg font-bold shadow-md transition-all duration-200 ease-in-out transform hover:scale-105 active:scale-95"
            >
                💰
                <span id="quickBonusCooldown" className="quick-bonus-countdown">{quickBonusCooldownText}</span>
            </button>
            {/* Leaderboard Button */}
            <button 
                id="leaderboardButton" 
                onClick={onShowLeaderboard}
                className="leaderboard-button absolute right-2 md:right-4 top-1/2 -translate-y-1/2 bg-yellow-500 hover:bg-yellow-600 text-gray-900 font-bold w-12 h-12 rounded-full text-2xl flex items-center justify-center shadow-md transition-all duration-200 ease-in-out transform hover:scale-105 active:scale-95"
            >
                👑
            </button>

            {/* Balance and Level Display */}
            <div className="balance-area absolute top-16 left-1/2 transform -translate-x-1/2 bg-gray-800 rounded-b-xl py-2 px-4 shadow-inner border border-gray-700 border-t-0 w-full max-w-xs md:max-w-sm z-20">
                <div className="flex justify-between items-center w-full mb-1">
                    <span className="text-base md:text-lg text-gray-300 font-medium">Баланс:</span>
                    <span className={`font-bold text-yellow-300 text-2xl md:text-3xl ${user.balance !== 0 ? 'animate-pulse-balance' : ''}`}>{user.balance}</span>
                    <span className="text-base md:text-lg text-gray-300 font-medium ml-2">фантиків</span>
                </div>
                <div className="level-progress w-full mt-1">
                    <div className="flex justify-between items-center text-xs text-gray-400 mb-0.5">
                        <span>Рівень: <span className="font-bold text-white">{user.level}</span></span>
                        <span>XP: <span className="font-bold text-white">{user.xp}</span>/<span className="font-bold text-white">{user.nextLevelXp}</span></span>
                    </div>
                    <div className="xp-bar w-full bg-gray-600 rounded-full h-2">
                        <div className="h-full bg-blue-500 rounded-full transition-all duration-300 ease-out" style={{ width: `${xpProgress}%` }}></div>
                    </div>
                </div>
                {/* Daily Bonus Button - moved here for visual grouping */}
                <button 
                    id="dailyBonusButton" 
                    onClick={handleClaimDailyBonus}
                    className="daily-bonus-button bg-gradient-to-r from-blue-500 to-indigo-600 hover:from-blue-600 hover:to-indigo-700 text-white font-bold py-2 px-4 rounded-full text-sm shadow-lg transition-all duration-300 ease-in-out transform hover:scale-105 active:scale-95 flex items-center justify-center whitespace-nowrap mt-2 w-full"
                >
                    Щоденна Винагорода <span id="dailyBonusCooldown" className="ml-2 text-xs text-blue-200">{dailyBonusCooldownText}</span>
                </button>
            </div>
        </div>
    );
};


// -----------------------------------------------------------------------------
// Main App Component
// -----------------------------------------------------------------------------
function App() {
    const { isLoading, error, fetchUserData } = useUser();
    const [currentPage, setCurrentPage] = useState('slots'); // 'slots', 'coin_flip', 'leaderboard'

    const renderGame = () => {
        switch (currentPage) {
            case 'slots':
                return <SlotMachine />;
            case 'coin_flip':
                return <CoinFlip />;
            case 'leaderboard':
                return <Leaderboard />;
            default:
                return <SlotMachine />;
        }
    };

    const handleShowLeaderboard = () => {
        setCurrentPage('leaderboard');
    };

    if (isLoading) {
        return (
            <div className="flex flex-col items-center justify-center w-screen h-screen bg-gradient-to-br from-purple-900 via-gray-900 to-indigo-900 text-white">
                <p className="text-xl">Завантаження гри...</p>
            </div>
        );
    }

    if (error && !isLoading) {
        return (
            <div className="flex flex-col items-center justify-center w-screen h-screen bg-gradient-to-br from-purple-900 via-gray-900 to-indigo-900 text-white text-center p-4">
                <p className="text-xl text-red-500 font-bold mb-4">Помилка завантаження:</p>
                <p className="text-lg mb-6">{error}</p>
                <button 
                    onClick={fetchUserData}
                    className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded-full text-lg shadow-lg transition-all duration-300 ease-in-out transform hover:scale-105 active:scale-95"
                >
                    Спробувати знову
                </button>
            </div>
        );
    }

    return (
        <div className="casino-app-container relative w-full h-full flex flex-col items-center justify-between bg-gradient-to-br from-purple-900 via-gray-900 to-indigo-900 text-white overflow-hidden">
            {/* Top Header with Balance, XP, Level, Bonuses, Leaderboard Button */}
            <TopHeader onShowLeaderboard={handleShowLeaderboard} />

            {/* Main Game Content Area - renders current game */}
            <div className="relative flex-grow flex flex-col items-center justify-around w-full mt-[120px] pb-20"> {/* Adjusted margin-top to clear header and balance */}
                {renderGame()}
            </div>

            {/* Bottom Navigation */}
            <div className="bottom-nav fixed bottom-0 w-full h-16 bg-gradient-to-t from-yellow-700 to-yellow-900 shadow-lg flex justify-around items-center z-30">
                <button
                    onClick={() => setCurrentPage('slots')}
                    className={`nav-button p-2 rounded-full text-2xl transition-all duration-200 ${currentPage === 'slots' ? 'bg-yellow-500 text-gray-900 scale-110 shadow-lg' : 'text-gray-700 hover:text-gray-800'}`}
                >
                    🎰
                </button>
                <button
                    onClick={() => setCurrentPage('coin_flip')}
                    className={`nav-button p-2 rounded-full text-2xl transition-all duration-200 ${currentPage === 'coin_flip' ? 'bg-yellow-500 text-gray-900 scale-110 shadow-lg' : 'text-gray-700 hover:text-gray-800'}`}
                >
                    🪙
                </button>
                <button
                    onClick={() => setCurrentPage('leaderboard')}
                    className={`nav-button p-2 rounded-full text-2xl transition-all duration-200 ${currentPage === 'leaderboard' ? 'bg-yellow-500 text-gray-900 scale-110 shadow-lg' : 'text-gray-700 hover:text-gray-800'}`}
                >
                    👑
                </button>
                {/* Add more game buttons here later */}
            </div>

            {/* Audio Context Activation Prompt */}
            <div id="audioPrompt" className="audio-prompt fixed inset-0 bg-gray-900 bg-opacity-95 flex flex-col items-center justify-center text-center p-8 z-50 hidden">
                <p className="text-xl md:text-2xl font-bold mb-4">Натисніть, щоб увімкнути звук</p>
                <button id="activateAudioButton" className="bg-blue-600 hover:bg-blue-700 text-white font-bold py-3 px-6 rounded-full text-lg shadow-lg transition-all duration-300 ease-in-out">
                    Увімкнути
                </button>
            </div>

            {/* Custom Modal for Alerts */}
            <div id="customModal" className="modal fixed inset-0 flex items-center justify-center z-50 bg-black bg-opacity-70 hidden">
                <div className="modal-content bg-gray-800 rounded-xl shadow-2xl p-6 md:p-8 text-center w-11/12 max-w-sm relative border-2 border-yellow-400">
                    <button className="close-button absolute top-2 right-4 text-gray-400 hover:text-white text-3xl font-bold transition-colors duration-200">&times;</button>
                    <p id="modalMessage" className="text-xl md:text-2xl font-semibold mb-4"></p>
                    <button className="bg-yellow-500 hover:bg-yellow-600 text-gray-900 font-bold py-2 px-6 rounded-full text-lg shadow-md transition-all duration-300">OK</button>
                </div>
            </div>
        </div>
    );
}

// Make App and UserProvider globally accessible to index.html
window.App = App;
window.UserProvider = UserProvider;

