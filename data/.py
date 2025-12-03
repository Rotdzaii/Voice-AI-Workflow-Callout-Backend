import pandas as pd
import re
from pathlib import Path

INPUT_FILE = "C:\\Users\\Admin\\MyProject\\va\\data\\dataaaaaa.csv"
OUTPUT_FILE = "C:\\Users\\Admin\\MyProject\\va\\data\\data_cleaned_chunked.csv"

# Tách câu bằng regex chuẩn

def split_sentences(text):
    # bỏ ký tự lạ
    text = text.replace("“", "\"").replace("”", "\"").replace("’", "'").replace("•", "")
    text = text.replace("\n", " ").replace("\r", " ").strip()
    text = re.sub(r"\s+", " ", text)

    # tách câu: ., ?, !
    sentences = re.split(r'(?<=[.!?])\s+', text)
    return [s.strip() for s in sentences if len(s.strip()) > 0]

def chunk_sentences(sentences, max_chars=350, min_chars=120):
    """Gom nhiều câu thành 1 chunk, tối ưu cho RAG"""
    chunks = []
    current = ""

    for sent in sentences:
        # Nếu thêm câu mà không vượt max_chars → tiếp tục ghép
        if len(current) + len(sent) + 1 <= max_chars:
            current += " " + sent
        else:
            # Nếu current quá ngắn → ghép tiếp, không tách
            if len(current) < min_chars:
                current += " " + sent
            else:
                chunks.append(current.strip())
                current = sent

    # thêm phần cuối
    if current.strip():
        chunks.append(current.strip())

    return chunks


df = pd.read_csv(INPUT_FILE, header=None)
df.columns = ["id", "topic", "contents"]

new_rows = []
current_id = 1

for _, row in df.iterrows():
    text = str(row["contents"])
    sentences = split_sentences(text)
    chunks = chunk_sentences(sentences)

    for c in chunks:
        new_rows.append([current_id, row["topic"], c])
        current_id += 1

clean_df = pd.DataFrame(new_rows, columns=["id", "topic", "contents"])
clean_df.to_csv(OUTPUT_FILE, index=False, encoding="utf-8")

print(f"✔ Làm sạch và chunk xong! Saved → {OUTPUT_FILE}")
print(clean_df.head(10))
