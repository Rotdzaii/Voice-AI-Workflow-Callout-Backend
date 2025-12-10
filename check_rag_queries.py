import asyncpg
import asyncio
import os
from dotenv import load_dotenv

load_dotenv()

async def check_queries():
    db_url = os.getenv("DATABASE_URL")
    conn = await asyncpg.connect(db_url)
    
    # Count total rows
    total = await conn.fetchval("SELECT COUNT(*) FROM rag_queries")
    print(f"Total rag_queries: {total}")
    
    # Count rows with non-empty source_ids
    with_ids = await conn.fetchval(
        "SELECT COUNT(*) FROM rag_queries WHERE array_length(source_ids, 1) > 0"
    )
    print(f"With source_ids: {with_ids}")
    
    # Get latest row
    row = await conn.fetchrow(
        "SELECT id, question, source_ids, groups, topics, scores FROM rag_queries ORDER BY created_at DESC LIMIT 1"
    )
    
    if row:
        print(f"\n=== Latest query ===")
        print(f"ID: {row['id']}")
        print(f"Question: {row['question']}")
        print(f"source_ids: {row['source_ids']}")
        print(f"groups: {row['groups']}")
        print(f"topics: {row['topics']}")
        print(f"scores: {row['scores']}")
    
    await conn.close()

asyncio.run(check_queries())
