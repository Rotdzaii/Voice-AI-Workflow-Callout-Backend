import os
import sys

# Make repo root importable
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from backend.db import run_init_sync

if __name__ == '__main__':
    # Prefer user's combined schema file if present
    preferred = os.path.join(ROOT, 'db', 'voiceai_schema_combined.sql')
    fallback = os.path.join(ROOT, 'db', 'voiceai_schema_and_rls.sql')
    sql_path = preferred if os.path.exists(preferred) else fallback
    if not os.path.exists(sql_path):
        print('SQL file not found:', sql_path)
        sys.exit(1)
    print('Applying SQL schema from', sql_path)
    run_init_sync(sql_path)
    print('Done.')
