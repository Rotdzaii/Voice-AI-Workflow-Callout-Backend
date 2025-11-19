"""NLU interface with a simple keyword+regex fallback and optional PhoBERT stub.

Set NLU_ENGINE=simple|phobert to switch. PhoBERT path requires transformers+torch (not installed by default).
"""
from __future__ import annotations
import re
from typing import List, Dict, Any
from .config import NLU_ENGINE


class BaseNLU:
    def parse_text(self, text: str) -> Dict[str, Any]:  # returns {intent: {...}, entities: [...]} 
        raise NotImplementedError


class SimpleNLU(BaseNLU):
    PHONE_RE = re.compile(r"\b(?:\+84|0)\d{9}\b")

    def parse_text(self, text: str) -> Dict[str, Any]:
        t = text.lower()
        intent = None
        if any(k in t for k in ["đồng ý", "ok", "oke", "đc", "được"]):
            intent = {"name": "affirm", "confidence": 0.8}
        elif any(k in t for k in ["không", "ko", "khong", "từ chối"]):
            intent = {"name": "deny", "confidence": 0.8}
        elif any(k in t for k in ["hẹn", "lịch", "schedule"]):
            intent = {"name": "schedule", "confidence": 0.7}
        else:
            intent = {"name": "chitchat", "confidence": 0.5}

        entities = []
        for m in self.PHONE_RE.finditer(text):
            entities.append({"entity": "phone", "value": m.group(0), "start": m.start(), "end": m.end(), "confidence": 0.9})
        return {"intent": intent, "entities": entities}


class PhoBERTNLU(BaseNLU):
    def __init__(self):
        try:
            from transformers import AutoTokenizer, AutoModelForSequenceClassification
            self.tokenizer = AutoTokenizer.from_pretrained("vinai/phobert-base-v2")
            self.model = AutoModelForSequenceClassification.from_pretrained("vinai/phobert-base-v2")
            self._ready = True
        except Exception:
            self._ready = False

    def parse_text(self, text: str) -> Dict[str, Any]:
        if not self._ready:
            return {"intent": {"name": "unknown", "confidence": 0.0}, "entities": []}
        # Placeholder inference: real intent classification would map logits to labels
        encoded = self.tokenizer(text, return_tensors="pt")
        try:
            import torch
            with torch.no_grad():
                outputs = self.model(**encoded)
            # Use the highest logit index as pseudo intent
            pred = int(outputs.logits.argmax(dim=-1).item())
            intent_label = f"intent_{pred}"
            return {"intent": {"name": intent_label, "confidence": 0.0}, "entities": []}
        except Exception:
            return {"intent": {"name": "unknown", "confidence": 0.0}, "entities": []}


def get_nlu() -> BaseNLU:
    if NLU_ENGINE == "phobert":
        return PhoBERTNLU()
    return SimpleNLU()
