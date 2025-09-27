from __future__ import annotations
import os
from typing import List, Tuple, Optional

from langchain_openai import OpenAIEmbeddings
from langchain_community.vectorstores import Chroma, Pinecone as LC_Pinecone, Qdrant as LC_Qdrant

def build_vectorstore_from_chunks(chunks, embed_model: str = "text-embedding-3-small"):
    """Return (retriever, n_chunks, provider_used).
    Provider is selected via VECTOR_DB_PROVIDER env: 'pinecone' | 'qdrant' | 'chroma' (default).
    """
    provider = (os.getenv("VECTOR_DB_PROVIDER") or "pinecone").lower()
    embeddings = OpenAIEmbeddings(model=embed_model)

    if provider == "pinecone":
        # Requires: pinecone-client, PINECONE_API_KEY, PINECONE_ENV, PINECONE_INDEX
        import pinecone
        api_key = os.getenv("PINECONE_API_KEY")
        env = os.getenv("PINECONE_ENV")
        index_name = os.getenv("PINECONE_INDEX", "sports_ai")
        if not api_key or not env:
            # fallback to chroma if not configured
            vectordb = Chroma.from_documents(chunks, embeddings, persist_directory=None)
            return vectordb.as_retriever(search_kwargs={"k": 5}), len(chunks), "chroma (fallback)"
        pinecone.init(api_key=api_key, environment=env)
        vectordb = LC_Pinecone.from_documents(chunks, embeddings, index_name=index_name)
        return vectordb.as_retriever(search_kwargs={"k": 5}), len(chunks), "pinecone"

    if provider == "qdrant":
        # Requires: qdrant-client, QDRANT_URL, QDRANT_API_KEY, QDRANT_COLLECTION
        from qdrant_client import QdrantClient
        url = os.getenv("QDRANT_URL")
        api_key = os.getenv("QDRANT_API_KEY")
        collection = os.getenv("QDRANT_COLLECTION", "sports_ai")
        if not url:
            vectordb = Chroma.from_documents(chunks, embeddings, persist_directory=None)
            return vectordb.as_retriever(search_kwargs={"k": 5}), len(chunks), "chroma (fallback)"
        client = QdrantClient(url=url, api_key=api_key)
        vectordb = LC_Qdrant.from_documents(chunks, embeddings, client=client, collection_name=collection)
        return vectordb.as_retriever(search_kwargs={"k": 5}), len(chunks), "qdrant"

    # default chroma (in-memory by default)
    vectordb = Chroma.from_documents(chunks, embeddings, persist_directory=None)
    return vectordb.as_retriever(search_kwargs={"k": 5}), len(chunks), "chroma"
