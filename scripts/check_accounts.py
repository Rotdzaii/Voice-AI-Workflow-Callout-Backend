#!/usr/bin/env python3
"""
Check accounts table using same config as backend.
Prints count and last 5 rows.
"""
import asyncio
import json
import sys

async def main():
    try:
        # ensure repo root is on sys.path
        from pathlib import Path
        repo_root = Path(__file__).resolve().parent.parent
        import sys
        if str(repo_root) not in sys.path:
            sys.path.insert(0, str(repo_root))
        from backend.config import DATABASE_URL
    except Exception as e:
        print("Failed to import backend.config; error:", e)
        sys.exit(2)
    try:
        import asyncpg
    except Exception as e:
        print("asyncpg not installed:", e)
        sys.exit(3)
    print("Using DATABASE_URL:", DATABASE_URL)
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval('SELECT COUNT(*) FROM accounts')
        print('accounts count =', count)
        rows = await conn.fetch('SELECT id, email, role, created_at FROM accounts ORDER BY created_at DESC LIMIT 5')
        for r in rows:
            print(dict(r))
    except Exception as e:
        print('Query failed:', e)
    finally:
        await conn.close()

if __name__ == '__main__':
    asyncio.run(main())
