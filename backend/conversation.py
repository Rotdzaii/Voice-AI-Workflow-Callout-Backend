"""Conversation Manager stub integrating Deeppavlov-Agent design.

For MVP we hold a simple state dict per call_id in memory.
Future: replace with external agent service at DEEPPAVLOV_URL.
"""
from __future__ import annotations
from typing import Dict, Any
from .nlu import get_nlu

_nlu = get_nlu()
_state: Dict[str, Dict[str, Any]] = {}


def get_state(call_id: str) -> Dict[str, Any]:
    return _state.setdefault(call_id, {"turn": 0, "context": {}})


def process_turn(call_id: str, speaker: str, text: str) -> Dict[str, Any]:
    st = get_state(call_id)
    st["turn"] += 1
    parsed = _nlu.parse_text(text)
    # Very naive policy example.
    intent_name = parsed["intent"]["name"]
    if speaker == "user":
        if intent_name == "affirm":
            reply = "Đã ghi nhận đồng ý, cảm ơn bạn."
        elif intent_name == "deny":
            reply = "Mình hiểu bạn từ chối. Bạn cần hỗ trợ thêm gì không?"
        elif intent_name == "schedule":
            reply = "Bạn muốn hẹn lịch vào thời điểm nào?"
        else:
            reply = "Bạn có thể cho mình biết mong muốn cụ thể không?"
    else:
        reply = ""
    return {
        "call_id": call_id,
        "turn": st["turn"],
        "nlu": parsed,
        "reply": reply,
    }
