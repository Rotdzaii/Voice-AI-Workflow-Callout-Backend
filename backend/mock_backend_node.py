"""
Node 3: Backend (Voice AI Backend) skeleton
Receives phone number, placeholder for call logic, TTS, etc.
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
    if not phone:
        return {"error": "Missing phone number"}
    # TODO: Implement call logic, STT, response, TTS, etc.
    return {"status": "received", "phone": phone, "message": "Call logic not implemented."}
