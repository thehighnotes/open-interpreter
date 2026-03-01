# Copyright (c) 2026 thehighnotes — AGPL-3.0
#!/usr/bin/env python3
"""Mini-RAG module for Open Interpreter context injection.

Lightweight semantic retrieval engine that loads knowledge entries from an
external JSON file and matches them against user queries using
sentence-transformers (all-MiniLM-L6-v2, 384-dim embeddings).

Knowledge entries are NOT embedded in source code — they're loaded at runtime
from ~/.config/hub/rag-entries.json (or RAG_ENTRIES_PATH env var).
See rag-entries.example.json in the repo root for the entry format.
"""

import json
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

import numpy as np

# ---------------------------------------------------------------------------
# Knowledge Base — loaded from external JSON file
# ---------------------------------------------------------------------------
# Entries are NOT embedded in source to avoid leaking local configuration
# into a public repository. Load from ~/.config/hub/rag-entries.json or
# a path specified by the RAG_ENTRIES_PATH environment variable.

def _load_knowledge_base():
    """Load RAG entries from external JSON file."""
    import os
    custom_path = os.environ.get("RAG_ENTRIES_PATH")
    if custom_path:
        kb_path = Path(custom_path)
    else:
        kb_path = Path.home() / ".config" / "hub" / "rag-entries.json"

    if kb_path.exists():
        with open(kb_path) as f:
            return json.load(f)
    return []

KNOWLEDGE_BASE = _load_knowledge_base()


# ---------------------------------------------------------------------------
# MiniRAG Class
# ---------------------------------------------------------------------------

class MiniRAG:
    """Lightweight semantic retrieval over an embedded knowledge base.

    Uses sentence-transformers all-MiniLM-L6-v2 (384-dim, ~45MB) with
    normalized embeddings for cosine similarity via dot product.
    """

    def __init__(self, model_name: str = "all-MiniLM-L6-v2"):
        self._model_name = model_name
        self._model = None
        self._kb_embeddings = None  # np.ndarray shape (N, dim)
        self._kb = KNOWLEDGE_BASE

    # -- Properties ----------------------------------------------------------

    @property
    def is_loaded(self) -> bool:
        """True if model and KB embeddings are ready."""
        return self._model is not None and self._kb_embeddings is not None

    @property
    def entry_count(self) -> int:
        """Number of entries in the knowledge base."""
        return len(self._kb)

    @property
    def embedding_dim(self) -> int:
        """Embedding vector dimension (0 if not loaded)."""
        if self._kb_embeddings is None:
            return 0
        return self._kb_embeddings.shape[1]

    # -- Core Methods --------------------------------------------------------

    def load(self) -> None:
        """Explicitly load the model and embed the knowledge base.

        Imports sentence_transformers on first call (heavy import).
        """
        if self.is_loaded:
            return
        from sentence_transformers import SentenceTransformer

        self._model = SentenceTransformer(self._model_name)
        descriptions = [e["description"] for e in self._kb]
        self._kb_embeddings = self._model.encode(
            descriptions, normalize_embeddings=True
        )

    def query(
        self, text: str, threshold: float = 0.35, top_k: int = 3
    ) -> list[dict]:
        """Retrieve top-k knowledge entries matching `text`.

        Args:
            text: User query string.
            threshold: Minimum cosine similarity to include (0.0–1.0).
            top_k: Maximum number of results to return.

        Returns:
            List of dicts with keys: topic, content, score, source, category.
            Sorted by descending score. Empty list if nothing exceeds threshold.
        """
        if not text or not text.strip():
            return []

        # Lazy load
        if not self.is_loaded:
            self.load()

        q_emb = self._model.encode([text], normalize_embeddings=True)
        scores = np.dot(self._kb_embeddings, q_emb.T).flatten()

        # Rank and filter
        ranked = sorted(enumerate(scores), key=lambda x: -x[1])
        results = []
        for idx, score in ranked[:top_k]:
            if score < threshold:
                break
            entry = self._kb[idx]
            results.append(
                {
                    "topic": entry["topic"],
                    "content": entry["content"],
                    "score": float(score),
                    "source": entry["source"],
                    "category": entry["category"],
                }
            )
        return results

    def format_context(self, matches: list[dict], max_chars: int = 1000) -> str:
        """Format matched entries into a context string for prompt injection.

        Args:
            matches: Output from query().
            max_chars: Maximum total characters in the output.

        Returns:
            Formatted context string, or empty string if no matches.
        """
        if not matches:
            return ""

        lines = []
        total = 0
        for m in matches:
            line = f"[{m['topic']}] {m['content']}"
            if total + len(line) + 1 > max_chars and lines:
                break
            lines.append(line)
            total += len(line) + 1  # +1 for newline

        return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI quick test
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import sys
    import time

    rag = MiniRAG()
    print(f"Knowledge base: {rag.entry_count} entries")

    t0 = time.time()
    rag.load()
    print(f"Model loaded in {time.time() - t0:.1f}s  (dim={rag.embedding_dim})")

    query = " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "check memory on the jetson"
    t0 = time.time()
    hits = rag.query(query)
    elapsed = (time.time() - t0) * 1000
    print(f"\nQuery: {query!r}  ({elapsed:.0f}ms)")
    for h in hits:
        print(f"  [{h['score']:.3f}] {h['topic']}  ({h['source']})")
        print(f"         {h['content'][:80]}...")
    if not hits:
        print("  (no matches above threshold)")

    ctx = rag.format_context(hits)
    if ctx:
        print(f"\nFormatted context ({len(ctx)} chars):")
        print(ctx)
