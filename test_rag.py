"""Test RAG search directly"""
import sys
import os
from pathlib import Path

# Load environment variables from .env
from dotenv import load_dotenv
env_path = Path(__file__).parent / ".env"
load_dotenv(env_path)
print(f"✅ Loaded .env from {env_path}")
print(f"   GEMINI_KEY: {os.environ.get('GEMINI_KEY', 'NOT SET')[:30]}...")

sys.path.insert(0, os.path.dirname(__file__))

from backend.rag_api import search_rag_context

print("=" * 60)
print("Testing RAG Search")
print("=" * 60)

queries = [
    "MiPBX là gì?",
    "MITEK làm gì?",
    "Sản phẩm của MITEK",
    "Auto Call là gì?"
]

for query in queries:
    print(f"\n🔍 Query: {query}")
    print("-" * 60)
    try:
        result = search_rag_context(query, top_k=2)
        if result:
            print(f"✅ Found context ({len(result)} chars):")
            print(result[:300] + "..." if len(result) > 300 else result)
        else:
            print("❌ No context found")
    except Exception as e:
        print(f"❌ Error: {e}")
        import traceback
        traceback.print_exc()
    print()

print("=" * 60)
print("Test complete!")
