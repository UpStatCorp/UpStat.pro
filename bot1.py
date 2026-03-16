import asyncio
import json
import os
import subprocess
import tempfile
from pathlib import Path

import requests
from aiogram import Bot, Dispatcher, F, types
from aiogram.filters import CommandStart
from dotenv import load_dotenv
from openai import OpenAI

# ---------- Конфигурация ----------
load_dotenv()
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

bot = Bot(token=TELEGRAM_BOT_TOKEN)
dp = Dispatcher()
client = OpenAI(api_key=OPENAI_API_KEY)

# ---------- Утилиты ----------
def run_ffmpeg_to_wav(input_path: Path, out_path: Path, sample_rate: int = 16000):
    cmd = ["ffmpeg", "-y", "-i", str(input_path), "-ac", "1", "-ar", str(sample_rate), str(out_path)]
    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

def split_for_tg(text: str, limit: int = 3800):
    chunks = []
    s = text or ""
    while len(s) > limit:
        cut = s.rfind("\n", 0, limit)
        if cut == -1:
            cut = limit
        chunks.append(s[:cut])
        s = s[cut:]
    if s.strip():
        chunks.append(s)
    return chunks

def get_transcription_from_elevenlabs(wav_path: Path):
    url = "https://api.elevenlabs.io/v1/speech-to-text"
    headers = { "xi-api-key": ELEVENLABS_API_KEY }
    data = {
        "model_id": "scribe_v1",
        "diarize": True,
        "num_speakers": 2,
        "timestamps_granularity": "word",
        "tag_audio_events": True
    }

    with open(wav_path, "rb") as f:
        files = {"file": f}
        resp = requests.post(url, headers=headers, data=data, files=files)

    if resp.status_code != 200:
        raise Exception(f"ElevenLabs API error: {resp.status_code}, {resp.text}")

    result = resp.json()
    words = result.get("words", [])
    if not words:
        return result.get("text", "").strip(), [], result

    dialogue_text = []
    dialogue_json = []
    current_speaker = None
    current_phrase = []

    def speaker_label(speaker_id):
        return "Спикер 2" if speaker_id == "speaker_0" else "Спикер 1"

    for word in words:
        speaker = word.get("speaker_id", "unknown")
        text = word.get("text", "")

        if speaker != current_speaker:
            if current_phrase:
                label = speaker_label(current_speaker)
                full_phrase = " ".join(current_phrase)
                dialogue_text.append(f"{label}: {full_phrase}")
                dialogue_json.append({"speaker": label, "text": full_phrase})
                current_phrase = []
            current_speaker = speaker

        current_phrase.append(text)

    if current_phrase:
        label = speaker_label(current_speaker)
        full_phrase = " ".join(current_phrase)
        dialogue_text.append(f"{label}: {full_phrase}")
        dialogue_json.append({"speaker": label, "text": full_phrase})

    return "\n".join(dialogue_text), dialogue_json, result

async def analyze_with_checklists(transcript: str, td: Path, filename_base: str):
    result_path = td / f"{filename_base}_analysis.txt"

    # Применим каждый чеклист
    checklist_dir = Path("checklists")
    checklist_files = list(checklist_dir.glob("*.json"))
    all_results = []

    for checklist_file in checklist_files:
        with open(checklist_file, "r", encoding="utf-8") as f:
            checklist_data = json.load(f)
        checklist_title = checklist_file.stem

        prompt = f"""Проанализируй диалог менеджера и клиента ниже на соответствие каждому пункту из чек-листа "{checklist_title}". Дай развернутые комментарии к каждому пункту. Если что-то отсутствует — укажи это. Вот диалог:\n\n{transcript}\n\nВот чеклист:\n{json.dumps(checklist_data, ensure_ascii=False, indent=2)}"""
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.4,
        )
        analysis_text = f"=== {checklist_title.upper()} ===\n" + response.choices[0].message.content.strip() + "\n\n"
        all_results.append(analysis_text)

    # Итоговая оценка
    combined_analysis = "\n".join(all_results)
    final_prompt = f"""Вот сводный анализ менеджера по различным чек-листам:\n\n{combined_analysis}\n\nНа основе этого, выведи краткий отчёт: сильные стороны, слабые места, что стоит улучшить. Пиши структурировано, как для руководителя отдела продаж."""
    final_response = client.chat.completions.create(
        model="gpt-4o",
        messages=[{"role": "user", "content": final_prompt}],
        temperature=0.4,
    )

    summary = "=== ИТОГОВЫЙ ВЫВОД ===\n" + final_response.choices[0].message.content.strip()
    result_path.write_text(combined_analysis + summary, encoding="utf-8")
    return result_path

# ---------- Хэндлеры ----------
@dp.message(CommandStart())
async def on_start(message: types.Message):
    await message.answer("Привет! Пришлите аудиофайл. Я распознаю диалог и проанализирую его по всем чек-листам. Пожалуйста, подождите 1–2 минуты после загрузки.")

@dp.message(F.voice | F.audio | F.document)
async def handle_audio(message: types.Message):
    try:
        if message.voice:
            tg_file = await bot.get_file(message.voice.file_id)
            filename = f"voice_{message.voice.file_unique_id}.ogg"
        elif message.audio:
            tg_file = await bot.get_file(message.audio.file_id)
            filename = message.audio.file_name or f"audio_{message.audio.file_unique_id}"
        else:
            tg_file = await bot.get_file(message.document.file_id)
            filename = message.document.file_name or f"doc_{message.document.file_unique_id}"
    except Exception as e:
        await message.answer(f"Ошибка получения файла: {e}")
        return

    await message.answer("Скачиваю и обрабатываю файл. Подождите...")

    try:
        with tempfile.TemporaryDirectory() as td:
            in_path = Path(td) / filename
            await bot.download_file(tg_file.file_path, destination=in_path)

            wav_path = in_path.with_suffix(".wav")
            run_ffmpeg_to_wav(in_path, wav_path)

            transcript_text, transcript_json, _ = get_transcription_from_elevenlabs(wav_path)
            if not transcript_text:
                await message.answer("Не удалось распознать речь.")
                return

            base_name = filename.split(".")[0]
            (Path(td) / f"{base_name}.txt").write_text(transcript_text, encoding="utf-8")
            (Path(td) / f"{base_name}.json").write_text(json.dumps(transcript_json, ensure_ascii=False, indent=2), encoding="utf-8")

            await message.answer("Анализирую по чеклистам. Это займёт 1–2 минуты...")

            result_path = await analyze_with_checklists(transcript_text, Path(td), base_name)
            await message.answer_document(types.FSInputFile(result_path), caption="📝 Итоговый отчёт по чеклистам")

    except Exception as e:
        await message.answer(f"Произошла ошибка: {e}")

# ---------- Запуск ----------
def main():
    if not TELEGRAM_BOT_TOKEN:
        raise RuntimeError("Нет TELEGRAM_BOT_TOKEN в .env")
    if not ELEVENLABS_API_KEY:
        raise RuntimeError("Нет ELEVENLABS_API_KEY в .env")
    if not OPENAI_API_KEY:
        raise RuntimeError("Нет OPENAI_API_KEY в .env")
    asyncio.run(dp.start_polling(bot))

if __name__ == "__main__":
    main()
