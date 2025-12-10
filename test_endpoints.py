#!/usr/bin/env python3
import requests
import json

BASE_URL = "http://localhost:8000"

# Test 1: Check RAG status
print("=== Test 1: RAG Status ===")
try:
    r = requests.get(f"{BASE_URL}/rag/status", timeout=5)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.json()}")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Test 2: RAG Query ===")
try:
    r = requests.post(f"{BASE_URL}/rag/query", json={"question": "MiDesk là gì?"}, timeout=60)
    print(f"Status: {r.status_code}")
    data = r.json()
    print(f"Answer: {data.get('answer', '')[:100]}...")
    print(f"Latency: {data.get('latency')}ms")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Test 3: TTS ===")
try:
    r = requests.post(
        f"{BASE_URL}/rag/tts",
        json={"text": "Xin chào", "base_filename": "test_tts"},
        timeout=30
    )
    print(f"Status: {r.status_code}")
    data = r.json()
    print(f"TTS OK: {data.get('ok')}")
    print(f"TTS Error: {data.get('error')}")
    print(f"Has audio_base64: {bool(data.get('audio_base64'))}")
    print(f"Audio path: {data.get('audio_path')}")
    print(f"Latency: {data.get('latency')}ms")
except Exception as e:
    print(f"Error: {e}")

print("\n=== Test complete ===")
