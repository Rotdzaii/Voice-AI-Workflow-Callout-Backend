import asyncio
import asyncpg
from typing import Optional
from .config import DATABASE_URL

_pool: Optional[asyncpg.Pool] = None
_pool_loop: Optional[asyncio.AbstractEventLoop] = None

async def get_pool() -> asyncpg.Pool:
    global _pool, _pool_loop
    current_loop = asyncio.get_running_loop()
    recreate = False
    if _pool is None or _pool_loop is None:
        recreate = True
    else:
        # Recreate the pool if it was created on a different/closed loop
        try:
            if _pool_loop is not current_loop:
                recreate = True
            else:
                # probe the pool with a simple acquire/release to ensure it's alive
                async with _pool.acquire() as _:
                    pass
        except Exception:
            recreate = True

    if recreate:
        if _pool is not None:
            try:
                await _pool.close()
            except Exception:
                pass
        _pool = await asyncpg.create_pool(DATABASE_URL, min_size=1, max_size=10)
        _pool_loop = current_loop
    return _pool

async def close_pool():
    global _pool, _pool_loop
    if _pool is not None:
        await _pool.close()
        _pool = None
        _pool_loop = None

async def init_db_from_file(sql_path: str) -> None:
    """Execute SQL file against the configured DATABASE_URL. For local/dev only."""
    pool = await get_pool()
    with open(sql_path, "r", encoding="utf-8") as f:
        sql = f.read()
    async with pool.acquire() as conn:
        # Use execute which runs the entire script
        await conn.execute(sql)

# sync helper for scripts
def run_init_sync(sql_path: str):
    asyncio.get_event_loop().run_until_complete(init_db_from_file(sql_path))
