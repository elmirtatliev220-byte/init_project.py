// API_URL и tg определены в index.html

let currentChannelId = localStorage.getItem('last_channel_id'); 
let selectedFile = null;
let selectedMediaType = null;
let userTgId = tg.initDataUnsafe?.user?.id || 0;
let userFirstName = tg.initDataUnsafe?.user?.first_name || "Unknown";
let userUsername = tg.initDataUnsafe?.user?.username || "";
let editingPostId = null;
let authToken = localStorage.getItem('auth_token');
let scheduledPosts = [];
let growthChart = null;
let adsList = [];
let adsInterval = null;
let currentPostType = 'default'; // 'default' or 'poll'
let aiCooldownTimer = null;
let currentAIAction = null;
let currentOnboardingStep = 0;
let currentLang = localStorage.getItem('app_lang') || 'ru';
let userChannels = [];

// --- ANALYTICS & TON CONNECT INIT ---
try {
    if (window.TelegramAnalytics) {
        window.TelegramAnalytics.init({ token: 'YOUR_APP_TOKEN', appName: 'TeleCore' });
    }
} catch(e) { console.error("Analytics init failed", e); }

const tonConnectUI = new TON_CONNECT_UI.TonConnectUI({
    manifestUrl: 'https://ton-connect.github.io/demo-dapp-with-react-ui/tonconnect-manifest.json', // Используем демо-манифест для старта
});

tonConnectUI.onStatusChange(wallet => {
    if (wallet) {
        const address = wallet.account.address;
        // Сохраняем адрес на сервере
        apiFetch('/api/save_wallet', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ address })
        });
        updateWalletUI(address);
    } else {
        updateWalletUI(null);
    }
});

function updateWalletUI(address) {
    const btn = document.getElementById('ton-connect-btn');
    if (!btn) return;
    if (address) {
        btn.innerText = `Connected: ${address.slice(0, 4)}...${address.slice(-4)}`;
        btn.onclick = () => tonConnectUI.disconnect();
    } else {
        btn.innerText = "Connect Wallet";
        btn.onclick = () => tonConnectUI.openModal();
    }
}

// --- TRANSLATIONS ---
const translations = {
    ru: {
        nav_post: "Пост", nav_queue: "Очередь", nav_stats: "Инфо", nav_tools: "Тулзы", nav_profile: "Профиль",
        ai_helper: "✨ AI Редактор",
        post_type: "Пост", poll_type: "Опрос",
        post_placeholder: "О чем расскажете сегодня?",
        publish_date: "Дата публикации",
        btn_publish: "Опубликовать",
        waiting_list: "Лист ожидания",
        subscribers: "Подписчики",
        avg_reach: "Ср. охват",
        er: "Вовлеченность (ER)",
        growth_chart: "Динамика роста",
        security_status: "Статус защиты",
        tools_title: "Инструменты",
        traffic_sources: "Источники трафика",
        auto_responder: "Автоответчик",
        language: "Язык / Language",
        support: "Поддержка",
        contact_dev: "Связаться с разработчиком",
        logging_notice: "🔒 Все действия в приложении логируются в целях безопасности и для улучшения качества сервиса.",
        traffic_desc: "Создавайте уникальные ссылки, чтобы отслеживать, откуда приходят новые подписчики.",
        auto_responder_desc: "Настройте автоматические ответы на ключевые слова в вашем канале или группе.",
        security_desc: "Анализирует историю подписок на предмет резких скачков (накрутки)."
    },
    en: {
        nav_post: "Post", nav_queue: "Queue", nav_stats: "Stats", nav_tools: "Tools", nav_profile: "Profile",
        ai_helper: "✨ AI Editor",
        post_type: "Post", poll_type: "Poll",
        post_placeholder: "What's on your mind?",
        publish_date: "Publish Date",
        btn_publish: "Publish",
        waiting_list: "Waiting List",
        subscribers: "Subscribers",
        avg_reach: "Avg. Reach",
        er: "Engagement (ER)",
        growth_chart: "Growth Chart",
        security_status: "Security Status",
        tools_title: "Tools",
        traffic_sources: "Traffic Sources",
        auto_responder: "Auto Responder",
        language: "Language",
        support: "Support",
        contact_dev: "Contact Developer",
        logging_notice: "🔒 All actions within the app are logged for security purposes and to improve service quality.",
        traffic_desc: "Create unique links to track where new subscribers come from.",
        auto_responder_desc: "Set up automatic replies to keywords in your channel or group.",
        security_desc: "Analyzes subscription history for sudden spikes (bot activity)."
    }
};

// --- AUDIT LOGGING ---
console.log(`[Telecore] Init: Version ${tg.version}, Platform ${tg.platform}`);

// GLOBAL EVENT LISTENER (Moved to root)
tg.onEvent('chat_shared', (data) => {
    console.log("[Telecore] Event chat_shared:", data);
    if (data && data.chat && data.chat.id) {
        addChannelToServer(data.chat.id);
        toggleChannelModal(false);
    }
});

// --- BACK BUTTON LOGIC ---
tg.BackButton.onClick(() => {
    if (!document.getElementById('channel-modal').classList.contains('hidden')) {
        toggleChannelModal(false);
    } else if (!document.getElementById('ai-modal').classList.contains('hidden')) {
        toggleAI(false);
    } else if (!document.getElementById('preview-modal').classList.contains('hidden')) {
        closePreview();
    } else if (editingPostId) {
        cancelEdit();
    } else {
        // Если мы не на главной вкладке, можно возвращать туда, но пока просто скрываем кнопку
        tg.BackButton.hide();
    }
});

function changeLanguage(lang) {
    currentLang = lang;
    localStorage.setItem('app_lang', lang);
    updateTexts();
    
    // Update buttons UI
    document.querySelectorAll('.lang-btn').forEach(btn => {
        btn.classList.remove('bg-white', 'shadow-sm', 'text-black');
        btn.classList.add('text-gray-400');
    });
    const activeBtn = document.getElementById(`lang-${lang}`);
    if(activeBtn) {
        activeBtn.classList.remove('text-gray-400');
        activeBtn.classList.add('bg-white', 'shadow-sm', 'text-black');
    }
}

function updateTexts() {
    const t = translations[currentLang];
    document.querySelectorAll('[data-i18n]').forEach(el => {
        const key = el.getAttribute('data-i18n');
        if (t[key]) el.innerText = t[key];
    });
    document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
        const key = el.getAttribute('data-i18n-placeholder');
        if (t[key]) el.placeholder = t[key];
    });
}

// ONBOARDING DATA
const onboardingSteps = [
    {
        icon: "🏢",
        title: "Твой AI-офис",
        desc: "Управляй каналами и создавай контент нового уровня с помощью искусственного интеллекта."
    },
    {
        icon: "🧠",
        title: "Умный редактор",
        desc: "Забудь о творческом кризисе. AI сам допишет пост, исправит ошибки и сделает текст цепляющим за секунды."
    },
    {
        icon: "📅",
        title: "График на неделю",
        desc: "Отдыхай, пока бот работает. Планируй публикации на неделю вперед и будь уверен — всё выйдет вовремя."
    },
    {
        icon: "📊",
        title: "Полный контроль",
        desc: "Следи за ростом аудитории и эффективностью постов с помощью детальной аналитики."
    }
];

// WELCOME SCREEN LOGIC (UPDATED)
function initOnboarding() {
    if (localStorage.getItem('welcome_seen_v2')) {
        document.getElementById('welcome-screen').style.display = 'none';
        return;
    }
    renderOnboardingStep();
}

function renderOnboardingStep() {
    const step = onboardingSteps[currentOnboardingStep];
    const slider = document.getElementById('onboarding-slider');
    const dotsContainer = document.getElementById('onboarding-dots');
    const btn = document.getElementById('onboarding-btn');

    // Render Content with Animation
    slider.innerHTML = `
        <div class="flex flex-col items-center text-center animate-post">
            <div class="${step.icon.startsWith('<svg') ? 'telecore-logo-container' : 'w-32 h-32 bg-white rounded-[2.5rem] flex items-center justify-center shadow-2xl shadow-violet-500/10 mb-8 border border-violet-50'}">
                ${step.icon.startsWith('<svg') ? step.icon : `<span class="text-6xl">${step.icon}</span>`}
            </div>
            ${step.title ? `<h1 class="text-3xl font-black mb-4 text-slate-900 tracking-tight">${step.title}</h1>` : ''}
            <p class="text-base font-medium text-slate-500 leading-relaxed max-w-[280px]">
                ${step.desc}
            </p>
        </div>
    `;

    // Render Dots
    dotsContainer.innerHTML = onboardingSteps.map((_, i) => `
        <div class="w-2 h-2 rounded-full transition-all duration-300 ${i === currentOnboardingStep ? 'bg-violet-600 w-6' : 'bg-slate-300'}"></div>
    `).join('');

    // Update Button Text
    btn.innerText = currentOnboardingStep === onboardingSteps.length - 1 ? "Начать работу" : "Продолжить";
}

function nextOnboardingStep() {
    if (currentOnboardingStep < onboardingSteps.length - 1) {
        currentOnboardingStep++;
        renderOnboardingStep();
        tg.HapticFeedback.impactOccurred('light');
    } else {
        closeWelcome();
        tg.HapticFeedback.notificationOccurred('success');
    }
}

function closeWelcome() {
    const screen = document.getElementById('welcome-screen');
    screen.style.opacity = '0';
    screen.style.pointerEvents = 'none';
    setTimeout(() => { screen.style.display = 'none'; }, 500);
    localStorage.setItem('welcome_seen_v2', 'true');
}

// Init Onboarding on load
initOnboarding();

document.getElementById('msg').addEventListener('input', function() {
    document.getElementById('char-count').innerText = this.value.length;
});

// --- API WRAPPER (AUTH) ---
async function apiFetch(endpoint, options = {}) {
    const headers = options.headers || {};
    if (authToken) {
        headers['Authorization'] = `Bearer ${authToken}`;
    }
    options.headers = headers;
    
    const res = await fetch(`${API_URL}${endpoint}`, options);
    if (res.status === 401) {
        // Токен протух
        localStorage.removeItem('auth_token');
        authToken = null;
        login(); // Пробуем перелогиниться
        throw new Error("Unauthorized");
    }
    return res;
}

// --- АВТОРИЗАЦИЯ И РОЛИ ---
async function login() {
    try {
        const res = await fetch(`${API_URL}/api/login`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                initData: tg.initData
            })
        });
        const data = await res.json();
        
        if (data.token) {
            authToken = data.token;
            localStorage.setItem('auth_token', authToken);
        }
        
        const debugElem = document.getElementById('debug-id');
        if (data.role === 'owner') {
            debugElem.innerHTML = `• ID: ${userTgId} <span class="text-purple-500 font-bold">👑 ВЛАДЕЛЕЦ</span>`;
            document.getElementById('btn-admin').classList.remove('hidden');
        } else {
            debugElem.innerHTML = `• ID: ${userTgId} <span class="opacity-50">👤 ПОЛЬЗОВАТЕЛЬ</span>`;
        }
        
        // Обновляем профиль
        loadProfile(data.role);
    } catch (e) { console.error("Login error", e); }
}

function loadProfile(role) {
    document.getElementById('profile-name').innerText = userFirstName;
    document.getElementById('profile-id').innerText = `ID: ${userTgId}`;
    document.getElementById('profile-role').innerText = role === 'owner' ? 'OWNER' : 'USER';
    if (tg.initDataUnsafe?.user?.photo_url) {
        document.getElementById('profile-avatar').src = tg.initDataUnsafe.user.photo_url;
    }
}

// --- УПРАВЛЕНИЕ КАНАЛАМИ (ТЗ 4.1) ---

async function initChannels() {
    try {
        const res = await apiFetch(`/api/user_channels`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const channels = await res.json();
        userChannels = channels; // Сохраняем список каналов глобально
        const select = document.getElementById('channel-select');
        
        if (channels && channels.length > 0) {
            select.innerHTML = channels.map(c => `
                <option value="${c.id}" ${currentChannelId && c.id == currentChannelId ? 'selected' : ''}>${c.title}</option>
            `).join('');
            
            if (!currentChannelId || !channels.find(c => c.id == currentChannelId)) {
                currentChannelId = channels[0].id;
                localStorage.setItem('last_channel_id', currentChannelId);
            }
        } else {
            // Если каналов нет, сбрасываем ID и просим добавить
            currentChannelId = null;
            select.innerHTML = `<option value="">➕ Добавьте канал</option>`;
        }
        
        // Обновляем иконку для текущего канала
        const current = userChannels.find(c => c.id == currentChannelId);
        updateChannelIcon(current);
        
        refreshChannelData();
        
        loadAdsForUser(); // Загружаем рекламу для пользователя
    } catch (e) {
        console.error("Ошибка загрузки каналов", e);
        document.getElementById('channel-select').innerHTML = `<option value="${currentChannelId}">Ошибка загрузки</option>`;
    }
}

function refreshChannelData() {
    checkConn();
    if(currentChannelId && currentChannelId !== 'null' && !document.getElementById('tab-stats').classList.contains('hidden')) {
        loadStats();
    }
    if(currentChannelId && currentChannelId !== 'null' && !document.getElementById('tab-queue').classList.contains('hidden')) {
        loadScheduled();
    }
}

function switchChannel(id) {
    currentChannelId = parseInt(id);
    localStorage.setItem('last_channel_id', currentChannelId);
    tg.HapticFeedback.impactOccurred('light');
    
    document.getElementById('sub-count').style.opacity = '0.3';
    document.getElementById('er-value').style.opacity = '0.3';
    
    cancelEdit();
    
    // Обновляем иконку
    const current = userChannels.find(c => c.id == currentChannelId);
    updateChannelIcon(current);
    
    refreshChannelData();
}

function updateChannelIcon(channel) {
    const icon = document.getElementById('channel-icon');
    if (channel && channel.photo) {
        icon.className = "w-8 h-8 rounded-full shadow-lg shadow-indigo-500/20 overflow-hidden";
        icon.innerHTML = `<img src="${API_URL}/${channel.photo}" class="w-full h-full object-cover">`;
    } else {
        icon.className = "w-8 h-8 rounded-full bg-gradient-to-br from-indigo-500 to-violet-500 flex items-center justify-center text-white font-bold text-xs shadow-lg shadow-indigo-500/20";
        icon.innerHTML = "#";
    }
}

function toggleChannelModal(show) {
    const modal = document.getElementById('channel-modal');
    if (show) {
        modal.classList.remove('hidden');
        document.getElementById('channel-id-input').value = "";
        tg.BackButton.show();
    } else {
        modal.classList.add('hidden');
        tg.BackButton.hide();
    }
}

function requestTelegramChannel() {
    // Проверяем наличие метода напрямую, а не только версию
    if (typeof tg.requestChat === 'function') {
        tg.requestChat({
            request_id: Math.floor(Math.random() * 100000), // Случайный ID для запроса
            chat_types: ["channel"],
            chat_is_created: true,
            user_is_admin: true // Важно: чтобы бот мог работать, юзер должен быть админом
        });
    } else {
        // Если метод совсем не найден, пробуем альтернативный вызов для новых версий
        try {
            window.Telegram.WebApp.sendData(JSON.stringify({action: "choose_channel"})); 
            // Или выводим внятное сообщение
            alert("Ваша версия Telegram не поддерживает выбор каналов. Используйте ввод по ссылке.");
        } catch(e) {
            console.error("Ошибка при выборе канала:", e);
        }
    }
}

function submitChannelForm() {
    const val = document.getElementById('channel-id-input').value;
    if(val) {
        addChannelToServer(val);
        toggleChannelModal(false);
    } else {
        tg.showAlert("Введите ссылку или ID!");
    }
}

function promptAddChannel() {
    toggleChannelModal(true);
}

async function addChannelToServer(chanId) {
    tg.MainButton.showProgress();
    try {
        const res = await apiFetch(`/api/add_channel`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ tg_id: chanId })
        });
        const result = await res.json();
        if(result.status === 'success') {
            tg.showAlert(`Канал "${result.title}" успешно добавлен!`);
            initChannels();
        } else {
            tg.showAlert(`Ошибка: ${result.message}`);
        }
    } catch(e) {
        tg.showAlert("Сервер недоступен");
    } finally {
        tg.MainButton.hideProgress();
    }
}

function promptDeleteChannel() {
    tg.showConfirm("Отключить текущий канал и удалить очередь постов?", async (ok) => {
        if (ok) {
            try {
                const res = await apiFetch(`/api/delete_channel`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ tg_id: currentChannelId })
                });
                const result = await res.json();
                if (result.status === 'success') {
                    tg.showAlert("Канал отключен");
                    initChannels(); // Перезагружаем список
                } else {
                    tg.showAlert("Ошибка: " + result.message);
                }
            } catch (e) { tg.showAlert("Ошибка сети"); }
        }
    });
}

// --- МЕДИА И ОТПРАВКА ---

function setPostType(type) {
    currentPostType = type;
    const defBtn = document.getElementById('type-default');
    const pollBtn = document.getElementById('type-poll');
    const defEditor = document.getElementById('post-editor-default');
    const pollEditor = document.getElementById('post-editor-poll');
    const mediaArea = document.getElementById('media-area');

    if (type === 'poll') {
        defBtn.classList.replace('bg-white', 'text-gray-400');
        defBtn.classList.remove('shadow-sm');
        pollBtn.classList.replace('text-gray-400', 'bg-white');
        pollBtn.classList.add('shadow-sm');
        
        defEditor.classList.add('hidden');
        pollEditor.classList.remove('hidden');
    } else {
        pollBtn.classList.replace('bg-white', 'text-gray-400');
        pollBtn.classList.remove('shadow-sm');
        defBtn.classList.replace('text-gray-400', 'bg-white');
        defBtn.classList.add('shadow-sm');
        
        pollEditor.classList.add('hidden');
        defEditor.classList.remove('hidden');
    }
}

function addPollOption() {
    const container = document.getElementById('poll-options-list');
    if (container.children.length >= 10) return tg.showAlert("Максимум 10 вариантов");
    const input = document.createElement('input');
    input.type = 'text';
    input.className = 'poll-option input-field w-full p-3 text-sm';
    input.placeholder = `Вариант ${container.children.length + 1}`;
    container.appendChild(input);
}

function previewMedia(input) {
    const imgPreview = document.getElementById('mini-image-preview');
    const vidPreview = document.getElementById('mini-video-preview');
    const container = document.getElementById('mini-preview-container');
    
    if (input.files && input.files[0]) {
        const file = input.files[0];
        const reader = new FileReader();
        selectedMediaType = file.type.startsWith('video') ? 'video' : 'photo';
        selectedFile = file;

        // Используем URL.createObjectURL для мгновенного превью без чтения всего файла
        const objectUrl = URL.createObjectURL(file);
        container.classList.remove('hidden');
        
        if (selectedMediaType === 'video') {
            imgPreview.classList.add('hidden');
            vidPreview.classList.remove('hidden');
            vidPreview.src = objectUrl;
        } else {
            vidPreview.classList.add('hidden');
            imgPreview.classList.remove('hidden');
            imgPreview.src = objectUrl;
            // Небольшая задержка для анимации
            setTimeout(() => imgPreview.classList.remove('opacity-0'), 50);
        }
        tg.HapticFeedback.impactOccurred('medium');
    }
}

function clearMedia() {
    document.getElementById('mini-preview-container').classList.add('hidden');
    document.getElementById('mini-image-preview').src = "";
    document.getElementById('mini-video-preview').src = "";
    document.getElementById('media-input').value = "";
    selectedFile = null;
    selectedMediaType = null;
}

async function checkConn() {
    const s = document.getElementById('conn-status');
    const dot = document.getElementById('status-dot');
    
    // Если канал не выбран, не пытаемся подключиться
    if (!currentChannelId || currentChannelId === 'null') {
        s.innerText = "Жду канал...";
        dot.className = "w-1.5 h-1.5 rounded-full bg-yellow-500";
        return;
    }

    try {
        const r = await apiFetch(`/api/get_scheduled?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        if(r.ok) {
            s.innerText = "Система онлайн";
            dot.className = "w-2 h-2 rounded-full bg-green-500 shadow-[0_0_8px_rgba(34,197,94,0.4)]";
            loadScheduled();
        } else { throw new Error(); }
    } catch(e) {
        s.innerText = "Оффлайн";
        dot.className = "w-2 h-2 rounded-full bg-red-500";
    }
}

function showTab(tabName) {
    document.getElementById('tab-main').classList.toggle('active', tabName === 'main');
    document.getElementById('tab-queue').classList.toggle('active', tabName === 'queue');
    document.getElementById('tab-stats').classList.toggle('active', tabName === 'stats');
    document.getElementById('tab-tools').classList.toggle('active', tabName === 'tools');
    document.getElementById('tab-profile').classList.toggle('active', tabName === 'profile');
    document.getElementById('tab-admin').classList.toggle('active', tabName === 'admin');
    
    document.getElementById('btn-main').classList.toggle('active', tabName === 'main');
    document.getElementById('btn-queue').classList.toggle('active', tabName === 'queue');
    document.getElementById('btn-stats').classList.toggle('active', tabName === 'stats');
    document.getElementById('btn-tools').classList.toggle('active', tabName === 'tools');
    document.getElementById('btn-profile').classList.toggle('active', tabName === 'profile');
    document.getElementById('btn-admin').classList.toggle('active', tabName === 'admin');
    
    tg.BackButton.hide(); // Скрываем кнопку назад при смене табов

    if(tabName === 'queue') {
        loadScheduled();
    } else if(tabName === 'tools') {
        loadTraffic();
        loadAutoResponses();
    } else if(tabName === 'stats') {
        loadStats();
    } else if(tabName === 'admin') {
        loadAdminData();
    }
    tg.HapticFeedback.selectionChanged();
}

async function loadScheduled() {
    if (!currentChannelId || currentChannelId === 'null') return;
    const listDiv = document.getElementById('scheduledList');
    try {
        const res = await apiFetch(`/api/get_scheduled?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const posts = await res.json();
        // ВАЖНО: Обновляем глобальный список для работы редактирования
        scheduledPosts = posts;
        const badge = document.getElementById('q-count');
        badge.innerText = posts.length;
        badge.classList.toggle('hidden', posts.length === 0);

        if (posts.length === 0) {
            listDiv.innerHTML = `
            <div class="py-20 text-center opacity-20">
                <div class="text-4xl mb-2">☕</div>
                <p class="text-[11px] font-bold uppercase tracking-widest">Очередь пуста</p>
            </div>
            `;
            return;
        }

        // Группировка по датам
        const grouped = {};
        posts.forEach(p => {
            const date = new Date(p.time);
            const dateKey = date.toLocaleDateString('ru-RU', { day: 'numeric', month: 'long' });
            if (!grouped[dateKey]) grouped[dateKey] = [];
            grouped[dateKey].push(p);
        });

        listDiv.innerHTML = Object.keys(grouped).map(date => `
            <div class="mb-4">
                <h4 class="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-2 ml-1 sticky top-0 bg-[var(--tg-bg)] z-10 py-1">${date}</h4>
                <div class="space-y-2">
                    ${grouped[date].map(p => `
                        <div class="card p-3 flex justify-between items-center">
                            <div class="flex items-center gap-3 overflow-hidden">
                                <div class="w-8 h-8 rounded-lg bg-blue-50 flex items-center justify-center shrink-0 text-xs">
                                    ${p.media_type === 'video' ? '🎬' : (p.has_image ? '🖼' : (p.text && p.text.startsWith('Опрос:') ? '📊' : '📝'))}
                                </div>
                                <div class="overflow-hidden">
                                    <p class="text-[10px] font-bold text-blue-500">
                                        ${new Date(p.time).toLocaleTimeString('ru-RU', {hour:'2-digit', minute:'2-digit'})}
                                    </p>
                                    <p class="text-xs font-medium truncate opacity-80">${p.text || 'Без описания'}</p>
                                </div>
                            </div>
                            <div class="flex items-center">
                                <button onclick="editPost(${p.id})" class="p-2 text-blue-500/60 active:text-blue-500"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M11 4H4a2 2 0 0 0-2 2v14a2 2 0 0 0 2 2h14a2 2 0 0 0 2-2v-7"></path><path d="M18.5 2.5a2.121 2.121 0 0 1 3 3L12 15l-4 1 1-4 9.5-9.5z"></path></svg></button>
                                <button onclick="deletePost(${p.id})" class="p-2 text-red-500/40 active:text-red-500"><svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><path d="M3 6h18M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6m3 0V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"></path></svg></button>
                            </div>
                        </div>
                    `).join('')}
                </div>
            </div>
        `).join('');
    } catch (e) {}
}

async function smartSend() {
    let text = document.getElementById('msg').value;

    if (!currentChannelId) {
        tg.showAlert("Сначала добавьте канал (кнопка + сверху)!");
        return;
    }

    const timeInput = document.getElementById('scheduleTime').value;
    const btn = document.getElementById('main-send-btn');
    const loader = document.getElementById('btn-loader');
    const btnText = document.getElementById('btn-text');

    // Сбор данных опроса
    let pollQuestion = null;
    let pollOptions = null;
    let pollConfig = null;

    if (currentPostType === 'poll') {
        pollQuestion = document.getElementById('poll-question').value;
        const opts = Array.from(document.querySelectorAll('.poll-option')).map(i => i.value).filter(v => v.trim());
        if (!pollQuestion || opts.length < 2) {
            return tg.showAlert("Для опроса нужен вопрос и минимум 2 варианта!");
        }
        pollOptions = JSON.stringify(opts);
        pollConfig = JSON.stringify({
            is_anonymous: document.getElementById('poll-anon').checked,
            allows_multiple_answers: document.getElementById('poll-multi').checked
        });
    } else {
        // Добавляем хештеги к тексту только для обычного поста
        const tags = document.getElementById('hashtags').value.trim();
        if(tags) text += `\n\n${tags}`;
    }
    
    let publishAt = null;

    // ВАЛИДАЦИЯ ДАТЫ: Защита от случайного выбора следующего года (например, 2026)
    if (timeInput) {
        const selected = new Date(timeInput);
        const limit = new Date();
        limit.setFullYear(limit.getFullYear() + 1);
        if (selected > limit) {
            tg.showAlert("⚠️ Ошибка: Выбрана дата более чем через год! Проверьте год.");
            return;
        }
        // Отправляем время в стандарте UTC (ISO string)
        publishAt = selected.toISOString();
    }

    if(currentPostType === 'default' && !text && !selectedFile && !editingPostId) {
        tg.HapticFeedback.notificationOccurred('error');
        tg.showAlert("Пустой пост нельзя отправить!");
        return;
    }

    btn.disabled = true;
    loader.style.display = 'block';
    if (editingPostId) {
        btnText.innerText = "СОХРАНЯЕМ...";
    } else {
        btnText.innerText = timeInput ? "ПЛАНИРУЕМ..." : "ОТПРАВЛЯЕМ...";
    }

    const endpoint = editingPostId ? '/api/edit_scheduled' : (timeInput ? '/api/schedule_post' : '/api/send_post');
    
    // Используем FormData для отправки файлов
    const formData = new FormData();
    formData.append('channel_id', currentChannelId);
    if (publishAt) formData.append('publish_at', publishAt);
    
    if (currentPostType === 'poll') {
        formData.append('poll_question', pollQuestion);
        formData.append('poll_options', pollOptions);
        formData.append('poll_config', pollConfig);
    } else {
        if (text) formData.append('text', text);
        if (selectedFile) formData.append('media', selectedFile);
    }

    if (editingPostId) {
        formData.append('id', editingPostId);
    }

    try {
        const res = await apiFetch(`${endpoint}`, {
            method: 'POST',
            // Не устанавливаем Content-Type, браузер сам поставит multipart/form-data
            body: formData
        });
        if(res.ok) {
            tg.HapticFeedback.notificationOccurred('success');
            cancelEdit(); // Сброс формы
            if(timeInput || editingPostId) showTab('queue');
            else tg.showAlert("Пост успешно опубликован!");
        } else {
            const err = await res.json();
            tg.showAlert("Ошибка: " + err.message);
        }
    } catch(e) { 
        tg.showAlert("Ошибка соединения с сервером");
    } finally {
        btn.disabled = false;
        loader.style.display = 'none';
        if (!editingPostId) btnText.innerText = "Опубликовать";
    }
}

function editPost(id) {
    tg.HapticFeedback.impactOccurred('medium');
    
    // Ищем пост (используем == для надежности сравнения типов)
    const post = scheduledPosts.find(p => p.id == id);
    if (!post) {
        tg.showAlert("⚠️ Ошибка: Данные поста не найдены. Попробуйте обновить страницу (свайп вниз).");
        return;
    }

    editingPostId = id;
    // TODO: Добавить загрузку данных опроса в форму (пока упрощенно только текст)
    document.getElementById('msg').value = post.text || "";
    document.getElementById('char-count').innerText = (post.text || "").length;
    
    if (post.time) {
        document.getElementById('scheduleTime').value = post.time.slice(0, 16);
    }
    
    clearMedia();
    if (post.has_image || post.media_type === 'video') {
        tg.showAlert("ℹ️ В посте есть медиа. Загрузите новое, чтобы заменить его.");
    }

    showTab('main');
    document.getElementById('btn-text').innerText = "СОХРАНИТЬ ИЗМЕНЕНИЯ";
    
    // Добавляем кнопку отмены
    if (!document.getElementById('cancel-edit-btn')) {
        const cancelBtn = document.createElement('button');
        cancelBtn.id = 'cancel-edit-btn';
        cancelBtn.className = "w-full py-3 text-red-500 font-bold text-sm uppercase";
        cancelBtn.innerText = "Отменить редактирование";
        cancelBtn.onclick = cancelEdit;
        document.getElementById('main-send-btn').parentNode.appendChild(cancelBtn);
    }
    tg.BackButton.show();
}

function cancelEdit() {
    editingPostId = null;
    document.getElementById('msg').value = "";
    document.getElementById('hashtags').value = "";
    document.getElementById('scheduleTime').value = "";
    document.getElementById('char-count').innerText = "0";
    clearMedia();
    document.getElementById('btn-text').innerText = "Опубликовать";
    
    const cancelBtn = document.getElementById('cancel-edit-btn');
    if (cancelBtn) cancelBtn.remove();
    tg.BackButton.hide();
}

async function deletePost(id) {
    tg.showConfirm("Удалить этот пост?", async (ok) => {
        if(ok) {
            try {
                await apiFetch(`/api/delete_scheduled`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ id })
                });
                loadScheduled();
                tg.HapticFeedback.notificationOccurred('warning');
            } catch(e) {}
        }
    });
}

async function loadStats() {
    if (!currentChannelId || currentChannelId === 'null') return;
    try {
        const r = await apiFetch(`/api/stats?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const d = await r.json();
        
        const subElem = document.getElementById('sub-count');
        const erElem = document.getElementById('er-value');
        const growthBadge = document.getElementById('sub-growth');
        const reachElem = document.getElementById('reach-count');
        
        // 1. Подписчики
        subElem.innerText = d.subscribers ? d.subscribers.toLocaleString() : 0;
        
        // 2. Динамика прироста (ТЗ 4.2.1)
        if (d.growth && d.growth['24h'] !== undefined) {
            const val = d.growth['24h'];
            growthBadge.innerText = val >= 0 ? `▲ +${val}` : `▼ ${val}`;
            
            if (val < 0) {
                growthBadge.classList.replace('text-green-500', 'text-red-500');
            } else {
                growthBadge.classList.replace('text-red-500', 'text-green-500');
            }
        }

        // 3. Вовлеченность и охват (ТЗ 4.2.2)
        erElem.innerText = d.er || '0%';
        reachElem.innerText = `REACH: ${d.avg_reach || 0}`;
        
        subElem.style.opacity = '1';
        erElem.style.opacity = '1';

        // Загрузка графика (Новая фича)
        loadChart();
        
        // Загрузка анализа защиты (ТЗ 4.6)
        loadSecurityAnalysis();

    } catch(e) {
        console.error("Stats load error", e);
        const st = document.getElementById('conn-status');
        if(st) st.innerText = "Ошибка статистики";
    }
}

async function loadSecurityAnalysis() {
    const statusElem = document.getElementById('security-status');
    const detailsElem = document.getElementById('security-details');
    try {
        const r = await apiFetch(`/api/analyze_protection?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const d = await r.json();
        
        statusElem.innerText = d.message;
        statusElem.className = d.status === 'ok' ? "text-sm font-medium text-green-600" : "text-sm font-bold text-red-500";
        
        detailsElem.innerHTML = d.spikes ? d.spikes.map(s => `<div>• ${s}</div>`).join('') : '';
    } catch(e) {
        statusElem.innerText = "Не удалось проверить";
    }
}

async function loadChart() {
    if (!currentChannelId || currentChannelId === 'null') return;
    try {
        const r = await apiFetch(`/api/stats_history?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const data = await r.json();

        const ctx = document.getElementById('growthChart').getContext('2d');
        
        if (growthChart) {
            growthChart.destroy();
        }

        growthChart = new Chart(ctx, {
            type: 'line',
            data: {
                labels: data.labels,
                datasets: [{
                    label: 'Подписчики',
                    data: data.data,
                    borderColor: '#248bed',
                    backgroundColor: 'rgba(36, 139, 237, 0.1)',
                    borderWidth: 2,
                    tension: 0.4,
                    fill: true,
                    pointRadius: 3
                }]
            },
            options: {
                responsive: true,
                plugins: { legend: { display: false } },
                scales: {
                    x: { grid: { display: false }, ticks: { font: { size: 10 } } },
                    y: { grid: { color: 'rgba(0,0,0,0.05)' }, ticks: { font: { size: 10 } } }
                }
            }
        });
    } catch (e) { console.error("Chart error", e); }
}

// --- USER ADS LOGIC ---
async function loadAdsForUser() {
    try {
        const res = await apiFetch(`/api/get_ads`, { headers: { 'ngrok-skip-browser-warning': 'true' } });
        const ads = await res.json();
        const container = document.getElementById('ad-container');
        
        if (ads.length > 0) {
            // Рендерим карусель (все объявления)
            container.innerHTML = ads.map(ad => {
                const isBeta = ad.link.includes('pux220');
                const badgeText = isBeta ? "BETA" : "HOT";

                return `
                <div class="w-[85vw] sm:w-[340px] snap-center shrink-0 relative overflow-hidden transition-transform active:scale-[0.98] font-sans" 
                     style="
                        background-color: #121214; 
                        border: 1px solid rgba(255,255,255,0.05);
                        border-radius: 20px; 
                        padding: 16px; 
                        display: flex; 
                        align-items: center; 
                        justify-content: space-between; 
                        transition: all 0.3s ease;
                     ">
                    
                    <div style="display: flex; flex-direction: column; align-items: flex-start; margin-right: 12px; flex: 1; min-width: 0;">
                        <span style="
                            font-size: 10px; 
                            font-weight: 700; 
                            text-transform: uppercase; 
                            background: #000000; 
                            color: #82868a;
                            padding: 2px 6px; 
                            border-radius: 4px; 
                            margin-bottom: 8px;
                            border: 1px solid rgba(255,255,255,0.05);
                            transition: all 0.3s ease;
                        ">
                            ${badgeText}
                        </span>
                        <h3 style="
                            font-size: 15px; 
                            font-weight: 600; 
                            margin: 0 0 4px 0; 
                            line-height: 1.3; 
                            white-space: nowrap; 
                            overflow: hidden; 
                            text-overflow: ellipsis; 
                            width: 100%;
                            color: #ffffff;
                            transition: color 0.3s ease;
                        ">
                            ${ad.title}
                        </h3>
                        <p style="
                            font-size: 12px; 
                            color: #82868a; 
                            margin: 0; 
                            line-height: 1.4; 
                            display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
                            transition: color 0.3s ease;
                        ">
                            ${ad.desc || ''}
                        </p>
                    </div>

                    <a href="${ad.link}" target="_blank" 
                       style="
                        text-decoration: none; 
                        background-color: #7042f8; 
                        color: #ffffff; 
                        padding: 10px 20px; 
                        border-radius: 10px; 
                        font-size: 14px; 
                        font-weight: 700; 
                        white-space: nowrap; 
                        flex-shrink: 0;
                        border: none;
                        transition: all 0.3s ease;
                    ">
                        GO
                    </a>
                </div>
            `}).join('');
            container.classList.remove('hidden');

            // --- AUTO SCROLL LOGIC ---
            if (ads.length > 1) {
                if (adsInterval) clearInterval(adsInterval);
                adsInterval = setInterval(() => {
                    const card = container.firstElementChild;
                    if (!card) return;
                    
                    const style = window.getComputedStyle(container);
                    const gap = parseFloat(style.columnGap) || 16;
                    const scrollAmount = card.offsetWidth + gap;
                    
                    if (container.scrollLeft + container.clientWidth >= container.scrollWidth - 10) {
                        container.scrollTo({ left: 0, behavior: 'smooth' });
                    } else {
                        container.scrollBy({ left: scrollAmount, behavior: 'smooth' });
                    }
                }, 4000);
            }
        }
    } catch(e) {}
}

// --- TOOLS LOGIC (Трафик и Автоответы) ---

async function createInvite() {
    const name = document.getElementById('invite-name').value;
    if(!name) return tg.showAlert("Введите название метки!");
    
    try {
        const res = await apiFetch(`/api/create_invite`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ channel_id: currentChannelId, name })
        });
        const d = await res.json();
        if(d.status === 'success') {
            document.getElementById('invite-name').value = "";
            loadTraffic();
            tg.showAlert("Ссылка создана!");
        } else {
            tg.showAlert("Ошибка: " + d.message);
        }
    } catch(e) { tg.showAlert("Ошибка сети"); }
}

async function loadTraffic() {
    if(!currentChannelId || currentChannelId === 'null') return;
    try {
        const res = await apiFetch(`/api/traffic_sources?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const list = await res.json();
        document.getElementById('traffic-list').innerHTML = list.length ? list.map(t => `
            <div class="flex justify-between items-center p-2 rounded-lg border border-gray-100" style="background-color: #1c1c1e;">
                <div class="overflow-hidden">
                    <p class="text-xs font-bold truncate">${t.name}</p>
                    <p class="text-[10px] text-blue-500 truncate select-all cursor-pointer" onclick="navigator.clipboard.writeText('${t.link}'); tg.showAlert('Скопировано!')">${t.link}</p>
                </div>
                <div class="bg-green-50 text-green-600 px-2 py-1 rounded-md text-xs font-bold whitespace-nowrap">
                    +${t.joins} чел.
                </div>
            </div>
        `).join('') : '<p class="text-center text-[10px] opacity-40">Нет активных ссылок</p>';
    } catch(e) {}
}

async function createAutoResponse() {
    const trigger = document.getElementById('ar-trigger').value;
    const response = document.getElementById('ar-response').value;
    if(!trigger || !response) return tg.showAlert("Заполните оба поля!");

    try {
        await apiFetch(`/api/add_auto_response`, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ channel_id: currentChannelId, trigger, response })
        });
        document.getElementById('ar-trigger').value = "";
        document.getElementById('ar-response').value = "";
        loadAutoResponses();
    } catch(e) { tg.showAlert("Ошибка"); }
}

async function loadAutoResponses() {
    if(!currentChannelId || currentChannelId === 'null') return;
    try {
        const res = await apiFetch(`/api/auto_responses?channel_id=${currentChannelId}`, {
            headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const list = await res.json();
        document.getElementById('ar-list').innerHTML = list.length ? list.map(a => `
            <div class="p-2 rounded-lg border border-gray-100 relative group" style="background-color: #1c1c1e;">
                <p class="text-[10px] font-bold text-purple-500 uppercase">Если: "${a.trigger}"</p>
                <p class="text-xs mt-1 line-clamp-2">${a.response}</p>
                <button onclick="deleteAutoResponse(${a.id})" class="absolute top-2 right-2 text-red-400 opacity-50 hover:opacity-100">✕</button>
            </div>
        `).join('') : '<p class="text-center text-[10px] opacity-40">Нет правил</p>';
    } catch(e) {}
}

async function deleteAutoResponse(id) {
    await apiFetch(`/api/delete_auto_response`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
    });
    loadAutoResponses();
}

// --- ADMIN PANEL LOGIC ---
async function loadAdminData() {
    try {
        const res = await apiFetch(`/api/admin/stats`, {
                headers: { 'ngrok-skip-browser-warning': 'true' }
        });
        const d = await res.json();
        if(d.status === 'success') {
            document.getElementById('adm-users').innerText = d.users;
            document.getElementById('adm-channels').innerText = d.channels;
            document.getElementById('adm-posts').innerText = d.posts;
            
            const list = document.getElementById('adm-channels-list');
            list.innerHTML = d.channels_list.length ? d.channels_list.map(c => `
                <div class="flex justify-between items-center border-b border-gray-100 pb-2 last:border-0">
                    <div>
                        <p class="text-xs font-bold">${c.title}</p>
                        <p class="text-[10px] opacity-50">Владелец: ${c.owner} (ID: ${c.owner_id})</p>
                    </div>
                    <button onclick="forceDeleteChannel('${c.id}')" class="text-red-500 bg-red-50 p-1.5 rounded-md text-[10px] font-bold">УДАЛИТЬ</button>
                </div>
            `).join('') : '<p class="text-center text-xs opacity-30">Нет каналов</p>';

            const adsListDiv = document.getElementById('adm-ads-list');
            adsListDiv.innerHTML = d.ads.length ? d.ads.map(a => `
                <div class="flex justify-between items-center p-2 rounded-lg" style="background-color: #121214;">
                    <span class="text-xs font-bold">${a.title}</span>
                    <button onclick="deleteAd(${a.id})" class="text-red-500 font-bold text-[10px]">✕</button>
                </div>
            `).join('') : '<p class="text-[10px] opacity-30">Нет активной рекламы</p>';
        }
    } catch(e) { console.error(e); }
}

async function forceDeleteChannel(tg_id) {
    tg.showConfirm("ВЫ АДМИН. Вы уверены, что хотите принудительно удалить этот канал?", async (ok) => {
        if(ok) {
            try {
                await apiFetch(`/api/admin/delete_channel_force`, {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ tg_id: tg_id })
                });
                loadAdminData();
                tg.showAlert("Канал удален властью Админа!");
            } catch(e) { tg.showAlert("Ошибка"); }
        }
    });
}

async function createAd() {
    const title = document.getElementById('ad-title').value;
    const link = document.getElementById('ad-link').value;
    const desc = document.getElementById('ad-desc').value;
    
    if(!title || !link) return tg.showAlert("Заполните поля!");

    await apiFetch(`/api/admin/add_ad`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ title, link, description: desc })
    });
    
    document.getElementById('ad-title').value = "";
    document.getElementById('ad-link').value = "";
    document.getElementById('ad-desc').value = "";
    loadAdminData();
}

async function deleteAd(id) {
    await apiFetch(`/api/admin/delete_ad`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({ id })
    });
    loadAdminData();
}

// --- PREVIEW LOGIC ---
function parseTelegramStyles(text) {
    if (!text) return "";
    return text
        .replace(/\*\*(.*?)\*\*/g, '<b>$1</b>') // Жирный **текст**
        .replace(/__(.*?)__/g, '<i>$1</i>')     // Курсив __текст__
        .replace(/^\* /gm, '• ')                // Замена звездочек в списках на буллиты
        .replace(/\n/g, '<br>');                // Сохранение переносов строк
}

function showPostPreview() {
    const postContent = document.getElementById('msg').value;
    
    if (!postContent) {
        tg.showAlert("Сначала напишите что-нибудь!");
        return;
    }

    const previewArea = document.getElementById('preview-text');
    previewArea.innerHTML = parseTelegramStyles(postContent);

    const timeArea = document.getElementById('preview-time');
    const now = new Date();
    timeArea.innerText = `${now.getHours().toString().padStart(2, '0')}:${now.getMinutes().toString().padStart(2, '0')}`;

    document.getElementById('preview-modal').classList.remove('hidden');
    tg.HapticFeedback.impactOccurred('light');
    tg.BackButton.show();
}

function closePreview() {
    const modal = document.getElementById('preview-modal');
    if (modal) {
        modal.classList.add('hidden');
        tg.BackButton.hide();
    }
}

// --- AI LOGIC ---
function toggleAI(show) {
    const modal = document.getElementById('ai-modal');
    if (show) {
        modal.classList.remove('hidden');
        resetAI();
        // Если в редакторе уже есть текст, предложим его для рерайта/анализа
        const currentText = document.getElementById('msg').value.trim();
        if (currentText) {
            document.getElementById('ai-prompt').value = currentText;
        }
        tg.BackButton.show();
    } else {
        modal.classList.add('hidden');
        tg.BackButton.hide();
    }
}

function runAI(action) {
    currentAIAction = action;
    document.getElementById('ai-input-area').classList.remove('hidden');
    const label = document.getElementById('ai-prompt-label');
    const input = document.getElementById('ai-prompt');
    
    if (action === 'generate') {
        label.innerText = "О чем должен быть пост?";
        input.placeholder = "О чем написать? Или вставь свой текст, чтобы я его улучшил...";
        input.value = ""; // Очищаем для новой генерации
    } else {
        label.innerText = "Исходный текст:";
        // Подтягиваем текст из редактора
        input.value = document.getElementById('msg').value;
        if (!input.value) {
            tg.showAlert("Сначала напишите текст в редакторе!");
            toggleAI(false);
            return;
        }
    }
    input.focus();
}

async function submitAI() {
    const btn = document.getElementById('ai-submit-btn');
    const promptField = document.getElementById('ai-prompt');
    const prompt = promptField.value;
    
    if (!prompt && currentAIAction === 'generate') {
        tg.showAlert("О чем мне написать?");
        return;
    }

    const originalText = "✨ Поехали"; 
    btn.innerText = "Работаю..."; 
    btn.disabled = true;

    try {
        const res = await fetch(`${API_URL}/api/ai/process`, {
            method: 'POST',
            headers: {
                'Content-Type': 'application/json',
                'Authorization': `Bearer ${authToken}`
            },
            body: JSON.stringify({ action: currentAIAction, prompt: prompt })
        });
        
        const data = await res.json();
        
        if (data.status === 'success') {
            document.getElementById('ai-input-area').classList.add('hidden');
            document.getElementById('ai-result-area').classList.remove('hidden');
            document.getElementById('ai-result-text').innerText = data.result;
            
            // Запуск таймера защиты
            startAICooldown(60);
        } else {
            tg.showAlert(data.message || "Нужно немного подождать...");
            btn.innerText = originalText;
            btn.disabled = false;
        }
    } catch (e) { 
        tg.showAlert("Проблема со связью"); 
        btn.innerText = originalText;
        btn.disabled = false;
    }
}

function startAICooldown(seconds) {
    const btn = document.getElementById('ai-submit-btn');
    let timeLeft = seconds;
    
    if (aiCooldownTimer) clearInterval(aiCooldownTimer);
    btn.disabled = true;
    
    aiCooldownTimer = setInterval(() => {
        if (timeLeft <= 0) {
            clearInterval(aiCooldownTimer);
            btn.innerText = "✨ Поехали"; 
            btn.disabled = false;
        } else {
            btn.innerText = `Ждать ${timeLeft}с`;
            timeLeft--;
        }
    }, 1000);
}

function useAIResult() {
    const text = document.getElementById('ai-result-text').innerText;
    document.getElementById('msg').value = text;
    document.getElementById('char-count').innerText = text.length;
    toggleAI(false);
}

function resetAI() {
    document.getElementById('ai-input-area').classList.add('hidden');
    document.getElementById('ai-result-area').classList.add('hidden');
    document.getElementById('ai-prompt').value = "";
}

// Запуск
(async () => {
    tg.ready();
    tg.expand();
    changeLanguage(currentLang); // Init language
    
    await login(); // Ждем токен перед загрузкой каналов
    await initChannels();
    
    // Устанавливаем минимальную дату на сегодня
    const now = new Date();
    now.setMinutes(now.getMinutes() - now.getTimezoneOffset());
    document.getElementById('scheduleTime').min = now.toISOString().slice(0,16);

    setInterval(checkConn, 15000);
})();