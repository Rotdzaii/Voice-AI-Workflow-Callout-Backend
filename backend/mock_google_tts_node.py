"""
Node 4: Google TTS Service skeleton
Receives text, placeholder for TTS logic
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

@app.post("/tts")
async def tts(request: Request):
    data = await request.json()
    text = data.get("text")
    if not text:
        return {"error": "Missing text"}
    # TODO: Implement TTS logic
    return {"status": "received", "text": text, "message": "TTS logic not implemented."}
