import os
import sys
import asyncio
import glob
from pathlib import Path

# Make repo root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.db import get_pool
from dotenv import load_dotenv

async def run_sql(pool, sql: str):
    async with pool.acquire() as conn:
        await conn.execute(sql)

async def run_migrations():
    load_dotenv()
    pool = await get_pool()
    mig_dir = Path(ROOT) / 'db' / 'migrations'
    if not mig_dir.exists():
        print('No migrations directory found at', mig_dir)
        return 0
    files = sorted(glob.glob(str(mig_dir / '*.sql')))
    if not files:
        print('No migration files found in', mig_dir)
        return 0
    print('Applying migrations to database...')
    for fpath in files:
        print('->', os.path.basename(fpath))
        with open(fpath, 'r', encoding='utf-8') as f:
            sql = f.read()
        await run_sql(pool, sql)
    print('All migrations applied.')
    return 0

if __name__ == '__main__':
    sys.exit(asyncio.run(run_migrations()))
