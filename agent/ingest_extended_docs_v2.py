"""
ingest_extended_docs_v2.py — EXPERIMENTAL. Populate ChromaDB with the same
extended ISP knowledge corpus as ingest_extended_docs.py, but into a
separate collection embedded with a multilingual model, to test cross-
lingual (French query vs English documents) retrieval quality.

Fully separate from ingest_extended_docs.py and ingest_docs.py: different
collection ("isp_knowledge_extended_v2"), same source file. Does not touch
"isp_knowledge_extended" (v1) or "isp_troubleshooting" in any way.

Run once (or after any update to data/kb/extended_mock.json):
    docker exec -it langchain-agent python ingest_extended_docs_v2.py

Embedding model: SentenceTransformerEmbeddingFunction. Uses a local copy at
/models/... if one is mounted, otherwise downloads the HF repo id on first use
(cached in the hf_cache Docker volume).
Must match rag_tools.py's EXTENDED_V2_MODEL_NAME.
"""

import json
import os

import chromadb
from chromadb.utils import embedding_functions

CHROMA_URL      = os.getenv("CHROMA_URL", "http://chromadb:8000")
COLLECTION_NAME = "isp_knowledge_extended_v2"
_LOCAL_MODEL_PATH = "/models/paraphrase-multilingual-MiniLM-L12-v2"
_HF_MODEL_NAME     = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
MODEL_NAME = _LOCAL_MODEL_PATH if os.path.isdir(_LOCAL_MODEL_PATH) else _HF_MODEL_NAME
SOURCE_FILE     = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "data", "kb", "extended_mock.json"
)


def _load_documents():
    with open(SOURCE_FILE, encoding="utf-8") as f:
        return json.load(f)


def ingest():
    documents = _load_documents()

    # Parse host and port from CHROMA_URL
    url  = CHROMA_URL.replace("http://", "").replace("https://", "")
    host = url.split(":")[0]
    port = int(url.split(":")[-1]) if ":" in url else 8000

    client = chromadb.HttpClient(host=host, port=port)

    # Must match rag_tools.py's EXTENDED_V2_MODEL_NAME
    ef = embedding_functions.SentenceTransformerEmbeddingFunction(model_name=MODEL_NAME)

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        embedding_function=ef,
        metadata={"hnsw:space": "cosine"},
    )

    ids       = [d["id"]       for d in documents]
    contents  = [d["content"]  for d in documents]
    metadatas = [d["metadata"] for d in documents]

    # Upsert — safe to run multiple times, updates existing docs
    collection.upsert(documents=contents, ids=ids, metadatas=metadatas)

    print(f"✅ Ingested {len(documents)} documents into '{COLLECTION_NAME}' (model: {MODEL_NAME})")
    print(f"   Collection total: {collection.count()} documents")
    print()
    print("Documents ingested:")
    for d in documents:
        print(f"  - {d['id']} [{d['metadata'].get('category', 'n/a')}]")


if __name__ == "__main__":
    ingest()
