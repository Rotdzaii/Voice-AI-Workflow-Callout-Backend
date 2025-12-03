import os
import asyncio
import argparse
import pandas as pd
import httpx
from pathlib import Path
from dotenv import load_dotenv

# Send generated questions to RAG API to populate rag_queries with source_ids
# Usage:
#   python services/populate_rag_labels.py --questions data/synthetic_questions.csv --api-url http://localhost:8000/rag/query --limit 200

async def send_question(client: httpx.AsyncClient, url: str, question: str, idx: int, total: int):
    try:
        response = await client.post(url, json={"question": question}, timeout=30.0)
        response.raise_for_status()
        data = response.json()
        print(f"[{idx+1}/{total}] ✓ {question[:60]}")
        return {"question": question, "query_id": data.get("query_id"), "status": "ok"}
    except Exception as e:
        print(f"[{idx+1}/{total}] ✗ {question[:60]} | Error: {e}")
        return {"question": question, "query_id": None, "status": "error", "error": str(e)}


async def populate_labels(questions_file: str, api_url: str, limit: int = 200, concurrency: int = 5):
    df = pd.read_csv(questions_file)
    if limit and limit < len(df):
        df = df.head(limit)
    
    print(f"Sending {len(df)} questions to {api_url} (concurrency={concurrency})...")
    
    async with httpx.AsyncClient() as client:
        semaphore = asyncio.Semaphore(concurrency)
        
        async def bounded_send(q, i, t):
            async with semaphore:
                return await send_question(client, api_url, q, i, t)
        
        tasks = [bounded_send(row["question"], i, len(df)) for i, row in df.iterrows()]
        results = await asyncio.gather(*tasks)
    
    success = sum(1 for r in results if r["status"] == "ok")
    print(f"\nDone: {success}/{len(results)} questions sent successfully.")
    return results


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="data/synthetic_questions.csv")
    ap.add_argument("--api-url", default="http://localhost:8000/rag/query")
    ap.add_argument("--limit", type=int, default=200)
    ap.add_argument("--concurrency", type=int, default=5)
    args = ap.parse_args()

    if not Path(args.questions).exists():
        print(f"Questions file not found: {args.questions}")
        print("Run: python services/generate_questions.py first")
        return 1

    asyncio.run(populate_labels(args.questions, args.api_url, args.limit, args.concurrency))
    print("\nCheck rag_queries table for populated source_ids.")
    return 0


if __name__ == "__main__":
    exit(main())
