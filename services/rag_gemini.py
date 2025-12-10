"""
RAG + Gemini deployment runner

Place this file in the project as an entrypoint to build a simple RAG system
using a local Chroma vectorstore + a Gemini LLM wrapper.

Usage:
  - Set environment variable `GEMINI_API_KEY` with your API key.
  - Adjust `INPUT_CLEANED_DATA` path to point to your cleaned chunk CSV.
  - Run: `python services/rag_gemini.py`

Notes:
  - This script expects several optional packages (LangChain community, langchain_huggingface, chroma),
    and a working `torch` install. It will print helpful errors if imports are missing.
"""

import os
import logging
import pandas as pd
import requests
import json
import time
from pathlib import Path

logger = logging.getLogger("services.rag_gemini")
logging.basicConfig()

# --- Thư viện Google GenAI ---
try:
    import google.generativeai as genai
except ImportError:
    # Nếu chạy script này, bạn phải cài đặt thư viện trước đó
    logger.error("Missing library 'google-generativeai'. Please install and retry.")
    raise

# --- LangChain & RAG Components ---
try:
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
    from langchain_core.runnables import RunnablePassthrough
    from langchain_core.output_parsers import StrOutputParser
    from langchain_core.prompts import ChatPromptTemplate
except ImportError:
    logger.error("Missing LangChain dependencies. Please install required packages and retry.")
    raise

import torch

# =========================================================================
## 1. CẤU HÌNH CHUNG VÀ LOGIC
# =========================================================================

# THAY THẾ KEY GEMINI CỦA BẠN VÀO ĐÂY
GEMINI_KEY = os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
LLM_MODEL_NAME = "gemini-2.0-flash"

# --- ĐƯỜNG DẪN FILE ---
INPUT_CLEANED_DATA = Path("mitek_chunks_cleaned.csv")

# --- CẤU HÌNH RAG VÀ EMBEDDING ---
CHROMA_DB_PATH = "./mitek_chroma_db"
EMBEDDING_MODEL_NAME = "sentence-transformers/paraphrase-multilingual-mpnet-base-v2"
MAX_RETRIEVED_CHUNKS = 1  # speed
BATCH_SIZE_GPU = 64


# =========================================================================
## 2. CUSTOM LLM GEMINI
# =========================================================================

class GeminiLLM:
    """Class tùy chỉnh để kết nối Gemini theo giao diện đơn giản."""
    def __init__(self, api_key, model_name):
        self.model_name = model_name
        if not api_key:
            raise ValueError("Vui lòng cung cấp GEMINI_API_KEY hợp lệ.")
        genai.configure(api_key=api_key)
        self.client = genai.GenerativeModel(model_name)

    def invoke(self, prompt_data):
        prompt_text = str(prompt_data)
        try:
            response = self.client.generate_content(prompt_text, generation_config={"temperature": 0.0})
            return getattr(response, "text", str(response))
        except Exception as e:
            logger.exception("Gemini API call failed: %s", e)
            return "Xin lỗi, tôi gặp lỗi kỹ thuật. Vui lòng kiểm tra khóa API hoặc số dư và thử lại."

    def __call__(self, prompt_data):
        return self.invoke(prompt_data)


# =========================================================================
## 3. TRIỂN KHAI RAG
# =========================================================================

def setup_rag_system(llm_class, input_file):
    logger.info("PHASE 2: Deploying RAG system")
    try:
        df = pd.read_csv(input_file, header=None)
        df.columns = ['id', 'topic', 'contents']
        documents = df['contents'].astype(str).tolist()
        sources = df['id'].astype(str).tolist()
        if not documents:
            logger.error("Input data file is empty: %s", input_file)
            return None, None
        logger.info("Loaded %d RAG chunks for embedding.", len(documents))
    except Exception as e:
        logger.exception("Error reading CSV file %s: %s", input_file, e)
        return None, None
    logger.info("Initializing embedding model (GPU if available)")
    try:
        embeddings = HuggingFaceEmbeddings(
            model_name=EMBEDDING_MODEL_NAME,
            model_kwargs={'device': 'cuda'} if torch.cuda.is_available() else {'device': 'cpu'},
            encode_kwargs={'batch_size': BATCH_SIZE_GPU, 'convert_to_tensor': True}
        )
        logger.info("Embedding model configured (device=%s)", 'cuda' if torch.cuda.is_available() else 'cpu')
    except Exception as e:
        logger.exception("Error loading embedding model: %s", e)
        return None, None

    if os.path.exists(CHROMA_DB_PATH):
        logger.info("Using existing ChromaDB at %s", CHROMA_DB_PATH)
        vectorstore = Chroma(persist_directory=CHROMA_DB_PATH, embedding_function=embeddings)
    else:
        logger.info("Creating new ChromaDB at %s and embedding %d chunks...", CHROMA_DB_PATH, len(documents))
        metadatas = [{"source_id": s} for s in sources]
        vectorstore = Chroma.from_texts(texts=documents, embedding=embeddings, metadatas=metadatas, persist_directory=CHROMA_DB_PATH)
        logger.info("Created and persisted ChromaDB at %s", CHROMA_DB_PATH)

    retriever = vectorstore.as_retriever(search_kwargs={"k": MAX_RETRIEVED_CHUNKS})
    gemini_llm = llm_class(api_key=GEMINI_KEY, model_name=LLM_MODEL_NAME)
    return gemini_llm, retriever


def create_rag_chain(llm, retriever):
    prompt_template = """
    Bạn là một trợ lý RAG chuyên nghiệp, trả lời câu hỏi của người dùng bằng tiếng Việt với giọng điệu **chuyên nghiệp, thân thiện và gần gũi như con người**.
    \n    QUY TẮC GIAO TIẾP: ...
    \n    Ngữ cảnh:
    ---
    {context}
    ---
    \n    Câu hỏi: {question}
    """
    RAG_PROMPT = ChatPromptTemplate.from_template(prompt_template)
    rag_chain = (
        {"context": retriever, "question": RunnablePassthrough()} 
        | RAG_PROMPT
        | llm
        | StrOutputParser()
    )
    return rag_chain


def start_chat(rag_chain, llm_name):
    logger.info("RAG chat ready. LLM=%s, DB=ChromaDB", llm_name)
    print("\n--- CHẾ ĐỘ CHAT RAG ĐÃ SẴN SÀNG (TỐC ĐỘ TỐI ĐA) ---")
    print(f"LLM: {llm_name}. Database: ChromaDB. (Gõ 'exit' để thoát)")
    while True:
        question = input("\nBạn hỏi (MITEK): ")
        if question.lower() == 'exit':
            logger.info("Exiting chat")
            break
        if not question.strip():
            continue
        logger.info("Generating answer for question")
        start_time = time.time()
        response = rag_chain.invoke(question)
        end_time = time.time()
        print("\n--- TRẢ LỜI ---")
        print(response)
        logger.info("Response time: %.2f sec", end_time - start_time)


if __name__ == "__main__":
    if GEMINI_KEY is None or GEMINI_KEY == "":
        logger.warning("GEMINI_API_KEY not found in environment")

    try:
        FILE_FOR_RAG_INPUT = INPUT_CLEANED_DATA
        logger.info("Running RAG deployment with input: %s", FILE_FOR_RAG_INPUT)
        gemini_llm, retriever = setup_rag_system(GeminiLLM, FILE_FOR_RAG_INPUT)
        if gemini_llm and retriever:
            rag_chain = create_rag_chain(gemini_llm, retriever)
            start_chat(rag_chain, LLM_MODEL_NAME)
    except Exception as e:
        logger.exception("RAG deployment failed: %s", e)