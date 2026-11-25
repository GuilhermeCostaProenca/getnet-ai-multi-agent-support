"""BM25 retrieval over the stored chunks (KNW-03, ADR-03): multi-query merge, product-page boost, LRU cache (OBS-04)."""

import math
import re
import threading
import unicodedata
from collections import Counter, OrderedDict
from dataclasses import dataclass

from app.storage import Store

STOPWORDS = set("""a o as os de da do das dos e em no na nos nas um uma uns umas para por com sem que se ao aos
como qual quais quando onde meu minha seu sua mais ou the of and to in on for is are be can how what when do does
i my your with it this that an at from""".split())


def fold(text: str) -> str:
    return unicodedata.normalize("NFKD", text.lower()).encode("ascii", "ignore").decode()


def tokenize(text: str) -> list[str]:
    return [t for t in re.findall(r"[a-z0-9]+", fold(text)) if len(t) > 1 and t not in STOPWORDS]


@dataclass
class Hit:
    id: str
    url: str
    title: str
    text: str
    score: float


class KnowledgeIndex:
    K1, B = 1.5, 0.75
    SLUG_BOOST = 0.5
    CACHE_SIZE = 512

    def __init__(self, store: Store):
        self._store = store
        self._lock = threading.Lock()
        self._cache: OrderedDict[tuple, list[Hit]] = OrderedDict()
        self.cache_hits = 0
        self.reload()

    def reload(self) -> None:
        chunks = self._store.all_chunks()
        tfs = [Counter(tokenize(c["text"])) for c in chunks]
        df = Counter(t for tf in tfs for t in tf)
        n = len(chunks)
        with self._lock:
            self._chunks, self._tfs = chunks, tfs
            self._lens = [sum(tf.values()) for tf in tfs]
            self._avg = (sum(self._lens) / n) if n else 1.0
            self._idf = {t: math.log(1 + (n - f + 0.5) / (f + 0.5)) for t, f in df.items()}
            self._cache.clear()

    @property
    def size(self) -> int:
        return len(self._chunks)

    def _scores(self, query: str) -> list[float]:
        terms = set(tokenize(query))
        out = []
        for tf, length in zip(self._tfs, self._lens):
            s = 0.0
            for t in terms & tf.keys():
                f = tf[t]
                s += self._idf[t] * f * (self.K1 + 1) / (f + self.K1 * (1 - self.B + self.B * length / self._avg))
            out.append(s)
        return out

    def search(self, queries: list[str], k: int) -> list[Hit]:
        """Each query's scores are normalised to its best hit and merged by max; a chunk from the page whose URL slug
        names a queried term (e.g. /crediario/) gets a boost, so the product's own page beats FAQ mentions of
        related products."""
        key = (tuple(queries), k)
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                self.cache_hits += 1
                return self._cache[key]
            if not self._chunks:
                return []
            merged = [0.0] * len(self._chunks)
            terms: set[str] = set()
            for q in queries:
                terms |= {t for t in tokenize(q) if len(t) >= 4}
                scores = self._scores(q)
                top = max(scores) if scores else 0.0
                if top > 0:
                    merged = [max(m, s / top) for m, s in zip(merged, scores)]
            hits = []
            for chunk, score in zip(self._chunks, merged):
                if score <= 0:
                    continue
                slug = set(re.findall(r"[a-z0-9]+", fold(chunk["url"].rstrip("/").rsplit("/", 1)[-1])))
                boost = self.SLUG_BOOST if terms & slug else 0.0
                hits.append(Hit(chunk["id"], chunk["url"], chunk["title"], chunk["text"], round(score + boost, 4)))
            hits.sort(key=lambda h: h.score, reverse=True)
            result = hits[:k]
            self._cache[key] = result
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
            return result
