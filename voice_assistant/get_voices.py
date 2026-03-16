"""
Скрипт для получения списка доступных голосов ElevenLabs.
Запустите этот скрипт, чтобы получить voice_id для настройки.
"""

import os
from dotenv import load_dotenv
from elevenlabs.client import ElevenLabs

load_dotenv()

api_key = os.getenv("ELEVENLABS_API_KEY")
if not api_key:
    print("❌ ELEVENLABS_API_KEY не установлен в .env файле")
    exit(1)

client = ElevenLabs(api_key=api_key)

try:
    print("🔍 Получение списка доступных голосов...\n")
    
    # Получаем все голоса
    voices = client.voices.get_all()
    
    if not voices.voices:
        print("⚠️  Голоса не найдены")
    else:
        print(f"✅ Найдено {len(voices.voices)} голосов:\n")
        print("-" * 80)
        
        for voice in voices.voices:
            print(f"ID: {voice.voice_id}")
            print(f"Имя: {voice.name}")
            if hasattr(voice, 'description') and voice.description:
                print(f"Описание: {voice.description}")
            if hasattr(voice, 'category'):
                print(f"Категория: {voice.category}")
            print("-" * 80)
        
        # Показываем первый голос как пример
        if voices.voices:
            first_voice = voices.voices[0]
            print(f"\n💡 Пример использования в .env:")
            print(f"ELEVENLABS_VOICE_ID={first_voice.voice_id}")
            
except Exception as e:
    print(f"❌ Ошибка при получении голосов: {e}")
    import traceback
    traceback.print_exc()


