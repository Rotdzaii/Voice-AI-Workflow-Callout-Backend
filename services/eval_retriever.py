import os
import argparse
import random
import re
from pathlib import Path
import numpy as np
import pandas as pd
from dotenv import load_dotenv
from sentence_transformers import SentenceTransformer

try:
    import asyncpg
except Exception:
    asyncpg = None


def sanitize_path(p: str) -> str:
    path = (p or "").strip().strip('"').replace("\\", "/")
    path = re.sub(r"[\x00-\x1f]", "", path)
    return path


def read_chunks(clean_chunk_file: str) -> pd.DataFrame:
    path = sanitize_path(clean_chunk_file)
    candidate = Path(path)
    if not candidate.exists():
        repo_root = Path(__file__).resolve().parents[1]
        fallback1 = repo_root / "rag_clean_chunks.csv"
        fallback2 = repo_root / "data" / "rag_clean_chunks.csv"
        for fb in [fallback1, fallback2]:
            if fb.exists():
                candidate = fb
                break
    if not candidate.exists():
        raise FileNotFoundError(f"Clean chunk CSV not found. Tried: {path}, {candidate}")
    df = pd.read_csv(candidate)
    # normalize columns
    if "id" not in df.columns:
        df["id"] = [str(i) for i in range(len(df))]
    df["id"] = df["id"].astype(str)
    if "topic" not in df.columns:
        df["topic"] = "_"
    if "group" not in df.columns:
        df["group"] = "_"
    df["contents"] = df["contents"].astype(str)
    return df


def embed_corpus(model_name: str, texts: list[str], batch_size: int = 64) -> np.ndarray:
    model = SentenceTransformer(model_name)
    emb = model.encode(texts, batch_size=batch_size, show_progress_bar=True, normalize_embeddings=True)
    return np.asarray(emb)


def recall_at_k(query_topics, topk_topics, k=5):
    hits = 0
    for qt, tops in zip(query_topics, topk_topics):
        if any(t == qt for t in tops[:k]):
            hits += 1
    return hits / len(query_topics) if query_topics else 0.0


def mrr_at_k(query_topics, topk_topics, k=10):
    rr = []
    for qt, tops in zip(query_topics, topk_topics):
        rank = 0
        for i, t in enumerate(tops[:k], start=1):
            if t == qt:
                rank = i
                break
        rr.append(1.0 / rank if rank > 0 else 0.0)
    return sum(rr) / len(rr) if rr else 0.0


async def fetch_rag_queries(dsn: str, limit: int = 200):
    if asyncpg is None:
        return []
    try:
        conn = await asyncpg.connect(dsn)
        rows = await conn.fetch("""
            SELECT question, source_ids
            FROM rag_queries
            WHERE source_ids IS NOT NULL AND array_length(source_ids,1) > 0
            ORDER BY created_at DESC
            LIMIT $1
        """, limit)
        await conn.close()
        return [{"question": r[0], "source_ids": [str(x) for x in (r[1] or [])]} for r in rows]
    except Exception:
        return []


def build_topk(corpus_emb: np.ndarray, query_emb: np.ndarray, k: int = 10, exclude_self_idx: list[int] | None = None):
    # cosine similarity with normalized vectors: dot product
    sims = np.dot(query_emb, corpus_emb.T)
    if exclude_self_idx is not None:
        for i, ex in enumerate(exclude_self_idx):
            if 0 <= ex < sims.shape[1]:
                sims[i, ex] = -1.0
    topk_idx = np.argpartition(-sims, kth=min(k, sims.shape[1]-1), axis=1)[:, :k]
    # sort each row's top-k
    sorted_idx = np.take_along_axis(topk_idx, np.argsort(-np.take_along_axis(sims, topk_idx, axis=1), axis=1), axis=1)
    return sorted_idx


def evaluate_on_chunks(df: pd.DataFrame, base_model: str, ft_model: str, limit: int = 500, k_list=(1,3,5,10)):
    if len(df) > limit:
        df = df.sample(n=limit, random_state=42).reset_index(drop=True)
    texts = df["contents"].tolist()

    # Baseline embeddings
    base_emb = embed_corpus(base_model, texts)
    # Finetuned embeddings
    ft_emb = embed_corpus(ft_model, texts)

    # Build per-query exclusions (self)
    exclude = list(range(len(df)))

    # Prepare topics
    topics = df["topic"].astype(str).tolist()

    # For qualitative examples
    examples = random.sample(range(len(df)), k=min(5, len(df)))

    results = {}
    for label, emb in [("baseline", base_emb), ("finetuned", ft_emb)]:
        topk = build_topk(emb, emb, k=max(k_list), exclude_self_idx=exclude)
        topk_topics = [[topics[j] for j in row] for row in topk]
        metrics = {}
        for k in k_list:
            r = recall_at_k(topics, topk_topics, k=k)
            metrics[f"Recall@{k}"] = r
        metrics["MRR@10"] = mrr_at_k(topics, topk_topics, k=10)
        results[label] = metrics

    # Build qualitative outputs
    qualis = []
    for i in examples:
        q = texts[i][:200]
        base_top = build_topk(base_emb, base_emb[i:i+1], k=5, exclude_self_idx=[i])[0]
        ft_top = build_topk(ft_emb, ft_emb[i:i+1], k=5, exclude_self_idx=[i])[0]
        qualis.append({
            "query_idx": i,
            "query_topic": topics[i],
            "query": q,
            "baseline": [(int(j), topics[int(j)], texts[int(j)][:120]) for j in base_top],
            "finetuned": [(int(j), topics[int(j)], texts[int(j)][:120]) for j in ft_top],
        })

    return results, qualis


def evaluate_on_logs(df: pd.DataFrame, base_model: str, ft_model: str, logs: list[dict], k_list=(1,3,5,10)):
    # Map chunk id -> row index
    id_to_idx = {str(r["id"]): i for i, r in df.iterrows()}
    texts = df["contents"].tolist()
    topics = df["topic"].astype(str).tolist()

    base_emb = embed_corpus(base_model, texts)
    ft_emb = embed_corpus(ft_model, texts)

    questions = [q["question"] for q in logs]
    q_base = embed_corpus(base_model, questions)
    q_ft = embed_corpus(ft_model, questions)

    def metric_for(model_label: str, corpus_emb: np.ndarray, q_emb: np.ndarray):
        topk = build_topk(corpus_emb, q_emb, k=max(k_list))
        recalls = {f"Recall@{k}": 0 for k in k_list}
        for qi, row in enumerate(topk):
            gold_ids = [gid for gid in logs[qi]["source_ids"] if gid in id_to_idx]
            gold_idx = set(id_to_idx[gid] for gid in gold_ids)
            for k in k_list:
                if any(int(j) in gold_idx for j in row[:k]):
                    recalls[f"Recall@{k}"] += 1
        n = len(logs)
        for k in k_list:
            recalls[f"Recall@{k}"] = recalls[f"Recall@{k}"] / n if n else 0.0
        return recalls

    res = {
        "baseline": metric_for("baseline", base_emb, q_base),
        "finetuned": metric_for("finetuned", ft_emb, q_ft)
    }
    return res


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.environ.get("CLEAN_CHUNK_FILE", "rag_clean_chunks.csv"))
    ap.add_argument("--baseline", default="sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
    ap.add_argument("--finetuned", default="models/embeddings/ft-emb")
    ap.add_argument("--limit", type=int, default=500)
    ap.add_argument("--use-logs", action="store_true")
    args = ap.parse_args()

    df = read_chunks(args.input)

    # Evaluate on chunks (topic-based)
    results, qualis = evaluate_on_chunks(df, args.baseline, args.finetuned, limit=args.limit)
    print("\n=== Topic-based Retrieval (Chunks) ===")
    for label, mets in results.items():
        print(label.upper())
        for k, v in mets.items():
            print(f"  {k}: {v:.4f}")

    # If requested and possible, evaluate using logs with labeled source_ids
    if args.use_logs:
        dsn = os.environ.get("DATABASE_URL")
        logs = []
        if dsn:
            import asyncio
            logs = asyncio.get_event_loop().run_until_complete(fetch_rag_queries(dsn, limit=200))
        if logs:
            res_logs = evaluate_on_logs(df, args.baseline, args.finetuned, logs)
            print("\n=== Labeled Retrieval (rag_queries) ===")
            for label, mets in res_logs.items():
                print(label.upper())
                for k, v in mets.items():
                    print(f"  {k}: {v:.4f}")
        else:
            print("\n(rag_queries has no labels or DB not accessible; skipping labeled eval)")

    print("\n=== Qualitative Examples (top-5) ===")
    for ex in qualis:
        print("\n-- Query #", ex["query_idx"], "topic=", ex["query_topic"])
        print("Q:", ex["query"]) 
        print("Baseline:")
        for j, t, txt in ex["baseline"]:
            print(f"  [{j}] topic={t} :: {txt}")
        print("Finetuned:")
        for j, t, txt in ex["finetuned"]:
            print(f"  [{j}] topic={t} :: {txt}")


if __name__ == "__main__":
    main()
