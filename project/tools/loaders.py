"""Document loading and the ``read_document`` tool.

Turns one file from a bundle's ``inputs/`` folder into content the model can
read:

* XLSX / CSV: loaded with pandas and rendered as a text grid. Every row keeps
  its real spreadsheet row number and column letter, so flags can cite
  ``SourceRef.row`` exactly. Messy layouts (title rows, blank rows, notes) are
  kept as they are; nothing is guessed.
* PDF: text per page (pdfplumber). Pages with little or no text (scans) are
  also sent as page images, so the model can read them. ``page_images=True``
  sends images for every requested page.
* TXT: text with line numbers.
* PNG / JPG: the image itself.

The same code serves both systems: the baseline calls the ``read_document``
tool, and the multi-agent Intake can call :func:`load_document` and
:meth:`LoadedDocument.content_blocks` directly.

Hidden-data rule (TASKS.md §0.3): only files inside ``inputs/`` can be read.
``state.json``, ``manifest.json`` and ``answer_key.json`` are never readable.
"""

from __future__ import annotations

import base64
import io
import math
from dataclasses import dataclass, field
from datetime import date, datetime, time
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import StructuredTool, ToolException
from pydantic import BaseModel, Field

SUPPORTED_TYPES: dict[str, str] = {
    ".pdf": "pdf",
    ".xlsx": "xlsx",
    ".xlsm": "xlsx",
    ".xls": "xls",
    ".csv": "csv",
    ".txt": "txt",
    ".png": "png",
    ".jpg": "jpg",
    ".jpeg": "jpg",
}
HIDDEN_FILES = {"state.json", "manifest.json", "answer_key.json"}

# A PDF page with fewer extracted characters than this is treated as a scan.
SCAN_TEXT_THRESHOLD = 40
PAGE_IMAGE_DPI = 150
# Anthropic recommends images no larger than ~1568 px on the long side.
MAX_IMAGE_SIDE = 1568
MAX_PAGES_PER_CALL = 20
MAX_SHEET_ROWS = 2_000
MAX_CELL_CHARS = 200

ImageMode = Literal["auto", "always", "never"]


class DocumentError(ToolException):
    """A document could not be found, is not allowed, or could not be read."""


# ---------------------------------------------------------------------------
# Data classes
# ---------------------------------------------------------------------------


@dataclass
class PageContent:
    page: int  # 1-based
    text: str
    image_png: bytes | None = None


@dataclass
class SheetContent:
    name: str
    # (spreadsheet row number, cells) for non-empty rows only; cells index 0 = column A
    rows: list[tuple[int, list[str]]]
    n_rows_total: int
    n_cols: int
    truncated: bool = False


@dataclass
class LoadedDocument:
    name: str
    type: str
    size_bytes: int
    pages: list[PageContent] = field(default_factory=list)
    n_pages: int | None = None
    sheets: list[SheetContent] = field(default_factory=list)
    text: str | None = None
    image: tuple[bytes, str] | None = None  # (data, mime type)
    notes: list[str] = field(default_factory=list)

    def to_text(self) -> str:
        """Everything except images, as one text block."""
        out = [f"=== Document: {self.name} (type: {self.type}) ==="]
        out += [f"Note: {n}" for n in self.notes]
        for sheet in self.sheets:
            out.append(_render_sheet(sheet))
        for p in self.pages:
            body = p.text.strip()
            label = f"--- Page {p.page} of {self.n_pages} ---"
            if not body:
                body = "(no text layer on this page; see the page image)" if p.image_png else "(no text on this page)"
            elif p.image_png:
                label += " (page image attached; text below may be incomplete)"
            out += [label, body]
        if self.text is not None:
            out.append(self.text)
        if self.image is not None:
            out.append("(image file; see the attached image)")
        return "\n".join(out)

    def content_blocks(self) -> list[dict[str, Any]]:
        """LangChain standard content blocks: one text block, then any images."""
        blocks: list[dict[str, Any]] = [{"type": "text", "text": self.to_text()}]
        for p in self.pages:
            if p.image_png:
                blocks.append({"type": "text", "text": f"Image of page {p.page} of {self.name}:"})
                blocks.append(_image_block(p.image_png, "image/png"))
        if self.image is not None:
            blocks.append(_image_block(*self.image))
        return blocks


def _image_block(data: bytes, mime: str) -> dict[str, Any]:
    return {"type": "image", "base64": base64.b64encode(data).decode("ascii"), "mime_type": mime}


# ---------------------------------------------------------------------------
# Bundle access
# ---------------------------------------------------------------------------


def inputs_dir(bundle_dir: Path | str) -> Path:
    """``<bundle>/inputs`` if it exists, otherwise the folder itself."""
    bundle_dir = Path(bundle_dir)
    sub = bundle_dir / "inputs"
    return sub if sub.is_dir() else bundle_dir


def document_type(path: Path | str) -> str | None:
    return SUPPORTED_TYPES.get(Path(path).suffix.lower())


def list_documents(bundle_dir: Path | str) -> list[dict[str, Any]]:
    """The readable documents in a bundle: ``[{name, type, size_bytes}]``, sorted by name."""
    folder = inputs_dir(bundle_dir)
    docs = []
    for p in sorted(folder.iterdir(), key=lambda p: p.name.lower()):
        if p.is_file() and p.name not in HIDDEN_FILES and not p.name.startswith(".") and document_type(p):
            docs.append({"name": p.name, "type": document_type(p), "size_bytes": p.stat().st_size})
    return docs


def resolve_document(bundle_dir: Path | str, file_name: str) -> Path:
    """Path of ``file_name`` inside the bundle's inputs, refusing anything outside it."""
    folder = inputs_dir(bundle_dir).resolve()
    name = file_name.strip()
    if not name or name != Path(name).name or name in (".", ".."):
        raise DocumentError(f"'{file_name}' is not a plain file name. Use a name from the document list.")
    if name in HIDDEN_FILES:
        raise DocumentError(f"'{name}' is not part of the input documents.")
    path = (folder / name).resolve()
    if path.parent != folder or not path.is_file():
        available = ", ".join(d["name"] for d in list_documents(bundle_dir)) or "(none)"
        raise DocumentError(f"No document named '{name}'. Available: {available}")
    if document_type(path) is None:
        raise DocumentError(f"'{name}' has an unsupported file type ({path.suffix}).")
    return path


# ---------------------------------------------------------------------------
# Loading
# ---------------------------------------------------------------------------


def load_document(
    path: Path | str,
    *,
    pages: list[int] | None = None,
    images: ImageMode = "auto",
) -> LoadedDocument:
    """Load one document.

    Args:
        path: The file.
        pages: 1-based PDF pages to load (default: all, up to MAX_PAGES_PER_CALL).
        images: PDF page images: ``auto`` (only pages that look scanned),
            ``always`` or ``never``.
    """
    path = Path(path)
    kind = document_type(path)
    if kind is None:
        raise DocumentError(f"Unsupported file type: {path.suffix}")
    doc = LoadedDocument(name=path.name, type=kind, size_bytes=path.stat().st_size)
    try:
        if kind == "pdf":
            _load_pdf(path, doc, pages, images)
        elif kind in ("xlsx", "xls"):
            _load_excel(path, doc)
        elif kind == "csv":
            _load_csv(path, doc)
        elif kind == "txt":
            text = _read_text(path)
            doc.text = "\n".join(f"{i:>4}: {line}" for i, line in enumerate(text.splitlines(), 1))
        else:
            doc.image = (_fit_image(path.read_bytes()), "image/png")
    except DocumentError:
        raise
    except Exception as exc:  # noqa: BLE001 - report any parse failure to the model
        raise DocumentError(f"Could not read '{path.name}': {type(exc).__name__}: {exc}") from exc
    return doc


def _load_pdf(path: Path, doc: LoadedDocument, pages: list[int] | None, images: ImageMode) -> None:
    import pdfplumber

    with pdfplumber.open(path) as pdf:
        n = len(pdf.pages)
        doc.n_pages = n
        wanted = pages or list(range(1, n + 1))
        bad = [p for p in wanted if not 1 <= p <= n]
        if bad:
            raise DocumentError(f"'{path.name}' has {n} pages; page(s) {bad} do not exist.")
        if len(wanted) > MAX_PAGES_PER_CALL:
            doc.notes.append(
                f"Showing pages {wanted[0]}-{wanted[MAX_PAGES_PER_CALL - 1]} of {n}. "
                "Call read_document again with `pages` for the rest."
            )
            wanted = wanted[:MAX_PAGES_PER_CALL]
        for number in wanted:
            page = pdf.pages[number - 1]
            text = page.extract_text() or ""
            looks_scanned = len(text.strip()) < SCAN_TEXT_THRESHOLD
            image = None
            if images == "always" or (images == "auto" and looks_scanned):
                rendered = page.to_image(resolution=PAGE_IMAGE_DPI).original
                image = _fit_image(rendered)
            doc.pages.append(PageContent(page=number, text=text, image_png=image))


def _load_excel(path: Path, doc: LoadedDocument) -> None:
    import pandas as pd

    engine = None if doc.type == "xlsx" else "xlrd"
    try:
        frames = pd.read_excel(path, sheet_name=None, header=None, dtype=object, engine=engine)
    except ImportError as exc:
        raise DocumentError(f"Cannot read legacy .xls files here ({exc}). Ask for an .xlsx copy.") from exc
    for name, df in frames.items():
        doc.sheets.append(_sheet_from_frame(str(name), df, first_row=1))
    doc.notes.append("Cells show stored values; formulas without a saved result appear empty.")


def _load_csv(path: Path, doc: LoadedDocument) -> None:
    # The csv module rather than pandas: messy exports often have rows of
    # different lengths, which pandas rejects, and reader.line_num gives the
    # exact line number of every row.
    import csv

    text = _read_text(path)
    try:
        delimiter = csv.Sniffer().sniff(text[:8192], delimiters=CSV_DELIMITERS).delimiter
    except csv.Error:
        delimiter = _guess_delimiter(text)  # the sniffer gives up on ragged rows
    reader = csv.reader(io.StringIO(text), delimiter=delimiter)
    rows: list[tuple[int, list[str]]] = []
    n_cols = 0
    line_start = 1
    for record in reader:
        cells = [_cell(v) for v in record]
        while cells and cells[-1] == "":
            cells.pop()
        if cells:
            rows.append((line_start, cells))
            n_cols = max(n_cols, len(cells))
        line_start = reader.line_num + 1
    doc.sheets.append(SheetContent(
        name=path.name, rows=rows[:MAX_SHEET_ROWS], n_rows_total=reader.line_num,
        n_cols=n_cols, truncated=len(rows) > MAX_SHEET_ROWS,
    ))


CSV_DELIMITERS = ",;\t|"


def _guess_delimiter(text: str) -> str:
    """The candidate found on the most of the first 20 non-empty lines (ties: most often)."""
    lines = [ln for ln in text.splitlines() if ln.strip()][:20]
    best, best_key = ",", (0, 0)
    for d in CSV_DELIMITERS:
        key = (sum(d in ln for ln in lines), sum(ln.count(d) for ln in lines))
        if key > best_key:
            best, best_key = d, key
    return best


def _sheet_from_frame(name: str, df: Any, first_row: int) -> SheetContent:
    rows: list[tuple[int, list[str]]] = []
    n_cols = 0
    for i, values in enumerate(df.itertuples(index=False, name=None)):
        cells = [_cell(v) for v in values]
        while cells and cells[-1] == "":
            cells.pop()
        if cells:
            rows.append((first_row + i, cells))
            n_cols = max(n_cols, len(cells))
    truncated = len(rows) > MAX_SHEET_ROWS
    return SheetContent(name=name, rows=rows[:MAX_SHEET_ROWS], n_rows_total=len(df), n_cols=n_cols, truncated=truncated)


def _cell(v: Any) -> str:
    if v is None:
        return ""
    if isinstance(v, float):
        if math.isnan(v):
            return ""
        if v.is_integer() and abs(v) < 1e15:
            return str(int(v))
        return repr(v)
    if isinstance(v, datetime):
        return v.date().isoformat() if v.time() == time(0) else v.isoformat(sep=" ")
    if isinstance(v, date):
        return v.isoformat()
    try:
        import pandas as pd

        if v is pd.NaT:
            return ""
    except ImportError:  # pragma: no cover
        pass
    s = str(v).replace("\r", " ").replace("\n", " ").replace("|", "/").strip()
    return s if len(s) <= MAX_CELL_CHARS else s[:MAX_CELL_CHARS] + "..."


def column_letter(index: int) -> str:
    """0 -> A, 25 -> Z, 26 -> AA."""
    letters = ""
    index += 1
    while index:
        index, rem = divmod(index - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def _render_sheet(sheet: SheetContent) -> str:
    header = f'--- Sheet "{sheet.name}": {len(sheet.rows)} non-empty rows'
    if sheet.n_cols:
        header += f", columns A-{column_letter(sheet.n_cols - 1)}"
    header += " (blank rows omitted; row numbers are the real spreadsheet rows) ---"
    if not sheet.rows:
        return header + "\n(empty sheet)"
    lines = [header, "row | " + " | ".join(column_letter(i) for i in range(sheet.n_cols))]
    for number, cells in sheet.rows:
        lines.append(f"{number} | " + " | ".join(cells))
    if sheet.truncated:
        lines.append(f"(truncated after {MAX_SHEET_ROWS} non-empty rows)")
    return "\n".join(lines)


def _read_text(path: Path) -> str:
    raw = path.read_bytes()
    for enc in ("utf-8-sig", "cp1252"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("latin-1")


def _fit_image(source: bytes | Any) -> bytes:
    """PNG bytes, scaled down so the long side is at most MAX_IMAGE_SIDE."""
    from PIL import Image

    img = Image.open(io.BytesIO(source)) if isinstance(source, (bytes, bytearray)) else source
    if img.mode not in ("RGB", "L"):
        img = img.convert("RGB")
    longest = max(img.size)
    if longest > MAX_IMAGE_SIDE:
        scale = MAX_IMAGE_SIDE / longest
        img = img.resize((max(1, round(img.width * scale)), max(1, round(img.height * scale))))
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    return buf.getvalue()


def parse_pages(spec: str | None) -> list[int] | None:
    """``"1-3,5"`` -> ``[1, 2, 3, 5]``. Empty or None -> None (all pages)."""
    if spec is None or not str(spec).strip():
        return None
    pages: list[int] = []
    try:
        for part in str(spec).replace(" ", "").split(","):
            if "-" in part:
                a, b = part.split("-", 1)
                pages.extend(range(int(a), int(b) + 1))
            elif part:
                pages.append(int(part))
    except ValueError as exc:
        raise DocumentError(f"Bad page list '{spec}'. Use a form like '1-3,5'.") from exc
    return sorted(set(pages))


# ---------------------------------------------------------------------------
# The tool
# ---------------------------------------------------------------------------

READ_DOCUMENT_DESCRIPTION = (
    "Read one input document from the bundle by its file name. "
    "Spreadsheets (XLSX/CSV) come back as a grid with the real row numbers and column letters; "
    "cite those row numbers in flag sources. PDFs come back as text per page, plus page images "
    "for scanned pages. Text files come back with line numbers, and image files as images. "
    "Use `pages` (e.g. '1-3,5') to read part of a long PDF, and `page_images=true` to get images "
    "of every requested page when the text looks wrong or incomplete."
)


class ReadDocumentInput(BaseModel):
    file_name: str = Field(description="Exact file name from the bundle's document list, e.g. 'stock_report.xlsx'.")
    pages: str | None = Field(default=None, description="PDF only: pages to read, e.g. '1-3,5'. Default: all.")
    page_images: bool = Field(default=False, description="PDF only: attach an image of every requested page.")


def make_read_document_tool(bundle_dir: Path | str) -> StructuredTool:
    """The ``read_document`` tool, bound to one bundle."""

    def read_document(file_name: str, pages: str | None = None, page_images: bool = False) -> list[dict[str, Any]]:
        path = resolve_document(bundle_dir, file_name)
        doc = load_document(path, pages=parse_pages(pages), images="always" if page_images else "auto")
        return doc.content_blocks()

    return StructuredTool.from_function(
        func=read_document,
        name="read_document",
        description=READ_DOCUMENT_DESCRIPTION,
        args_schema=ReadDocumentInput,
        response_format="content",
        handle_tool_error=True,
    )


def document_list_text(bundle_dir: Path | str) -> str:
    """A short list of the bundle's documents, for a prompt."""
    docs = list_documents(bundle_dir)
    if not docs:
        return "(no documents)"
    return "\n".join(f"- {d['name']} ({d['type']}, {d['size_bytes'] // 1024 or 1} KB)" for d in docs)
