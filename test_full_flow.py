"""Test full voice AI flow: STT -> RAG -> LLM -> TTS"""
import sys
import os
from pathlib import Path
from dotenv import load_dotenv

# Load .env
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)
print(f"✅ Loaded .env")

sys.path.insert(0, os.path.dirname(__file__))

# Test RAG search
print("\n" + "="*60)
print("1️⃣ Testing RAG Search")
print("="*60)

from backend.rag_api import search_rag_context

user_text = "MiPBX là gì?"
print(f"User: {user_text}")

try:
    context = search_rag_context(user_text, top_k=2)
    if context:
        print(f"✅ RAG Context ({len(context)} chars):")
        print(context[:200] + "...")
    else:
        print("❌ No RAG context")
except Exception as e:
    print(f"❌ RAG Error: {e}")
    import traceback
    traceback.print_exc()

# Test Gemini LLM
print("\n" + "="*60)
print("2️⃣ Testing Gemini LLM")
print("="*60)

try:
    import google.generativeai as genai
    from backend.config import GEMINI_API_KEY
    
    genai.configure(api_key=GEMINI_API_KEY)
    llm = genai.GenerativeModel(
        model_name="gemini-2.0-flash-exp",
        generation_config={
            "temperature": 0.7,
            "top_p": 0.9,
            "max_output_tokens": 200,
        }
    )
    
    # Build prompt with context
    if context:
        prompt = f"""Bạn là trợ lý AI thân thiện.

Thông tin tham khảo: {context}

Người dùng hỏi: {user_text}

Hãy trả lời ngắn gọn (1-2 câu)."""
    else:
        prompt = f"""Bạn là trợ lý AI thân thiện.

Người dùng hỏi: {user_text}

Hãy trả lời ngắn gọn (1-2 câu)."""
    
    print(f"Prompt length: {len(prompt)} chars")
    print("Calling Gemini...")
    
    response = llm.generate_content(prompt)
    ai_text = response.text.strip() if hasattr(response, 'text') else str(response).strip()
    
    print(f"✅ AI Response:")
    print(ai_text)
    
except Exception as e:
    print(f"❌ LLM Error: {e}")
    import traceback
    traceback.print_exc()

# Test TTS
print("\n" + "="*60)
print("3️⃣ Testing Google TTS")
print("="*60)

try:
    from google.cloud import texttospeech
    
    client = texttospeech.TextToSpeechClient()
    
    synthesis_input = texttospeech.SynthesisInput(text=ai_text[:100])
    voice = texttospeech.VoiceSelectionParams(
        language_code="vi-VN",
        name="vi-VN-Standard-A"
    )
    audio_config = texttospeech.AudioConfig(
        audio_encoding=texttospeech.AudioEncoding.LINEAR16,
        sample_rate_hertz=24000
    )
    
    print("Calling Google TTS...")
    response = client.synthesize_speech(
        input=synthesis_input,
        voice=voice,
        audio_config=audio_config
    )
    
    print(f"✅ TTS Success: {len(response.audio_content)} bytes")
    
except Exception as e:
    print(f"❌ TTS Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "="*60)
print("✅ Test Complete!")
print("="*60)
