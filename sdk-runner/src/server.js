const express = require('express');
const WebSocket = require('ws');
const cors = require('cors');
const ZoomSDKRunner = require('./zoom-sdk-runner');
require('dotenv').config();

const app = express();
const port = process.env.PORT || 3001;

// Middleware
app.use(cors());
app.use(express.json());

// Хранилище активных сессий
const activeSessions = new Map();

// WebSocket сервер для связи с ai_agent_service
const wss = new WebSocket.Server({ port: 3002 });

class SDKRunnerServer {
    constructor() {
        this.setupRoutes();
        this.setupWebSocket();
    }

    setupRoutes() {
        // Health check
        app.get('/health', (req, res) => {
            res.json({ 
                status: 'healthy', 
                service: 'sdk-runner',
                active_sessions: activeSessions.size
            });
        });

        // Подключение к Zoom встрече
        app.post('/join', async (req, res) => {
            try {
                const { meetingNumber, signature, userName, sdkKey } = req.body;
                
                console.log('Received join request:', { meetingNumber, userName, sdkKey: sdkKey ? 'present' : 'missing', signature: signature ? 'present' : 'missing' });
                
                if (!meetingNumber || !signature || !sdkKey) {
                    console.log('Missing required parameters:', { meetingNumber: !!meetingNumber, signature: !!signature, sdkKey: !!sdkKey });
                    return res.status(400).json({ 
                        error: 'Missing required parameters: meetingNumber, signature, sdkKey' 
                    });
                }

                console.log(`Starting Zoom SDK session for meeting ${meetingNumber}`);
                
                // Создаем новую сессию SDK Runner
                console.log('Creating ZoomSDKRunner instance...');
                const runner = new ZoomSDKRunner({
                    meetingNumber,
                    signature,
                    userName: userName || 'AI Assistant',
                    sdkKey,
                    onAudioData: (audioData) => {
                        // Отправляем аудио данные в ai_agent_service
                        this.sendAudioToAI(meetingNumber, audioData);
                    },
                    onStatusChange: (status) => {
                        console.log(`Meeting ${meetingNumber} status: ${status}`);
                    }
                });

                // Запускаем подключение
                console.log('Calling runner.connect()...');
                await runner.connect();
                console.log('runner.connect() completed successfully');
                
                // Сохраняем сессию
                activeSessions.set(meetingNumber, runner);
                console.log(`Session saved for meeting ${meetingNumber}`);

                res.json({
                    message: 'Successfully joined Zoom meeting',
                    meetingNumber,
                    status: 'connected'
                });

            } catch (error) {
                console.error('Error joining meeting:', error);
                console.error('Error stack:', error.stack);
                res.status(500).json({ 
                    error: 'Failed to join meeting', 
                    details: error.message 
                });
            }
        });

        // Отключение от встречи
        app.post('/leave', async (req, res) => {
            try {
                const { meetingNumber } = req.body;
                
                if (!meetingNumber) {
                    return res.status(400).json({ error: 'meetingNumber is required' });
                }

                const runner = activeSessions.get(meetingNumber);
                if (!runner) {
                    return res.status(404).json({ error: 'Session not found' });
                }

                await runner.disconnect();
                activeSessions.delete(meetingNumber);

                res.json({
                    message: 'Successfully left Zoom meeting',
                    meetingNumber
                });

            } catch (error) {
                console.error('Error leaving meeting:', error);
                res.status(500).json({ 
                    error: 'Failed to leave meeting', 
                    details: error.message 
                });
            }
        });

        // Получение статуса сессии
        app.get('/status/:meetingNumber', (req, res) => {
            const { meetingNumber } = req.params;
            const runner = activeSessions.get(meetingNumber);
            
            if (!runner) {
                return res.status(404).json({ 
                    error: 'Session not found',
                    meetingNumber,
                    status: 'disconnected'
                });
            }

            res.json({
                meetingNumber,
                status: runner.getStatus(),
                connected: runner.isConnected(),
                participants: runner.getParticipants()
            });
        });

        // Остановка TTS (для barge-in)
        app.post('/stop-tts', (req, res) => {
            const { meetingNumber } = req.body;
            
            if (!meetingNumber) {
                return res.status(400).json({ error: 'meetingNumber is required' });
            }

            const runner = activeSessions.get(meetingNumber);
            if (!runner) {
                return res.status(404).json({ error: 'Session not found' });
            }

            runner.stopTTS();
            res.json({ message: 'TTS stopped', meetingNumber });
        });
    }

    setupWebSocket() {
        wss.on('connection', (ws) => {
            console.log('WebSocket connection established with ai_agent_service');
            
            ws.on('message', (data) => {
                try {
                    const message = JSON.parse(data);
                    this.handleAIMessage(message, ws);
                } catch (error) {
                    console.error('Error parsing WebSocket message:', error);
                }
            });

            ws.on('close', () => {
                console.log('WebSocket connection closed');
            });

            ws.on('error', (error) => {
                console.error('WebSocket error:', error);
            });
        });
    }

    handleAIMessage(message, ws) {
        const { type, meetingNumber, data } = message;
        
        const runner = activeSessions.get(meetingNumber);
        if (!runner) {
            console.error(`No active session for meeting ${meetingNumber}`);
            return;
        }

        switch (type) {
            case 'tts_audio':
                // Воспроизводим TTS аудио
                runner.playTTS(data.audioBuffer);
                break;
                
            case 'stop_tts':
                // Останавливаем воспроизведение TTS
                runner.stopTTS();
                break;
                
            case 'agent_status':
                // Обновляем статус агента
                runner.updateStatus(data.status);
                break;
                
            default:
                console.warn(`Unknown message type: ${type}`);
        }
    }

    sendAudioToAI(meetingNumber, audioData) {
        // Отправляем аудио данные в ai_agent_service через WebSocket
        const message = {
            type: 'audio_chunk',
            meetingNumber,
            data: {
                audioBuffer: audioData,
                timestamp: Date.now()
            }
        };

        // Отправляем всем подключенным ai_agent_service клиентам
        wss.clients.forEach((client) => {
            if (client.readyState === WebSocket.OPEN) {
                client.send(JSON.stringify(message));
            }
        });
    }

    start() {
        app.listen(port, '0.0.0.0', () => {
            console.log(`🚀 SDK Runner server started on port ${port}`);
            console.log(`📡 WebSocket server listening on port 3002`);
        });
    }
}

// Запускаем сервер
const server = new SDKRunnerServer();
server.start();

// Graceful shutdown
process.on('SIGTERM', async () => {
    console.log('Shutting down SDK Runner...');
    
    // Отключаем все активные сессии
    for (const [meetingNumber, runner] of activeSessions) {
        try {
            await runner.disconnect();
            console.log(`Disconnected from meeting ${meetingNumber}`);
        } catch (error) {
            console.error(`Error disconnecting from meeting ${meetingNumber}:`, error);
        }
    }
    
    process.exit(0);
});
