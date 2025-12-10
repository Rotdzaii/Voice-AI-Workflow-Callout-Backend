"""
Node 3: Backend (Voice AI Backend) mock
Receives phone number, simulates call handling, generates text, calls Google TTS node (mock)
"""
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

@app.post("/call/start")
async def call_start(request: Request):
    data = await request.json()
    phone = data.get("phone")
    # Simulate call handling, return text for TTS
    if not phone:
        return {"error": "Missing phone number"}
    response_text = f"Calling {phone}, please wait..."
    # Call Google TTS node (mock)
    import requests
    tts_resp = requests.post("http://localhost:9000/tts", json={"text": response_text})
    tts_audio_url = tts_resp.json().get("audio_url")
    return {"status": "ok", "audio_url": tts_audio_url}

# Run: uvicorn backend.backend_node:app --reload --port 8000
