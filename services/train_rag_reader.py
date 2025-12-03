"""
Fine-tune a seq2seq reader for RAG using contexts retrieved from a Chroma DB.

Requirements:
  pip install transformers datasets langchain langchain-community langchain-huggingface chromadb sentence-transformers accelerate

Usage example:
  python services/train_rag_reader.py \
    --qa-file data/qa_pairs.csv \
    --chroma-db ./mitek_chroma_db \
    --embedding-model sentence-transformers/paraphrase-multilingual-mpnet-base-v2 \
    --model google/mt5-small \
    --out-dir ./models/rag_reader --epochs 3 --per-device-batch-size 8

Input QA file: CSV with columns `question` and `answer` (header required).
The script will retrieve top-k contexts for each question and build seq2seq training pairs.
"""
import os
from pathlib import Path
import argparse
import pandas as pd
from typing import List

import torch
from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM,
    DataCollatorForSeq2Seq,
    Seq2SeqTrainer,
    Seq2SeqTrainingArguments,
)
from datasets import Dataset

try:
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception as e:
    print("Missing LangChain/Chroma dependencies. Install langchain_community and langchain_huggingface.")
    raise


def load_qa_pairs(path: str):
    df = pd.read_csv(path)
    if 'question' not in df.columns or 'answer' not in df.columns:
        raise ValueError("QA file must contain 'question' and 'answer' columns")
    return df[['question', 'answer']]


def build_retriever(chroma_dir: str, embedding_model: str):
    # load embeddings and chroma
    embeddings = HuggingFaceEmbeddings(model_name=embedding_model, model_kwargs={'device': 'cpu'})
    db = Chroma(persist_directory=chroma_dir, embedding_function=embeddings)
    retriever = db.as_retriever(search_kwargs={'k': 3})
    return retriever


def prepare_training_examples(df: pd.DataFrame, retriever, tokenizer, max_input_length=512):
    inputs = []
    targets = []
    for _, row in df.iterrows():
        q = str(row['question'])
        a = str(row['answer'])
        # retrieve contexts
        docs = retriever.get_relevant_documents(q)
        context = "\n---\n".join([d.page_content for d in docs])[:2000]
        input_text = f"context: {context}\nquestion: {q}"
        # tokenize to ensure length
        tok = tokenizer(input_text, truncation=True, max_length=max_input_length)
        inputs.append(input_text)
        targets.append(a)
    return inputs, targets


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--qa-file', required=True)
    p.add_argument('--chroma-db', required=True)
    p.add_argument('--embedding-model', default='sentence-transformers/paraphrase-multilingual-mpnet-base-v2')
    p.add_argument('--model', default='google/mt5-small')
    p.add_argument('--out-dir', default='./models/rag_reader')
    p.add_argument('--epochs', type=int, default=3)
    p.add_argument('--per-device-batch-size', type=int, default=8)
    p.add_argument('--max-input-length', type=int, default=512)
    p.add_argument('--max-target-length', type=int, default=128)
    args = p.parse_args()

    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    print('Device:', device)

    df = load_qa_pairs(args.qa_file)
    print('Loaded', len(df), 'QA examples')

    print('Loading retriever (Chroma)...')
    retriever = build_retriever(args.chroma_db, args.embedding_model)

    print('Loading seq2seq model and tokenizer:', args.model)
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSeq2SeqLM.from_pretrained(args.model).to(device)

    print('Preparing training examples (retrieving contexts)')
    inputs, targets = prepare_training_examples(df, retriever, tokenizer, max_input_length=args.max_input_length)

    ds = Dataset.from_dict({'input_text': inputs, 'target_text': targets})

    def preprocess(batch):
        enc = tokenizer(batch['input_text'], truncation=True, max_length=args.max_input_length, padding='max_length')
        with tokenizer.as_target_tokenizer():
            labels = tokenizer(batch['target_text'], truncation=True, max_length=args.max_target_length, padding='max_length')
        enc['labels'] = labels['input_ids']
        return enc

    ds = ds.map(preprocess, batched=True, remove_columns=['input_text', 'target_text'])

    data_collator = DataCollatorForSeq2Seq(tokenizer, model=model)

    training_args = Seq2SeqTrainingArguments(
        output_dir=args.out_dir,
        evaluation_strategy='no',
        per_device_train_batch_size=args.per_device_batch_size,
        num_train_epochs=args.epochs,
        save_total_limit=2,
        predict_with_generate=True,
        fp16=torch.cuda.is_available(),
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        data_collator=data_collator,
        tokenizer=tokenizer,
    )

    print('Starting training...')
    trainer.train()
    print('Saving model to', args.out_dir)
    trainer.save_model(args.out_dir)


if __name__ == '__main__':
    main()
"""
Train a RAG reader (seq2seq) by retrieving top-k contexts from a Chroma DB
and fine-tuning a seq2seq model (default: google/mt5-small).

Requirements:
  pip install transformers datasets sentence-transformers langchain-huggingface langchain-community accelerate

Usage example:
  python services/train_rag_reader.py \
    --train data/train_qa.csv \
    --chroma-dir ./mitek_chroma_db \
    --model google/mt5-small \
    --output ./models/rag_reader --epochs 3 --per-device-batch-size 8 --max-source-len 512 --max-target-len 128

Input `train` CSV must have columns: `question`,`answer` (header row).
"""
import argparse
import os
from pathlib import Path
import pandas as pd
import math

from transformers import (
    AutoTokenizer,
    AutoModelForSeq2SeqLM,
    Seq2SeqTrainingArguments,
    Seq2SeqTrainer,
    DataCollatorForSeq2Seq,
)

from datasets import Dataset

try:
    from langchain_community.vectorstores import Chroma
    from langchain_huggingface import HuggingFaceEmbeddings
except Exception:
    print("Missing LangChain/Chroma dependencies. Please install 'langchain_community' and 'langchain_huggingface'.")
    raise

import torch


def build_dataset(df, retriever, tokenizer, max_source_len=512, max_target_len=128, k=3):
    inputs = []
    targets = []
    for i, row in df.iterrows():
        q = str(row['question']).strip()
        a = str(row['answer']).strip()
        docs = retriever.get_relevant_documents(q)
        # docs may be list of Document objects or dicts
        contexts = []
        for d in docs[:k]:
            txt = d.page_content if hasattr(d, 'page_content') else (d.get('text') if isinstance(d, dict) else str(d))
            contexts.append(txt)
        context_text = "\n\n".join(contexts)
        src = f"question: {q}\n\ncontext: {context_text}"
        inputs.append(src)
        targets.append(a)

    ds = Dataset.from_dict({"input_texts": inputs, "labels": targets})

    def tokenize_fn(x):
        model_inputs = tokenizer(x['input_texts'], truncation=True, padding='max_length', max_length=max_source_len)
        labels = tokenizer(x['labels'], truncation=True, padding='max_length', max_length=max_target_len)
        model_inputs['labels'] = labels['input_ids']
        return model_inputs

    ds_tok = ds.map(tokenize_fn, batched=True, remove_columns=['input_texts', 'labels'])
    return ds_tok


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--train', required=True, help='CSV with question,answer columns')
    p.add_argument('--chroma-dir', default='./mitek_chroma_db')
    p.add_argument('--model', default='google/mt5-small')
    p.add_argument('--output', default='./models/rag_reader')
    p.add_argument('--epochs', type=int, default=3)
    p.add_argument('--per-device-batch-size', type=int, default=8)
    p.add_argument('--lr', type=float, default=5e-5)
    p.add_argument('--max-source-len', type=int, default=512)
    p.add_argument('--max-target-len', type=int, default=128)
    p.add_argument('--k', type=int, default=3, help='Top-k contexts to retrieve')
    args = p.parse_args()

    df = pd.read_csv(args.train)
    if 'question' not in df.columns or 'answer' not in df.columns:
        raise ValueError('Train CSV must contain question and answer columns')

    # init embeddings + chroma retriever
    device = 'cuda' if torch.cuda.is_available() else 'cpu'
    embeddings = HuggingFaceEmbeddings(model_name='sentence-transformers/paraphrase-multilingual-mpnet-base-v2', model_kwargs={'device': device})
    vect = Chroma(persist_directory=args.chroma_dir, embedding_function=embeddings)
    retriever = vect.as_retriever(search_kwargs={'k': args.k})

    # tokenizer + model
    tokenizer = AutoTokenizer.from_pretrained(args.model)
    model = AutoModelForSeq2SeqLM.from_pretrained(args.model)

    ds = build_dataset(df, retriever, tokenizer, max_source_len=args.max_source_len, max_target_len=args.max_target_len, k=args.k)

    data_collator = DataCollatorForSeq2Seq(tokenizer, model=model)

    training_args = Seq2SeqTrainingArguments(
        output_dir=args.output,
        num_train_epochs=args.epochs,
        per_device_train_batch_size=args.per_device_batch_size,
        learning_rate=args.lr,
        save_total_limit=2,
        predict_with_generate=True,
        fp16=torch.cuda.is_available(),
    )

    trainer = Seq2SeqTrainer(
        model=model,
        args=training_args,
        train_dataset=ds,
        tokenizer=tokenizer,
        data_collator=data_collator,
    )

    trainer.train()
    trainer.save_model(args.output)


if __name__ == '__main__':
    main()
