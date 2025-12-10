"""
Node 4: Google TTS Service mock
Receives text and returns mock audio url
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
    # Return mock audio url
    return {"audio_url": f"http://localhost:9000/fake_audio/{text.replace(' ', '_')}.mp3"}

# Run: uvicorn backend.mock_google_tts_node:app --reload --port 9000
