from __future__ import annotations

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, File, UploadFile
from pydantic import BaseModel

router = APIRouter(prefix="/workflows", tags=["workflows"])


class NodeData(BaseModel):
    id: str
    type: str
    data: Optional[Dict[str, Any]] = None


class WorkflowRequest(BaseModel):
    nodes: List[NodeData]
    edges: List[Dict[str, Any]]


class LLMRequest(BaseModel):
    prompt: str
    temperature: Optional[float] = None


class TTSRequest(BaseModel):
    text: str
    voice: Optional[str] = None
    language: Optional[str] = None


@router.post("/llm")
async def run_llm(req: LLMRequest):
    # TODO(#manage_todo_list): Replace with real Gemini API after the mockup is completely
    return {
        "model": "Gemini 1.5 Flash (mock)",
        "prompt": req.prompt,
        "temperature": req.temperature if req.temperature is not None else 0.7,
        "reply": "Đây là câu trả lời mẫu từ Gemini (Mock Mode).",
        "mode": "mock",
    }


@router.post("/tts")
async def synthesize_tts(req: TTSRequest):
    return {
        "text": req.text,
        "voice": req.voice or "vi-VN-Standard-A",
        "language": req.language or "vi-VN",
        "audio_url": "https://www2.cs.uic.edu/~i101/SoundFiles/preamble10.wav",
        "mode": "mock",
    }


@router.post("/stt")
async def transcribe_stt(file: UploadFile = File(...)):
    _ = file.filename  # placeholder to avoid unused var warnings
    return {
        "text": "Đây là bản dịch mock từ âm thanh.",
        "confidence": 0.93,
        "mode": "mock",
    }


@router.post("/execute")
async def execute_workflow(req: WorkflowRequest):
    results = []
    for idx, node in enumerate(req.nodes):
        results.append(
            {
                "node_id": node.id,
                "type": node.type,
                "status": "ok",
                "order": idx + 1,
                "output": node.data or {"message": f"Kết quả giả cho node {node.type}"},
            }
        )
    return {
        "total_nodes": len(results),
        "results": results,
        "edges": req.edges,
        "mode": "mock",
    }
