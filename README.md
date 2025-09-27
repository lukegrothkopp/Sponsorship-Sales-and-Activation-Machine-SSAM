# SSAM (S3 + Pinecone/Qdrant)

Streamlit starter for a Trak-style workflow with **S3 persistence** and pluggable vector DBs (**Pinecone**, **Qdrant**, or fallback **Chroma**).

## Quickstart
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
# macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt
streamlit run app.py
```

## Configure
Set secrets/env (Streamlit Cloud → Settings → Secrets, or local env):
```toml
OPENAI_API_KEY = "sk-..."

# S3 (optional – enables uploads persistence + presigned links)
S3_BUCKET = "your-bucket"
AWS_ACCESS_KEY_ID = "..."
AWS_SECRET_ACCESS_KEY = "..."
AWS_DEFAULT_REGION = "us-east-1"

# Vector DB (choose one)
VECTOR_DB_PROVIDER = "pinecone"  # or "qdrant" or "chroma"

# Pinecone
PINECONE_API_KEY = "..."
PINECONE_ENV = "us-east-1-aws"   # your env
PINECONE_INDEX = "sports_ai"

# Qdrant
QDRANT_URL = "https://your-qdrant-url"
QDRANT_API_KEY = "..."
QDRANT_COLLECTION = "sports_ai"
```

**Tip:** If Pinecone/Qdrant isn’t configured, the app falls back to **Chroma** in-memory.

## What’s included
- Prospecting (SponsorUnited stub), LLM pitch insights
- Activation logging with optional **S3 uploads** and presigned links
- Contract Q&A: PDF ingestion → embeddings → retrieval with your chosen vector DB
- Partner dashboard: Tableau embed link or native KPIs

> Replace API stubs in `services/providers.py` with real integrations when ready.
