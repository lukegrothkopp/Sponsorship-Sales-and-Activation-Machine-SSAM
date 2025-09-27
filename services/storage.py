from __future__ import annotations
import os
from pathlib import Path
from typing import List, Tuple
from langchain_community.document_loaders import PyPDFLoader
from langchain.text_splitter import RecursiveCharacterTextSplitter
from .vectorstores import build_vectorstore_from_chunks
from .s3store import s3_enabled, upload_bytes

EMBED_MODEL = os.getenv("EMBED_MODEL", "text-embedding-3-small")

def build_contract_store(pdf_paths: List[str], persist_dir: str | None = None):
    docs = []
    for p in pdf_paths:
        if not p.lower().endswith(".pdf") or not Path(p).exists():
            continue
        # Optionally upload original to S3 for persistence
        if s3_enabled():
            try:
                with open(p, "rb") as fh:
                    key = f"contracts/{Path(p).name}"
                    upload_bytes(key, fh.read(), content_type="application/pdf")
            except Exception as e:
                print("S3 upload warning:", e)
        loader = PyPDFLoader(p)
        docs.extend(loader.load())
    if not docs:
        return None, 0, "none"
    splitter = RecursiveCharacterTextSplitter(chunk_size=1000, chunk_overlap=150)
    chunks = splitter.split_documents(docs)
    retriever, n_chunks, provider = build_vectorstore_from_chunks(chunks, embed_model=EMBED_MODEL)
    return retriever, n_chunks, provider
