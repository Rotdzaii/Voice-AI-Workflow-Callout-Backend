
import os
import time
import json
import csv
import logging
from pathlib import Path
from datetime import datetime
import re
import pandas as pd

# ============================================================
# 0. CONFIG
# ============================================================

CLEAN_ENV_RAW = os.environ.get
RAW_INPUT_FILE = os.environ.get("RAW_INPUT_FILE", os.path.join("data", "data_cleaned_chunked.csv"))
# Sanitize potential quoted/backslash paths from .env on Windows
RAW_INPUT_FILE = (RAW_INPUT_FILE or "").strip().strip('"')
RAW_INPUT_FILE = os.path.expandvars(RAW_INPUT_FILE)
if not os.path.isabs(RAW_INPUT_FILE):
    RAW_INPUT_FILE = os.path.join(os.getcwd(), RAW_INPUT_FILE)

CLEAN_CHUNK_FILE = os.environ.get("CLEAN_CHUNK_FILE", "rag_clean_chunks.csv")
CLEAN_CHUNK_FILE = (CLEAN_CHUNK_FILE or "").strip().strip('"')
CHROMA_DB_PATH = os.environ.get("CHROMA_DB_PATH", "./mitek_chroma_db")
LOG_FILE_PATH = os.environ.get("LOG_FILE_PATH", "rag_logs.csv")

# GEMINI API key should be provided via environment variable for safety
GEMINI_KEY = os.environ.get("GEMINI_KEY")
LLM_MODEL_NAME = os.environ.get("LLM_MODEL_NAME", "gemini-2.0-flash-lite")

EMBEDDING_MODEL_NAME = os.environ.get("EMBEDDING_MODEL_NAME", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
MAX_RETRIEVED_CHUNKS = int(os.environ.get("MAX_RETRIEVED_CHUNKS", 3))
BATCH_SIZE_GPU = int(os.environ.get("BATCH_SIZE_GPU", 32))

# Logger for this module. Default level WARNING to keep backend output clean.
logger = logging.getLogger("rag")
if os.environ.get("RAG_VERBOSE", "0") == "1":
    logger.setLevel(logging.INFO)
else:
    logger.setLevel(logging.WARNING)

# Reduce noisy library loggers by default
logging.getLogger("sentence_transformers").setLevel(logging.WARNING)
logging.getLogger("chromadb").setLevel(logging.WARNING)


# ============================================================
# 1. CLEANING + CHUNKING (DỮ LIỆU THEO group/topic)
# ============================================================

def split_sentences(text):
    text = (text.replace("“", "\"").replace("”", "\"")
                  .replace("’", "'")
                  .replace("\n", " ")
                  .replace("\r", " ")
                  .strip())
    text = re.sub(r"\s+", " ", text)
    return [s.strip() for s in re.split(r'(?<=[.!?])\s+', text) if s.strip()]


def chunk_sentences(sentences, max_chars=350, min_chars=120):
    chunks, current = [], ""

    for sent in sentences:
        if len(current) + len(sent) + 1 <= max_chars:
            current += " " + sent
        else:
            if len(current) < min_chars:
                current += " " + sent
            else:
                chunks.append(current.strip())
                current = sent

    if current.strip():
        chunks.append(current.strip())

    return chunks


def prepare_clean_chunks():
    logger.info("Preparing and chunking data...")

    df = pd.read_csv(
        RAW_INPUT_FILE,
        sep=",",
        engine="python",
        quotechar='"',
        on_bad_lines="warn",
        encoding="utf-8"
    )

    logger.info("Columns read: %s", list(df.columns))

    if set(["id", "group", "topic", "contents"]).issubset(df.columns):
        df = df[["id", "group", "topic", "contents"]]
    elif set(["id", "topic", "contents"]).issubset(df.columns):
        df["group"] = "general"
        df = df[["id", "group", "topic", "contents"]]
    else:
        raise ValueError(f"Bad schema: {list(df.columns)}")

    new_rows = []
    new_id = 1

    for _, row in df.iterrows():
        group = str(row["group"]).strip()
        topic = str(row["topic"]).strip()
        text = str(row["contents"]).strip()

        sentences = split_sentences(text)
        chunks = chunk_sentences(sentences)

        for ch in chunks:
            new_rows.append([new_id, group, topic, ch])
            new_id += 1

    clean_df = pd.DataFrame(new_rows, columns=["id", "group", "topic", "contents"])
    clean_df.to_csv(CLEAN_CHUNK_FILE, index=False, encoding="utf-8")

    logger.info("DONE -> %s", CLEAN_CHUNK_FILE)
    return CLEAN_CHUNK_FILE
# ============================================================
# 2. EMBEDDING + CHROMA
# ============================================================

from langchain_community.vectorstores import Chroma
from langchain_huggingface import HuggingFaceEmbeddings


def build_rag_system(clean_file):
    logger.info("BUILDING RAG SYSTEM...")

    df = pd.read_csv(clean_file)

    documents = df["contents"].astype(str).tolist()
    metadatas = df[["id", "group", "topic"]].astype(str).to_dict(orient="records")

    logger.info("Loaded %d chunks", len(documents))

    # choose device automatically if torch available
    try:
        import torch
        device = "cuda" if torch.cuda.is_available() else "cpu"
    except Exception:
        device = "cpu"

    embeddings = HuggingFaceEmbeddings(
        model_name=EMBEDDING_MODEL_NAME,
        model_kwargs={"device": device},
        encode_kwargs={"batch_size": BATCH_SIZE_GPU, "convert_to_tensor": False}
    )

    if os.path.exists(CHROMA_DB_PATH):
        vectorstore = Chroma(
            persist_directory=CHROMA_DB_PATH,
            embedding_function=embeddings
        )
    else:
        vectorstore = Chroma.from_texts(
            documents,
            embedding=embeddings,
            metadatas=metadatas,
            persist_directory=CHROMA_DB_PATH
        )

    logger.info("ChromaDB ready")
    return vectorstore
# ============================================================
# 3. GEMINI LLM
# ============================================================

import google.generativeai as genai


class GeminiLLM:
    def __init__(self, key, model):
        genai.configure(api_key=key)
        self.client = genai.GenerativeModel(model)

    def invoke(self, prompt):
        try:
            res = self.client.generate_content(
                prompt,
                generation_config={
                    "temperature": 0.0,
                    "top_p": 0.1,
                    "max_output_tokens": 900
                }
            )
            return res.text or ""
        except Exception as e:
            return f"Gemini Error: {e}"
        # ============================================================
# 4. LOGGING
# ============================================================

def init_log():
    if not os.path.exists(LOG_FILE_PATH):
        with open(LOG_FILE_PATH, "w", encoding="utf-8", newline="") as f:
            writer = csv.writer(f)
            writer.writerow([
                "timestamp",
                "question",
                "answer",

                # latency
                "latency_total",
                "latency_retriever",
                "latency_context",
                "latency_prompt",
                "latency_llm",

                # chunk info
                "retrieved_chunks",
                "source_ids",
                "groups",
                "topics",
                "similarity_scores",
                "context_length",
                "answer_tokens",

                # NEW
                "answer_chunk_ids",
                "answer_chunk_positions"
            ])


def write_log(*row):
    with open(LOG_FILE_PATH, "a", encoding="utf-8", newline="") as f:
        csv.writer(f).writerow(row)
# ============================================================
# 5. CHAT LOOP (HIỂN THỊ VỊ TRÍ CHUNK + CÂU LIÊN QUAN)
# ============================================================

def find_relevant_sentences(text, question):
    sentences = re.split(r'(?<=[.!?])\s+', text)
    result = []

    for i, sent in enumerate(sentences):
        if any(w.lower() in sent.lower() for w in question.split()):
            result.append((i+1, sent.strip()))

    return result


BASE_PROMPT = """
Bạn là trợ lý RAG.
Chỉ trả lời dựa vào NGỮ CẢNH bên dưới.

--- NGỮ CẢNH ---
{context}

--- CÂU HỎI ---
{question}

Trả lời rõ ràng, đầy đủ, đúng trọng tâm.
"""


def build_context(docs):
    return "\n\n".join(
        f"[id={d.metadata.get('id')} | group={d.metadata.get('group')} | topic={d.metadata.get('topic')}] "
        f"{d.page_content}"
        for d in docs
    )


def chat(llm, vectorstore):
    init_log()

    print("\n🎉 READY! Type 'exit' to quit.\n")

    while True:
        q = input("❓ Câu hỏi: ").strip()
        if q.lower() == "exit":
            break

        total_start = time.time()

        # ============== RETRIEVAL ==============
        t1 = time.time()
        docs_scores = vectorstore.similarity_search_with_score(q, k=MAX_RETRIEVED_CHUNKS)
        retriever_time = time.time() - t1

        if not docs_scores:
            print("⚠ Không tìm thấy dữ liệu.")
            continue

        docs = [d for d, _ in docs_scores]
        scores = [float(s) for _, s in docs_scores]

        source_ids = [d.metadata.get("id") for d in docs]
        groups = [d.metadata.get("group") for d in docs]
        topics = [d.metadata.get("topic") for d in docs]

        answer_chunk_ids = source_ids
        answer_chunk_positions = list(range(1, len(docs) + 1))

        # ============== BUILD CONTEXT ==============
        t2 = time.time()
        context = build_context(docs)
        context_time = time.time() - t2

        # ============== PROMPT ==============
        t3 = time.time()
        prompt = BASE_PROMPT.format(context=context, question=q)
        prompt_time = time.time() - t3

        # ============== LLM ==============
        t4 = time.time()
        answer = llm.invoke(prompt)
        llm_time = time.time() - t4

        total_time = time.time() - total_start
        context_len = len(context.split())
        answer_tokens = len(answer.split())

        # ============== SHOW ANSWER ==============
        print("\n📝 TRẢ LỜI:")
        print(answer)

        print("\n📌 CHUNK ĐƯỢC DÙNG:")
        for i, d in enumerate(docs):
            print(f" - Chunk #{i+1} | id={d.metadata.get('id')} | group={d.metadata.get('group')} | topic={d.metadata.get('topic')}")

        print("\n🔎 CÂU LIÊN QUAN TRONG CHUNK:")
        for d in docs:
            matches = find_relevant_sentences(d.page_content, q)
            if matches:
                for pos, sent in matches:
                    print(f"   - id={d.metadata.get('id')} | câu {pos}: {sent}")
            else:
                print(f"   - id={d.metadata.get('id')} | không tìm thấy câu liên quan.")

        print("\n⏱ TIME STATS:")
        print(f"Retriever    : {retriever_time:.4f}s")
        print(f"Context Build: {context_time:.4f}s")
        print(f"Prompt Build : {prompt_time:.4f}s")
        print(f"LLM Generate : {llm_time:.4f}s")
        print(f"TOTAL        : {total_time:.4f}s\n")

        # ============== LOGGING ==============
        write_log(
            datetime.now().isoformat(),
            q,
            answer,

            total_time,
            retriever_time,
            context_time,
            prompt_time,
            llm_time,

            len(docs),
            json.dumps(source_ids),
            json.dumps(groups),
            json.dumps(topics),
            json.dumps(scores),
            context_len,
            answer_tokens,

            json.dumps(answer_chunk_ids),
            json.dumps(answer_chunk_positions)
        )
        # ============================================================
# 6. RAG initialisation helper
# ============================================================

def init_rag():
    """Initialise and return (llm, vectorstore).

    This function is safe to import and will build the cleaned chunks,
    embeddings and Chroma DB on demand. Raises RuntimeError if required
    configuration is missing.
    """
    if not os.path.exists(RAW_INPUT_FILE):
        raise RuntimeError(f"RAW_INPUT_FILE not found: {RAW_INPUT_FILE}")

    if not GEMINI_KEY:
        raise RuntimeError("GEMINI_KEY is not set. Export it via environment variable GEMINI_KEY.")

    clean_file = prepare_clean_chunks()
    vectorstore = build_rag_system(clean_file)
    llm = GeminiLLM(GEMINI_KEY, LLM_MODEL_NAME)
    return llm, vectorstore


if __name__ == "__main__":
    try:
        llm, vectorstore = init_rag()
    except Exception as e:
        print(f"Failed to initialise RAG: {e}")
    else:
        chat(llm, vectorstore)