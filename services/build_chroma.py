"""
Build a Chroma vector database from a CSV of chunks.

Usage:
  python services/build_chroma.py --input data/dataaaaaa.csv --db ./mitek_chroma_db --model sentence-transformers/paraphrase-multilingual-mpnet-base-v2 --batch-size 32

This script does NOT call any LLM. It only computes embeddings and persists a Chroma DB.
"""
import argparse
import os
import logging
from pathlib import Path
import pandas as pd

try:
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception as e:
    logging.error("Missing dependencies: please install 'langchain_community' and 'langchain_huggingface' and their requirements.")
    raise

import torch

logger = logging.getLogger("services.build_chroma")
logging.basicConfig()


def pick_text_column(df: pd.DataFrame):
    priority = ["doc", "content", "text", "chunk", "body", "contents"]
    for c in df.columns:
        if c.lower() in priority:
            return c
    return max(df.columns, key=lambda c: df[c].astype(str).str.len().sum())


def build_db(input_csv: str, db_dir: str, model_name: str, batch_size: int = 32):
    input_path = Path(input_csv)
    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    df = pd.read_csv(input_path, header=0)
    text_col = pick_text_column(df)
    texts = df[text_col].astype(str).tolist()
    ids = [str(i) for i in range(len(texts))]

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    logger.info("Using device=%s for embeddings. Model=%s", device, model_name)

    embeddings = HuggingFaceEmbeddings(
        model_name=model_name,
        model_kwargs={'device': device},
        encode_kwargs={'batch_size': batch_size}
    )

    db_path = Path(db_dir)
    logger.info("Persisting Chroma DB to %s", db_path)
    # create or overwrite
    chroma = Chroma.from_texts(texts=texts, embedding=embeddings, metadatas=[{"source_id": i} for i in ids], persist_directory=str(db_path))
    logger.info("Chroma DB built and persisted.")


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--input", default="data/dataaaaaa.csv")
    p.add_argument("--db", default="./mitek_chroma_db")
    p.add_argument("--model", default="sentence-transformers/paraphrase-multilingual-mpnet-base-v2")
    p.add_argument("--batch-size", type=int, default=32)
    args = p.parse_args()

    build_db(args.input, args.db, args.model, args.batch_size)


if __name__ == "__main__":
    main()
