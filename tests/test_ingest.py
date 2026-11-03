from app.rag.ingest import Page, chunk_text, clean_html, remove_boilerplate


def test_clean_html_drops_navigation_and_scripts():
    html = "<html><title>Pix</title><header>Menu</header><nav>Links</nav><script>x()</script>" \
           "<main><p>Receba via Pix na hora</p></main><footer>Rodapé</footer></html>"
    title, lines = clean_html(html)
    assert title == "Pix" and lines == ["Receba via Pix na hora"]


def test_ac23_boilerplate_repeated_across_pages_is_removed():
    pages = [Page(f"u{i}", "t", ["Já é cliente? Informe seu CPF", f"conteúdo único {i}"]) for i in range(4)]
    cleaned = remove_boilerplate(pages)
    assert all(p.lines == [f"conteúdo único {i}"] for i, p in enumerate(cleaned))


def test_ac23_chunks_respect_size_and_overlap():
    lines = [f"linha {i} " + "x" * 80 for i in range(40)]
    chunks = chunk_text(lines, size=500, overlap=100)
    assert len(chunks) > 1
    assert all(len(c) <= 500 + 100 + 90 for c in chunks)
    assert chunks[0][-100:] in chunks[1]


def test_long_line_is_split():
    chunks = chunk_text(["y" * 2500], size=1000, overlap=100)
    assert len(chunks) >= 3
