"""
Точка входа для Docker контейнера
"""
from app.main import app

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        app,
        host="0.0.0.0",
        port=8000,
        ws_ping_interval=30.0,
        ws_ping_timeout=120.0,
    )
