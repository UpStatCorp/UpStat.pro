import os
import json
import httpx
from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import StreamingResponse

router = APIRouter()

# URL AI Agent Service
_ws_url = os.getenv("AI_AGENT_WS_URL", "ws://ai_agent_service:8001/ws")
# Конвертируем WebSocket URL в HTTP URL
if _ws_url.startswith("ws://"):
    AI_AGENT_URL = _ws_url.replace("ws://", "http://").split("/ws")[0]
elif _ws_url.startswith("wss://"):
    AI_AGENT_URL = _ws_url.replace("wss://", "https://").split("/ws")[0]
else:
    AI_AGENT_URL = "http://ai_agent_service:8001"

@router.post("/tts-proxy")
async def tts_proxy(request: Request):
    """Проксирует TTS запросы к AI Agent Service"""
    try:
        body = await request.json()
        
        # Проксируем запрос к ai_agent_service
        async with httpx.AsyncClient(timeout=30.0) as client:
            url = f"{AI_AGENT_URL}/api/tts-proxy"
            resp = await client.post(url, json=body, timeout=30.0)
            
            if resp.status_code != 200:
                raise HTTPException(status_code=resp.status_code, detail=f"TTS service error: {resp.text}")
            
            async def gen():
                async for chunk in resp.aiter_bytes(8192):
                    if chunk:
                        yield chunk
            
            return StreamingResponse(
                gen(),
                media_type="audio/mpeg",
                headers={
                    "Cache-Control": "no-cache",
                    "Connection": "keep-alive",
                }
            )
    except httpx.RequestError as e:
        raise HTTPException(status_code=503, detail=f"Failed to connect to TTS service: {str(e)}")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS proxy error: {str(e)}")

