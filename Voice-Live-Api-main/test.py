#Speech example to test the Azure Voice Live API
# 
# BEFORE RUNNING THIS SCRIPT:
# 1. Create a .env file with your Azure Voice Live API configuration
# 2. Make sure you're logged in to Azure CLI with 'az login'
# 3. Install dependencies with: pip install -r requirements.txt
# 4. See env_template.txt for the required environment variables
#
import os
import uuid
import json
import time
import base64
import logging
import threading
import numpy as np
import sounddevice as sd
import queue
import signal
import sys

from collections import deque
from dotenv import load_dotenv
from azure.core.credentials import TokenCredential
from azure.identity import DefaultAzureCredential
from typing import Dict, Union, Literal, Set
from typing_extensions import Iterator, TypedDict, Required
import websocket
from websocket import WebSocketApp
from datetime import datetime

# Global variables for thread coordination
stop_event = threading.Event()
connection_queue = queue.Queue()

# This is the main function to run the Voice Live API client.
def main() -> None: 
    # Set environment variables or edit the corresponding values here.
    endpoint = "xxxxxxx"
    model = "gpt-4o"
    api_version = "2025-05-01-preview"
    api_key = "xxxxxx"

    # Check if required environment variables are set
    if not endpoint:
        print("Error: AZURE_VOICE_LIVE_ENDPOINT environment variable is not set.")
        print("Please create a .env file with your Azure Voice Live API configuration:")
        print("AZURE_VOICE_LIVE_ENDPOINT=https://your-resource.voice.azure.com/")
        print("AZURE_VOICE_LIVE_MODEL=your-model-name")
        print("AZURE_VOICE_LIVE_API_KEY=your-api-key-here")
        return

    if not model:
        print("Error: AZURE_VOICE_LIVE_MODEL environment variable is not set.")
        print("Please create a .env file with your Azure Voice Live API configuration.")
        return

    # For the recommended keyless authentication, get and
    # use the Microsoft Entra token instead of api_key:
    try:
        credential = DefaultAzureCredential()
        scopes = "https://ai.azure.com/.default"
        token = credential.get_token(scopes)
        print(f"Successfully obtained Azure token for endpoint: {endpoint}")
    except Exception as e:
        print(f"Error obtaining Azure token: {e}")
        print("Make sure you are logged in with 'az login' or have proper Azure credentials configured.")
        return

    try:
        client = AzureVoiceLive(
            azure_endpoint = endpoint,
            api_version = api_version,
            token = token.token,
            # api_key = api_key,
        )

        print(f"🔗 Connecting to Azure Voice Live API with model: {model}")
        connection = client.connect(model = model)
        print("✅ Successfully connected to Azure Voice Live API!")

        session_update = {
            "type": "session.update",
            "session": {
                "instructions": "You are a helpful AI assistant. Respond quickly and concisely in natural, engaging language. Keep responses brief and conversational.",
                
                # === TURN DETECTION SETTINGS ===
                "turn_detection": {
                    "type": "azure_semantic_vad",
                    "threshold": 0.2,  # Lower threshold for faster detection
                    "prefix_padding_ms": 100,  # Reduced from 200ms
                    "silence_duration_ms": 100,  # Reduced from 200ms
                    "remove_filler_words": True,  # Enable to reduce processing time
                    "end_of_utterance_detection": {
                        "model": "semantic_detection_v1",
                        "threshold": 0.005,  # Lower threshold for faster detection
                        "timeout": 1,  # Reduced from 2 seconds
                    },
                },
                
                # === INPUT AUDIO SETTINGS ===
                # Explicit sampling rate (default is 24000, but good to be explicit)
                # "input_audio_sampling_rate": 24000,
                
                # Noise reduction and echo cancellation
                "input_audio_noise_reduction": {
                    "type": "azure_deep_noise_suppression"
                },
                "input_audio_echo_cancellation": {
                    "type": "server_echo_cancellation"
                },
                
                # Custom phrase list for better recognition of specific terms
                # "input_audio_transcription": {
                #     "model": "azure-speech",
                #     "phrase_list": ["Azure", "OpenAI", "Voice Live API", "your-custom-terms"]
                # },
                
                # === OUTPUT MODALITIES ===
                # Explicitly specify supported communication modes
                # "modalities": ["text", "audio"],
                
                # === VOICE SETTINGS ===
                "voice": {
                    "name": "en-US-Ava:DragonHDLatestNeural",
                    "type": "azure-standard",
                    "temperature": 0.7,  # Slightly lower for faster responses (0.0-1.0)
                    "rate": "1.5",  # Speaking speed: 0.5 (slow) to 1.5 (fast)
                    # "custom_lexicon_url": "https://your-storage.com/lexicon.xml"  # Custom pronunciation
                },
                
                # === ADVANCED OUTPUT FEATURES ===
                # Word-level timestamps for precise audio synchronization
                # "output_audio_timestamp_types": ["word"],
                
                # Viseme data for facial animation synchronization
                # "animation": {
                #     "outputs": ["viseme_id"]
                # },
                
                # === AVATAR SETTINGS (Text-to-Speech Avatar) ===
                # Uncomment to enable photorealistic avatar with synchronized speech
                # "avatar": {
                #     "character": "lisa",  # Available: lisa, anna, etc.
                #     "style": "casual-sitting",  # casual-sitting, graceful-sitting, etc.
                #     "customized": False,  # Set to True for custom avatars
                #     "ice_servers": [  # Optional: specify your own ICE servers
                #         {
                #             "urls": ["stun:stun.l.google.com:19302"],
                #             "username": "",
                #             "credential": ""
                #         }
                #     ],
                #     "video": {
                #         "bitrate": 2000000,  # Video bitrate
                #         "codec": "h264",
                #         "crop": {
                #             "top_left": [560, 0],
                #             "bottom_right": [1360, 1080]
                #         },
                #         "resolution": {
                #             "width": 1080,
                #             "height": 1920
                #         },
                #         "background": {
                #             "color": "#00FF00FF"  # Green screen background
                #             # "image_url": "https://example.com/background.jpg"
                #         }
                #     }
                # }
            },
            "event_id": ""
        }
        connection.send(json.dumps(session_update))
        print("📝 Session configuration sent")

        # Create and start threads
        send_thread = threading.Thread(target=listen_and_send_audio, args=(connection,))
        receive_thread = threading.Thread(target=receive_audio_and_playback, args=(connection,))
        keyboard_thread = threading.Thread(target=read_keyboard_and_quit)

        print("🚀 Starting the chat...")

        send_thread.start()
        receive_thread.start()
        keyboard_thread.start()

        # Wait for any thread to complete (usually the keyboard thread when user quits)
        keyboard_thread.join()

        # Signal other threads to stop
        stop_event.set()

        # Wait for other threads to finish
        send_thread.join(timeout=2)
        receive_thread.join(timeout=2)

        connection.close()
        print("✅ Chat done.")
        
    except Exception as e:
        print(f"Error connecting to Azure Voice Live API: {e}")
        print("Please check your endpoint, model, and authentication credentials.")
        return

# --- End of Main Function ---

logger = logging.getLogger(__name__)
AUDIO_SAMPLE_RATE = 24000

class VoiceLiveConnection:
    def __init__(self, url: str, headers: dict) -> None:
        self._url = url
        self._headers = headers
        self._ws = None
        self._message_queue = queue.Queue()
        self._connected = False

    def connect(self) -> None:
        def on_message(ws, message):
            self._message_queue.put(message)

        def on_error(ws, error):
            logger.error(f"WebSocket error: {error}")
            print(f"WebSocket error: {error}")

        def on_close(ws, close_status_code, close_msg):
            logger.info("WebSocket connection closed")
            print(f"WebSocket connection closed - Status: {close_status_code}, Message: {close_msg}")
            self._connected = False

        def on_open(ws):
            logger.info("WebSocket connection opened")
            print("WebSocket connection opened successfully!")
            self._connected = True

        print(f"Attempting to connect to WebSocket URL: {self._url}")
        print(f"Headers: {self._headers}")
        
        self._ws = websocket.WebSocketApp(
            self._url,
            header=self._headers,
            on_message=on_message,
            on_error=on_error,
            on_close=on_close,
            on_open=on_open
        )

        # Start WebSocket in a separate thread
        self._ws_thread = threading.Thread(target=self._ws.run_forever)
        self._ws_thread.daemon = True
        self._ws_thread.start()

        # Wait for connection to be established
        timeout = 5  # Reduced from 10 seconds for faster startup
        start_time = time.time()
        while not self._connected and time.time() - start_time < timeout:
            time.sleep(0.05)  # Reduced from 0.1 for faster connection

        if not self._connected:
            error_msg = f"Failed to establish WebSocket connection to {self._url} within {timeout} seconds"
            logger.error(error_msg)
            print(error_msg)
            print("Possible causes:")
            print("1. Invalid endpoint URL")
            print("2. Authentication token expired or invalid")
            print("3. Network connectivity issues")
            print("4. Azure Voice Live service not available")
            raise ConnectionError(error_msg)

    def recv(self) -> str:
        try:
            return self._message_queue.get(timeout=0.5)  # Reduced from 1 second for faster response
        except queue.Empty:
            return None

    def send(self, message: str) -> None:
        if self._ws and self._connected:
            self._ws.send(message)

    def close(self) -> None:
        if self._ws:
            self._ws.close()
            self._connected = False

class AzureVoiceLive:
    def __init__(
        self,
        *,
        azure_endpoint: str | None = None,
        api_version: str | None = None,
        token: str | None = None,
        api_key: str | None = None,
    ) -> None:

        self._azure_endpoint = azure_endpoint
        self._api_version = api_version
        self._token = token
        self._api_key = api_key
        self._connection = None

    def connect(self, model: str) -> VoiceLiveConnection:
        if self._connection is not None:
            raise ValueError("Already connected to the Voice Live API.")
        if not model:
            raise ValueError("Model name is required.")

        azure_ws_endpoint = self._azure_endpoint.rstrip('/').replace("https://", "wss://")

        url = f"{azure_ws_endpoint}/voice-live/realtime?api-version={self._api_version}&model={model}"

        auth_header = {"Authorization": f"Bearer {self._token}"} if self._token else {"api-key": self._api_key}
        request_id = uuid.uuid4()
        headers = {"x-ms-client-request-id": str(request_id), **auth_header}

        self._connection = VoiceLiveConnection(url, headers)
        self._connection.connect()
        return self._connection

class AudioPlayerAsync:
    def __init__(self):
        self.queue = deque()
        self.lock = threading.Lock()
        self.stream = sd.OutputStream(
            callback=self.callback,
            samplerate=AUDIO_SAMPLE_RATE,
            channels=1,
            dtype=np.int16,
            blocksize=1200,  # Reduced from 2400 for faster response
        )
        self.playing = False

    def callback(self, outdata, frames, time, status):
        if status:
            logger.warning(f"Stream status: {status}")
        with self.lock:
            # Pre-allocate array for better performance
            data = np.zeros(frames, dtype=np.int16)
            data_filled = 0
            
            while data_filled < frames and len(self.queue) > 0:
                item = self.queue.popleft()
                frames_needed = min(frames - data_filled, len(item))
                data[data_filled:data_filled + frames_needed] = item[:frames_needed]
                data_filled += frames_needed
                
                # If item has more data than needed, put the rest back
                if len(item) > frames_needed:
                    self.queue.appendleft(item[frames_needed:])
                    break
                    
        outdata[:] = data.reshape(-1, 1)

    def add_data(self, data: bytes):
        with self.lock:
            np_data = np.frombuffer(data, dtype=np.int16)
            self.queue.append(np_data)
            if not self.playing and len(self.queue) > 0:
                self.start()

    def start(self):
        if not self.playing:
            self.playing = True
            self.stream.start()

    def stop(self):
        with self.lock:
            self.queue.clear()
        self.playing = False
        self.stream.stop()

    def terminate(self):
        with self.lock:
            self.queue.clear()
        self.stream.stop()
        self.stream.close()

def listen_and_send_audio(connection: VoiceLiveConnection) -> None:
    logger.info("Starting audio stream ...")

    stream = sd.InputStream(channels=1, samplerate=AUDIO_SAMPLE_RATE, dtype="int16", blocksize=1200)
    try:
        stream.start()
        read_size = int(AUDIO_SAMPLE_RATE * 0.01)  # Reduced from 0.02 to 0.01 seconds
        while not stop_event.is_set():
            if stream.read_available >= read_size:
                data, _ = stream.read(read_size)
                audio = base64.b64encode(data).decode("utf-8")
                param = {"type": "input_audio_buffer.append", "audio": audio, "event_id": ""}
                data_json = json.dumps(param)
                connection.send(data_json)
            else:
                time.sleep(0.0005)  # Reduced sleep time for faster response
    except Exception as e:
        logger.error(f"Audio stream interrupted. {e}")
    finally:
        stream.stop()
        stream.close()
        logger.info("Audio stream closed.")

def receive_audio_and_playback(connection: VoiceLiveConnection) -> None:
    last_audio_item_id = None
    audio_player = AudioPlayerAsync()
    current_transcript = ""
    current_response_transcript = ""
    last_response_id = None
    response_transcripts = {}  # Track transcripts by response ID
    completed_responses = set()  # Track completed responses

    logger.info("Starting audio playback ...")
    print("\n=== Azure Voice Live Chat Started ===")
    print("Speak into your microphone to chat with the AI...\n")
    
    try:
        while not stop_event.is_set():
            raw_event = connection.recv()
            if raw_event is None:
                continue

            try:
                event = json.loads(raw_event)
                event_type = event.get("type")

                # Only log important events to console, keep detailed logging to file
                if event_type == "session.created":
                    session = event.get("session")
                    logger.info(f"Session created: {session.get('id')}")
                    print("✅ Session created successfully")

                elif event_type == "input_audio_buffer.speech_started":
                    print("🎤 Speech started - listening...")
                    audio_player.stop()

                elif event_type == "input_audio_buffer.speech_stopped":
                    # Don't print this to reduce clutter - just log it
                    logger.debug("Speech stopped")

                elif event_type == "response.created":
                    response_id = event.get("response", {}).get("id")
                    print("🧠 AI is thinking...")
                    # Show a simple typing indicator
                    print("🤖 AI: ", end="", flush=True)
                    # Clear any previous AI response line and reset response tracking
                    current_response_transcript = ""
                    last_response_id = response_id
                    if response_id:
                        response_transcripts[response_id] = ""

                elif event_type == "conversation.item.input_audio_transcription.completed":
                    # User's speech transcription completed
                    transcript = event.get("transcript", "")
                    if transcript:
                        print(f"\n👤 You: {transcript}")
                        current_transcript = transcript
                        # Add a minimal delay for better readability
                        time.sleep(0.05)

                elif event_type == "response.audio_transcript.delta":
                    # AI response transcription in progress - just accumulate, don't print yet
                    delta = event.get("delta", "")
                    response_id = event.get("response_id") or event.get("item_id")
                    
                    if delta and response_id:
                        # Initialize transcript for this response if not exists
                        if response_id not in response_transcripts:
                            response_transcripts[response_id] = ""
                        
                        # Add the delta to the response transcript
                        response_transcripts[response_id] += delta
                        
                        # Update current transcript if this is the active response
                        if response_id == last_response_id:
                            current_response_transcript = response_transcripts[response_id]

                elif event_type == "response.audio_transcript.done":
                    # AI response transcription completed
                    response_id = event.get("response_id") or event.get("item_id")
                    
                    if response_id and response_id not in completed_responses:
                        # Mark this response as completed to prevent duplicates
                        completed_responses.add(response_id)
                        
                        # Get the final transcript for this response
                        final_transcript = response_transcripts.get(response_id, current_response_transcript)
                        
                        if final_transcript:
                            # Print the final response (replace the typing indicator)
                            print(f"\r🤖 AI: {final_transcript}")
                            print()  # Add a blank line for better readability
                            
                        # Clean up old transcripts to prevent memory buildup
                        if len(completed_responses) > 10:
                            oldest_responses = list(completed_responses)[:5]
                            for old_id in oldest_responses:
                                completed_responses.discard(old_id)
                                response_transcripts.pop(old_id, None)

                elif event_type == "response.audio.delta":
                    # Audio data for playback
                    if event.get("item_id") != last_audio_item_id:
                        last_audio_item_id = event.get("item_id")

                    bytes_data = base64.b64decode(event.get("delta", ""))
                    if bytes_data:
                        logger.debug(f"Received audio data of length: {len(bytes_data)}")   
                    audio_player.add_data(bytes_data)

                # === ADVANCED FEATURE EVENT HANDLERS (COMMENTED) ===
                
                # Uncomment when using output_audio_timestamp_types
                # elif event_type == "response.audio_timestamp.delta":
                #     # Word-level timestamp information
                #     audio_offset = event.get("audio_offset_ms")
                #     audio_duration = event.get("audio_duration_ms") 
                #     text = event.get("text")
                #     timestamp_type = event.get("timestamp_type")
                #     logger.debug(f"Audio timestamp: '{text}' at {audio_offset}ms ({timestamp_type})")
                #     
                # elif event_type == "response.audio_timestamp.done":
                #     # All timestamps received
                #     logger.debug("Audio timestamps completed")

                # Uncomment when using animation.outputs for visemes
                # elif event_type == "response.animation_viseme.delta":
                #     # Viseme data for facial animation
                #     audio_offset = event.get("audio_offset_ms")
                #     viseme_id = event.get("viseme_id")
                #     logger.debug(f"Viseme {viseme_id} at {audio_offset}ms")
                #     
                # elif event_type == "response.animation_viseme.done":
                #     # All viseme data received
                #     logger.debug("Viseme data completed")

                # Avatar-specific events (when using avatar configuration)
                # elif event_type == "session.avatar.connecting":
                #     # Avatar connection in progress
                #     server_sdp = event.get("server_sdp")
                #     logger.info("Avatar connecting with server SDP")
                #     # Handle WebRTC connection setup here
                #
                # elif event_type == "session.avatar.connected":
                #     # Avatar successfully connected
                #     logger.info("Avatar connected successfully")
                #     print("🎭 Avatar ready!")

                elif event_type == "error":
                    error_details = event.get("error", {})
                    error_type = error_details.get("type", "Unknown")
                    error_code = error_details.get("code", "Unknown")
                    error_message = error_details.get("message", "No message provided")
                    print(f"❌ Error: {error_message}")
                    logger.error(f"Error received: Type={error_type}, Code={error_code}, Message={error_message}")

                # Log all events to file for debugging, but keep console clean
                logger.debug(f"Event: {event_type} - Response ID: {event.get('response_id', event.get('item_id', 'N/A'))}")

            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse JSON event: {e}")
                continue

    except Exception as e:
        logger.error(f"Error in audio playback: {e}")
        print(f"❌ Error: {e}")
    finally:
        audio_player.terminate()
        logger.info("Playback done.")
        print("\n=== Chat ended ===")

def read_keyboard_and_quit() -> None:
    print("💡 Press 'q' and Enter to quit the chat.")
    while not stop_event.is_set():
        try:
            user_input = input()
            if user_input.strip().lower() == 'q':
                print("👋 Quitting the chat...")
                stop_event.set()
                break
        except EOFError:
            # Handle case where input is interrupted
            break

if __name__ == "__main__":
    try:
        # Change to the directory where this script is located
        os.chdir(os.path.dirname(os.path.abspath(__file__)))
        # Add folder for logging
        if not os.path.exists('logs'):
            os.makedirs('logs')
        # Add timestamp for logfiles
        timestamp = datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        # Set up logging
        logging.basicConfig(
            filename=f'logs/{timestamp}_voicelive.log',
            filemode="w",
            level=logging.DEBUG,
            format='%(asctime)s:%(name)s:%(levelname)s:%(message)s'
        )
        # Load environment variables from .env file
        load_dotenv("./.env", override=True)

        # Set up signal handler for graceful shutdown
        def signal_handler(signum, frame):
            print("\nReceived interrupt signal, shutting down...")
            stop_event.set()
            sys.exit(0)

        signal.signal(signal.SIGINT, signal_handler)
        signal.signal(signal.SIGTERM, signal_handler)

        main()
    except Exception as e:
        print(f"Error: {e}")
        stop_event.set()