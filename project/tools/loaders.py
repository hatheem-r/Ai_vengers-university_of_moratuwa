"""read_document: turns one input file into tables, text and page images for an LLM.

    from tools.loaders import read_document, list_documents
    doc = read_document("data/bundles/dev_A_S1_clean/inputs/bom_techpack.xlsx")
    doc.sheets["BoM"]            # pandas DataFrame per sheet (XLSX/CSV), raw cells, no header guessing
    doc.text                     # the whole file as text (sheets as tab-separated rows r1, r2, ...)
    doc.pages[0].image_jpeg      # PDF page image (JPEG bytes), see `images` below
    doc.content_blocks()         # Anthropic message content: one text block + image blocks

Formats
- .xlsx/.xlsm: every sheet with pandas (openpyxl engine), header=None so the model sees
  title rows and the real header row as printed. Dates become ISO dates.
- .csv: one sheet called "csv".
- .pdf: text per page with pdfplumber, plus page images. `images="auto"` (default)
  renders only pages whose text layer is thin (scans); "all" renders every page; "none"
  renders nothing.
- .png/.jpg: the image itself; .txt/.eml/.md/.json: the text.

The baseline agent calls this as its `read_document` tool. Systems may only read files in
a bundle's inputs/ folder (TASKS.md 0.3, hidden-data rule): `read_input` enforces that.
"""
from __future__ import annotations

import base64
import datetime as dt
import io
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal, Optional, Union

import pandas as pd

TABLE_SUFFIXES = {".xlsx", ".xlsm", ".csv"}
TEXT_SUFFIXES = {".txt", ".eml", ".md", ".json"}
IMAGE_SUFFIXES = {".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg"}
SUPPORTED_SUFFIXES = TABLE_SUFFIXES | TEXT_SUFFIXES | set(IMAGE_SUFFIXES) | {".pdf"}
SCAN_TEXT_THRESHOLD = 40       # fewer text characters on a page than this = treat it as a scan
PAGE_DPI = 110                 # ~ 900 x 1300 px for A4: readable, about 1.5k image tokens
JPEG_QUALITY = 80              # ~ 70 KB per page (PNG would be ~ 370 KB)
MAX_ROWS_PER_SHEET = 500
MAX_TEXT_CHARS = 60_000

ImageMode = Literal["auto", "all", "none"]


@dataclass
class PdfPage:
    number: int                       # 1-based
    text: str
    looks_scanned: bool
    image_jpeg: Optional[bytes] = None


@dataclass
class LoadedDocument:
    name: str
    path: Path
    kind: Literal["table", "pdf", "image", "text"]
    text: str
    sheets: dict[str, pd.DataFrame] = field(default_factory=dict)
    pages: list[PdfPage] = field(default_factory=list)
    image: Optional[tuple[str, bytes]] = None          # (media type, bytes) for image files

    @property
    def looks_scanned(self) -> bool:
        return any(p.looks_scanned for p in self.pages)

    def images(self) -> list[tuple[str, bytes]]:
        out = [("image/jpeg", p.image_jpeg) for p in self.pages if p.image_jpeg]
        return out + ([self.image] if self.image else [])

    def content_blocks(self) -> list[dict]:
        """Anthropic content blocks: images first, then the text (with the file name)."""
        blocks = [{"type": "image", "source": {"type": "base64", "media_type": mt,
                                               "data": base64.b64encode(data).decode()}}
                  for mt, data in self.images()]
        text = self.text
        if self.looks_scanned:
            text += "\n\n(Some pages have little or no text layer: read the attached page images.)"
        blocks.append({"type": "text", "text": f"File: {self.name}\n\n{text}"})
        return blocks


def list_documents(inputs_dir: Union[str, Path]) -> list[Path]:
    """Readable files in a bundle's inputs/ folder, in a stable order (hidden files skipped)."""
    return sorted(p for p in Path(inputs_dir).iterdir()
                  if p.is_file() and not p.name.startswith(".") and p.suffix.lower() in SUPPORTED_SUFFIXES)


def read_input(inputs_dir: Union[str, Path], name: str, images: ImageMode = "auto") -> LoadedDocument:
    """read_document for a file name the model gave, refusing anything outside inputs_dir."""
    root = Path(inputs_dir).resolve()
    path = (root / name).resolve()
    if root not in path.parents or not path.is_file():
        available = ", ".join(p.name for p in list_documents(root))
        raise FileNotFoundError(f"{name!r} is not a file in inputs/. Available: {available}")
    return read_document(path, images=images)


def read_document(path: Union[str, Path], images: ImageMode = "auto") -> LoadedDocument:
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"unsupported file type {suffix!r} ({path.name})")
    if suffix in TABLE_SUFFIXES:
        sheets = _read_tables(path)
        return LoadedDocument(path.name, path, "table", _cut(_sheets_text(sheets)), sheets=sheets)
    if suffix == ".pdf":
        pages = _read_pdf(path, images)
        text = "\n\n".join(f"### Page {p.number}\n{p.text}" for p in pages)
        return LoadedDocument(path.name, path, "pdf", _cut(text), pages=pages)
    if suffix in IMAGE_SUFFIXES:
        return LoadedDocument(path.name, path, "image", "(Image document: read the attached image.)",
                              image=(IMAGE_SUFFIXES[suffix], path.read_bytes()))
    return LoadedDocument(path.name, path, "text", _cut(path.read_text(encoding="utf-8", errors="replace")))


# --- tables ---------------------------------------------------------------------------------

def _read_tables(path: Path) -> dict[str, pd.DataFrame]:
    if path.suffix.lower() == ".csv":
        frames = {"csv": pd.read_csv(path, header=None, dtype=object, encoding="utf-8-sig",
                                     skip_blank_lines=True, keep_default_na=False)}
    else:
        frames = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine="openpyxl")
    out = {}
    for name, df in frames.items():
        df = df.replace({"": None}).dropna(how="all").dropna(axis=1, how="all")
        out[str(name)] = df.reset_index(drop=True)
    return out


def _cell(v) -> str:
    if v is None or (isinstance(v, float) and pd.isna(v)) or v is pd.NaT:
        return ""
    if isinstance(v, (pd.Timestamp, dt.datetime)):
        return v.date().isoformat() if v.time() == dt.time(0) else v.isoformat(sep=" ")
    if isinstance(v, dt.date):
        return v.isoformat()
    if isinstance(v, float) and v.is_integer():
        return str(int(v))
    return str(v).replace("\t", " ").replace("\n", " ")


def _sheets_text(sheets: dict[str, pd.DataFrame]) -> str:
    parts = []
    for name, df in sheets.items():
        rows = []
        for i, row in enumerate(df.itertuples(index=False), 1):
            if i > MAX_ROWS_PER_SHEET:
                rows.append(f"... ({len(df) - MAX_ROWS_PER_SHEET} more rows cut)")
                break
            cells = [_cell(v) for v in row]
            while cells and cells[-1] == "":
                cells.pop()
            rows.append(f"r{i}\t" + "\t".join(cells))
        parts.append(f"### Sheet: {name}\n" + "\n".join(rows))
    return "\n\n".join(parts)


# --- PDFs ----------------------------------------------------------------------------------

def _read_pdf(path: Path, images: ImageMode) -> list[PdfPage]:
    import pdfplumber
    pages = []
    with pdfplumber.open(path) as pdf:
        for i, page in enumerate(pdf.pages, 1):
            text = page.extract_text() or ""
            scanned = len(text.strip()) < SCAN_TEXT_THRESHOLD
            jpg = None
            if images == "all" or (images == "auto" and scanned):
                buf = io.BytesIO()
                page.to_image(resolution=PAGE_DPI).original.convert("RGB").save(buf, format="JPEG", quality=JPEG_QUALITY)
                jpg = buf.getvalue()
            pages.append(PdfPage(i, text, scanned, jpg))
    return pages


def _cut(text: str) -> str:
    return text if len(text) <= MAX_TEXT_CHARS else text[:MAX_TEXT_CHARS] + "\n... (cut)"
