"""Ingestion pipeline: fetch → clean → de-boilerplate → chunk → embed → store (REQ-RAG-01).

Run manually with `python -m app.rag.ingest [--reset]`; also triggered at startup when the
collection is empty (AUTO_INGEST=true).
"""

import argparse
import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass

import httpx
from bs4 import BeautifulSoup

from app.llm import LLMClient
from app.rag.sources import SOURCE_URLS
from app.rag.store import KnowledgeStore

log = logging.getLogger(__name__)

USER_AGENT = "Mozilla/5.0 (compatible; GetnetSupportBot/1.0; +challenge-demo)"
_DROP_TAGS = ["head", "title", "script", "style", "noscript", "svg", "header", "footer", "nav", "form", "iframe"]


@dataclass
class Page:
    url: str
    title: str
    lines: list[str]


def clean_html(html: str) -> tuple[str, list[str]]:
    soup = BeautifulSoup(html, "html.parser")
    title = (soup.title.get_text(strip=True) if soup.title else "").strip()
    for tag in soup(_DROP_TAGS):
        tag.decompose()
    lines = []
    for raw in soup.get_text(separator="\n").splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if len(line) >= 3:
            lines.append(line)
    return title, lines


def remove_boilerplate(pages: list[Page], ratio: float = 0.5, min_pages: int = 4) -> list[Page]:
    """Drop lines that appear in >= `ratio` of pages (menus, modals, cookie banners) (ADR-05)."""
    if len(pages) < min_pages:
        return pages
    freq = Counter(line for p in pages for line in set(p.lines))
    threshold = max(2, int(len(pages) * ratio))
    return [Page(p.url, p.title, [ln for ln in p.lines if freq[ln] < threshold]) for p in pages]


def chunk_text(lines: list[str], size: int = 1000, overlap: int = 150) -> list[str]:
    """Paragraph-aware chunking: accumulate lines up to `size` chars, carrying `overlap` chars."""
    pieces: list[str] = []
    for line in lines:
        while len(line) > size:
            pieces.append(line[:size])
            line = line[size - overlap :]
        pieces.append(line)
    chunks: list[str] = []
    current = ""
    for piece in pieces:
        candidate = f"{current}\n{piece}" if current else piece
        if len(candidate) > size and current:
            chunks.append(current)
            current = f"{current[-overlap:]}\n{piece}" if overlap else piece
        else:
            current = candidate
    if current.strip():
        chunks.append(current)
    return chunks


def fetch_pages(urls: list[str]) -> list[Page]:
    pages = []
    with httpx.Client(headers={"User-Agent": USER_AGENT}, timeout=30, follow_redirects=True) as client:
        for url in urls:
            try:
                resp = client.get(url)
                resp.raise_for_status()
                title, lines = clean_html(resp.text)
                pages.append(Page(url=url, title=title or url, lines=lines))
            except httpx.HTTPError as exc:
                log.warning("ingest_fetch_failed", extra={"url": url, "error": str(exc)})
    return pages


def ingest(llm: LLMClient, store: KnowledgeStore, urls: list[str] | None = None) -> dict[str, int]:
    pages = remove_boilerplate(fetch_pages(urls or SOURCE_URLS))
    ids, texts, metas = [], [], []
    for page in pages:
        for i, chunk in enumerate(chunk_text(page.lines)):
            ids.append(hashlib.sha1(f"{page.url}#{i}".encode()).hexdigest())
            texts.append(chunk)
            metas.append({"url": page.url, "title": page.title, "chunk_index": i})
    if texts:
        store.add(ids, texts, llm.embed(texts), metas)
    stats = {"pages": len(pages), "chunks": len(texts)}
    log.info("ingest_completed", extra=stats)
    return stats


def main() -> None:
    from app.config import get_settings
    from app.llm import OpenAIClient
    from app.rag.store import ChromaStore

    parser = argparse.ArgumentParser(description="Ingest Getnet web pages into the vector store")
    parser.add_argument("--reset", action="store_true", help="drop the collection before ingesting")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    store = ChromaStore(settings.chroma_path, settings.chroma_collection)
    if args.reset:
        store.reset()
    print(ingest(OpenAIClient(settings), store))


if __name__ == "__main__":
    main()
