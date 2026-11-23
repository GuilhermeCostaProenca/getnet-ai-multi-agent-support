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

    def reset(self) -> None:
        self._client.delete_collection(self._name)
        self._collection = self._open()
