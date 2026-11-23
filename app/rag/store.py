"""Vector store: embedded persistent ChromaDB with embeddings computed by our LLM client."""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Hit:
    text: str
    title: str
    url: str
    score: float  # cosine similarity


class KnowledgeStore(Protocol):
    def count(self) -> int: ...

    def add(self, ids: list[str], texts: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]) -> None: ...

    def query(self, embedding: list[float], k: int) -> list[Hit]: ...

    def reset(self) -> None: ...

    def ids(self) -> set[str]: ...

    def delete(self, ids: list[str]) -> None: ...


class ChromaStore:
    def __init__(self, path: str, collection: str):
        import chromadb

        self._client = chromadb.PersistentClient(path=path)
        self._name = collection
        self._collection = self._open()

    def _open(self):
        return self._client.get_or_create_collection(name=self._name, embedding_function=None,
                                                     metadata={"hnsw:space": "cosine"})

    def count(self) -> int:
        return self._collection.count()

    def add(self, ids, texts, embeddings, metadatas) -> None:
        self._collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)

    def query(self, embedding: list[float], k: int) -> list[Hit]:
        res = self._collection.query(query_embeddings=[embedding], n_results=k)
        return [Hit(text=t, title=m.get("title", ""), url=m.get("url", ""), score=round(1 - d, 4))
                for t, m, d in zip(res["documents"][0], res["metadatas"][0], res["distances"][0])]

    def ids(self) -> set[str]:
        return set(self._collection.get(include=[])["ids"])

    def all_hits(self) -> list[Hit]:
        res = self._collection.get(include=["documents", "metadatas"])
        return [Hit(text=t, title=m.get("title", ""), url=m.get("url", ""), score=0.0)
                for t, m in zip(res["documents"], res["metadatas"])]

    def delete(self, ids: list[str]) -> None:
        if ids:
            self._collection.delete(ids=ids)

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._open()


class BM25Index:
    """Lexical retrieval over the chunks already stored in the vector store (no embedding API needed). Used when the
    chat provider has no embeddings endpoint (LLM_PROVIDER=anthropic). Scores are normalised to the best hit (0..1);
    abstention then relies on the knowledge agent's answered_from_context check."""

    K1, B = 1.5, 0.75

    def __init__(self, docs: list[Hit]):
        import math
        import re
        import unicodedata
        from collections import Counter

        def tokens(text: str) -> list[str]:
            folded = unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()
            return [t for t in re.findall(r"[a-z0-9]+", folded) if len(t) > 2]

        self._tokens = tokens
        self._docs = docs
        self._tf = [Counter(tokens(d.text + " " + d.title)) for d in docs]
        self._len = [sum(tf.values()) for tf in self._tf]
        self._avg = (sum(self._len) / len(docs)) if docs else 1.0
        df = Counter(t for tf in self._tf for t in tf)
        n = len(docs)
        self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}

    def search(self, query: str, k: int) -> list[Hit]:
        q = set(self._tokens(query))
        scored = []
        for doc, tf, ln in zip(self._docs, self._tf, self._len):
            s = sum(self._idf[t] * tf[t] * (self.K1 + 1) / (tf[t] + self.K1 * (1 - self.B + self.B * ln / self._avg))
                    for t in q if t in tf)
            if s > 0:
                scored.append((s, doc))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[0][0] if scored else 1.0
        return [Hit(text=d.text, title=d.title, url=d.url, score=round(s / top, 4)) for s, d in scored[:k]]
