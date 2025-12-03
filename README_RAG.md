# RAG / Retriever pipeline (local)

This small pipeline scans the `data/` folder, creates a combined `processed_docs.jsonl`, computes embeddings with `sentence-transformers`, builds a FAISS index and provides a simple FastAPI retrieval endpoint.

Quick steps

1. Create or activate your Python virtualenv (Windows PowerShell example):

```powershell
python -m venv .venv
. .venv/Scripts/Activate.ps1
pip install -r requirements-rag.txt
```

2. Build the processed docs file:

```powershell
python -m rag_tools.data_pipeline --data-dir ./data --out ./processed_docs.jsonl
```

3. Build embeddings and FAISS index:

```powershell
python -m rag_tools.build_embeddings --docs ./processed_docs.jsonl --out-dir ./rag_index
```

4. Run the retriever API:

```powershell
uvicorn rag_tools.serve_rag:app --reload --port 8001
```

5. Query the retriever (curl example):

```powershell
curl -X POST "http://127.0.0.1:8001/query" -H "Content-Type: application/json" -d '{"q":"tổng đài cloud là gì","k":5}'
```

Notes
- The pipeline uses `all-MiniLM-L6-v2` by default — small and fast. Swap model names in `build_embeddings.py` / `serve_rag.py` as needed.
- This project returns retrieved contexts; you can pass them to your chosen LLM to generate final answers (local LLM or cloud API).
