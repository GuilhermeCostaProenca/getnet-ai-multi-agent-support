"""Ingestion of Getnet's official pages (REQ-KNW-01, KNW-04, KNW-06).

Seeds + same-site link discovery from the home page → boilerplate-free text → FAQ-aware chunks (a question stays with
its answer; the page title prefixes every chunk) → content-hash ids, so re-ingestion only stores what changed.
Usage: python -m app.rag.ingest
"""

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from app.storage import Store

log = logging.getLogger("app.ingest")

BASE = "https://site.getnet.com.br/"
SEEDS = [BASE, f"{BASE}duvidas/", f"{BASE}crediario/", f"{BASE}pix/", f"{BASE}link-de-pagamento/",
         f"{BASE}emprestimo/", f"{BASE}maquininha/", f"{BASE}antecipacao/", f"{BASE}conta-digital/"]
EXCLUDE = re.compile(r"blog|politica|privacidade|termos|trabalhe|imprensa|login|wp-|cookie|ouvidoria|\.(pdf|jpg|png)$", re.I)
MAX_PAGES = 22
MAX_CHARS = 1000
REMOVE_TAGS = ["script", "style", "noscript", "svg", "nav", "header", "footer", "form", "iframe"]


@dataclass
class Page:
    url: str
    title: str
    lines: list[str]


def clean_html(url: str, html: str) -> Page:
    soup = BeautifulSoup(html, "html.parser")
    raw_title = soup.title.get_text(" ", strip=True) if soup.title else url
    title = re.split(r"\s[|–-]\s", raw_title)[0].strip() or url
    for tag in soup(REMOVE_TAGS):
        tag.decompose()
    lines = [re.sub(r"\s+", " ", ln).strip() for ln in soup.get_text("\n").splitlines()]
    return Page(url=url, title=title, lines=[ln for ln in lines if len(ln) >= 3])


def remove_boilerplate(pages: list[Page]) -> list[Page]:
    """Lines repeated on at least half of the pages (menus, footers, cookie banners) are dropped."""
    if len(pages) < 4:
        return pages
    counts = Counter(line for p in pages for line in set(p.lines))
    threshold = len(pages) / 2
    return [Page(p.url, p.title, [ln for ln in p.lines if counts[ln] < threshold]) for p in pages]


def faq_blocks(lines: list[str]) -> list[list[str]]:
    """A line ending in '?' opens a new block, so each FAQ question travels with its answer (KNW-04)."""
    blocks: list[list[str]] = []
    current: list[str] = []
    for line in lines:
        if line.endswith("?") and current:
            blocks.append(current)
            current = []
        current.append(line)
    if current:
        blocks.append(current)
    return blocks


def chunk_page(page: Page, max_chars: int = MAX_CHARS) -> list[str]:
    chunks: list[str] = []
    current = ""
    for block in faq_blocks(page.lines):
        text = "\n".join(block)
        if len(text) > max_chars:  # an oversized block is split by lines, never mid-line
            for line in block:
                if current and len(current) + len(line) + 1 > max_chars:
                    chunks.append(current)
                    current = ""
                current = f"{current}\n{line}" if current else line
            continue
        if current and len(current) + len(text) + 1 > max_chars:
            chunks.append(current)
            current = ""
        current = f"{current}\n{text}" if current else text
    if current:
        chunks.append(current)
    return [f"{page.title}\n{c}" for c in chunks if len(c) >= 40]


def chunk_id(url: str, text: str) -> str:
    return hashlib.sha1(f"{url}|{text}".encode()).hexdigest()


def discover(client: httpx.Client) -> list[str]:
    urls = list(SEEDS)
    try:
        home = client.get(BASE)
        for a in BeautifulSoup(home.text, "html.parser").find_all("a", href=True):
            url = urljoin(BASE, a["href"]).split("#")[0].split("?")[0]
            parsed = urlparse(url)
            if parsed.netloc == "site.getnet.com.br" and parsed.path.count("/") <= 2 and not EXCLUDE.search(url):
                urls.append(url if url.endswith("/") else f"{url}/")
    except httpx.HTTPError:
        log.warning("discovery_failed")
    return list(dict.fromkeys(urls))[:MAX_PAGES]


def fetch_pages(urls: list[str] | None = None) -> list[Page]:
    pages: list[Page] = []
    with httpx.Client(timeout=20, follow_redirects=True, headers={"User-Agent": "getnet-support-ingest/3.0"}) as client:
        for url in urls or discover(client):
            try:
                resp = client.get(url)
                if resp.status_code == 200 and "text/html" in resp.headers.get("content-type", ""):
                    pages.append(clean_html(str(resp.url), resp.text))
            except httpx.HTTPError:
                log.warning("fetch_failed", extra={"url": url})
    unique = {p.url: p for p in pages}  # redirects can collapse seeds onto the same page
    return list(unique.values())


def ingest(store: Store, pages: list[Page] | None = None) -> dict[str, int]:
    pages = remove_boilerplate(pages if pages is not None else fetch_pages())
    rows = []
    for page in pages:
        for position, text in enumerate(chunk_page(page)):
            rows.append((chunk_id(page.url, text), page.url, page.title, position, text))
    existing = store.chunk_ids()
    new = [r for r in rows if r[0] not in existing]
    store.add_chunks(new)
    stale = sorted(existing - {r[0] for r in rows}) if pages else []  # never wipe the index when every fetch failed
    store.delete_chunks(stale)
    stats = {"pages": len(pages), "chunks": len(rows), "added": len(new), "unchanged": len(rows) - len(new),
             "deleted": len(stale)}
    log.info("ingest_completed", extra=stats)
    return stats


if __name__ == "__main__":
    from app.config import get_settings

    logging.basicConfig(level=logging.INFO)
    print(ingest(Store(get_settings().database_path)))
