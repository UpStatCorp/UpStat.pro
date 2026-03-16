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
        
        // Инициализация
        this.init();
    }
    
    async init() {
        console.log('🎤 Инициализация голосовой тренировки...');
        
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
    
    async connectWebSocket() {
        try {
            // Закрываем старое соединение если оно есть (при переподключении)
            if (this.ws) {
                const oldState = this.ws.readyState;
                console.log('🔌 Закрываем старое WebSocket соединение перед переподключением', {
                    state: oldState,
                    stateName: ['CONNECTING', 'OPEN', 'CLOSING', 'CLOSED'][oldState]
                });
                // Убираем все обработчики, чтобы не было автопереподключения
                this.ws.onclose = null;
                this.ws.onopen = null;
                this.ws.onmessage = null;
                this.ws.onerror = null;
                // Закрываем соединение с кодом 1000 (нормальное закрытие)
                if (oldState === WebSocket.OPEN || oldState === WebSocket.CONNECTING) {
                    this.ws.close(1000, "Client reconnecting");
                }
                this.ws = null;
                // Даем больше времени на закрытие
                await new Promise(resolve => setTimeout(resolve, 1000));
            }
            
            const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:';
            
            // Используем новый масштабируемый endpoint с сохранением в БД
            const userId = window.currentUserId;
            const urlParams = new URLSearchParams(window.location.search);
            const trainingId = urlParams.get('training_id') || this.trainingId || '1';
            
            let wsUrl;
            if (userId) {
                wsUrl = `${protocol}//${window.location.host}/voice-training/ws?user_id=${userId}&training_id=${trainingId}`;
                console.log('🔌 Подключение к WebSocket (масштабируемая версия с сохранением в БД)', {userId, trainingId});
            } else {
                // Fallback на старый endpoint
                wsUrl = `${protocol}//${window.location.host}/voice-assistant/ws`;
                console.log('⚠️ user_id не найден, используем старый endpoint');
            }
            
            this.ws = new WebSocket(wsUrl);
            
            this.ws.onopen = () => {
                console.log('✅ WebSocket подключен');
                this.isConnected = true;
                this.updateConnectionStatus('connected', 'Подключено к серверу');
                // НЕ показываем уведомление здесь - дождемся сообщения 'connected' с session_id
                // this.showNotification('success', 'Подключено', 'Соединение с сервером установлено');
                
                // Разблокируем кнопку микрофона
                if (this.micButton) {
                    this.micButton.disabled = false;
                    this.micStatus.textContent = 'Ожидание сессии...';
                }
            };
            
            this.ws.onmessage = (event) => {
                this.handleWebSocketMessage(event);
            };
            
            this.ws.onerror = (error) => {
                console.error('❌ Ошибка WebSocket:', error);
                this.updateConnectionStatus('error', 'Ошибка подключения');
                this.showNotification('error', 'Ошибка', 'Проблема с подключением к серверу');
            };
            
            this.ws.onclose = (event) => {
                console.log('🔌 WebSocket отключен', {
                    code: event.code,
                    reason: event.reason,
                    wasClean: event.wasClean
                });
                this.isConnected = false;
                this.updateConnectionStatus('connecting', 'Отключено');
                
                // Попытка переподключения через 3 секунды (только если не было нормального закрытия)
                if (event.code !== 1000 && event.code !== 1001) {
                    setTimeout(() => {
                        if (!this.isConnected) {
                            console.log('🔄 Попытка переподключения...');
                            this.connectWebSocket();
                        }
                    }, 3000);
                } else {
                    console.log('✅ WebSocket закрыт нормально, переподключение не требуется');
                }
            };
            
        } catch (error) {
            console.error('❌ Ошибка при подключении WebSocket:', error);
            this.showNotification('error', 'Ошибка', 'Не удалось подключиться к серверу');
        }
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
            
            // Обрабатываем события точно так же, как в оригинале
            switch (eventType) {
                case 'connected':
                    console.log('✅ Сессия создана:', data.session_id);
                    this.isConnected = true;
                    this.showNotification('success', 'Подключено', data.message || 'Сессия создана');
                    break;
                    
                case 'session.created':
                    console.log('✅ Сессия Azure создана');
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
                            this.micStatus.textContent = '🔇 Микрофон отключён (ИИ говорит)';
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
                                    this.micStatus.textContent = '🔇 Микрофон отключён (ИИ говорит)';
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
            this.micStatus.textContent = '🔇 Микрофон отключён (ИИ говорит)';
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
                this.micStatus.textContent = '🎤 Слушаю...';
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
                this.micStatus.textContent = '🔇 Микрофон отключён (ИИ говорит)';
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
                this.micStatus.textContent = '🎤 Слушаю...';
            }
            this.showNotification('info', '👂 Слушаю', 'Говорите, когда будете готовы', 2000);
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
    
    handleError(data) {
        console.error('❌ Ошибка от сервера:', data.message);
        this.showNotification('error', 'Ошибка', data.message);
    }
    
    async requestMicrophoneAccess() {
        try {
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

        } catch (error) {
            console.error('❌ Ошибка доступа к микрофону:', error);
            let errorMessage = 'Не удалось получить доступ к микрофону';
            if (error.name === 'NotAllowedError') {
                errorMessage = 'Доступ к микрофону запрещен. Разрешите доступ в настройках браузера.';
            } else if (error.name === 'NotFoundError') {
                errorMessage = 'Микрофон не найден. Убедитесь, что микрофон подключен.';
            }
            this.showNotification('error', 'Ошибка', errorMessage);

            if (this.micButton) {
                this.micButton.disabled = true;
                this.micStatus.textContent = 'Микрофон недоступен';
            }
        }
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
        this.audioWorkletNode = new AudioWorkletNode(this.audioContext, 'audio-processor');
        
        let mutedLogCount = 0;
        let audioChunkCount = 0;
        let droppedChunkCount = 0;
        
        // Обрабатываем аудио данные от AudioWorklet
        this.audioWorkletNode.port.onmessage = (event) => {
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
            this.ws.send(JSON.stringify({
                type: 'input_audio_buffer.append',
                audio: base64,
                event_id: ''
            }));
            
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
            
            this.ws.send(JSON.stringify(message));
            
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
                    this.ws.send(JSON.stringify({
                        type: 'input_audio_buffer.append',
                        audio: base64,
                        event_id: ''
                    }));
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
        this.showNotification('success', '🎤 Активировано', 'Я вас слушаю. Говорите когда готовы');
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
        
        // Останавливаем все
        if (this.isRecording) {
            this.stopRecording();
        }
        
        if (this.timerInterval) {
            clearInterval(this.timerInterval);
        }
        
        // Сохраняем результаты перед закрытием
        await this.saveTrainingResults();
        
        // Закрываем WebSocket
        if (this.ws) {
            // Отправляем сообщение о завершении сессии
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
            this.ws.close();
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
                this.progressText.textContent = '🎉 Чеклист выполнен!';
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
                
                this.showNotification('success', '🏆 Достижение!', `Получено: ${name}`);
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
        
        // Сохраняем в localStorage
        localStorage.setItem('voiceTrainingSettings', JSON.stringify({
            audioVolume,
            trainingDifficulty,
            feedbackLevel
        }));
        
        this.showNotification('success', 'Сохранено', 'Настройки успешно применены');
        this.closeSettings();
    }
    
    exportTranscript() {
        console.log('📥 Экспорт транскрипта...');
        
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
        // Сохраняем результаты тренировки если это тренировка из плана
        if (!this.trainingId || this.trainingId === 'new' || !this.sessionId) {
            console.log('ℹ️ Обычная тренировка (не из плана), результаты не сохраняются');
            return;
        }
        
        console.log(`💾 Сохранение результатов тренировки: training_id=${this.trainingId}, session_id=${this.sessionId}`);
        
        // Собираем транскрипт
        const messages = this.chatMessages.querySelectorAll('.message-group');
        let transcript = '';
        messages.forEach(msg => {
            const isUser = msg.classList.contains('user-message-group');
            const text = msg.querySelector('.message-bubble p')?.textContent || '';
            const time = msg.querySelector('.message-time')?.textContent || '';
            transcript += `[${time}] ${isUser ? 'Вы' : 'ИИ'}: ${text}\n`;
        });
        
        // Рассчитываем итоговый score на основе статистики
        const score = Math.min(100, Math.floor(
            (this.stats.userResponses * 10) + 
            (this.stats.checklistProgress * 0.5)
        ));
        
        try {
            console.log('📤 Отправка запроса на завершение тренировки:', {
                training_id: this.trainingId,
                session_id: this.sessionId,
                score: score,
                user_responses: this.stats.userResponses,
                ai_questions: this.stats.aiQuestions
            });
            
            const response = await fetch('/voice-training/training/complete', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json'
                },
                body: JSON.stringify({
                    training_id: parseInt(this.trainingId),
                    session_id: parseInt(this.sessionId),
                    transcript: transcript,
                    score: score,
                    user_responses_count: this.stats.userResponses,
                    ai_questions_count: this.stats.aiQuestions
                })
            });
            
            console.log('📥 Ответ сервера:', response.status, response.statusText);
            
            if (!response.ok) {
                const errorText = await response.text();
                console.error('❌ Ошибка HTTP:', response.status, errorText);
                throw new Error(`HTTP ${response.status}: ${errorText}`);
            }
            
            const data = await response.json();
            
            if (data.success) {
                console.log('✅ Результаты тренировки сохранены:', data);
                this.showNotification('success', 'Сохранено', `Ваш результат: ${score}/100`);
                
                // Через 3 секунды перенаправляем обратно к плану тренировок
                setTimeout(() => {
                    // Извлекаем report_msg_id из URL или перенаправляем на /calls
                    const urlParams = new URLSearchParams(window.location.search);
                    window.location.href = '/calls';
                }, 3000);
            } else {
                console.error('❌ Ошибка сохранения:', data);
                this.showNotification('error', 'Ошибка', 'Не удалось сохранить результаты');
            }
        } catch (error) {
            console.error('❌ Ошибка при сохранении результатов:', error);
            this.showNotification('error', 'Ошибка', 'Не удалось сохранить результаты');
        }
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

