import os
import argparse
import random
import asyncio
import asyncpg
import pandas as pd
import re
from pathlib import Path
from sentence_transformers import SentenceTransformer, InputExample, losses
from torch.utils.data import DataLoader
from dotenv import load_dotenv

# Train a retriever (bi-encoder) on your domain data using triplet loss.
# Positives come from rag_queries.source_ids -> chunks in CLEAN_CHUNK_FILE.
# Negatives are sampled from other chunks (diff topic/group).
# Usage:
#   python services/train_retriever.py --output models/embeddings/ft-emb --epochs 1 --batch 16
# Set DATABASE_URL and ensure CLEAN_CHUNK_FILE exists.


def read_chunks(clean_chunk_file: str) -> pd.DataFrame:
    # Sanitize Windows path from .env (backslash escapes like \v)
    path = (clean_chunk_file or "").strip().strip('"').replace("\\", "/")
    # remove control characters like vertical tab introduced by dotenv escape sequences (e.g., \v)
    path = re.sub(r"[\x00-\x1f]", "", path)
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
    # normalize id to str for matching
    df["id"] = df["id"].astype(str)
    return df


async def fetch_rag_queries(dsn: str) -> list:
    conn = await asyncpg.connect(dsn)
    try:
        rows = await conn.fetch("SELECT question, source_ids FROM rag_queries WHERE array_length(source_ids,1) > 0 ORDER BY created_at DESC LIMIT 5000")
        return [{"question": r[0], "source_ids": [str(x) for x in (r[1] or [])]} for r in rows]
    finally:
        await conn.close()


def build_triplets(queries: list, chunks: pd.DataFrame, max_neg_per_pos: int = 1):
    id_to_row = {str(r["id"]): r for _, r in chunks.iterrows()}
    examples = []
    all_ids = set(id_to_row.keys())

    for q in queries:
        question = (q["question"] or "").strip()
        pos_ids = [str(x) for x in (q["source_ids"] or [])]
        for pid in pos_ids:
            pos_row = id_to_row.get(pid)
            if not pos_row is None:
                pos_text = str(pos_row["contents"])  # positive passage
                # sample negatives
                neg_candidates = list(all_ids - {pid})
                random.shuffle(neg_candidates)
                for neg_id in neg_candidates[:max_neg_per_pos]:
                    neg_row = id_to_row.get(neg_id)
                    if not neg_row is None:
                        neg_text = str(neg_row["contents"])  # negative passage
                        examples.append(InputExample(texts=[question, pos_text, neg_text]))
    return examples


def build_triplets_from_chunks(chunks: pd.DataFrame, max_samples: int = 50000):
    # Weakly-supervised: use topic/group to sample positives; negatives from other topics
    # Requires columns: contents, topic (optional group)
    if "contents" not in chunks.columns:
        raise RuntimeError("CLEAN_CHUNK_FILE must contain a 'contents' column")
    df = chunks.copy()
    # Normalize
    if "topic" not in df.columns:
        df["topic"] = "_"
    df["topic"] = df["topic"].astype(str)
    df["group"] = df.get("group", "_")
    df["group"] = df["group"].astype(str)

    by_topic = {t: g for t, g in df.groupby("topic")}
    topics = list(by_topic.keys())
    all_indices = df.index.tolist()
    examples = []

    # For each topic, sample pairs within and negatives outside
    for t in topics:
        g = by_topic[t]
        if len(g) < 2:
            continue
        rows = g.sample(n=min(len(g), 50), random_state=42) if len(g) > 50 else g
        others_idx = list(set(all_indices) - set(rows.index.tolist()))
        if not others_idx:
            continue
        for i in range(0, len(rows) - 1):
            anchor = str(rows.iloc[i]["contents"])[:2000]
            positive = str(rows.iloc[i + 1]["contents"])[:2000]
            neg_row = df.loc[random.choice(others_idx)]
            negative = str(neg_row["contents"])[:2000]
            if anchor and positive and negative and positive != negative:
                examples.append(InputExample(texts=[anchor, positive, negative]))
            if len(examples) >= max_samples:
                break
        if len(examples) >= max_samples:
            break
    return examples


def main():
    # Load environment variables from .env if present
    load_dotenv()
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True, help="Output directory for fine-tuned model")
    parser.add_argument("--epochs", type=int, default=3)
    parser.add_argument("--batch", type=int, default=16)
    parser.add_argument("--model", default=os.environ.get("EMBEDDING_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"))
    parser.add_argument("--clean-chunk-file", default=os.environ.get("CLEAN_CHUNK_FILE", "rag_clean_chunks.csv"))
    parser.add_argument("--limit", type=int, default=5000)
    parser.add_argument("--fallback-samples", type=int, default=5000, help="Max triplets to build from chunks when DB labels are missing")
    parser.add_argument("--val-split", type=float, default=0.2, help="Validation split ratio")
    args = parser.parse_args()

    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        raise RuntimeError("DATABASE_URL not set")

    chunks = read_chunks(args.clean_chunk_file)

    queries = asyncio.get_event_loop().run_until_complete(fetch_rag_queries(dsn))
    if args.limit:
        queries = queries[:args.limit]

    triplets = build_triplets(queries, chunks)
    if not triplets:
        print("No triplets from rag_queries; falling back to chunk-based triplets...")
        triplets = build_triplets_from_chunks(chunks, max_samples=args.fallback_samples)
        if not triplets:
            raise RuntimeError("No training triplets built from DB nor chunks. Check data.")

    # Train/val split
    random.shuffle(triplets)
    val_size = int(len(triplets) * args.val_split)
    train_triplets = triplets[val_size:]
    val_triplets = triplets[:val_size]
    print(f"Train: {len(train_triplets)}, Val: {len(val_triplets)} triplets")

    model = SentenceTransformer(args.model)
    train_dataloader = DataLoader(train_triplets, shuffle=True, batch_size=args.batch)
    train_loss = losses.TripletLoss(model)

    # Validation evaluator (optional, simple metric)
    from sentence_transformers.evaluation import TripletEvaluator
    evaluator = None
    if val_triplets:
        evaluator = TripletEvaluator.from_input_examples(val_triplets, name='val')

    Path(args.output).mkdir(parents=True, exist_ok=True)
    model.fit(
        train_objectives=[(train_dataloader, train_loss)],
        epochs=args.epochs,
        warmup_steps=100,
        evaluator=evaluator,
        evaluation_steps=len(train_dataloader) // 2,
        output_path=args.output,
        save_best_model=True,
        show_progress_bar=True
    )

    print(f"Saved fine-tuned retriever to {args.output}")


if __name__ == "__main__":
    main()
