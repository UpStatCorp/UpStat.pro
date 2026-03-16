import os
import json
import aiohttp
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse

router = APIRouter()

ELEVEN_KEY = os.getenv("ELEVENLABS_API_KEY")
ELEVEN_MODEL = os.getenv("ELEVENLABS_MODEL_ID", "eleven_turbo_v2_5")
ELEVEN_VOICE = os.getenv("ELEVENLABS_VOICE_ID")

_session: aiohttp.ClientSession | None = None


def get_session() -> aiohttp.ClientSession:
    global _session
    if _session is None or _session.closed:
        _session = aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=60))
    return _session


@router.post("/tts-proxy")
async def tts_proxy(request: Request):
    body = await request.json()
    text = body.get("text", "")
    voice_id = body.get("voice_id", ELEVEN_VOICE)

    url = f"https://api.elevenlabs.io/v1/text-to-speech/{voice_id}/stream"
    headers = {
        "xi-api-key": ELEVEN_KEY or "",
        "Accept": "audio/mpeg",
        "Content-Type": "application/json",
        "Connection": "keep-alive",
    }
    payload = {
        "text": text,
        "model_id": ELEVEN_MODEL,
        "voice_settings": {
            "stability": 0.2,
            "similarity_boost": 0.8,
            "use_speaker_boost": True,
            "style": 0.0,
        },
        "output_format": "mp3_22050_32",
    }

    session = get_session()
    resp = await session.post(url, data=json.dumps(payload), headers=headers)

    async def gen():
        try:
            async for chunk in resp.content.iter_chunked(2048):
                if chunk:
                    yield chunk
        finally:
            await resp.release()

    return StreamingResponse(gen(), media_type="audio/mpeg")



