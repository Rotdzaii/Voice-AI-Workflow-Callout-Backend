# Node 2: API Trung Gian (Start Call API)
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
import httpx

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

BACKEND_URL = "http://localhost:8000/call/start"

@app.post("/api/start_call")
async def start_call(request: Request):
    data = await request.json()
    phone = data.get("phone")
    if not phone:
        return {"error": "Missing phone number"}
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.post(BACKEND_URL, json={"phone": phone})
            return resp.json()
        except Exception as e:
            return {"error": str(e)}

# Chạy: uvicorn frontend.api_trung_gian_node:app --reload --port 4001
