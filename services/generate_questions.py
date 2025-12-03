import os
import re
import random
import argparse
import pandas as pd
from pathlib import Path
from dotenv import load_dotenv

# Generate synthetic questions from chunk contents using templates
# Usage:
#   python services/generate_questions.py --input rag_clean_chunks.csv --output data/synthetic_questions.csv --count 300

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
    if "id" not in df.columns:
        df["id"] = [str(i) for i in range(len(df))]
    df["id"] = df["id"].astype(str)
    if "topic" not in df.columns:
        df["topic"] = "_"
    if "group" not in df.columns:
        df["group"] = "_"
    df["contents"] = df["contents"].astype(str)
    return df


# Vietnamese question templates
TEMPLATES = [
    "{keyword} là gì?",
    "Giải thích về {keyword}",
    "Tính năng {keyword}",
    "Lợi ích của {keyword}",
    "{keyword} hoạt động như thế nào?",
    "Hướng dẫn sử dụng {keyword}",
    "Tại sao nên dùng {keyword}?",
    "{keyword} có những ưu điểm gì?",
    "Tôi muốn biết về {keyword}",
    "Chi tiết về {keyword}",
]


def extract_keywords(text: str) -> list[str]:
    # Extract potential keywords: capitalized sequences, product names, technical terms
    words = re.findall(r'\b[A-Z][a-z]+(?:\s+[A-Z][a-z]+)*\b', text)
    # Also look for Vietnamese technical terms
    vn_terms = re.findall(r'\b(?:tổng đài|khách hàng|dịch vụ|hệ thống|phần mềm|giải pháp|tính năng|quản lý|tích hợp|báo cáo|API|CRM|SMS|ticket|agent)\b', text, re.IGNORECASE)
    candidates = list(set(words + vn_terms))
    return [c for c in candidates if len(c) > 3 and len(c) < 50]


def generate_question(content: str, topic: str) -> str:
    keywords = extract_keywords(content)
    if not keywords:
        # Fallback: use first 5-10 words
        words = content.split()[:random.randint(5, 10)]
        return " ".join(words) + "?"
    kw = random.choice(keywords)
    template = random.choice(TEMPLATES)
    return template.format(keyword=kw)


def generate_questions_from_chunks(df: pd.DataFrame, count: int = 300) -> pd.DataFrame:
    questions = []
    # Sample diverse chunks
    sampled = df.sample(n=min(count, len(df)), random_state=42)
    for _, row in sampled.iterrows():
        q = generate_question(row["contents"], row["topic"])
        questions.append({
            "question": q,
            "chunk_id": row["id"],
            "topic": row["topic"],
            "group": row["group"],
            "source_content": row["contents"][:200]  # for reference
        })
    return pd.DataFrame(questions)


def main():
    load_dotenv()
    ap = argparse.ArgumentParser()
    ap.add_argument("--input", default=os.environ.get("CLEAN_CHUNK_FILE", "rag_clean_chunks.csv"))
    ap.add_argument("--output", default="data/synthetic_questions.csv")
    ap.add_argument("--count", type=int, default=300)
    args = ap.parse_args()

    df = read_chunks(args.input)
    print(f"Loaded {len(df)} chunks from {args.input}")

    questions_df = generate_questions_from_chunks(df, count=args.count)
    
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    questions_df.to_csv(output_path, index=False, encoding="utf-8")
    
    print(f"Generated {len(questions_df)} questions → {output_path}")
    print("\nSample questions:")
    for _, row in questions_df.head(5).iterrows():
        print(f"  Q: {row['question']}")
        print(f"     Topic: {row['topic']}, Chunk: {row['chunk_id']}")


if __name__ == "__main__":
    main()
