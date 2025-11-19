import asyncio
import os
import asyncpg
from dotenv import load_dotenv, dotenv_values

loaded = load_dotenv()
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    # Fallback: try loading from .env.example (for convenience in demo).
    example_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), ".env.example")
    if os.path.exists(example_path):
        vals = dotenv_values(example_path)
        DATABASE_URL = vals.get("DATABASE_URL")

async def main():
    if not DATABASE_URL:
        print("ERROR: DATABASE_URL is not set")
        return 2
    try:
        conn = await asyncpg.connect(DATABASE_URL)
        version = await conn.fetchval("SELECT version()")
        tables = await conn.fetch(
            """
            SELECT table_name FROM information_schema.tables
            WHERE table_schema = 'public'
            ORDER BY table_name
            LIMIT 10
            """
        )
        await conn.close()
        print("OK: Connected to DB")
        print("Postgres:", version)
        print("Tables:", [r[0] for r in tables])
        return 0
    except Exception as e:
        print("ERROR:", e)
        return 1

if __name__ == "__main__":
    exit(asyncio.run(main()))
