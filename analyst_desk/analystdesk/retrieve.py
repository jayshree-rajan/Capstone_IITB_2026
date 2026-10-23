"""Hybrid retrieval — BM25 + Chroma vectors + RRF + cross-encoder reranker.

Pipeline for one query:
  1. BM25 search over all passage texts          (keyword matching)
  2. Chroma vector search with local embeddings  (semantic matching)
  3. Reciprocal Rank Fusion to merge both lists  (no weight tuning needed)
  4. Cross-encoder reranker on top-k results     (precise relevance scoring)

Why each step:
  BM25   — catches exact terminology (e.g. "goodwill impairment", "CIK")
  Chroma — catches semantic similarity even when wording differs
  RRF    — combines without needing to tune score scales
  Rerank — cross-encoder sees query + passage together, much more accurate
           but too slow to run on all passages, so only on top-k after RRF
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

INDEX_DIR = Path(__file__).parent.parent / "data" / "index"

_retriever = None


def _rrf(bm25_hits: list[str], vec_hits: list[str], k: int = 60) -> list[str]:
    """Reciprocal Rank Fusion — merge two ranked lists into one.

    RRF score = sum of 1/(k + rank) across lists.
    k=60 is the standard default (Robertson & Zaragoza 2009).
    Returns passage_ids sorted by descending RRF score.
    """
    scores: dict[str, float] = {}
    for rank, pid in enumerate(bm25_hits, 1):
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank)
    for rank, pid in enumerate(vec_hits, 1):
        scores[pid] = scores.get(pid, 0) + 1 / (k + rank)
    return sorted(scores, key=lambda p: scores[p], reverse=True)


class HybridRetriever:
    def __init__(self, passages: list[dict]):
        from rank_bm25 import BM25Okapi
        import chromadb
        from sentence_transformers import SentenceTransformer, CrossEncoder

        self.passages = passages
        self.pid_to_passage = {p["passage_id"]: p for p in passages}
        texts = [p["text"] for p in passages]
        pids = [p["passage_id"] for p in passages]

        # BM25 over tokenised passage texts
        tokenised = [t.lower().split() for t in texts]
        self.bm25 = BM25Okapi(tokenised)
        self.pids = pids

        # Chroma vector store with local MiniLM embeddings (no API cost)
        print("  Loading embedding model (first run downloads ~90MB)...")
        self.embedder = SentenceTransformer("all-MiniLM-L6-v2")
        self.reranker = CrossEncoder("cross-encoder/ms-marco-MiniLM-L-6-v2")

        client = chromadb.PersistentClient(path=str(INDEX_DIR))
        self.collection = client.get_or_create_collection("analyst_desk")

        # Only embed if collection is empty
        if self.collection.count() == 0:
            print(f"  Embedding {len(passages)} passages (first run only)...")
            batch = 64
            for i in range(0, len(passages), batch):
                batch_p = passages[i:i+batch]
                embeddings = self.embedder.encode(
                    [p["text"] for p in batch_p], show_progress_bar=False
                ).tolist()
                self.collection.add(
                    ids=[p["passage_id"] for p in batch_p],
                    embeddings=embeddings,
                    documents=[p["text"] for p in batch_p],
                    metadatas=[{
                        "company": p["company"],
                        "fiscal_year": str(p["fiscal_year"]),
                        "item": p["item"],
                    } for p in batch_p],
                )
            print(f"  ✓ Index built: {len(passages)} passages")

    def search(self, query: str, top_k: int = 5,
               company: str | None = None,
               year: int | None = None) -> list[dict]:
        """Return top_k passages for query using BM25 + vectors + RRF + rerank.

        Optional filters narrow the search to a specific company or year.
        """
        n_candidates = min(50, len(self.passages))

        # BM25 — keyword search
        scores = self.bm25.get_scores(query.lower().split())
        bm25_ranked = sorted(
            range(len(self.pids)), key=lambda i: scores[i], reverse=True
        )[:n_candidates]
        bm25_pids = [self.pids[i] for i in bm25_ranked]

        # Vector search — semantic similarity
        q_emb = self.embedder.encode([query], show_progress_bar=False).tolist()
        # Chroma requires $and when combining multiple filter keys
        filters = []
        if company:
            filters.append({"company": {"$eq": company}})
        if year:
            filters.append({"fiscal_year": {"$eq": str(year)}})
        if len(filters) == 1:
            where = filters[0]
        elif len(filters) > 1:
            where = {"$and": filters}
        else:
            where = None
        vec_results = self.collection.query(
            query_embeddings=q_emb,
            n_results=n_candidates,
            where=where,
        )
        vec_pids = vec_results["ids"][0] if vec_results["ids"] else []

        # RRF — merge both ranked lists
        merged_pids = _rrf(bm25_pids, vec_pids)

        # Apply company/year filter to merged list
        if company or year:
            merged_pids = [
                pid for pid in merged_pids
                if pid in self.pid_to_passage
                and (not company or self.pid_to_passage[pid]["company"] == company)
                and (not year or self.pid_to_passage[pid]["fiscal_year"] == year)
            ]

        candidates = [
            self.pid_to_passage[pid]
            for pid in merged_pids[:n_candidates]
            if pid in self.pid_to_passage
        ]

        if not candidates:
            return []

        # Cross-encoder reranker — scores query+passage jointly
        pairs = [(query, c["text"]) for c in candidates]
        rerank_scores = self.reranker.predict(pairs, show_progress_bar=False)
        ranked = sorted(
            zip(candidates, rerank_scores), key=lambda x: x[1], reverse=True
        )

        results = []
        for passage, score in ranked[:top_k]:
            results.append({**passage, "score": float(score)})
        return results


def get_retriever(passages: list[dict] | None = None) -> HybridRetriever:
    """Return a cached HybridRetriever. Builds the index on first call."""
    global _retriever
    if _retriever is None:
        if passages is None:
            from .ingest import load_passages
            passages = load_passages()
        if not passages:
            raise RuntimeError(
                "No passages found. Run ingest first: "
                "python3 -m analystdesk --mode ingest"
            )
        print(f"Building retriever over {len(passages)} passages...")
        _retriever = HybridRetriever(passages)
    return _retriever


def reset_retriever():
    """Force rebuild on next call (useful after re-ingestion)."""
    global _retriever
    _retriever = None
