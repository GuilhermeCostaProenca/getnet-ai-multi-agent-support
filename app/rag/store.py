"""Vector store wrapper over an embedded, persistent ChromaDB collection (ADR-03).

Embeddings are computed by our LLM client and passed explicitly, so Chroma never downloads
its default local embedding model.
"""

from dataclasses import dataclass
from typing import Any, Protocol


@dataclass
class Hit:
    text: str
    title: str
    url: str
    score: float  # cosine similarity (1 - cosine distance)


class KnowledgeStore(Protocol):
    def count(self) -> int: ...

    def add(self, ids: list[str], texts: list[str], embeddings: list[list[float]], metadatas: list[dict[str, Any]]) -> None: ...

    def query(self, embedding: list[float], k: int) -> list[Hit]: ...

    def reset(self) -> None: ...


class ChromaStore:
    def __init__(self, path: str, collection: str):
        import chromadb

        self._client = chromadb.PersistentClient(path=path)
        self._name = collection
        self._collection = self._get()

    def _get(self):
        return self._client.get_or_create_collection(
            name=self._name, embedding_function=None, metadata={"hnsw:space": "cosine"}
        )

    def count(self) -> int:
        return self._collection.count()

    def add(self, ids, texts, embeddings, metadatas) -> None:
        self._collection.upsert(ids=ids, documents=texts, embeddings=embeddings, metadatas=metadatas)

    def query(self, embedding: list[float], k: int) -> list[Hit]:
        res = self._collection.query(query_embeddings=[embedding], n_results=k)
        hits = []
        for text, meta, dist in zip(res["documents"][0], res["metadatas"][0], res["distances"][0]):
            hits.append(Hit(text=text, title=meta.get("title", ""), url=meta.get("url", ""), score=round(1 - dist, 4)))
        return hits

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._get()
