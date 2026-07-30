/**
 * Голосовая тренировка с ИИ
 * Интеграция с существующим WebSocket голосового ассистента
 */

class VoiceTraining {
    constructor(trainingId, sessionId = null) {
        this.trainingId = trainingId;
        this.sessionId = sessionId;
        this.ws = null;
        this.mediaRecorder = null;
        this.audioContext = null;
        this.audioChunks = [];  // Буфер для накопления аудио чанков (для батчинга)
        this.isRecording = false;
        this.isListening = false;
        this.isConnected = false;
        this.isPaused = false;
        this.isTrainingFinished = false;  // флаг: тренировка уже завершена и провалидирована
        this.audioQueue = [];  // Оставляем для совместимости, но используем audioChunks для батчинга
        this.isPlayingAudio = false;
        this.isProcessingAudio = false;  // Флаг обработки аудио батчами
        this.nextPlayTime = 0;  // Время начала следующего воспроизведения для seamless chaining
        this.currentAudioSource = null;  // Текущий источник аудио
        this.scheduledSources = [];  // Отслеживание запланированных источников
        this.sampleRate = 24000;  // Sample rate для высокого качества
        this.aiIsSpeaking = false; // Флаг: ИИ сейчас говорит
        this.microphoneMuted = false; // Флаг: микрофон отключен на время озвучивания
        this.audioEndReceived = false; // Флаг: получен сигнал audio_end от сервера
        this.activeResponseId = null;  // ID текущего ответа ИИ
        this.lastBargeInTime = 0;  // Время последнего прерывания
        this.bargeInCooldownMs = 1200;  // Минимальный интервал между прерываниями (как в оригинале)
        this.isCancelling = false;  // Флаг: идет отмена ответа
        this.cancelledResponses = new Set();  // Множество отмененных ответов
        this.completedResponses = new Set();  // Множество завершенных ответов

        // Auto-reconnect & heartbeat
        this.connectionState = 'idle';   // idle | connecting | connected | reconnecting | failed
        this.reconnectAttempt = 0;
        this.maxReconnectAttempts = 5;
        this.reconnectTimer = null;
        this.heartbeatTimer = null;
        this.heartbeatTimeoutTimer = null;
        this.heartbeatIntervalMs = 15000;
        this.heartbeatTimeoutMs = 30000;
        this.manualClose = false;          // true — закрытие инициировано клиентом (не реконнект)
        this.pendingAudioBuffer = [];      // буфер аудио чанков, пока соединение не восстановлено
        this.postTrainingUrl = window.postTrainingUrl || '/dashboard';

        // Статистика
        this.stats = {
            userResponses: 0,
            userScore: 0,
            aiQuestions: 0,
            aiTips: 0,
            startTime: null,
            checklistProgress: 0
        };
        
        // Таймер
        this.timerInterval = null;

        // Голос тренера ('male' | 'female'). Начальное значение — сохранённый
        // выбор пользователя; сервер подтвердит фактический в событии connected.
        this.trainerVoice = this.getSavedVoiceChoice() || 'male';

        // Инициализация
        this.init();
    }
    
    async init() {
        console.log('🎤 Инициализация голосовой тренировки...');

        this._showInitOverlay();

        // Подключаем элементы DOM
        this.connectDOMElements();

        // Подключаем обработчики событий
        this.attachEventListeners();

        // Загружаем историю диалога из БД
        await this.loadHistory();

        // Подключаем WebSocket
        await this.connectWebSocket();

        // Запрашиваем доступ к микрофону
        await this.requestMicrophoneAccess();

        this._hideInitOverlay();
    }

    _showInitOverlay() {
        if (document.getElementById('vt-init-overlay')) return;
        const el = document.createElement('div');
        el.id = 'vt-init-overlay';
        el.setAttribute('role', 'status');
        el.setAttribute('aria-label', 'Инициализация тренировки…');
        el.setAttribute('aria-busy', 'true');
        el.style.cssText = [
            'position:fixed;inset:0;z-index:9990;',
            'background:rgba(15,23,42,.55);backdrop-filter:blur(4px);',
            'display:grid;place-items:center;',
        ].join('');
        el.innerHTML = `
          <div style="background:#fff;border-radius:20px;padding:28px 36px;text-align:center;
               box-shadow:0 24px 60px rgba(0,0,0,.25);font-family:Inter,system-ui,sans-serif;min-width:220px;">
            <div id="vt-init-dots" style="display:flex;gap:7px;justify-content:center;margin-bottom:14px;">
              ${[0,1,2].map(i=>`<span style="width:10px;height:10px;border-radius:50%;background:#1e3a8a;
                opacity:.2;animation:vt-pulse 1.2s ease-in-out ${i*0.2}s infinite alternate;display:block"></span>`).join('')}
            </div>
            <div style="font-size:14px;font-weight:600;color:#0f172a">Подготовка тренировки…</div>
            <div style="font-size:12px;color:#64748b;margin-top:4px">Подключение и запрос микрофона</div>
          </div>`;
        if (!document.getElementById('vt-pulse-style')) {
            const s = document.createElement('style');
            s.id = 'vt-pulse-style';
            s.textContent = '@keyframes vt-pulse{to{opacity:.9;transform:scale(1.15)}}';
            document.head.appendChild(s);
        }
        document.body.appendChild(el);
    }

    _hideInitOverlay() {
        const el = document.getElementById('vt-init-overlay');
        if (!el) return;
        el.style.transition = 'opacity .3s';
        el.style.opacity = '0';
        setTimeout(() => el.remove(), 320);
    }
    
    connectDOMElements() {
        // Кнопки управления
        this.micButton = document.getElementById('mic-button');
        this.pauseBtn = document.getElementById('pause-btn');
        this.stopBtn = document.getElementById('stop-btn');
        this.settingsBtn = document.getElementById('settings-btn');
        
        // Чат
        this.chatMessages = document.getElementById('chat-messages');
        this.aiTyping = document.getElementById('ai-typing');
        this.aiSpeakingMain = document.getElementById('ai-speaking-main');
        this.exportTranscriptBtn = document.getElementById('export-transcript');
        this.clearChatBtn = document.getElementById('clear-chat');
        
        // Статус
        this.connectionDot = document.getElementById('connection-dot');
        this.connectionStatus = document.getElementById('connection-status');
        this.chatConnectionDot = document.getElementById('chat-connection-dot');
        this.chatConnectionText = document.getElementById('chat-connection-text');
        this.micStatus = document.getElementById('mic-status');
        this.recordingContainer = document.getElementById('recording-container');
        this.trainingStatus = document.getElementById('training-status');
        this.trainingTime = document.getElementById('training-time');
        
        // Прогресс
        this.progressFill = document.getElementById('progress-fill');
        this.progressText = document.getElementById('progress-text');
        this.progressPercent = document.getElementById('progress-percent');
        
        // Статистика участников
        this.userResponsesEl = document.getElementById('user-responses');
        this.userScoreEl = document.getElementById('user-score');
        this.aiQuestionsEl = document.getElementById('ai-questions');
        this.aiTipsEl = document.getElementById('ai-tips');
        this.aiParticipant = document.getElementById('ai-participant');
        this.aiStatusDot = document.getElementById('ai-status');
        this.aiSpeaking = document.getElementById('ai-speaking');
        
        // Чеклист
        this.checklistToggle = document.getElementById('checklist-toggle');
        this.checklistSidebar = document.getElementById('checklist-sidebar');
        this.closeChecklist = document.getElementById('close-checklist');
        
        // Модальное окно настроек
        this.settingsModal = document.getElementById('settings-modal');
        this.closeSettingsModal = document.getElementById('close-settings-modal');
        this.applySettings = document.getElementById('apply-settings');
        this.cancelSettings = document.getElementById('cancel-settings');
        
        // Модальное окно подтверждения завершения
        this.confirmStopModal = document.getElementById('confirm-stop-modal');
        this.confirmStopBtn = document.getElementById('confirm-stop-btn');
        this.cancelStopBtn = document.getElementById('cancel-stop-btn');
        
        // Уведомления
        this.notificationsContainer = document.getElementById('notifications-container');
    }
    
    attachEventListeners() {
        // Кнопка микрофона
        if (this.micButton) {
            this.micButton.addEventListener('click', () => this.toggleRecording());
        }
        
        // Пауза
        if (this.pauseBtn) {
            this.pauseBtn.addEventListener('click', () => this.togglePause());
        }
        
        // Завершить
        if (this.stopBtn) {
            this.stopBtn.addEventListener('click', () => this.stopTraining());
        }
        
        // Настройки
        if (this.settingsBtn) {
            this.settingsBtn.addEventListener('click', () => this.openSettings());
        }
        
        // Чат
        if (this.exportTranscriptBtn) {
            this.exportTranscriptBtn.addEventListener('click', () => this.exportTranscript());
        }
        
        if (this.clearChatBtn) {
            this.clearChatBtn.addEventListener('click', () => this.clearChat());
        }
        
        // Чеклист
        if (this.checklistToggle) {
            this.checklistToggle.addEventListener('click', () => this.toggleChecklist());
        }
        
        if (this.closeChecklist) {
            this.closeChecklist.addEventListener('click', () => this.closeChecklistSidebar());
        }
        
        // Модальное окно
        if (this.closeSettingsModal) {
            this.closeSettingsModal.addEventListener('click', () => this.closeSettings());
        }
        
        if (this.applySettings) {
            this.applySettings.addEventListener('click', () => this.saveSettings());
        }
        
        if (this.cancelSettings) {
            this.cancelSettings.addEventListener('click', () => this.closeSettings());
        }
        
        // Чеклист - отслеживание изменений
        document.querySelectorAll('.checklist-checkbox').forEach(checkbox => {
            checkbox.addEventListener('change', () => this.updateChecklistProgress());
        });
        
        // Закрытие модального окна по клику вне его
        if (this.settingsModal) {
            this.settingsModal.addEventListener('click', (e) => {
                if (e.target === this.settingsModal) {
                    this.closeSettings();
                }
            });
        }
        
        // Модальное окно подтверждения завершения
        if (this.confirmStopBtn) {
            this.confirmStopBtn.addEventListener('click', () => {
                this.closeConfirmStopModal();
                this.confirmStopTraining();
            });
        }
        
        if (this.cancelStopBtn) {
            this.cancelStopBtn.addEventListener('click', () => {
                this.closeConfirmStopModal();
            });
        }
        
        // Закрытие модального окна подтверждения по клику вне его
        if (this.confirmStopModal) {
            this.confirmStopModal.addEventListener('click', (e) => {
                if (e.target === this.confirmStopModal) {
                    this.closeConfirmStopModal();
                }
            });
        }
        
        // Закрытие модального окна подтверждения по Escape
        document.addEventListener('keydown', (e) => {
            if (e.key === 'Escape' && this.confirmStopModal && this.confirmStopModal.style.display === 'flex') {
                this.closeConfirmStopModal();
            }
        });
    }
    
    closeConfirmStopModal() {
        if (this.confirmStopModal) {
            this.confirmStopModal.style.display = 'none';
        }
    }
    
    async connectWebSocket(isReconnect = false) {
        try {
            if (this.reconnectTimer) {
                clearTimeout(this.reconnectTimer);
                this.reconnectTimer = null;
            }

            this.connectionState = isReconnect ? 'reconnecting' : 'connecting';
            this.updateConnectionIndicator();
            this.manualClose = false;

            // Закрываем старое соединение если оно есть (при переподключении)
            if (this.ws) {
                const oldState = this.ws.readyState;
                console.log('🔌 Закрываем старое WebSocket соединение перед переподключением', {
                    state: oldState,
                    stateName: ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][oldState]
                });
                this.ws.onclose = null;
                this.ws.onopen = null;
                this.ws.onmessage = null;
                this.ws.onerror = null;
                if (oldState === WebSocket.OPEN || oldState === WebSocket.CONNECTING) {
                    try { this.ws.close(1000, "Client reconnecting"); } catch (_) {}
                }
                this.ws = null;
                await new Promise(resolve => setTimeout(resolve, 200));
            }

            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';

            const userId = window.currentUserId;
            const urlParams = new URLSearchParams(window.location.search);
            const trainingId = urlParams.get('training_id') || this.trainingId || '1';

            let wsUrl;
            if (userId) {
                wsUrl = `${protocol}//${window.location.host}/voice-training/ws?user_id=${userId}&training_id=${trainingId}`;
                if (this.sessionId) {
                    wsUrl += `&db_session_id=${encodeURIComponent(this.sessionId)}`;
                }
                console.log('🔌 Подключение к WebSocket', {userId, trainingId, isReconnect, db_session_id: this.sessionId});
            } else {
                wsUrl = `${protocol}//${window.location.host}/voice-assistant/ws`;
                console.log('⚠️ user_id не найден, используем старый endpoint');
            }

            this.ws = new WebSocket(wsUrl);

            this.ws.onopen = () => {
                console.log('✅ WebSocket подключен');
                this.isConnected = true;
                this.connectionState = 'connected';
                this.reconnectAttempt = 0;
                this.updateConnectionStatus('connected', 'Подключено к серверу');
                this.updateConnectionIndicator();

                if (this.micButton) {
                    this.micButton.disabled = false;
                    this.micStatus.textContent = 'Ожидание сессии...';
                }

                this.startHeartbeat();
                this.flushPendingAudio();
                this.hideReconnectBanner();

                if (isReconnect) {
                    this.showNotification('success', 'Соединение восстановлено', 'Можно продолжать тренировку.');
                }
            };

            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event);
            };

            this.ws.onerror = (error) => {
                console.error('❌ Ошибка WebSocket:', error);
                this.updateConnectionStatus('error', 'Ошибка подключения');
                this.updateConnectionIndicator();
            };

            this.ws.onclose = (event) => {
                console.log('🔌 WebSocket отключен', {
                    code: event.code,
                    reason: event.reason,
                    wasClean: event.wasClean,
                });
                this.isConnected = false;
                this.stopHeartbeat();

                const wasManual = this.manualClose || event.code === 1000 || event.code === 1001;
                if (wasManual || this.isTrainingFinished) {
                    this.connectionState = 'idle';
                    this.updateConnectionStatus('connecting', 'Отключено');
                    this.updateConnectionIndicator();
                    return;
                }

                this.scheduleReconnect();
            };

        } catch (error) {
            console.error('❌ Ошибка при подключении WebSocket:', error);
            this.connectionState = 'failed';
            this.updateConnectionIndicator();
            this.scheduleReconnect();
        }
    }

    scheduleReconnect() {
        if (this.reconnectAttempt >= this.maxReconnectAttempts) {
            console.error('❌ Достигнут лимит попыток реконнекта');
            this.connectionState = 'failed';
            this.updateConnectionIndicator();
            this.hideReconnectBanner();
            this.showReconnectFailedOverlay();
            return;
        }

        this.reconnectAttempt += 1;
        const delays = [1000, 2000, 4000, 8000, 16000];
        const delay = delays[Math.min(this.reconnectAttempt - 1, delays.length - 1)];

        this.connectionState = 'reconnecting';
        this.updateConnectionStatus('connecting', `Переподключение (попытка ${this.reconnectAttempt}/${this.maxReconnectAttempts}) через ${Math.round(delay/1000)}с…`);
        this.updateConnectionIndicator();
        this.showReconnectBanner(this.reconnectAttempt, this.maxReconnectAttempts, Math.round(delay / 1000));

        console.log(`🔄 Реконнект через ${delay}мс (попытка ${this.reconnectAttempt}/${this.maxReconnectAttempts})`);
        this.reconnectTimer = setTimeout(() => {
            this.connectWebSocket(true);
        }, delay);
    }

    showReconnectBanner(attempt, max, delaySec) {
        let banner = document.getElementById('vt-reconnect-banner');
        if (!banner) {
            banner = document.createElement('div');
            banner.id = 'vt-reconnect-banner';
            banner.setAttribute('role', 'status');
            banner.setAttribute('aria-live', 'polite');
            banner.style.cssText = [
                'position:sticky;top:0;z-index:490;',
                'background:#fffbeb;border:1.5px solid #fcd34d;border-radius:10px;',
                'padding:11px 16px;margin:0 0 12px;',
                'display:flex;align-items:center;gap:10px;',
                'font-family:Inter,system-ui,sans-serif;font-size:13px;color:#78350f;',
            ].join('');
            const target = this.chatMessages || document.querySelector('.voice-training-container') || document.body;
            target.prepend(banner);
        }
        banner.innerHTML = `
          <svg style="flex-shrink:0;animation:vt-spin 1.2s linear infinite" width="16" height="16"
               viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5"
               aria-hidden="true">
            <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.5"/>
          </svg>
          <span>Связь прервана — переподключение (${attempt}/${max}), через ${delaySec}с…</span>`;
        if (!document.getElementById('vt-spin-style')) {
            const s = document.createElement('style');
            s.id = 'vt-spin-style';
            s.textContent = '@keyframes vt-spin{to{transform:rotate(360deg)}}';
            document.head.appendChild(s);
        }
    }

    hideReconnectBanner() {
        const b = document.getElementById('vt-reconnect-banner');
        if (b) b.remove();
    }

    startHeartbeat() {
        this.stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
            try {
                this.ws.send(JSON.stringify({ type: 'ping', ts: Date.now() }));
                if (this.heartbeatTimeoutTimer) clearTimeout(this.heartbeatTimeoutTimer);
                this.heartbeatTimeoutTimer = setTimeout(() => {
                    console.warn('⚠️ Heartbeat timeout — соединение считаем мёртвым, инициируем реконнект');
                    try { this.ws && this.ws.close(4000, 'Heartbeat timeout'); } catch (_) {}
                }, this.heartbeatTimeoutMs);
            } catch (e) {
                console.warn('⚠️ Не удалось отправить ping:', e);
            }
        }, this.heartbeatIntervalMs);
    }

    stopHeartbeat() {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
        if (this.heartbeatTimeoutTimer) {
            clearTimeout(this.heartbeatTimeoutTimer);
            this.heartbeatTimeoutTimer = null;
        }
    }

    handlePong() {
        if (this.heartbeatTimeoutTimer) {
            clearTimeout(this.heartbeatTimeoutTimer);
            this.heartbeatTimeoutTimer = null;
        }
    }

    flushPendingAudio() {
        if (!this.pendingAudioBuffer || this.pendingAudioBuffer.length === 0) return;
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) return;
        console.log(`📤 Отправляем ${this.pendingAudioBuffer.length} буферизованных аудио-сообщений после реконнекта`);
        const buffer = this.pendingAudioBuffer.slice();
        this.pendingAudioBuffer = [];
        for (const msg of buffer) {
            try { this.ws.send(typeof msg === 'string' ? msg : JSON.stringify(msg)); }
            catch (e) { console.warn('⚠️ Не удалось отправить буферизованное сообщение:', e); }
        }
    }

    bufferAudioMessage(payload) {
        // Ограничиваем буфер ~5 секундами (около 100 чанков по 50мс)
        const MAX_BUFFER = 100;
        if (this.pendingAudioBuffer.length >= MAX_BUFFER) {
            this.pendingAudioBuffer.shift();
        }
        this.pendingAudioBuffer.push(payload);
    }

    updateConnectionIndicator() {
        let el = document.getElementById('vt-conn-indicator');
        if (!el) {
            el = document.createElement('div');
            el.id = 'vt-conn-indicator';
            el.style.cssText = 'position:fixed;top:14px;right:14px;z-index:9999;padding:8px 14px;border-radius:999px;font-size:12px;font-weight:700;letter-spacing:.02em;box-shadow:0 8px 22px rgba(0,0,0,.18);transition:all .25s ease;display:flex;align-items:center;gap:8px;font-family:Inter,system-ui,sans-serif;';
            document.body.appendChild(el);
        }
        const state = this.connectionState;
        const map = {
            idle:         { bg: '#64748b', text: 'Не подключено' },
            connecting:   { bg: '#f59e0b', text: 'Подключение…' },
            connected:    { bg: '#10b981', text: 'Соединение' },
            reconnecting: { bg: '#f59e0b', text: `Переподключение ${this.reconnectAttempt}/${this.maxReconnectAttempts}` },
            failed:       { bg: '#ef4444', text: 'Соединение потеряно' },
        };
        const cfg = map[state] || map.idle;
        el.style.background = cfg.bg;
        el.style.color = 'white';
        el.innerHTML = `<span style="width:8px;height:8px;border-radius:50%;background:white;opacity:.85;${state==='connected'?'box-shadow:0 0 0 4px rgba(255,255,255,.25);':''}"></span>${cfg.text}`;
    }

    showReconnectFailedOverlay() {
        if (document.getElementById('vt-reconnect-overlay')) return;
        const overlay = document.createElement('div');
        overlay.id = 'vt-reconnect-overlay';
        overlay.style.cssText = 'position:fixed;inset:0;z-index:10000;background:rgba(10,35,64,.78);backdrop-filter:blur(6px);display:grid;place-items:center;padding:20px;font-family:Inter,system-ui,sans-serif;';
        overlay.innerHTML = `
          <div style="background:white;border-radius:24px;padding:28px;max-width:440px;width:100%;box-shadow:0 28px 80px rgba(0,0,0,.3);text-align:center;">
            <div style="display:flex;justify-content:center;margin-bottom:10px;color:#1e3a8a;">
              <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><line x1="1" y1="1" x2="23" y2="23"/><path d="M16.72 11.06A10.94 10.94 0 0 1 19 12.55M5 12.55a10.94 10.94 0 0 1 5.17-2.39M10.71 5.05A16 16 0 0 1 22.58 9M1.42 9a15.91 15.91 0 0 1 4.7-2.88M8.53 16.11a6 6 0 0 1 6.95 0M12 20h.01"/></svg>
            </div>
            <div style="font-size:20px;font-weight:900;color:#0f172a;margin-bottom:10px;">Соединение потеряно</div>
            <div style="font-size:14px;color:#64748b;line-height:1.6;margin-bottom:20px;">
              Не удалось восстановить связь с сервером после нескольких попыток. Проверьте интернет
              и попробуйте подключиться вручную, или завершите тренировку.
            </div>
            <div style="display:flex;gap:10px;justify-content:center;flex-wrap:wrap;">
              <button id="vt-reconnect-btn" style="background:#1e3a8a;color:white;border:0;padding:12px 20px;border-radius:999px;font-weight:700;font-size:14px;cursor:pointer;">Переподключиться</button>
              <button id="vt-end-btn" style="background:#f1f5f9;color:#0f172a;border:1px solid #e6e9ef;padding:12px 20px;border-radius:999px;font-weight:700;font-size:14px;cursor:pointer;">Завершить тренировку</button>
            </div>
          </div>
        `;
        document.body.appendChild(overlay);
        document.getElementById('vt-reconnect-btn').addEventListener('click', () => {
            overlay.remove();
            this.reconnectAttempt = 0;
            this.connectWebSocket(true);
        });
        document.getElementById('vt-end-btn').addEventListener('click', () => {
            overlay.remove();
            // Корректное завершение через confirmStopTraining: прогоняет AI-валидатор и
            // сохраняет результат (POST /training/complete идёт по HTTP — работает и без
            // живого WS). Раньше звались несуществующие endTraining/finishTraining, поэтому
            // кнопка просто закрывала оверлей и ничего не завершала.
            this.confirmStopTraining();
        });
    }
    
    handleWebSocketMessage(event) {
        try {
            const data = JSON.parse(event.data);
            const eventType = data.type;
            
            console.log('📨 Получено событие:', eventType, 'Полные данные:', data);
            
            // Проверяем, что eventType - строка
            if (typeof eventType !== 'string') {
                console.error('❌ eventType не является строкой:', typeof eventType, eventType);
                return;
            }

            // Heartbeat: сервер ответил на ping — соединение живое
            if (eventType === 'pong') {
                this.handlePong();
                return;
            }

            // Обрабатываем события точно так же, как в оригинале
            switch (eventType) {
                case 'connected':
                    console.log('✅ Сессия создана:', data.session_id, 'db_session_id:', data.db_session_id);
                    this.isConnected = true;
                    if (data.db_session_id) {
                        this.sessionId = data.db_session_id;
                        console.log(`✅ sessionId обновлён на db_session_id=${data.db_session_id} (для валидации)`);
                    } else {
                        console.warn('⚠️ connected: db_session_id не пришёл, sessionId остаётся:', this.sessionId);
                    }
                    this.showNotification('success', 'Подключено', data.message || 'Сессия создана');
                    // Сервер сообщает, каким голосом сессия говорит сейчас.
                    // Если у пользователя сохранён другой выбор — просим переключить.
                    this.trainerVoice = data.voice || this.trainerVoice || 'male';
                    {
                        const saved = this.getSavedVoiceChoice();
                        if (saved && saved !== this.trainerVoice) this.applyVoiceChoice(saved);
                    }
                    this.autoStartListening();
                    break;

                case 'voice_changed':
                    this.trainerVoice = data.voice;
                    console.log('🗣️ Голос переключён на:', data.voice, data.voice_name);
                    this.showNotification(
                        'success',
                        'Голос изменён',
                        data.voice === 'female'
                            ? 'Тренер будет говорить женским голосом со следующей реплики.'
                            : 'Тренер будет говорить мужским голосом со следующей реплики.'
                    );
                    break;
                    
                case 'session.created':
                    console.log('✅ Сессия Azure создана');
                    this.autoStartListening();
                    break;
                
                case 'input_audio_buffer.speech_started':
                    // Событие от Azure (проксированное через сервер)
                    const nowTs = Date.now();
                    // Debounce multiple rapid speech_started events
                    if (nowTs - this.lastBargeInTime < this.bargeInCooldownMs) {
                        console.log('🛑 Ignoring speech_started (within cooldown)');
                        break;
                    }
                    // Only treat as interruption if AI audio is currently playing / scheduled or active response present
                    const aiSpeaking = this.scheduledSources.length > 0 || this.currentAudioSource || this.isPlayingAudio;
                    if (aiSpeaking || (this.activeResponseId && !this.isCancelling)) {
                        console.log('🎤 Speech detected (interrupt)...');
                        this.interruptForUserSpeech();
                        this.lastBargeInTime = nowTs;
                    } else {
                        console.log('Speech started (no AI audio to interrupt)');
                    }
                    break;
                
                case 'input_audio_buffer.speech_stopped':
                    console.log('Speech stopped event received');
                    break;
                
                case 'response.created':
                    // ИИ начал генерировать ответ
                    this.showAITyping(true);
                    this.currentAssistantMessage = '';
                    // Allow currently scheduled audio to finish; only reset chunk accumulator
                    this.audioChunks = [];
                    // Maintain nextPlayTime so new audio appends seamlessly
                        this.isCancelling = false;
                    this.activeResponseId = (data.response && data.response.id) || data.response_id || data.item_id || null;
                    console.log('📌 Активный response_id установлен:', this.activeResponseId);
                    // Clear any leftover cancelled response audio
                    this.stopAllScheduledAudio();
                    break;
                
                case 'conversation.item.created':
                    // Некоторые бэкенды встраивают транскрипт здесь
                    try {
                        const item = data.item || data.data || {};
                        if (item.type === 'input_audio' && item.transcript) {
                            console.log('✅ User transcript (item.created):', item.transcript);
                            this.addUserMessage(item.transcript);
                            this.stats.userResponses++;
                            this.updateStats();
                        }
                        // Иногда транскрипт вложен в content array
                        if (item.type === 'input_audio' && Array.isArray(item.content)) {
                            for (const c of item.content) {
                                if (c.transcript) {
                                    console.log('✅ User transcript (item.content):', c.transcript);
                                    this.addUserMessage(c.transcript);
                                    this.stats.userResponses++;
                                    this.updateStats();
                                    break;
                                }
                            }
                        }
                    } catch(e) {
                        console.warn('conversation.item.created parse issue', e);
                    }
                    break;
                
                case 'conversation.item.input_audio_transcription.completed':
                case 'input_audio_transcription.completed':
                case 'input_audio_buffer.transcription.completed':
                    // Транскрипция речи пользователя завершена
                    const userTranscript = data.transcript || this.pendingUserTranscript;
                    if (userTranscript) {
                        console.log('✅ User transcription completed:', userTranscript);
                        this.addUserMessage(userTranscript);
                        this.stats.userResponses++;
                        this.updateStats();
                        this.pendingUserTranscript = '';
                    }
                    break;
                
                case 'conversation.item.input_audio_transcription.delta':
                case 'input_audio_transcription.delta':
                case 'input_audio_buffer.transcription.delta':
                    // Частичная транскрипция
                    const partial = data.delta || data.text || data.transcript;
                    if (partial) {
                        this.pendingUserTranscript += partial;
                        console.log('🗣️ User partial transcript acc:', this.pendingUserTranscript);
                    }
                    break;
                
                case 'response.audio_transcript.delta':
                    // Частичный текст ответа ИИ
                    const delta = data.delta;
                    const responseId = data.response_id || data.item_id;
                    
                    if (delta && responseId) {
                        if (!this.responseTranscripts) {
                            this.responseTranscripts = new Map();
                        }
                        if (!this.responseTranscripts.has(responseId)) {
                            this.responseTranscripts.set(responseId, '');
                        }
                        
                        const updatedTranscript = this.responseTranscripts.get(responseId) + delta;
                        this.responseTranscripts.set(responseId, updatedTranscript);
                        this.currentAssistantMessage = updatedTranscript;
                        this.updateAIMessage(updatedTranscript);
                    }
                    break;
                
                case 'response.audio_transcript.done':
                    // Текст ответа ИИ завершен
                    const finalResponseId = data.response_id || data.item_id;
                    
                    if (finalResponseId && !this.completedResponses.has(finalResponseId)) {
                        this.completedResponses.add(finalResponseId);
                        if (this.activeResponseId === finalResponseId) {
                            this.activeResponseId = null; // Clear active if done
                        }
                        this.showAITyping(false);
                        
                        const finalTranscript = this.responseTranscripts?.get(finalResponseId) || this.currentAssistantMessage || data.text;
                        if (finalTranscript) {
                            this.updateAIMessage(finalTranscript);
                            this.stats.aiQuestions++;
                            this.updateStats();
                        }
                    }
                    break;
                
                case 'response.audio.delta':
                    // Аудио чанк ответа ИИ
                    console.log('✅ ОБРАБОТКА response.audio.delta НАЧАЛАСЬ');
                    const audioData = data.delta;
                    const responseIdForAudio = data.response_id || data.item_id;
                    
                    console.log('🔊 AUDIO DELTA EVENT RECEIVED', { 
                        hasAudio: !!audioData,
                        audioDataLength: audioData ? audioData.length : 0,
                        responseId: responseIdForAudio,
                        activeResponseId: this.activeResponseId,
                        fullData: data
                    });
                    
                    // Устанавливаем activeResponseId если он передан (на случай если response.created не пришел)
                    if (responseIdForAudio && !this.activeResponseId) {
                        this.activeResponseId = responseIdForAudio;
                        console.log('📌 Активный response_id установлен из audio.delta:', this.activeResponseId);
                    }
                    
                    // Если это первый чанк и микрофон ещё не отключен - отключаем
                    if (!this.microphoneMuted && this.isRecording) {
                        console.log('⚠️ Получен response.audio.delta до отключения микрофона - отключаем сейчас');
                        this.microphoneMuted = true;
                        this.aiIsSpeaking = true;
                        if (this.micButton) {
                            this.micStatus.textContent = 'Микрофон отключён (ИИ говорит)';
                            this.micButton.classList.add('muted-for-ai');
                        }
                    }
                    
                    if (this.isCancelling) {
                        console.log('⚠️ Ignoring audio delta during cancellation');
                        break;
                    }
                    
                    if (audioData) {
                        // Buffer the audio chunk instead of playing immediately
                        this.audioChunks.push(audioData);
                        console.log('📦 Добавлен аудио чанк в буфер, всего:', this.audioChunks.length);
                        
                        // Показываем индикатор проигрывания
                        this.showAISpeaking(true);
                        
                        // Обрабатываем буфер батчами
                        this.processAudioBuffer();
                    } else {
                        console.warn('⚠️ response.audio.delta без данных');
                    }
                    break;
                    
                case 'response.audio.done':
                    console.log('🔊 AUDIO DONE EVENT RECEIVED');
                    // Process any remaining audio and mark as complete
                    this.processAudioBuffer(true);
                    break;
                    
                case 'response.cancelled':
                    // Ответ был отменен
                    const cancelledResponseId = data.response_id || data.item_id || this.activeResponseId;
                    if (cancelledResponseId) {
                        this.cancelledResponses.add(cancelledResponseId);
                        if (this.activeResponseId === cancelledResponseId) {
                            this.activeResponseId = null;
                        }
                        this.isCancelling = false;
                        console.log('⛔ Ответ отменен:', cancelledResponseId);
                    }
                    break;
                    
                case 'ai_text':
                case 'user_text':
                    // Новый формат от сервера (для обратной совместимости)
                    if (eventType === 'user_text') {
                        this.handleTranscriptMessage(data);
                    } else {
                    this.handleAITextMessage(data);
                    }
                    break;
                    
                case 'audio_start':
                    this.handleAudioStart(data);
                    break;
                    
                case 'audio_chunk':
                    this.handleAudioChunk(data);
                    break;
                    
                case 'audio_end':
                    this.handleAudioEndSignal();
                    break;
                    
                case 'status':
                    // Обрабатываем статусы от сервера
                    if (data.status === 'thinking' && data.response_id) {
                        this.activeResponseId = data.response_id;
                        this.isCancelling = false;
                        console.log('📌 Активный response_id установлен из status:', this.activeResponseId);
                    } else if (data.status === 'completed' || data.status === 'cancelled') {
                        if (data.response_id && this.activeResponseId === data.response_id) {
                            if (data.status === 'cancelled') {
                                this.cancelledResponses.add(data.response_id);
                            } else {
                                this.completedResponses.add(data.response_id);
                            }
                        }
                        this.activeResponseId = null;
                        this.isCancelling = false;
                    } else if (data.status === 'cancelling') {
                        this.isCancelling = true;
                    }
                    this.handleStatusMessage(data);
                    break;
                    
                case 'stage_changed':
                    this.handleStageChanged(data);
                    break;
                
                case 'training_completed':
                    this.handleTrainingCompleted(data);
                    break;
                
                case 'error':
                    this.handleError(data);
                    break;
                    
                default:
                    console.warn('⚠️ Неизвестный тип события:', eventType, 'Тип:', typeof eventType, 'Длина:', eventType?.length);
                    console.warn('Полные данные события:', data);
                    // Попытка обработать response.audio.delta даже если case не сработал
                    if (eventType === 'response.audio.delta' || String(eventType).trim() === 'response.audio.delta') {
                        console.log('🔧 Попытка обработать response.audio.delta через fallback');
                        const audioData = data.delta;
                        const responseIdForAudio = data.response_id || data.item_id;
                        
                        if (audioData) {
                            if (!this.microphoneMuted && this.isRecording) {
                                this.microphoneMuted = true;
                                this.aiIsSpeaking = true;
                                if (this.micButton) {
                                    this.micStatus.textContent = 'Микрофон отключён (ИИ говорит)';
                                    this.micButton.classList.add('muted-for-ai');
                                }
                            }
                            if (!this.isCancelling) {
                                this.audioChunks.push(audioData);
                                this.showAISpeaking(true);
                                this.processAudioBuffer();
                            }
                        }
                    }
            }
        } catch (error) {
            console.error('❌ Ошибка обработки сообщения:', error);
        }
    }
    
    handleStatusMessage(data) {
        console.log('📊 Статус компонентов:', data.components);
    }
    
    handleTranscriptMessage(data) {
        console.log('📝 Транскрипция:', data.text);
        
        // Добавляем сообщение пользователя в чат
        this.addUserMessage(data.text);
        
        // Обновляем статистику
        this.stats.userResponses++;
        this.updateStats();
        
        // Показываем индикатор печати ИИ
        this.showAITyping(true);
        
        // Обновляем статус микрофона
        if (this.micButton && this.isRecording) {
            this.micStatus.textContent = 'ИИ думает...';
        }
    }
    
    currentAssistantMessage = '';
    
    handleAssistantChunk(data) {
        // Накапливаем текст ответа ассистента
        this.currentAssistantMessage += data.text;
        
        // Обновляем сообщение ИИ в чате (или создаем новое)
        this.updateAIMessage(this.currentAssistantMessage);
    }
    
    handleAITextMessage(data) {
        // Обработка полного текста от ИИ (новый формат)
        console.log('💬 ИИ ответил:', data.text);
        
        // Скрываем индикатор печати
        this.showAITyping(false);
        
        // Сбрасываем текущий элемент сообщения чтобы создать новое
        this.currentAIMessageElement = null;
        
        // Добавляем полное сообщение ИИ
        this.updateAIMessage(data.text);
        
        // Обновляем статистику
        this.stats.aiQuestions++;
        this.updateStats();
    }
    
    handleAssistantComplete(data) {
        console.log('✅ Ответ ассистента завершен');
        
        // Скрываем индикатор печати
        this.showAITyping(false);
        
        // Финализируем сообщение ИИ
        if (this.currentAssistantMessage) {
            this.finalizeAIMessage();
            this.currentAssistantMessage = '';
        }
        
        // Обновляем статистику
        this.stats.aiQuestions++;
        this.updateStats();
    }
    
    handleAudioStart(data) {
        console.log('🔊 === ИИ НАЧАЛ ОЗВУЧИВАНИЕ ===');
        console.log('🔇 Отключаем микрофон на время озвучивания');
        
        // Устанавливаем activeResponseId если он передан в audio_start (на случай если response.created не пришел)
        if (data.response_id && !this.activeResponseId) {
            this.activeResponseId = data.response_id;
            console.log('📌 Активный response_id установлен из audio_start:', this.activeResponseId);
        }
        
        // Останавливаем все текущие источники аудио (как в оригинале)
        this.stopAllScheduledAudio();
        
        // Очищаем буферы для нового ответа
        this.audioChunks = [];
        this.isProcessingAudio = false;
        this.nextPlayTime = this.audioContext ? this.audioContext.currentTime : 0;
        
        // Устанавливаем флаг, что ИИ сейчас говорит
        this.aiIsSpeaking = true;
        this.microphoneMuted = true; // Отключаем микрофон
        this.audioEndReceived = false; // Сбрасываем флаг окончания
        console.log('🏴 Флаг aiIsSpeaking установлен в TRUE, microphoneMuted = TRUE, audioEndReceived = FALSE');
        
        // Обновляем статус
        if (this.micButton && this.isRecording) {
            this.micStatus.textContent = 'Микрофон отключён (ИИ говорит)';
            this.micButton.classList.add('muted-for-ai');
        }
    }
    
    handleSpeechStarted() {
        // Обработка speech_started с debounce и проверками (как в оригинале)
        const nowTs = Date.now();
        
        // Debounce: игнорируем слишком частые события
        if (nowTs - this.lastBargeInTime < this.bargeInCooldownMs) {
            console.log('🛑 Игнорируем speech_started (в пределах cooldown)');
            return;
        }
        
        // Проверяем, что ИИ действительно говорит (воспроизводит аудио или есть активный ответ)
        const aiSpeaking = this.scheduledSources.length > 0 || this.currentAudioSource || this.isPlayingAudio;
        const hasActiveResponse = this.activeResponseId && !this.isCancelling && !this.cancelledResponses.has(this.activeResponseId);
        
        console.log('🔍 Проверка прерывания:', {
            aiSpeaking,
            hasActiveResponse,
            activeResponseId: this.activeResponseId,
            isCancelling: this.isCancelling,
            scheduledSources: this.scheduledSources.length,
            currentAudioSource: !!this.currentAudioSource,
            isPlayingAudio: this.isPlayingAudio
        });
        
        // Прерываем только если ИИ действительно говорит
        if (aiSpeaking || hasActiveResponse) {
            console.log('🎤 Обнаружена речь пользователя (прерывание)');
            this.interruptForUserSpeech();
            this.lastBargeInTime = nowTs;
        } else {
            console.log('🎤 Обнаружена речь пользователя (ИИ не говорит, просто слушаем)');
            // Просто логируем, не прерываем
        }
    }
    
    interruptForUserSpeech() {
        // Прерывание для речи пользователя (как в оригинале)
        try {
            console.log('⛔ Прерывание для речи пользователя');
            
            // Останавливаем текущее воспроизведение
            this.stopAllScheduledAudio();
            
            // Очищаем очередь/буферы аудио
            this.audioChunks = [];
            this.isProcessingAudio = false;
            this.nextPlayTime = this.audioContext ? this.audioContext.currentTime : 0;
            
            // Отменяем активный ответ на сервере (если есть)
            if (this.ws && this.ws.readyState === WebSocket.OPEN && this.activeResponseId && !this.completedResponses?.has(this.activeResponseId)) {
                console.log('⛔ Отправляем response.cancel для response_id:', this.activeResponseId);
                const cancelMsg = {
                    type: 'response.cancel',
                    response_id: this.activeResponseId,
                    event_id: ''
                };
                this.ws.send(JSON.stringify(cancelMsg));
                this.isCancelling = true;
                
                // Отмечаем ответ как отмененный
                if (!this.cancelledResponses) {
                    this.cancelledResponses = new Set();
                }
                this.cancelledResponses.add(this.activeResponseId);
            } else {
                console.log('⚠️ Не удалось отправить response.cancel:', {
                    ws: !!this.ws,
                    readyState: this.ws?.readyState,
                    activeResponseId: this.activeResponseId,
                    isCancelling: this.isCancelling
                });
            }
            
            // Включаем микрофон обратно
            this.microphoneMuted = false;
            this.aiIsSpeaking = false;
            
            if (this.micButton) {
                this.micButton.classList.remove('muted-for-ai');
                this.micStatus.textContent = 'Слушаю...';
            }
            
        } catch (e) {
            console.error('Ошибка при прерывании:', e);
        }
    }
    
    handleAudioChunk(data) {
        // Устанавливаем activeResponseId если он передан в audio_chunk (на случай если response.created не пришел)
        if (data.response_id && !this.activeResponseId) {
            this.activeResponseId = data.response_id;
            console.log('📌 Активный response_id установлен из audio_chunk:', this.activeResponseId);
        }
        
        // Если это первый чанк и микрофон ещё не отключен - отключаем
        if (!this.microphoneMuted && this.isRecording) {
            console.log('⚠️ Получен audio_chunk до audio_start - отключаем микрофон сейчас');
            this.microphoneMuted = true;
            this.aiIsSpeaking = true;
            if (this.micButton) {
                this.micStatus.textContent = 'Микрофон отключён (ИИ говорит)';
                this.micButton.classList.add('muted-for-ai');
            }
        }
        
        // Добавляем аудио чанк в буфер для батчинга (как в оригинальном клиенте)
        const audioBase64 = data.audio || data.audio_data;
        if (audioBase64) {
            this.audioChunks.push(audioBase64);
            console.log('📦 Добавлен аудио чанк в буфер, всего:', this.audioChunks.length);
        }
        
        // Показываем индикатор проигрывания
        this.showAISpeaking(true);
        
        // Обрабатываем буфер батчами (как в оригинальном клиенте)
        this.processAudioBuffer(false);
    }
    
    handleAudioEndSignal() {
        console.log('📢 Получен сигнал audio_end от сервера');
        this.audioEndReceived = true;
        
        // Обрабатываем оставшиеся чанки в буфере
        this.processAudioBuffer(true);
        
        // НЕ вызываем handleAudioEnd() сразу - ждем завершения всех источников
        // Проверяем, есть ли еще активные источники
        this.checkAndCompleteAudioPlayback();
    }
    
    checkAndCompleteAudioPlayback() {
        // Проверяем, можно ли завершить воспроизведение
        // Ждем пока все чанки обработаны И все источники завершены
        if (this.audioEndReceived && 
            this.audioChunks.length === 0 && 
            !this.isProcessingAudio &&
            this.scheduledSources.length === 0 &&
            !this.currentAudioSource) {
            // Все чанки обработаны и все источники завершены - можно завершать
            console.log('✅ Все аудио чанки обработаны и все источники завершены');
            setTimeout(() => {
                this.handleAudioEnd();
            }, 100); // Небольшая задержка для надежности
        } else {
            // Еще есть активные источники или чанки - ждем
            console.log('⏳ Ждем завершения воспроизведения:', {
                audioEndReceived: this.audioEndReceived,
                chunksRemaining: this.audioChunks.length,
                isProcessing: this.isProcessingAudio,
                scheduledSources: this.scheduledSources.length,
                currentSource: !!this.currentAudioSource
            });
        }
    }
    
    processAudioBuffer(isComplete = false) {
        console.log('processAudioBuffer called:', {
            isProcessing: this.isProcessingAudio,
            chunks: this.audioChunks.length,
            isComplete
        });
        
        // Не обрабатываем если уже обрабатываем
        if (this.isProcessingAudio) {
            console.log('Already processing audio, skipping');
            return;
        }
        
        // Если мы отменяем - не обрабатываем
        if (this.isCancelling) {
            console.log('Cancellation in progress - skipping buffer processing');
            return;
        }
        
        // Ждем больше чанков если не завершено (минимум 3 чанка для плавности, но начинаем с 1 если долго ждем)
        const minChunks = isComplete ? 1 : 3;
        if (this.audioChunks.length < minChunks) {
            console.log('Waiting for more chunks, current:', this.audioChunks.length, 'min:', minChunks);
            // Если есть хотя бы 1 чанк и прошло время - начинаем воспроизведение
            if (this.audioChunks.length >= 1 && !isComplete) {
                // Ждем немного (50мс) для накопления, затем начинаем
                setTimeout(() => {
                    if (this.audioChunks.length >= 1 && !this.isProcessingAudio && !this.isCancelling) {
                        console.log('Starting playback with', this.audioChunks.length, 'chunks (timeout)');
                        this.processAudioBuffer(false);
                    }
                }, 50);
            }
            if (isComplete && this.audioEndReceived && this.audioChunks.length === 0) {
                // Нет чанков и получен audio_end - проверяем активные источники
                this.isPlayingAudio = false;
                this.checkAndCompleteAudioPlayback();
            }
            return;
        }
        
        // Обрабатываем все накопленные чанки сразу для лучшей непрерывности
        const chunksToProcess = this.audioChunks.splice(0, this.audioChunks.length);
        console.log('Processing batch of', chunksToProcess.length, 'chunks');
        
        if (chunksToProcess.length > 0) {
            this.isProcessingAudio = true;
            this.isPlayingAudio = true;
            this.playAudioChunks(chunksToProcess).then(() => {
                this.isProcessingAudio = false;
                console.log('Batch processed, remaining chunks:', this.audioChunks.length);
                // Обрабатываем оставшиеся чанки
                if (this.audioChunks.length > 0) {
                    setTimeout(() => this.processAudioBuffer(false), 10);
                } else if (isComplete && this.audioEndReceived) {
                    // Все чанки обработаны, но нужно проверить активные источники
                    this.isPlayingAudio = false;
                    this.checkAndCompleteAudioPlayback();
                }
            }).catch(error => {
                console.error('Error processing audio batch:', error);
                this.isProcessingAudio = false;
                this.isPlayingAudio = false;
            });
        } else if (isComplete && this.audioEndReceived) {
            // Нет чанков и получен audio_end - проверяем активные источники
            this.isPlayingAudio = false;
            this.checkAndCompleteAudioPlayback();
        }
    }
    
    async playAudioChunks(chunks) {
        try {
            console.log('playAudioChunks called with', chunks.length, 'chunks');
            
            // Объединяем несколько чанков в один буфер для плавного воспроизведения
            let totalLength = 0;
            const pcmDataArrays = [];
            
            // Декодируем все чанки
            for (let chunkIdx = 0; chunkIdx < chunks.length; chunkIdx++) {
                const base64Audio = chunks[chunkIdx];
                try {
                    console.log(`🔍 Декодирование чанка ${chunkIdx + 1}/${chunks.length}, длина base64: ${base64Audio?.length || 0}`);
                    
                    if (!base64Audio || base64Audio.length === 0) {
                        console.warn(`⚠️ Пустой чанк ${chunkIdx + 1}`);
                        continue;
                    }
                    
                    const binaryString = atob(base64Audio);
                    console.log(`✅ Base64 декодирован, бинарная длина: ${binaryString.length}`);
                    
                    const audioData = new ArrayBuffer(binaryString.length);
                    const audioView = new Uint8Array(audioData);
                    
                    for (let i = 0; i < binaryString.length; i++) {
                        audioView[i] = binaryString.charCodeAt(i);
                    }
                    
                    // Проверяем, что длина кратна 2 (для int16)
                    if (audioData.byteLength % 2 !== 0) {
                        console.warn(`⚠️ Длина аудио данных не кратна 2 (${audioData.byteLength}), обрезаем`);
                        const trimmedLength = audioData.byteLength - 1;
                        const trimmedBuffer = new ArrayBuffer(trimmedLength);
                        new Uint8Array(trimmedBuffer).set(new Uint8Array(audioData, 0, trimmedLength));
                        const pcmData = new Int16Array(trimmedBuffer);
                        pcmDataArrays.push(pcmData);
                        totalLength += pcmData.length;
                    } else {
                    const pcmData = new Int16Array(audioData);
                        console.log(`✅ Int16Array создан, длина: ${pcmData.length}, первые 5 значений:`, Array.from(pcmData.slice(0, 5)));
                    pcmDataArrays.push(pcmData);
                    totalLength += pcmData.length;
                    }
                } catch (decodeError) {
                    console.error(`❌ Ошибка декодирования чанка ${chunkIdx + 1}:`, decodeError);
                    console.error('Детали ошибки:', {
                        chunkLength: base64Audio?.length,
                        errorMessage: decodeError.message,
                        errorStack: decodeError.stack
                    });
                }
            }
            
            if (totalLength === 0) {
                console.error('❌ Нет валидных аудио данных для воспроизведения');
                console.error('Детали:', {
                    chunksReceived: chunks.length,
                    pcmArraysCreated: pcmDataArrays.length
                });
                return;
            }
            
            console.log('✅ Total PCM samples:', totalLength, `(${(totalLength / this.sampleRate).toFixed(2)} секунд при ${this.sampleRate}Hz)`);
            
            // Объединяем все PCM данные
            const combinedPcmData = new Int16Array(totalLength);
            let offset = 0;
            for (const pcmData of pcmDataArrays) {
                combinedPcmData.set(pcmData, offset);
                offset += pcmData.length;
            }
            
            // Инициализируем AudioContext если нужно (для воспроизведения)
            if (!this.audioContext) {
                console.log('⚠️ AudioContext не создан в playAudioChunks, создаем сейчас');
                this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                    sampleRate: this.sampleRate
                });
            }
            
            // Убеждаемся, что AudioContext активен
            if (this.audioContext.state === 'suspended') {
                console.log('🔊 RESUMING SUSPENDED AUDIO CONTEXT в playAudioChunks');
                await this.audioContext.resume();
            }
            
            console.log('✅ AudioContext готов:', {
                state: this.audioContext.state,
                sampleRate: this.audioContext.sampleRate
            });
            
            // Создаем AudioBuffer
            const frameCount = combinedPcmData.length;
            const audioBuffer = this.audioContext.createBuffer(1, frameCount, this.sampleRate);
            const outputData = audioBuffer.getChannelData(0);
            
            // Конвертируем 16-bit PCM в float32 (как в оригинале: 32768.0)
            for (let i = 0; i < frameCount; i++) {
                outputData[i] = combinedPcmData[i] / 32768.0;
            }
            
            console.log('Created audio buffer:', {
                duration: audioBuffer.duration,
                sampleRate: audioBuffer.sampleRate,
                length: audioBuffer.length
            });
            
            // Диагностика амплитуды (как в оригинале)
            let min = 1.0, max = -1.0, sumSq = 0;
            for (let i = 0; i < outputData.length; i++) {
                const v = outputData[i];
                if (v < min) min = v;
                if (v > max) max = v;
                sumSq += v * v;
            }
            const rms = Math.sqrt(sumSq / outputData.length);
            console.log('PCM amplitude stats:', { min, max, rms });
            
            // Планируем воспроизведение с seamless chaining
            await this.scheduleAudioPlayback(audioBuffer);
            
        } catch (error) {
            console.error('Error processing audio chunks:', error);
        }
    }
    
    async scheduleAudioPlayback(audioBuffer) {
        console.log('scheduleAudioPlayback called');
        
        // Убеждаемся, что AudioContext создан (если еще не создан)
        if (!this.audioContext) {
            console.log('⚠️ AudioContext не создан, создаем сейчас');
            this.audioContext = new (window.AudioContext || window.webkitAudioContext)({
                sampleRate: this.sampleRate
            });
        }
        
        // Убеждаемся, что AudioContext активен
        if (this.audioContext.state === 'suspended') {
            console.log('🔊 RESUMING SUSPENDED AUDIO CONTEXT');
            await this.audioContext.resume();
        }
        
        // Проверяем что буфер имеет осмысленную длительность (минимум 10мс)
        if (audioBuffer.duration < 0.01) {
            console.log('Audio buffer too short, skipping:', audioBuffer.duration);
            return;
        }
        
        const source = this.audioContext.createBufferSource();
        const gainNode = this.audioContext.createGain();
        
        source.buffer = audioBuffer;
        gainNode.gain.value = 1.2; // Усиление для лучшего качества (как в оригинале)
        if (gainNode.gain.value > 2.0) gainNode.gain.value = 2.0; // Ограничение
        
        // Подключаем: source -> gain -> destination
        source.connect(gainNode);
        gainNode.connect(this.audioContext.destination);
        
        console.log('🔊 AUDIO SETUP:', {
            audioContextState: this.audioContext.state,
            sampleRate: this.audioContext.sampleRate,
            bufferDuration: audioBuffer.duration,
            volume: gainNode.gain.value
        });
        
        // Детектирование тишины в буфере (как в оригинале)
        const chData = audioBuffer.getChannelData(0);
        let sum = 0; let peak = 0;
        for (let i = 0; i < chData.length; i++) { 
            const v = Math.abs(chData[i]); 
            sum += v*v; 
            if (v > peak) peak = v; 
        }
        const rms = Math.sqrt(sum / chData.length);
        if (peak < 0.001 || rms < 0.0003) {
            console.warn('⚠️ Audio buffer appears near-silent', { peak, rms, length: chData.length });
        }
        
        // Планируем воспроизведение с seamless chaining
        const currentTime = this.audioContext.currentTime;
        const startTime = Math.max(currentTime, this.nextPlayTime);
        
        console.log('Scheduling audio playback:', {
            currentTime,
            startTime,
            nextPlayTime: this.nextPlayTime,
            duration: audioBuffer.duration
        });
        
        console.log('🔊 STARTING AUDIO PLAYBACK AT:', startTime);
        console.log('🔊 AudioContext state перед start:', this.audioContext.state);
        console.log('🔊 Source buffer duration:', source.buffer?.duration);
        console.log('🔊 Source buffer sampleRate:', source.buffer?.sampleRate);
        
        try {
        source.start(startTime);
            console.log('✅ source.start() вызван успешно');
        } catch (error) {
            console.error('❌ Ошибка при вызове source.start():', error);
            return;
        }
        
        this.currentAudioSource = source;
        
        // Отслеживаем запланированный источник для возможного прерывания
        this.scheduledSources.push({ source, startTime, duration: audioBuffer.duration });
        
        // Обновляем nextPlayTime для seamless playback
        this.nextPlayTime = startTime + audioBuffer.duration;
        
        // Автоматическая очистка при завершении
        source.onended = () => {
            console.log('Audio playback ended naturally');
            if (this.currentAudioSource === source) {
                this.currentAudioSource = null;
            }
            // Удаляем из списка запланированных
            this.scheduledSources = this.scheduledSources.filter(s => s.source !== source);
            
            // Проверяем, можно ли завершить воспроизведение после завершения этого источника
            if (this.audioEndReceived && this.scheduledSources.length === 0 && !this.currentAudioSource) {
                console.log('✅ Последний источник завершен, проверяем завершение воспроизведения');
                this.checkAndCompleteAudioPlayback();
            }
        };
        
        // Обработка ошибок
        source.onerror = (error) => {
            console.error('Audio playback error:', error);
        };
        
        // Проверка что аудио действительно играет
        setTimeout(() => {
            if (this.audioContext.state !== 'running') {
                console.error('❌ Audio context not running after playback start!');
            } else {
                console.log('✅ Audio context is running during playback');
            }
        }, 100);
    }
    
    handleAudioEnd() {
        // Защита от множественных вызовов
        if (!this.aiIsSpeaking && !this.microphoneMuted) {
            console.log('⚠️ handleAudioEnd вызван повторно - игнорируем');
            return;
        }
        
        console.log('✅ ИИ полностью закончил озвучивание');
        console.log('🎤 Включаем микрофон обратно');
        
        // Останавливаем все запланированные источники
        this.stopAllScheduledAudio();
        
        // Очищаем буферы аудио на всякий случай
        if (this.audioChunks.length > 0) {
            console.log(`⚠️ В буфере ещё остались ${this.audioChunks.length} чанков - очищаем`);
            this.audioChunks = [];
        }
        if (this.audioQueue.length > 0) {
            console.log(`⚠️ В очереди ещё остались ${this.audioQueue.length} чанков - очищаем`);
            this.audioQueue = [];
        }
        
        // Сбрасываем nextPlayTime
        this.nextPlayTime = 0;
        
        // Снимаем флаги
        this.aiIsSpeaking = false;
        this.microphoneMuted = false; // Включаем микрофон обратно
        this.isPlayingAudio = false;
        this.isProcessingAudio = false;  // Сбрасываем флаг обработки
        this.audioEndReceived = false; // Сбрасываем флаг
        console.log('🏴 Все флаги сброшены: aiIsSpeaking = FALSE, microphoneMuted = FALSE, audioEndReceived = FALSE');
        
        // Убираем визуальную индикацию
        if (this.micButton) {
            this.micButton.classList.remove('muted-for-ai');
        }
        
        // Автоматически продолжаем слушать если режим активен
        if (this.isRecording && !this.isPaused) {
            if (this.micButton) {
                this.micStatus.textContent = 'Слушаю...';
            }
            this.showNotification('info', 'Слушаю', 'Говорите, когда будете готовы', 2000);
        } else {
            console.warn('⚠️ handleAudioEnd: Запись не активна после завершения ответа ИИ:', {
                isRecording: this.isRecording,
                isPaused: this.isPaused
            });
        }
        
        // Дополнительная диагностика состояния
        console.log('📊 Состояние после handleAudioEnd:', {
            isRecording: this.isRecording,
            isPaused: this.isPaused,
            microphoneMuted: this.microphoneMuted,
            aiIsSpeaking: this.aiIsSpeaking,
            isConnected: this.isConnected,
            wsReadyState: this.ws?.readyState
        });
    }
    
    stopAllScheduledAudio() {
        const now = this.audioContext ? this.audioContext.currentTime : 0;
        console.log('🛑 Stopping all scheduled audio sources. Count:', this.scheduledSources.length, 'currentTime:', now);
        for (const entry of this.scheduledSources) {
            try {
                entry.source.stop();
            } catch (e) { 
                // Уже остановлен
            }
        }
        this.scheduledSources = [];
        this.currentAudioSource = null;
    }
    
    handleStageChanged(data) {
        const stageNumber = data.stage_number || 1;
        const totalStages = data.total_stages || 1;
        const aiRole = data.ai_role || 'Тренер';
        const description = data.ai_role_description || '';
        
        console.log(`🎯 Этап тренировки изменён: #${stageNumber}/${totalStages}, роль ИИ: ${aiRole}`);
        
        const badge = document.getElementById('stage-badge');
        const stageNumEl = document.getElementById('stage-number');
        const totalEl = document.getElementById('stage-total');
        const roleEl = document.getElementById('ai-role-label');
        const roleDescEl = document.getElementById('ai-role-description');
        const aiParticipantRole = document.querySelector('#ai-participant .participant-role');
        
        if (badge) badge.style.display = 'flex';
        if (stageNumEl) stageNumEl.textContent = String(stageNumber);
        if (totalEl) totalEl.textContent = String(totalStages);
        if (roleEl) roleEl.textContent = aiRole;
        if (roleDescEl) roleDescEl.textContent = description;
        if (aiParticipantRole) aiParticipantRole.textContent = aiRole;
        
        // Подсказка пользователю — показываем нотификацию о смене этапа
        if (stageNumber > 1) {
            this.showNotification(
                'info',
                `Этап ${stageNumber}/${totalStages}`,
                `Роль ИИ: ${aiRole}${description ? ' · ' + description : ''}`
            );
        }
        
        // Обновляем прогресс-бар если возможно
        if (this.progressFill && this.progressText && this.progressPercent) {
            const percent = Math.round((stageNumber - 1) / totalStages * 100);
            this.progressFill.style.width = percent + '%';
            this.progressText.textContent = `Этап ${stageNumber} из ${totalStages}`;
            this.progressPercent.textContent = percent + '%';
        }
    }
    
    async handleTrainingCompleted(data) {
        console.log('🏁 Тренировка завершена:', data);
        
        if (this.isTrainingFinished) {
            console.log('ℹ️ Тренировка уже завершена, пропускаем повторную обработку');
            return;
        }
        this.isTrainingFinished = true;
        
        if (this.progressFill && this.progressText && this.progressPercent) {
            this.progressFill.style.width = '100%';
            this.progressText.textContent = 'Тренировка завершена';
            this.progressPercent.textContent = '100%';
        }
        
        this.showNotification(
            'success',
            'Тренировка завершена',
            'AI-валидатор оценивает вашу тренировку...'
        );
        
        // Останавливаем запись
        if (this.isRecording) {
            // Метод называется stopContinuousListening() (не stopRecording — такого нет).
            // try/catch: ошибка остановки микрофона НЕ должна срывать запуск AI-валидатора
            // (баг: при включённом микрофоне завершение падало до saveTrainingResults).
            try { this.stopContinuousListening(); } catch (e) { console.error('Ошибка остановки записи при завершении:', e); }
        }
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
        
        // Запускаем AI-валидатор и показываем результат
        await this.saveTrainingResults();
        
        // Закрываем WebSocket после валидации (вручную, без реконнекта)
        this.manualClose = true;
        this.stopHeartbeat();
        if (this.ws) {
            try {
                if (this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({ type: 'end_session', event_id: '' }));
                }
            } catch (e) {
                console.error('Ошибка отправки end_session:', e);
            }
            try { this.ws.close(1000, 'Training ended by user'); } catch (_) {}
        }
        
        // Останавливаем медиа поток
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
        }
        
        window.dispatchEvent(new Event('trainingEnded'));
    }
    
    handleError(data) {
        console.error('❌ Ошибка от сервера:', data.message);
        this.showNotification('error', 'Ошибка', data.message);
    }
    
    /**
     * Разбирает текущий аудио-граф (worklet → AudioContext → MediaStream).
     *
     * Без этого повторный вызов requestMicrophoneAccess() (кнопка «Попробовать снова»,
     * событие Permissions API, реконнект) создавал ВТОРОЙ AudioContext и второй
     * AudioWorklet поверх живого первого: каждый чанк уходил на сервер дважды,
     * распознавание задваивалось, и микрофон вёл себя «затупленно».
     */
    teardownAudioGraph() {
        if (this.audioWorkletNode) {
            try {
                this.audioWorkletNode.port.onmessage = null;
                this.audioWorkletNode.disconnect();
            } catch (_) {}
            this.audioWorkletNode = null;
        }
        if (this.audioContext) {
            try { this.audioContext.close(); } catch (_) {}
            this.audioContext = null;
        }
        if (this.mediaStream) {
            try { this.mediaStream.getTracks().forEach((t) => t.stop()); } catch (_) {}
            this.mediaStream = null;
        }
        this.isRecording = false;
        this.isListening = false;
    }

    async requestMicrophoneAccess() {
        // Повторный вход реален: кнопка «Попробовать снова», onchange разрешения
        // и autoStartListening при реконнекте могут сработать почти одновременно.
        if (this._micRequestInFlight) {
            console.log('🎤 Запрос микрофона уже выполняется — повторный вызов пропущен');
            return;
        }
        this._micRequestInFlight = true;
        try {
            // Старый граф (если был) разбираем до создания нового.
            this.teardownAudioGraph();
            // Подписка на смену разрешения — ставится один раз, дальше работает
            // и после отзыва доступа, и после повторной выдачи.
            this.watchMicPermission();

            // Проверяем поддержку getUserMedia
            if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
                // Fallback для старых браузеров
                const getUserMedia = navigator.getUserMedia || 
                                   navigator.webkitGetUserMedia || 
                                   navigator.mozGetUserMedia;
                if (!getUserMedia) {
                    throw new Error('getUserMedia не поддерживается в этом браузере');
                }
            }

            // Запрашиваем микрофон с правильными параметрами (как в оригинале)
            // В Safari sampleRate может быть проигнорирован, но это нормально
            const constraints = {
                audio: {
                    sampleRate: this.sampleRate,
                    channelCount: 1,
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                }
            };

            // В Safari некоторые параметры могут не поддерживаться
            const isSafari = /^((?!chrome|android).)*safari/i.test(navigator.userAgent);
            if (isSafari) {
                // Упрощаем constraints для Safari
                constraints.audio = {
                    echoCancellation: true,
                    noiseSuppression: true,
                    autoGainControl: true
                };
                console.log('🍎 Safari обнаружен, используем упрощенные constraints');
            }

            const stream = await navigator.mediaDevices.getUserMedia(constraints);
            console.log('🎤 Доступ к микрофону получен');

            // Доступ есть — снимаем баннер отказа и разблокируем кнопку.
            // Без этого красный баннер «Доступ к микрофону запрещён» висел
            // на экране даже после того, как пользователь разрешил микрофон.
            this.hideMicErrorBanner();

            // Сохраняем stream для последующего использования
            this.mediaStream = stream;
            
            // В Safari AudioContext должен быть создан в ответ на user gesture
            // Поэтому НЕ создаем его здесь, а создадим в startContinuousListening()
            if (!isSafari) {
                // Для других браузеров можно создать сразу
                await this.initializeAudioContext(stream);
            } else {
                console.log('🍎 Safari: AudioContext будет создан при первом клике пользователя');
            }

            this.showNotification('success', 'Готово', 'Микрофон настроен и готов к работе');
            if (this.isConnected) {
                await this.autoStartListening();
            }

        } catch (error) {
            console.error('❌ Ошибка доступа к микрофону:', error);
            this.showMicErrorBanner(error);
            if (this.micButton) {
                this.micButton.disabled = true;
                this.micButton.setAttribute('aria-disabled', 'true');
            }
            if (this.micStatus) this.micStatus.textContent = 'Микрофон недоступен';
        } finally {
            this._micRequestInFlight = false;
        }
    }

    /**
     * Следит за разрешением на микрофон через Permissions API.
     *
     * Chrome НЕ перезапускает getUserMedia после того, как пользователь сменил
     * разрешение через 🔒 в адресной строке — страница обязана попросить доступ
     * заново сама. Раньше этого не происходило: баннер «запрещён» оставался, и
     * микрофон не включался, пока не перезагрузишь вкладку. Здесь мы ловим
     * переход состояния в 'granted' и сразу повторяем запрос.
     */
    watchMicPermission() {
        if (this._micPermissionWatched) return;
        if (!navigator.permissions || !navigator.permissions.query) return;

        navigator.permissions.query({ name: 'microphone' })
            .then((status) => {
                this._micPermissionWatched = true;
                status.onchange = () => {
                    console.log('🎤 Состояние разрешения микрофона изменилось:', status.state);
                    if (status.state === 'granted') {
                        this.hideMicErrorBanner();
                        this.requestMicrophoneAccess();
                    }
                };
            })
            .catch((e) => {
                // Permissions API с name:'microphone' поддержан не везде (напр. Safari) —
                // там остаётся кнопка «Попробовать снова».
                console.warn('⚠️ Permissions API для микрофона недоступен:', e);
            });
    }

    hideMicErrorBanner() {
        const banner = document.getElementById('vt-mic-error-banner');
        if (banner) banner.remove();
        if (this.micButton) {
            this.micButton.disabled = false;
            this.micButton.removeAttribute('aria-disabled');
        }
    }

    showMicErrorBanner(error) {
        const existing = document.getElementById('vt-mic-error-banner');
        if (existing) existing.remove();

        let isDenied = error && (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError');
        const isNotFound = error && error.name === 'NotFoundError';

        // Отказ на уровне документа (заголовок Permissions-Policy или iframe без
        // allow="microphone") тоже прилетает как NotAllowedError, но пользователь
        // починить его не может — советовать «разрешите в 🔒» бессмысленно и
        // сбивает с толку. Различаем эти два случая явно.
        const policyBlocked = typeof document.featurePolicy !== 'undefined'
            && typeof document.featurePolicy.allowsFeature === 'function'
            && !document.featurePolicy.allowsFeature('microphone');
        if (policyBlocked) isDenied = false;

        const ua = navigator.userAgent;
        const isChrome = /chrome/i.test(ua) && !/edg/i.test(ua);
        const isFirefox = /firefox/i.test(ua);
        const isSafari = /^((?!chrome|android).)*safari/i.test(ua);

        let hint = '';
        if (policyBlocked) {
            hint = 'Микрофон заблокирован политикой сайта (заголовок Permissions-Policy), '
                 + 'а не настройками браузера — менять разрешения бесполезно. '
                 + 'Сообщите администратору платформы.';
        } else if (isDenied) {
            if (isChrome) hint = 'В Chrome: нажмите 🔒 слева в адресной строке → «Разрешения сайта» → Микрофон → Разрешить → обновите страницу.';
            else if (isFirefox) hint = 'В Firefox: нажмите 🔒 слева в адресной строке → «Разрешения» → Использовать микрофон → Разрешить.';
            else if (isSafari) hint = 'В Safari: Сафари → Настройки для этого сайта → Микрофон → Разрешить.';
            else hint = 'Откройте настройки браузера → Настройки сайта → Микрофон и разрешите доступ для этого сайта.';
        } else if (isNotFound) {
            hint = 'Подключите микрофон или гарнитуру и обновите страницу.';
        } else {
            hint = 'Убедитесь, что браузер имеет доступ к микрофону, затем обновите страницу.';
        }

        const title = policyBlocked ? 'Микрофон отключён политикой сайта'
                    : isDenied ? 'Доступ к микрофону запрещён'
                    : isNotFound ? 'Микрофон не найден'
                    : 'Не удалось включить микрофон';

        const banner = document.createElement('div');
        banner.id = 'vt-mic-error-banner';
        banner.setAttribute('role', 'alert');
        banner.setAttribute('aria-live', 'assertive');
        banner.style.cssText = [
            'position:sticky;top:0;z-index:500;',
            'background:#fef2f2;border:1.5px solid #fca5a5;border-radius:12px;',
            'padding:16px 20px;margin:0 0 16px;',
            'display:flex;align-items:flex-start;gap:14px;',
            'font-family:Inter,system-ui,sans-serif;',
        ].join('');
        banner.innerHTML = `
          <svg style="flex-shrink:0;margin-top:2px;color:#ef4444" width="22" height="22" viewBox="0 0 24 24"
               fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
               aria-hidden="true">
            <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
            <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
          </svg>
          <div style="flex:1">
            <div style="font-weight:700;font-size:14px;color:#b91c1c;margin-bottom:4px">${title}</div>
            <div style="font-size:13px;color:#7f1d1d;line-height:1.55">${hint}</div>
            ${isDenied ? `
            <button id="vt-mic-retry-btn" style="margin-top:12px;display:inline-flex;align-items:center;gap:6px;background:#ef4444;color:#fff;border:0;padding:9px 16px;border-radius:8px;font-weight:600;font-size:13px;cursor:pointer;">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" aria-hidden="true">
                <polyline points="1 4 1 10 7 10"/><path d="M3.51 15a9 9 0 1 0 .49-4.5"/>
              </svg>Попробовать снова
            </button>` : ''}
          </div>`;

        // Insert at top of chat-messages container or body
        const target = this.chatMessages || document.querySelector('.voice-training-container') || document.body;
        target.prepend(banner);

        if (isDenied) {
            const retryBtn = document.getElementById('vt-mic-retry-btn');
            if (retryBtn) {
                retryBtn.addEventListener('click', () => {
                    this.hideMicErrorBanner();
                    // Клик — это user gesture: если разрешение в состоянии 'prompt',
                    // Chrome покажет обычный запрос доступа, а не тихий отказ.
                    this.requestMicrophoneAccess();
                });
            }
        }
        // role="alert" + aria-live="assertive" озвучивает баннер скринридером
        // без необходимости явно переводить фокус.
    }

    async initializeAudioContext(stream) {
        try {
            // Инициализируем AudioContext с высоким качеством (24kHz для профессионального звука)
            // В Safari используем webkitAudioContext
            const AudioContextClass = window.AudioContext || window.webkitAudioContext;
            if (!AudioContextClass) {
                throw new Error('AudioContext не поддерживается');
            }

            this.audioContext = new AudioContextClass({
                sampleRate: this.sampleRate  // Используем sampleRate из конструктора (24000)
            });
            
            // Диагностика: реальный sampleRate может отличаться от запрошенного
            // Браузер на macOS часто игнорирует запрос 24000 и использует 44100/48000
            const actualSampleRate = this.audioContext.sampleRate;
            if (actualSampleRate !== this.sampleRate) {
                console.warn(`⚠️ AudioContext sampleRate: запрошен ${this.sampleRate}Hz, реальный ${actualSampleRate}Hz. Используем реальный.`);
                this.actualSampleRate = actualSampleRate;
            } else {
                console.log(`✅ AudioContext sampleRate: ${actualSampleRate}Hz (совпадает с запрошенным)`);
                this.actualSampleRate = actualSampleRate;
            }

            // В Safari может потребоваться resume() для AudioContext
            if (this.audioContext.state === 'suspended') {
                await this.audioContext.resume();
            }

            // Используем AudioWorklet для лучшего качества (как в оригинале)
            try {
                await this.audioContext.audioWorklet.addModule('/static/js/audio-processor.js');
                this.setupAudioWorklet(stream);
                console.log('✅ AudioWorklet инициализирован');
            } catch (workletError) {
                console.warn('⚠️ AudioWorklet не поддерживается, используем ScriptProcessor:', workletError);
                // Fallback на ScriptProcessor
                this.setupMediaRecorder(stream);
            }
        } catch (error) {
            console.error('❌ Ошибка инициализации AudioContext:', error);
            throw error;
        }
    }
    
    setupAudioWorklet(stream) {
        // Создаем источник из медиа потока
        const source = this.audioContext.createMediaStreamSource(stream);
        const actualRate = this.actualSampleRate || this.audioContext.sampleRate;
        this.audioWorkletNode = new AudioWorkletNode(this.audioContext, 'audio-processor', {
            processorOptions: { sampleRate: actualRate }
        });
        console.log(`🎤 AudioWorklet создан с sampleRate: ${actualRate}Hz`);
        
        let mutedLogCount = 0;
        let audioChunkCount = 0;
        let droppedChunkCount = 0;
        
        // Обрабатываем аудио данные от AudioWorklet
        this.audioWorkletNode.port.onmessage = (event) => {
            // Инициализационное сообщение от AudioWorklet (не аудио)
            if (event.data && event.data.type === 'init') {
                console.log(`✅ AudioWorklet инициализирован: sampleRate=${event.data.sampleRate}Hz, bufferSize=${event.data.bufferSize}`);
                return;
            }
            audioChunkCount++;
            
            // ВАЖНО: В Safari проверяем состояние записи ПЕРЕД обработкой
            // Если запись не активна, просто игнорируем чанки (не логируем постоянно)
            if (!this.isRecording) {
                droppedChunkCount++;
                // Логируем только первые несколько раз и потом периодически
                if (droppedChunkCount === 1) {
                    console.log('⚠️ AudioWorklet: Получен аудио чанк, но запись не активна. Ожидание активации...');
                } else if (droppedChunkCount === 10) {
                    console.log(`⚠️ AudioWorklet: Получено ${droppedChunkCount} чанков, но запись не активна. Нажмите кнопку микрофона.`);
                } else if (droppedChunkCount % 100 === 0) {
                    console.log(`⚠️ AudioWorklet: Получено ${droppedChunkCount} чанков, но запись не активна.`);
                }
                return; // Не обрабатываем, если запись не активна
            }
            
            // Если запись только что активировалась, логируем
            if (droppedChunkCount > 0 && this.isRecording) {
                console.log(`✅ AudioWorklet: Запись активирована! Обработано ${audioChunkCount} чанков, пропущено ${droppedChunkCount}`);
                droppedChunkCount = 0; // Сбрасываем счетчик
            }
            
            if (this.isPaused) {
                if (!this._pausedWarningShown) {
                    console.warn('⚠️ AudioWorklet: Тренировка на паузе (isPaused = true)');
                    this._pausedWarningShown = true;
                }
                return;
            }
            
            // Сбрасываем флаги предупреждений если запись активна
            if (this.isRecording && !this.isPaused) {
                this._recordingWarningShown = false;
                this._pausedWarningShown = false;
            }
            
            // ВАЖНО: Продолжаем отправлять аудио в Azure даже когда ИИ говорит
            // Это необходимо для обнаружения речи пользователя и прерывания ответа ИИ
            // Azure сам обработает эхо-подавление на сервере
            if (this.microphoneMuted) {
                mutedLogCount++;
                if (mutedLogCount === 1) {
                    console.log('🔇 Микрофон заглушён (ИИ говорит), но продолжаем отправлять аудио для прерывания');
                } else if (mutedLogCount % 100 === 0) {
                    console.log(`🔇 Микрофон заглушён, но продолжаем отправлять (${mutedLogCount} чанков отправлено)`);
                }
                // НЕ возвращаемся - продолжаем отправлять аудио для возможности прерывания
            } else {
                if (mutedLogCount > 0) {
                    console.log(`🎤 Микрофон разблокирован - всего отправлено ${mutedLogCount} чанков во время заглушения`);
                    mutedLogCount = 0;
                }
            }
            
            // AudioWorklet уже конвертировал float32 -> int16, отправляем напрямую
            const int16Buffer = event.data; // ArrayBuffer с Int16Array
            this.sendAudioDataInt16(int16Buffer);
        };
        
        // Подключаем источник к AudioWorklet
        source.connect(this.audioWorkletNode);
        // НЕ подключаем к destination (избегаем эха)
        
        this.mediaStream = stream;
        console.log('✅ AudioWorklet настроен и готов к обработке аудио (ожидание активации записи)');
    }
    
    setupMediaRecorder(stream) {
        try {
            // Используем AudioContext для обработки аудио
            if (!this.audioContext) {
                throw new Error('AudioContext не инициализирован');
            }

            const source = this.audioContext.createMediaStreamSource(stream);
            
            // В Safari ScriptProcessor может быть устаревшим, но это единственный fallback
            // Используем createScriptProcessor с параметрами, совместимыми с Safari
            let processor;
            try {
                // Пытаемся создать ScriptProcessor (может не работать в Safari)
                processor = this.audioContext.createScriptProcessor(4096, 1, 1);
            } catch (spError) {
                console.error('❌ ScriptProcessor не поддерживается:', spError);
                // В Safari может потребоваться другой подход
                throw new Error('Обработка аудио не поддерживается в этом браузере');
            }
            
            source.connect(processor);
            // НЕ подключаем к destination (избегаем эха) - как в оригинале
            // processor.connect(this.audioContext.destination);  // УБРАНО для предотвращения эха
            
            let mutedLogCount = 0; // Для логирования каждые 10 раз
            
        let audioChunkCount = 0;
        let droppedChunkCount = 0;
        
        processor.onaudioprocess = (e) => {
            audioChunkCount++;
            
            // ВАЖНО: В Safari проверяем состояние записи ПЕРЕД обработкой
            if (!this.isRecording) {
                droppedChunkCount++;
                // Логируем только первые несколько раз
                if (droppedChunkCount === 1) {
                    console.log('⚠️ ScriptProcessor: Получен аудио чанк, но запись не активна. Ожидание активации...');
                } else if (droppedChunkCount === 10) {
                    console.log(`⚠️ ScriptProcessor: Получено ${droppedChunkCount} чанков, но запись не активна. Нажмите кнопку микрофона.`);
                }
                return; // Не обрабатываем, если запись не активна
            }
            
            // Если запись только что активировалась, логируем
            if (droppedChunkCount > 0 && this.isRecording) {
                console.log(`✅ ScriptProcessor: Запись активирована! Обработано ${audioChunkCount} чанков, пропущено ${droppedChunkCount}`);
                droppedChunkCount = 0;
            }
            
            if (this.isPaused) return;
            
            // ВАЖНО: Продолжаем отправлять аудио в Azure даже когда ИИ говорит
            // Это необходимо для обнаружения речи пользователя и прерывания ответа ИИ
            // Azure сам обработает эхо-подавление на сервере
            if (this.microphoneMuted) {
                mutedLogCount++;
                if (mutedLogCount === 1) {
                    console.log('🔇 Микрофон заглушён (ИИ говорит), но продолжаем отправлять аудио для прерывания');
                } else if (mutedLogCount % 100 === 0) {
                    console.log(`🔇 Микрофон заглушён, но продолжаем отправлять (${mutedLogCount} чанков отправлено)`);
                }
                // НЕ возвращаемся - продолжаем отправлять аудио для возможности прерывания
            } else {
                if (mutedLogCount > 0) {
                    console.log(`🎤 Микрофон разблокирован - всего отправлено ${mutedLogCount} чанков во время заглушения`);
                    mutedLogCount = 0;
                }
            }
            
            const inputData = e.inputBuffer.getChannelData(0);
            
            // Отправляем аудио данные через WebSocket
            this.sendAudioData(inputData);
        };
            
            this.mediaProcessor = processor;
            this.mediaStream = stream;
            console.log('✅ ScriptProcessor настроен для обработки аудио');
        } catch (error) {
            console.error('❌ Ошибка настройки ScriptProcessor:', error);
            this.showNotification('error', 'Ошибка', 'Не удалось настроить обработку аудио. Попробуйте использовать Chrome или Firefox.');
            throw error;
        }
    }
    
    sendAudioData(audioData) {
        // Метод для ScriptProcessor fallback - конвертируем float32 -> int16
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            console.warn('⚠️ WebSocket не подключен');
            return;
        }
        
        if (!this.isConnected) {
            console.warn('⚠️ WebSocket не подключен (isConnected = false)');
            return; // Не отправляем если не подключены
        }
        
        // Проверяем состояние записи
        if (!this.isRecording) {
            // Не логируем постоянно, только раз
            if (!this._recordingWarningShown) {
                console.warn('⚠️ Запись не активна (isRecording = false), аудио не отправляется');
                this._recordingWarningShown = true;
            }
            return;
        }
        
        if (this.isPaused) {
            // Не логируем постоянно, только раз
            if (!this._pausedWarningShown) {
                console.warn('⚠️ Тренировка на паузе (isPaused = true), аудио не отправляется');
                this._pausedWarningShown = true;
            }
            return;
        }
        
        // Сбрасываем флаги предупреждений если запись активна
        if (this.isRecording && !this.isPaused) {
            this._recordingWarningShown = false;
            this._pausedWarningShown = false;
        }
        
        try {
            // Конвертируем Float32Array в Int16Array (как в оригинале)
            const int16Buffer = new Int16Array(audioData.length);
            for (let i = 0; i < audioData.length; i++) {
                // Clamp to [-1, 1] and convert to 16-bit
                const sample = Math.max(-1, Math.min(1, audioData[i]));
                int16Buffer[i] = Math.round(sample * 32767);
            }
            
            // Конвертируем в base64
            const bytes = new Uint8Array(int16Buffer.buffer);
            const base64 = btoa(String.fromCharCode(...bytes));
            
            // Отправляем в формате input_audio_buffer.append (как в оригинале)
            const audioMsg = {
                type: 'input_audio_buffer.append',
                audio: base64,
                event_id: ''
            };
            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(audioMsg));
            } else {
                this.bufferAudioMessage(audioMsg);
            }

        } catch (error) {
            console.error('❌ Ошибка отправки аудио:', error);
        }
    }
    
    sendAudioDataInt16(int16Buffer) {
        // Отправляем аудио в формате input_audio_buffer.append (как в оригинале)
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            // Не логируем постоянно в Safari, чтобы не засорять консоль
            return;
        }
        
        if (!this.isConnected) {
            return; // Не отправляем если не подключены
        }
        
        // Проверяем состояние записи
        if (!this.isRecording) {
            // Не логируем - это нормально до активации записи
            return;
        }
        
        if (this.isPaused) {
            return;
        }
        
        // Сбрасываем флаги предупреждений если запись активна
        if (this.isRecording && !this.isPaused) {
            this._recordingWarningShown = false;
            this._pausedWarningShown = false;
        }
        
        try {
            // Конвертируем ArrayBuffer в base64
            const bytes = new Uint8Array(int16Buffer);
            
            // В Safari может быть проблема с большими массивами в btoa
            // Используем более безопасный метод конвертации
            let base64;
            if (bytes.length > 65535) {
                // Для больших массивов используем chunking (хотя обычно не нужно)
                const chunks = [];
                for (let i = 0; i < bytes.length; i += 65535) {
                    const chunk = bytes.slice(i, i + 65535);
                    chunks.push(String.fromCharCode(...chunk));
                }
                base64 = btoa(chunks.join(''));
            } else {
                base64 = btoa(String.fromCharCode(...bytes));
            }
            
            // Отправляем в формате input_audio_buffer.append (как в оригинале)
            const message = {
                type: 'input_audio_buffer.append',
                audio: base64,
                event_id: ''
            };

            if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                this.ws.send(JSON.stringify(message));
            } else {
                this.bufferAudioMessage(message);
            }

            // Логируем только первые несколько отправок для диагностики в Safari
            if (!this._audioSendCount) {
                this._audioSendCount = 0;
            }
            this._audioSendCount++;
            if (this._audioSendCount <= 3) {
                console.log(`✅ Аудио отправлено (${this._audioSendCount}): ${base64.length} байт base64, ${bytes.length} байт raw`);
            } else if (this._audioSendCount === 10) {
                console.log(`✅ Отправлено ${this._audioSendCount} аудио чанков. Продолжаю отправку...`);
            }
            
        } catch (error) {
            console.error('❌ Ошибка отправки аудио:', error);
            // В Safari может быть проблема с конвертацией, попробуем альтернативный метод
            if (error.message && error.message.includes('Maximum call stack')) {
                console.warn('⚠️ Проблема с большим массивом, используем альтернативный метод');
                try {
                    // Альтернативный метод для Safari
                    const binaryString = Array.from(new Uint8Array(int16Buffer))
                        .map(byte => String.fromCharCode(byte))
                        .join('');
                    const base64 = btoa(binaryString);
                    const altMsg = {
                        type: 'input_audio_buffer.append',
                        audio: base64,
                        event_id: ''
                    };
                    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
                        this.ws.send(JSON.stringify(altMsg));
                    } else {
                        this.bufferAudioMessage(altMsg);
                    }
                    console.log('✅ Аудио отправлено (альтернативный метод)');
                } catch (altError) {
                    console.error('❌ Альтернативный метод также не сработал:', altError);
                }
            }
        }
    }
    
    async toggleRecording() {
        if (this.isRecording) {
            this.stopContinuousListening();
        } else {
            await this.startContinuousListening();
        }
    }

    async autoStartListening() {
        if (this.isRecording || !this.isConnected || !this.mediaStream) {
            return;
        }
        try {
            await this.startContinuousListening();
        } catch (error) {
            console.warn('⚠️ Автозапуск микрофона не удался, нажмите кнопку микрофона:', error);
        }
    }
    
    async startContinuousListening() {
        if (!this.isConnected) {
            this.showNotification('error', 'Ошибка', 'Нет подключения к серверу');
            return;
        }
        
        // В Safari AudioContext должен быть создан в ответ на user gesture
        // Проверяем, нужно ли инициализировать AudioContext
        if (!this.audioContext && this.mediaStream) {
            try {
                console.log('🎤 Инициализация AudioContext (в ответ на user gesture для Safari)');
                await this.initializeAudioContext(this.mediaStream);
            } catch (error) {
                console.error('❌ Ошибка инициализации AudioContext при старте записи:', error);
                this.showNotification('error', 'Ошибка', 'Не удалось инициализировать аудио. Попробуйте обновить страницу.');
                return;
            }
        }
        
        // Проверяем, что AudioContext активен
        if (this.audioContext && this.audioContext.state === 'suspended') {
            try {
                await this.audioContext.resume();
                console.log('✅ AudioContext возобновлен');
            } catch (error) {
                console.error('❌ Ошибка возобновления AudioContext:', error);
            }
        }
        
        // ВАЖНО: Устанавливаем флаги ДО начала обработки аудио
        // Это критично для Safari, чтобы избежать race condition
        console.log('🎤 Активация записи...');
        this.isRecording = true;
        this.isListening = true;
        
        // Небольшая задержка для Safari, чтобы убедиться, что флаги установлены
        // перед тем как AudioWorklet начнет обрабатывать данные
        await new Promise(resolve => setTimeout(resolve, 50));
        
        console.log('✅ Запись активирована, isRecording =', this.isRecording);
        
        // Устанавливаем флаг активности тренировки для защиты от закрытия
        window.isTrainingActive = true;
        window.hasUnsavedChanges = true;
        console.log('🔒 Защита от закрытия страницы активирована (начало записи)');
        
        // Обновляем UI
        if (this.micButton) {
            this.micButton.classList.add('recording');
            this.micButton.classList.add('ready');
            this.micStatus.textContent = 'Слушаю...';
        }
        
        if (this.recordingContainer) {
            this.recordingContainer.style.display = 'flex';
        }
        
        if (this.pauseBtn) {
            this.pauseBtn.disabled = false;
        }
        
        // Запускаем таймер если это первый раз
        if (!this.stats.startTime) {
            this.stats.startTime = Date.now();
            this.startTimer();
        }
        
        // Проверяем, что аудио действительно обрабатывается
        console.log('🎤 Начало непрерывного прослушивания - готов к приему аудио');
        this.showNotification('success', 'Активировано', 'Я вас слушаю. Говорите когда готовы');
    }
    
    stopContinuousListening() {
        console.log('⏹️ Остановка непрерывного прослушивания');
        this.isRecording = false;
        this.isListening = false;
        
        // Обновляем UI
        if (this.micButton) {
            this.micButton.classList.remove('recording');
            this.micButton.classList.remove('ready');
            this.micStatus.textContent = 'Нажмите для начала';
        }
        
        if (this.recordingContainer) {
            this.recordingContainer.style.display = 'none';
        }
    }
    
    togglePause() {
        if (this.isPaused) {
            this.resumeTraining();
        } else {
            this.pauseTraining();
        }
    }
    
    pauseTraining() {
        console.log('⏸️ Пауза');
        this.isPaused = true;
        
        // Останавливаем запись если активна
        if (this.isRecording) {
            this.stopContinuousListening();
        }
        
        // Обновляем UI
        if (this.pauseBtn) {
            this.pauseBtn.querySelector('.btn-text').textContent = 'Продолжить';
        }
        
        if (this.trainingStatus) {
            this.trainingStatus.innerHTML = '<span class="status-dot" style="background: #fbbf24;"></span> На паузе';
            this.trainingStatus.className = 'training-status';
        }
        
        // Останавливаем таймер
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
    }
    
    resumeTraining() {
        console.log('▶️ Продолжение');
        this.isPaused = false;
        
        // Обновляем UI
        if (this.pauseBtn) {
            this.pauseBtn.querySelector('.btn-text').textContent = 'Пауза';
        }
        
        if (this.trainingStatus) {
            this.trainingStatus.innerHTML = '<span class="status-dot"></span> Активна';
            this.trainingStatus.className = 'training-status status-active';
        }
        
        // Возобновляем таймер
        this.startTimer();
    }
    
    async stopTraining() {
        // Показываем модальное окно подтверждения
        const confirmModal = document.getElementById('confirm-stop-modal');
        if (!confirmModal) {
            console.error('❌ Модальное окно подтверждения не найдено');
            return;
        }
        
        // Показываем модальное окно
        confirmModal.style.display = 'flex';
        
        // Обработчики будут установлены один раз при инициализации
    }
    
    async confirmStopTraining() {
        console.log('🛑 Завершение тренировки');
        this._clearAIResponseTimer();
        this.hideReconnectBanner();
        this._hideInitOverlay();

        // Если тренировка уже завершена автоматически — не дублируем валидацию
        if (this.isTrainingFinished) {
            console.log('ℹ️ Тренировка уже завершена, пропускаем confirmStopTraining');
            return;
        }
        this.isTrainingFinished = true;
        
        // Останавливаем все
        if (this.isRecording) {
            // Метод называется stopContinuousListening() (не stopRecording — такого нет).
            // try/catch: ошибка остановки микрофона НЕ должна срывать запуск AI-валидатора
            // (баг: при включённом микрофоне завершение падало до saveTrainingResults).
            try { this.stopContinuousListening(); } catch (e) { console.error('Ошибка остановки записи при завершении:', e); }
        }
        
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
        
        // Показываем уведомление перед запуском валидатора
        this.showNotification('info', 'Проверка', 'AI-валидатор оценивает вашу тренировку...');
        
        // Сохраняем результаты перед закрытием
        await this.saveTrainingResults();
        
        // Закрываем WebSocket (ручное завершение, без реконнекта)
        this.manualClose = true;
        this.stopHeartbeat();
        if (this.ws) {
            try {
                if (this.ws.readyState === WebSocket.OPEN) {
                    this.ws.send(JSON.stringify({
                        type: 'end_session',
                        event_id: ''
                    }));
                }
            } catch (e) {
                console.error('Ошибка отправки end_session:', e);
            }
            try { this.ws.close(1000, 'Training ended by user'); } catch (_) {}
        }
        
        // Останавливаем медиа поток
        if (this.mediaStream) {
            this.mediaStream.getTracks().forEach(track => track.stop());
        }
        
        // Отправляем событие о завершении тренировки (для снятия защиты от закрытия)
        window.dispatchEvent(new Event('trainingEnded'));
        
        // Показываем результаты
        this.showTrainingResults();
    }
    
    showTrainingResults() {
        const duration = this.stats.startTime ? 
            Math.floor((Date.now() - this.stats.startTime) / 1000) : 0;
        const minutes = Math.floor(duration / 60);
        const seconds = duration % 60;
        
        const checkedItems = document.querySelectorAll('.checklist-checkbox:checked').length;
        const totalItems = document.querySelectorAll('.checklist-checkbox').length;
        const checklistPercent = totalItems > 0 ? Math.round((checkedItems / totalItems) * 100) : 0;
        
        const results = `
            <div style="text-align: center; padding: 20px;">
                <h2 style="margin-bottom: 20px;">📊 Результаты тренировки</h2>
                <div style="display: grid; gap: 16px; max-width: 400px; margin: 0 auto;">
                    <div style="padding: 16px; background: var(--bg); border-radius: 12px;">
                        <div style="font-size: 32px; font-weight: 700; color: var(--primary);">${minutes}:${seconds.toString().padStart(2, '0')}</div>
                        <div style="color: var(--muted); margin-top: 4px;">Длительность</div>
                    </div>
                    <div style="padding: 16px; background: var(--bg); border-radius: 12px;">
                        <div style="font-size: 32px; font-weight: 700; color: var(--primary);">${this.stats.userResponses}</div>
                        <div style="color: var(--muted); margin-top: 4px;">Ваших ответов</div>
                    </div>
                    <div style="padding: 16px; background: var(--bg); border-radius: 12px;">
                        <div style="font-size: 32px; font-weight: 700; color: var(--primary);">${checklistPercent}%</div>
                        <div style="color: var(--muted); margin-top: 4px;">Чеклист выполнен</div>
                    </div>
                    <div style="padding: 16px; background: var(--bg); border-radius: 12px;">
                        <div style="font-size: 32px; font-weight: 700; color: var(--primary);">${this.stats.userScore}</div>
                        <div style="color: var(--muted); margin-top: 4px;">Баллов набрано</div>
                    </div>
                </div>
                <button onclick="window.location.href='/dashboard'" style="margin-top: 24px; padding: 12px 24px; background: var(--primary); color: white; border: none; border-radius: 10px; cursor: pointer; font-weight: 600;">
                    Вернуться в дашборд
                </button>
            </div>
        `;
        
        // Показываем в модальном окне или на странице
        if (this.chatMessages) {
            this.chatMessages.innerHTML = results;
        }
    }
    
    startTimer() {
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
        
        this.timerInterval = setInterval(() => {
            if (!this.stats.startTime || this.isPaused) return;
            
            const duration = Math.floor((Date.now() - this.stats.startTime) / 1000);
            const minutes = Math.floor(duration / 60);
            const seconds = duration % 60;
            
            if (this.trainingTime) {
                this.trainingTime.innerHTML = `
                    <svg class="icon" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zm1-12a1 1 0 10-2 0v4a1 1 0 00.293.707l2.828 2.829a1 1 0 101.415-1.415L11 9.586V6z" clip-rule="evenodd"/>
                    </svg>
                    ${minutes}:${seconds.toString().padStart(2, '0')}
                `;
            }
        }, 1000);
    }
    
    addUserMessage(text) {
        if (!this.chatMessages) return;
        
        const messageGroup = document.createElement('div');
        messageGroup.className = 'message-group user-message-group';
        messageGroup.innerHTML = `
            <div class="message-avatar">
                <div class="avatar-circle user-avatar">
                    <svg class="icon" viewBox="0 0 20 20" fill="currentColor">
                        <path fill-rule="evenodd" d="M10 9a3 3 0 100-6 3 3 0 000 6zm-7 9a7 7 0 1114 0H3z" clip-rule="evenodd"/>
                    </svg>
                </div>
            </div>
            <div class="message-content">
                <div class="message-bubble user-message">
                    <p>${this.escapeHtml(text)}</p>
                </div>
                <span class="message-time">${this.formatTime(new Date())}</span>
            </div>
        `;
        
        this.chatMessages.appendChild(messageGroup);
        this.scrollChatToBottom();
    }
    
    currentAIMessageElement = null;
    
    updateAIMessage(text) {
        if (!this.chatMessages) return;
        
        // Если еще нет текущего сообщения ИИ - создаем
        if (!this.currentAIMessageElement) {
            const messageGroup = document.createElement('div');
            messageGroup.className = 'message-group ai-message-group';
            messageGroup.innerHTML = `
                <div class="message-avatar">
                    <div class="avatar-circle ai-avatar">
                        <svg class="icon" viewBox="0 0 20 20" fill="currentColor">
                            <path d="M2 11a1 1 0 011-1h2a1 1 0 011 1v5a1 1 0 01-1 1H3a1 1 0 01-1-1v-5zM8 7a1 1 0 011-1h2a1 1 0 011 1v9a1 1 0 01-1 1H9a1 1 0 01-1-1V7zM14 4a1 1 0 011-1h2a1 1 0 011 1v12a1 1 0 01-1 1h-2a1 1 0 01-1-1V4z"/>
                        </svg>
                    </div>
                </div>
                <div class="message-content">
                    <div class="message-bubble ai-message">
                        <p class="ai-message-text"></p>
                    </div>
                    <span class="message-time">${this.formatTime(new Date())}</span>
                </div>
            `;
            
            this.chatMessages.appendChild(messageGroup);
            this.currentAIMessageElement = messageGroup.querySelector('.ai-message-text');
        }
        
        // Обновляем текст
        this.currentAIMessageElement.textContent = text;
        this.scrollChatToBottom();
    }
    
    finalizeAIMessage() {
        // Сбрасываем текущее сообщение
        this.currentAIMessageElement = null;
    }
    
    showAITyping(show) {
        if (this.aiTyping) {
            this.aiTyping.style.display = show ? 'flex' : 'none';
        }

        if (this.aiStatusDot) {
            this.aiStatusDot.className = show ? 'participant-status status-online' : 'participant-status';
        }

        if (show) {
            this._startAIResponseTimer();
        } else {
            this._clearAIResponseTimer();
        }
    }

    _startAIResponseTimer() {
        this._clearAIResponseTimer();
        this._aiResponseTimeoutId = setTimeout(() => {
            this._aiResponseTimeoutId = null;
            if (this.aiTyping && this.aiTyping.style.display !== 'none') {
                this.showNotification('warning', 'ИИ медленно отвечает', 'Подождите или проверьте интернет-соединение.', 8000);
            }
        }, 35000);
    }

    _clearAIResponseTimer() {
        if (this._aiResponseTimeoutId) {
            clearTimeout(this._aiResponseTimeoutId);
            this._aiResponseTimeoutId = null;
        }
    }
    
    showAISpeaking(show) {
        if (this.aiSpeakingMain) {
            this.aiSpeakingMain.style.display = show ? 'flex' : 'none';
        }
        
        if (this.aiSpeaking) {
            this.aiSpeaking.style.display = show ? 'block' : 'none';
        }
        
        if (this.aiParticipant && show) {
            this.aiParticipant.style.borderColor = 'var(--primary)';
        } else if (this.aiParticipant) {
            this.aiParticipant.style.borderColor = 'var(--border)';
        }
    }
    
    // Метод playNextAudioChunk больше не используется - заменен на батчинг через processAudioBuffer/playAudioChunks
    // Оставляем для совместимости, но он не должен вызываться
    async playNextAudioChunk() {
        console.warn('⚠️ playNextAudioChunk вызван, но используется батчинг через processAudioBuffer');
        // Если есть чанки в буфере - обрабатываем их
        if (this.audioChunks.length > 0) {
            this.processAudioBuffer(false);
        }
    }
    
    updateConnectionStatus(status, text) {
        if (this.connectionDot) {
            this.connectionDot.className = `status-dot ${status}`;
        }
        
        if (this.connectionStatus) {
            this.connectionStatus.textContent = text;
        }
        
        // Показываем рядом с заголовком чата
        if (this.chatConnectionText) {
            this.chatConnectionText.textContent = text;
        }
        if (this.chatConnectionDot) {
            this.chatConnectionDot.className = `status-dot ${status}`;
        }
    }
    
    updateStats() {
        if (this.userResponsesEl) {
            this.userResponsesEl.textContent = this.stats.userResponses;
        }
        
        if (this.userScoreEl) {
            this.userScoreEl.textContent = this.stats.userScore;
        }
        
        if (this.aiQuestionsEl) {
            this.aiQuestionsEl.textContent = this.stats.aiQuestions;
        }
        
        if (this.aiTipsEl) {
            this.aiTipsEl.textContent = this.stats.aiTips;
        }
    }
    
    updateChecklistProgress() {
        const checkedItems = document.querySelectorAll('.checklist-checkbox:checked').length;
        const totalItems = document.querySelectorAll('.checklist-checkbox').length;
        
        if (totalItems === 0) return;
        
        const percent = Math.round((checkedItems / totalItems) * 100);
        
        if (this.progressFill) {
            this.progressFill.style.width = `${percent}%`;
        }
        
        if (this.progressPercent) {
            this.progressPercent.textContent = `${percent}%`;
        }
        
        if (this.progressText) {
            if (percent === 100) {
                this.progressText.textContent = 'Чеклист выполнен!';
                this.unlockAchievement('Первый шаг');
            } else {
                this.progressText.textContent = `Выполнено: ${checkedItems} из ${totalItems}`;
            }
        }
        
        this.stats.checklistProgress = percent;
    }
    
    unlockAchievement(name) {
        const achievements = document.querySelectorAll('.achievement');
        achievements.forEach(achievement => {
            if (achievement.querySelector('.achievement-name').textContent === name) {
                achievement.classList.remove('locked');
                achievement.classList.add('unlocked');
                
                this.showNotification('success', 'Достижение!', `Получено: ${name}`);
            }
        });
    }
    
    toggleChecklist() {
        if (this.checklistSidebar) {
            this.checklistSidebar.classList.toggle('open');
        }
    }
    
    closeChecklistSidebar() {
        if (this.checklistSidebar) {
            this.checklistSidebar.classList.remove('open');
        }
    }
    
    openSettings() {
        if (this.settingsModal) {
            // Показываем реально активный голос, а не значение по умолчанию из вёрстки.
            const select = document.getElementById('trainer-voice');
            if (select && this.trainerVoice) select.value = this.trainerVoice;
            this.settingsModal.style.display = 'flex';
        }
    }
    
    closeSettings() {
        if (this.settingsModal) {
            this.settingsModal.style.display = 'none';
        }
    }
    
    saveSettings() {
        // Сохранение настроек
        console.log('💾 Сохранение настроек...');

        const audioVolume = document.getElementById('audio-volume')?.value || 80;
        const trainingDifficulty = document.getElementById('training-difficulty')?.value || 'medium';
        const feedbackLevel = document.getElementById('feedback-level')?.value || 'normal';
        const trainerVoice = document.getElementById('trainer-voice')?.value || this.trainerVoice;

        // Сохраняем в localStorage
        localStorage.setItem('voiceTrainingSettings', JSON.stringify({
            audioVolume,
            trainingDifficulty,
            feedbackLevel,
            trainerVoice
        }));

        this.applyVoiceChoice(trainerVoice);

        this.showNotification('success', 'Сохранено', 'Настройки успешно применены');
        this.closeSettings();
    }

    /**
     * Просит сервер переключить голос тренера.
     *
     * На сервер уходит только ключ ('male'/'female'), имя голоса Azure выбирается
     * там по белому списку — принимать имя голоса с клиента нельзя.
     */
    applyVoiceChoice(voiceKey) {
        if (!voiceKey || voiceKey === this.trainerVoice) return;
        this.trainerVoice = voiceKey;
        if (!this.ws || this.ws.readyState !== WebSocket.OPEN) {
            // Не беда: выбор лежит в localStorage и уедет при следующем connected.
            console.log('🗣️ Голос сохранён, применится при подключении:', voiceKey);
            return;
        }
        this.ws.send(JSON.stringify({ type: 'set_voice', voice: voiceKey, event_id: '' }));
        console.log('🗣️ Запрошена смена голоса:', voiceKey);
    }

    /** Сохранённый выбор голоса (или null, если пользователь ещё не выбирал). */
    getSavedVoiceChoice() {
        try {
            const raw = localStorage.getItem('voiceTrainingSettings');
            return raw ? (JSON.parse(raw).trainerVoice || null) : null;
        } catch (_) {
            return null;
        }
    }
    
    exportTranscript() {
        console.log('📥 Экспорт транскрипта...');
        if (this.exportTranscriptBtn) {
            this.exportTranscriptBtn.setAttribute('aria-busy', 'true');
            this.exportTranscriptBtn.disabled = true;
        }

        const messages = this.chatMessages.querySelectorAll('.message-group');
        let transcript = `Транскрипт тренировки\n`;
        transcript += `Дата: ${new Date().toLocaleString('ru-RU')}\n`;
        transcript += `\n${'='.repeat(50)}\n\n`;
        
        messages.forEach(msg => {
            const isUser = msg.classList.contains('user-message-group');
            const text = msg.querySelector('.message-bubble p')?.textContent || '';
            const time = msg.querySelector('.message-time')?.textContent || '';
            
            transcript += `[${time}] ${isUser ? 'Вы' : 'ИИ-тренер'}: ${text}\n\n`;
        });
        
        // Скачиваем как текстовый файл
        const blob = new Blob([transcript], { type: 'text/plain;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `transcript_${Date.now()}.txt`;
        a.click();
        URL.revokeObjectURL(url);

        if (this.exportTranscriptBtn) {
            this.exportTranscriptBtn.removeAttribute('aria-busy');
            this.exportTranscriptBtn.disabled = false;
        }
        this.showNotification('success', 'Экспорт', 'Транскрипт сохранен');
    }
    
    clearChat() {
        if (!confirm('Очистить историю чата?')) {
            return;
        }
        
        if (this.chatMessages) {
            // Удаляем все сообщения кроме приветственного
            const messages = this.chatMessages.querySelectorAll('.message-group');
            messages.forEach((msg, index) => {
                if (index > 0) { // Оставляем первое приветственное сообщение
                    msg.remove();
                }
            });
        }
        
        this.showNotification('info', 'Очищено', 'История чата удалена');
    }
    
    showNotification(type, title, message, duration = 5000) {
        if (!this.notificationsContainer) return;
        
        const notification = document.createElement('div');
        notification.className = `notification ${type}`;
        
        const icons = {
            success: '✅',
            error: '❌',
            info: 'ℹ️',
            warning: '⚠️'
        };
        
        notification.innerHTML = `
            <div class="notification-icon">${icons[type] || 'ℹ️'}</div>
            <div class="notification-content">
                <div class="notification-title">${title}</div>
                <div class="notification-message">${message}</div>
            </div>
        `;
        
        this.notificationsContainer.appendChild(notification);
        
        // Автоматически удаляем через указанное время
        setTimeout(() => {
            notification.style.opacity = '0';
            notification.style.transform = 'translateX(100px)';
            setTimeout(() => notification.remove(), 300);
        }, duration);
    }
    
    scrollChatToBottom() {
        if (this.chatMessages) {
            this.chatMessages.scrollTop = this.chatMessages.scrollHeight;
        }
    }
    
    escapeHtml(text) {
        const div = document.createElement('div');
        div.textContent = text;
        return div.innerHTML;
    }
    
    formatTime(date) {
        return date.toLocaleTimeString('ru-RU', {
            hour: '2-digit',
            minute: '2-digit'
        });
    }
    
    async saveTrainingResults() {
        if (!this.trainingId || this.trainingId === 'new' || !this.sessionId) {
            console.warn('⚠️ saveTrainingResults: trainingId или sessionId не установлен', {
                trainingId: this.trainingId, sessionId: this.sessionId
            });
            // Показываем сообщение что тренировка завершена, даже без валидации
            this._showSimpleCompletionMessage();
            return;
        }
        
        console.log(`💾 Сохранение результатов тренировки: training_id=${this.trainingId}, session_id=${this.sessionId}`);
        
        // Показываем overlay загрузки пока AI-валидатор работает
        const loadingOverlay = this._showValidationLoading();
        
        const messages = this.chatMessages.querySelectorAll('.message-group');
        let transcript = '';
        messages.forEach(msg => {
            const isUser = msg.classList.contains('user-message-group');
            const text = msg.querySelector('.message-bubble p')?.textContent || '';
            const time = msg.querySelector('.message-time')?.textContent || '';
            transcript += `[${time}] ${isUser ? 'Вы' : 'ИИ'}: ${text}\n`;
        });
        
        try {
            // Таймаут 60 сек — GPT-валидатор может думать долго
            const controller = new AbortController();
            const timeoutId = setTimeout(() => controller.abort(), 60000);
            
            const response = await fetch('/voice-training/training/complete', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                signal: controller.signal,
                body: JSON.stringify({
                    training_id: parseInt(this.trainingId),
                    session_id: parseInt(this.sessionId),
                    transcript: transcript,
                    user_responses_count: this.stats.userResponses,
                    ai_questions_count: this.stats.aiQuestions
                })
            });
            
            clearTimeout(timeoutId);
            
            // Убираем loading overlay
            if (loadingOverlay && loadingOverlay.parentNode) {
                loadingOverlay.remove();
            }
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error('❌ Ошибка HTTP:', response.status, errorText);
                throw new Error(`HTTP ${response.status}: ${errorText}`);
            }
            
            const data = await response.json();
            console.log('📊 Ответ валидатора:', data);
            
            if (data.success) {
                console.log('✅ Результаты AI-валидации:', data);
                this.showValidationResult(data);
            } else {
                console.error('❌ Ошибка сохранения:', data);
                // Показываем простое завершение если валидатор не вернул success
                this._showSimpleCompletionMessage();
            }
        } catch (error) {
            // Убираем loading overlay при ошибке
            if (loadingOverlay && loadingOverlay.parentNode) {
                loadingOverlay.remove();
            }
            if (error.name === 'AbortError') {
                console.error('❌ Таймаут: валидатор не ответил за 60 сек');
                this._showSimpleCompletionMessage();
            } else {
                console.error('❌ Ошибка при сохранении результатов:', error);
                this._showSimpleCompletionMessage();
            }
        }
    }

    _showValidationLoading() {
        // Убираем предыдущий loading если есть
        document.querySelector('.validation-loading-overlay')?.remove();
        
        const overlay = document.createElement('div');
        overlay.className = 'validation-loading-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.75); z-index: 10000;
            display: flex; align-items: center; justify-content: center;
            flex-direction: column; gap: 16px;
        `;
        overlay.innerHTML = `
            <div style="width: 56px; height: 56px; border: 4px solid #444; border-top-color: #6c63ff;
                border-radius: 50%; animation: spin 0.9s linear infinite;"></div>
            <div style="color: #fff; font-size: 16px; font-weight: 600;">AI-валидатор оценивает тренировку...</div>
            <div style="color: #aaa; font-size: 13px;">Это может занять до 30 секунд</div>
            <style>@keyframes spin { to { transform: rotate(360deg); } }</style>
        `;
        document.body.appendChild(overlay);
        return overlay;
    }

    _showSimpleCompletionMessage() {
        document.querySelector('.validation-overlay')?.remove();
        const overlay = document.createElement('div');
        overlay.className = 'validation-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7); z-index: 10000;
            display: flex; align-items: center; justify-content: center;
        `;
        overlay.innerHTML = `
            <div style="background: #1e1e2e; border-radius: 16px; padding: 32px; max-width: 400px;
                width: 90%; color: #fff; text-align: center; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
                <div style="font-size: 48px; margin-bottom: 16px;">✅</div>
                <div style="font-size: 20px; font-weight: 700; margin-bottom: 12px;">Тренировка завершена</div>
                <div style="font-size: 14px; color: #aaa; margin-bottom: 24px;">Все этапы пройдены</div>
                <div style="display: flex; flex-direction: column; gap: 10px; align-items: center;">
                    <button onclick="window.location.href='${this.postTrainingUrl}';"
                        style="background: #6c63ff; color: #fff; border: none; border-radius: 8px;
                        padding: 12px 32px; font-size: 15px; cursor: pointer; font-weight: 600;">
                        Продолжить
                    </button>
                    <button onclick="window.location.reload();"
                        style="background: transparent; color: #aaa; border: 1px solid #444; border-radius: 8px;
                        padding: 10px 24px; font-size: 13px; cursor: pointer;">
                        Пройти ещё раз
                    </button>
                </div>
            </div>
        `;
        document.body.appendChild(overlay);
    }

    showValidationResult(data) {
        const score = data.score || 0;
        const passed = data.passed || false;
        const feedback = data.feedback || '';
        const criteria = data.criteria || {};
        
        const overlay = document.createElement('div');
        overlay.className = 'validation-overlay';
        overlay.style.cssText = `
            position: fixed; top: 0; left: 0; width: 100%; height: 100%;
            background: rgba(0,0,0,0.7); z-index: 10000;
            display: flex; align-items: center; justify-content: center;
        `;
        
        const criteriaNames = {
            full_cycle: 'Полный цикл',
            understanding: 'Понимание техники',
            execution_quality: 'Качество исполнения',
            active_participation: 'Активное участие'
        };
        
        let criteriaHtml = '';
        for (const [key, label] of Object.entries(criteriaNames)) {
            const val = criteria[key] || 0;
            const barColor = val >= 18 ? '#4CAF50' : val >= 12 ? '#FFC107' : '#F44336';
            criteriaHtml += `
                <div style="margin-bottom: 8px;">
                    <div style="display: flex; justify-content: space-between; font-size: 13px; margin-bottom: 3px;">
                        <span>${label}</span><span>${val}/25</span>
                    </div>
                    <div style="background: #333; border-radius: 4px; height: 6px;">
                        <div style="background: ${barColor}; width: ${(val/25)*100}%; height: 100%; border-radius: 4px;"></div>
                    </div>
                </div>
            `;
        }
        
        const statusColor = passed ? '#4CAF50' : '#F44336';
        const statusText = passed ? 'ТРЕНИРОВКА ПРОЙДЕНА' : 'ТРЕНИРОВКА НЕ ПРОЙДЕНА';
        const statusIcon = passed ? '✅' : '❌';
        
        overlay.innerHTML = `
            <div style="background: #1e1e2e; border-radius: 16px; padding: 32px; max-width: 480px; width: 90%; color: #fff; box-shadow: 0 20px 60px rgba(0,0,0,0.5);">
                <div style="text-align: center; margin-bottom: 24px;">
                    <div style="font-size: 48px; margin-bottom: 8px;">${statusIcon}</div>
                    <div style="font-size: 20px; font-weight: 700; color: ${statusColor};">${statusText}</div>
                </div>
                
                <div style="text-align: center; margin-bottom: 24px;">
                    <div style="font-size: 56px; font-weight: 800; color: ${statusColor};">${score}</div>
                    <div style="font-size: 14px; color: #888;">из 100 баллов (нужно 70+)</div>
                </div>
                
                <div style="margin-bottom: 20px;">
                    ${criteriaHtml}
                </div>
                
                ${feedback ? `
                <div style="background: #2a2a3e; border-radius: 8px; padding: 14px; margin-bottom: 20px;">
                    <div style="font-size: 12px; color: #888; margin-bottom: 6px;">Обратная связь от AI</div>
                    <div style="font-size: 14px; line-height: 1.5;">${feedback}</div>
                </div>
                ` : ''}
                
                <div style="text-align: center; display: flex; flex-direction: column; gap: 10px; align-items: center;">
                    ${passed ? `
                        <button onclick="window.location.href='${this.postTrainingUrl}';"
                            style="background: ${statusColor}; color: #fff; border: none; border-radius: 8px; padding: 12px 32px; font-size: 15px; cursor: pointer; font-weight: 600;">
                            Отлично! Продолжить
                        </button>
                    ` : `
                        <button onclick="window.location.reload();"
                            style="background: #6c63ff; color: #fff; border: none; border-radius: 8px; padding: 12px 32px; font-size: 15px; cursor: pointer; font-weight: 600;">
                            Попробовать ещё раз
                        </button>
                        <button onclick="window.location.href='${this.postTrainingUrl}';"
                            style="background: transparent; color: #aaa; border: 1px solid #444; border-radius: 8px; padding: 10px 24px; font-size: 13px; cursor: pointer;">
                            Выйти
                        </button>
                    `}
                </div>
            </div>
        `;
        
        document.body.appendChild(overlay);
    }
    
    getCookie(name) {
        /**
         * Получает значение cookie по имени
         */
        const value = `; ${document.cookie}`;
        const parts = value.split(`; ${name}=`);
        if (parts.length === 2) {
            return parts.pop().split(';').shift();
        }
        return null;
    }
    
    async loadHistory() {
        /**
         * Загружает историю диалога из БД
         */
        try {
            const userId = window.currentUserId;
            let trainingId = this.trainingId;
            
            // Проверяем и конвертируем trainingId
            if (trainingId === 'new' || trainingId === null || trainingId === undefined) {
                console.log('⚠️ TrainingId не указан или равен "new", пропускаем загрузку истории');
                return;
            }
            
            // Конвертируем в число если строка
            trainingId = parseInt(trainingId);
            if (isNaN(trainingId)) {
                console.warn('⚠️ TrainingId не является числом:', this.trainingId);
                return;
            }
            
            if (!userId) {
                console.warn('⚠️ UserId не указан, пропускаем загрузку истории');
                return;
            }
            
            console.log(`📥 Загрузка истории для trainingId=${trainingId}, userId=${userId}`);
            
            const response = await fetch(
                `/voice-training/training/${trainingId}/history?user_id=${userId}`,
                {
                    method: 'GET',
                    headers: {
                        'Content-Type': 'application/json'
                    }
                }
            );
            
            if (!response.ok) {
                const errorText = await response.text();
                console.warn(`⚠️ Не удалось загрузить историю: ${response.status}`, errorText);
                return;
            }
            
            const data = await response.json();
            console.log('📦 Данные истории:', data);
            
            if (data.messages && Array.isArray(data.messages) && data.messages.length > 0) {
                console.log(`📚 Загружено ${data.messages.length} сообщений из истории`);
                
                // Очищаем существующие сообщения (кроме приветственного)
                const existingMessages = this.chatMessages.querySelectorAll('.message-group:not(.ai-message-group:first-child)');
                existingMessages.forEach(msg => msg.remove());
                
                // Отображаем сообщения в чате
                data.messages.forEach((msg, index) => {
                    if (!msg.text || !msg.role) {
                        console.warn(`⚠️ Пропущено некорректное сообщение ${index + 1}:`, msg);
                        return;
                    }
                    
                    try {
                        if (msg.role === 'user') {
                            this.addUserMessage(msg.text);
                        } else if (msg.role === 'assistant') {
                            this.currentAIMessageElement = null; // Сбрасываем чтобы создать новое сообщение
                            this.updateAIMessage(msg.text);
                        }
                    } catch (err) {
                        console.error(`❌ Ошибка отображения сообщения ${index + 1}:`, err);
                    }
                });
                
                // Прокручиваем вниз после загрузки
                setTimeout(() => {
                    this.scrollChatToBottom();
                }, 100);
                
                // Обновляем статистику
                this.stats.userResponses = data.messages.filter(m => m.role === 'user').length;
                this.stats.aiQuestions = data.messages.filter(m => m.role === 'assistant').length;
                this.updateStats();
            } else {
                console.log('📭 История пуста или не содержит сообщений');
            }
            
        } catch (error) {
            console.error('❌ Ошибка загрузки истории:', error);
            console.error('Stack:', error.stack);
        }
    }
}

// Экспортируем класс в глобальную область
window.VoiceTraining = VoiceTraining;

