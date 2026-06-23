"""DocumentIngestor: RAG ingestion pipeline."""
# ruff: noqa
# fmt: off
from __future__ import annotations
from essence._shared import *  # noqa: F401,F403

# DOCUMENT INGESTOR  (RAG pipeline)
# ══════════════════════════════════════════════════════════════════════════════
# Ingests PDF, DOCX, HTML, CSV, Markdown, plain text into the Memory backend.
# Chunks at CHUNK_SIZE tokens with OVERLAP overlap.
# Stored chunks are retrieved by Agent._sys() before every LLM call,
# injecting only relevant passages — Karpathy precision RAM management.

class DocumentIngestor:
    """
    Parse → chunk → embed → store pipeline.
    T0: text extraction only (pypdf2 / strip HTML).
    T1+: same pipeline, richer embeddings via faiss/qdrant backend.
    """
    CHUNK_SIZE = 512    # characters per chunk (≈128 tokens at 4 chars/tok)
    OVERLAP    = 64     # overlap between adjacent chunks

    def __init__(self, memory: "Memory", workspace: Path):
        self._mem  = memory
        self._ws   = workspace
        self.SUPPORTED = {".pdf", ".docx", ".txt", ".md",
                          ".html", ".htm", ".csv", ".json"}

    # ── Extractors ──────────────────────────────────────────────────────────
    def _extract_pdf(self, path: Path) -> str:
        try:
            import pypdf  # type: ignore
            reader = pypdf.PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except ImportError:
            pass
        try:
            from PyPDF2 import PdfReader  # type: ignore
            reader = PdfReader(str(path))
            return "\n".join(p.extract_text() or "" for p in reader.pages)
        except ImportError:
            return f"[PDF extraction requires pypdf: pip install pypdf]"

    def _extract_docx(self, path: Path) -> str:
        try:
            import docx  # type: ignore
            doc = docx.Document(str(path))
            return "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            return "[DOCX extraction requires python-docx: pip install python-docx]"

    def _extract_html(self, path: Path) -> str:
        raw = path.read_text(encoding="utf-8", errors="replace")
        try:
            from bs4 import BeautifulSoup  # type: ignore
            return BeautifulSoup(raw, "html.parser").get_text(separator="\n")
        except ImportError:
            return re.sub(r"<[^>]+>", " ", raw)

    def _extract_csv(self, path: Path) -> str:
        import csv
        rows = []
        with open(path, newline="", encoding="utf-8", errors="replace") as f:
            for row in csv.reader(f):
                rows.append(", ".join(row))
        return "\n".join(rows)

    def _extract(self, path: Path) -> str:
        ext = path.suffix.lower()
        if ext == ".pdf":   return self._extract_pdf(path)
        if ext == ".docx":  return self._extract_docx(path)
        if ext in (".html", ".htm"): return self._extract_html(path)
        if ext == ".csv":   return self._extract_csv(path)
        return path.read_text(encoding="utf-8", errors="replace")

    # ── Chunker ─────────────────────────────────────────────────────────────
    def _chunk(self, text: str) -> list[str]:
        chunks, i = [], 0
        while i < len(text):
            end = min(i + self.CHUNK_SIZE, len(text))
            chunks.append(text[i:end].strip())
            i += self.CHUNK_SIZE - self.OVERLAP
        return [c for c in chunks if len(c) > 10]

    # ── Public API ───────────────────────────────────────────────────────────
    def ingest(self, path: str | Path) -> int:
        """Parse a document, chunk it, store in Memory. Returns chunk count."""
        p = Path(path).expanduser()
        if not p.exists():
            return 0
        if p.suffix.lower() not in self.SUPPORTED:
            return 0
        text   = self._extract(p)
        chunks = self._chunk(text)
        source = str(p.relative_to(self._ws)) if str(p).startswith(str(self._ws)) \
                 else p.name
        for i, chunk in enumerate(chunks):
            self._mem.store(chunk, {"source": source, "chunk": i})
        # Record ingestion in KV so agent knows what's available
        ingested = self._mem.get("_ingested_docs", {})
        ingested[source] = len(chunks)
        self._mem.set("_ingested_docs", ingested)
        return len(chunks)

    def ingest_dir(self, directory: Path, glob: str = "**/*") -> dict[str, int]:
        """Recursively ingest all supported files in a directory."""
        results: dict[str, int] = {}
        for p in Path(directory).glob(glob):
            if p.is_file() and p.suffix.lower() in self.SUPPORTED:
                n = self.ingest(p)
                if n: results[p.name] = n
        return results

    def ingest_url(self, url: str) -> int:
        """Fetch a URL via Jina Reader and ingest as text."""
        try:
            jina_url = "https://reader.jina.ai/" + urllib.parse.quote(url, safe=":/?=&%")
            text = urllib.request.urlopen(jina_url, timeout=15).read().decode(
                "utf-8", errors="replace")[:50000]
            if not text.strip():
                return 0
            chunks = self._chunk(text)
            for i, chunk in enumerate(chunks):
                self._mem.store(chunk, {"source": url, "chunk": i})
            ingested = self._mem.get("_ingested_docs", {})
            ingested[url] = len(chunks)
            self._mem.set("_ingested_docs", ingested)
            return len(chunks)
        except Exception:
            return 0

    def list_ingested(self) -> dict[str, int]:
        return self._mem.get("_ingested_docs", {})


def _tool_ingest(path_or_url: str, workspace: Path,
                 memory: "Memory") -> str:
    """Tool wrapper for DocumentIngestor."""
    ingestor = DocumentIngestor(memory, workspace)
    if path_or_url.startswith("http"):
        n = ingestor.ingest_url(path_or_url)
        return f"[ingest] Fetched and stored {n} chunks from {path_or_url}"
    p = Path(path_or_url).expanduser()
    if p.is_dir():
        results = ingestor.ingest_dir(p)
        total = sum(results.values())
        return (f"[ingest_dir] {len(results)} files → {total} chunks\n" +
                "\n".join(f"  {k}: {v} chunks" for k, v in results.items()))
    n = ingestor.ingest(p)
    return f"[ingest] {p.name} → {n} chunks stored in memory"


# ══════════════════════════════════════════════════════════════════════════════
