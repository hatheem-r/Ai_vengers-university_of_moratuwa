"""Tests for project/tools/loaders.py with a generated, deliberately messy bundle."""

import base64
import io
from datetime import datetime
from pathlib import Path

import openpyxl
import pytest
from langchain_core.messages import ToolMessage
from PIL import Image, ImageDraw
from pydantic import BaseModel

from project.core.harness import RunBudget, create_harness_agent, run_agent
from project.tools.loaders import (
    DocumentError,
    column_letter,
    document_list_text,
    list_documents,
    load_document,
    make_read_document_tool,
    parse_pages,
    resolve_document,
)
from tests.conftest import ScriptedChatModel, ai


def make_text_pdf(path: Path, pages: list[list[str]]) -> None:
    """Write a minimal valid PDF with a real text layer (one list of lines per page)."""
    objs: list[bytes] = []
    n = len(pages)
    page_ids = [3 + 2 * i for i in range(n)]
    font_id = 3 + 2 * n
    objs.append(b"<< /Type /Catalog /Pages 2 0 R >>")
    objs.append(f"<< /Type /Pages /Kids [{' '.join(f'{p} 0 R' for p in page_ids)}] /Count {n} >>".encode())
    for i, lines in enumerate(pages):
        ops = ["BT", "/F1 12 Tf", "72 720 Td", "14 TL"]
        for line in lines:
            ops.append(f"({line}) Tj T*")
        ops.append("ET")
        stream = "\n".join(ops).encode()
        objs.append(
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 {font_id} 0 R >> >> "
            f"/Contents {page_ids[i] + 1} 0 R >>".encode()
        )
        objs.append(b"<< /Length %d >>\nstream\n" % len(stream) + stream + b"\nendstream")
    objs.append(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    out = io.BytesIO()
    out.write(b"%PDF-1.4\n")
    offsets = []
    for i, body in enumerate(objs, 1):
        offsets.append(out.tell())
        out.write(f"{i} 0 obj\n".encode() + body + b"\nendobj\n")
    xref = out.tell()
    out.write(f"xref\n0 {len(objs) + 1}\n0000000000 65535 f \n".encode())
    for off in offsets:
        out.write(f"{off:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {len(objs) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    path.write_bytes(out.getvalue())


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "dev_A_S1_clean"
    inputs = root / "inputs"
    inputs.mkdir(parents=True)
    # Hidden files (must never be readable)
    (root / "state.json").write_text("{}")
    (root / "answer_key.json").write_text("{}")
    (inputs / "manifest.json").write_text("{}")

    # Messy Excel: title row, blank rows, header on row 3, a date, two sheets
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Stock"
    ws["A1"] = "Weekly stock report"
    ws.append([])
    ws["A3"], ws["B3"], ws["C3"], ws["D3"] = "Item code", "On hand", "UoM", "Counted"
    ws["A4"], ws["B4"], ws["C4"], ws["D4"] = "FAB-PIQ-NVY", 367.2, "kg", datetime(2026, 11, 20)
    ws["A6"], ws["B6"], ws["C6"] = "BTN-4H-15L", 12000, "pcs"
    other = wb.create_sheet("Notes")
    other["B2"] = "Counted by | J. Perera\nnight shift"
    wb.save(inputs / "stock_report.xlsx")

    # Ragged, semicolon-separated CSV with a blank line
    (inputs / "orders.csv").write_text("po;item;qty;due\nPO-4471;POLO-PIQ-NVY;2400;2026-12-08\n\nPO-4472;POLO;100\n", encoding="utf-8")
    (inputs / "maintenance.txt").write_bytes("LINE-2 down 1–2 Dec for service\nCUT-01 ok\n".encode("cp1252"))
    make_text_pdf(inputs / "customer_po_4471.pdf", [
        ["PURCHASE ORDER PO-4471", "Item: POLO-PIQ-NVY  Qty: 2400 pcs", "Delivery date: 2026-12-08"],
        ["Terms: FOB Colombo", "Page two text for the second page of this order"],
    ])
    scan = Image.new("RGB", (1700, 2200), "white")
    ImageDraw.Draw(scan).text((100, 100), "DELIVERY NOTE DN-88 FAB-PIQ-NVY 300 kg", fill="black")
    scan.save(inputs / "delivery_note_scan.pdf")
    Image.new("RGB", (3200, 1000), "gray").save(inputs / "photo_of_board.png")
    (inputs / "random.docx").write_bytes(b"not supported")
    return root


# --- bundle access ----------------------------------------------------------

def test_list_documents_skips_hidden_and_unsupported(bundle):
    names = [d["name"] for d in list_documents(bundle)]
    assert names == ["customer_po_4471.pdf", "delivery_note_scan.pdf", "maintenance.txt", "orders.csv", "photo_of_board.png", "stock_report.xlsx"]
    assert "orders.csv (csv" in document_list_text(bundle)


@pytest.mark.parametrize("name", ["../state.json", "..\\answer_key.json", "answer_key.json", "manifest.json", "/etc/passwd", "inputs/orders.csv", "", "missing.pdf", "random.docx"])
def test_resolve_refuses_anything_outside_inputs(bundle, name):
    with pytest.raises(DocumentError):
        resolve_document(bundle, name)


def test_bundle_without_inputs_folder_uses_folder_itself(tmp_path):
    (tmp_path / "a.csv").write_text("x,y\n1,2\n")
    assert resolve_document(tmp_path, "a.csv").name == "a.csv"


# --- spreadsheets -----------------------------------------------------------

def test_excel_keeps_real_row_numbers_and_columns(bundle):
    doc = load_document(bundle / "inputs" / "stock_report.xlsx")
    stock, notes = doc.sheets
    assert stock.name == "Stock" and notes.name == "Notes"
    rows = dict(stock.rows)
    assert rows[1] == ["Weekly stock report"]
    assert rows[3] == ["Item code", "On hand", "UoM", "Counted"]
    assert rows[4] == ["FAB-PIQ-NVY", "367.2", "kg", "2026-11-20"]
    assert rows[6] == ["BTN-4H-15L", "12000", "pcs"]
    assert 2 not in rows and 5 not in rows  # blank rows omitted, numbering unchanged
    text = doc.to_text()
    assert "row | A | B | C | D" in text and "4 | FAB-PIQ-NVY | 367.2 | kg | 2026-11-20" in text
    # Cell with a pipe and a newline stays on one line and does not break the grid
    assert dict(notes.rows)[2] == ["", "Counted by / J. Perera night shift"]


def test_csv_ragged_rows_and_line_numbers(bundle):
    doc = load_document(bundle / "inputs" / "orders.csv")
    rows = dict(doc.sheets[0].rows)
    assert rows[1] == ["po", "item", "qty", "due"]
    assert rows[2] == ["PO-4471", "POLO-PIQ-NVY", "2400", "2026-12-08"]
    assert rows[4] == ["PO-4472", "POLO", "100"]


def test_txt_with_line_numbers_and_cp1252(bundle):
    doc = load_document(bundle / "inputs" / "maintenance.txt")
    assert "   1: LINE-2 down 1–2 Dec for service" in doc.text


# --- PDFs and images --------------------------------------------------------

def test_text_pdf_gives_text_per_page_without_images(bundle):
    doc = load_document(bundle / "inputs" / "customer_po_4471.pdf")
    assert doc.n_pages == 2 and [p.page for p in doc.pages] == [1, 2]
    assert "Delivery date: 2026-12-08" in doc.pages[0].text
    assert all(p.image_png is None for p in doc.pages)
    assert len(doc.content_blocks()) == 1


def test_scanned_pdf_page_gets_image(bundle):
    doc = load_document(bundle / "inputs" / "delivery_note_scan.pdf")
    page = doc.pages[0]
    assert page.text.strip() == "" and page.image_png
    img = Image.open(io.BytesIO(page.image_png))
    assert max(img.size) <= 1568
    blocks = doc.content_blocks()
    assert [b["type"] for b in blocks] == ["text", "text", "image"]
    assert "no text layer" in blocks[0]["text"]
    assert blocks[2]["mime_type"] == "image/png" and base64.b64decode(blocks[2]["base64"])[:4] == b"\x89PNG"


def test_page_selection_and_forced_images(bundle):
    doc = load_document(bundle / "inputs" / "customer_po_4471.pdf", pages=[2], images="always")
    assert [p.page for p in doc.pages] == [2] and doc.pages[0].image_png
    with pytest.raises(DocumentError, match="do not exist"):
        load_document(bundle / "inputs" / "customer_po_4471.pdf", pages=[3])


def test_large_image_is_scaled_down(bundle):
    doc = load_document(bundle / "inputs" / "photo_of_board.png")
    data, mime = doc.image
    assert mime == "image/png" and Image.open(io.BytesIO(data)).size == (1568, 490)


def test_corrupt_pdf_gives_clear_error(bundle):
    bad = bundle / "inputs" / "broken.pdf"
    bad.write_bytes(b"%PDF-1.4 garbage")
    with pytest.raises(DocumentError, match="Could not read 'broken.pdf'"):
        load_document(bad)


# --- helpers ----------------------------------------------------------------

def test_parse_pages_and_column_letters():
    assert parse_pages("1-3, 5,2") == [1, 2, 3, 5]
    assert parse_pages(None) is None and parse_pages("  ") is None
    with pytest.raises(DocumentError):
        parse_pages("one")
    assert [column_letter(i) for i in (0, 25, 26, 51, 52)] == ["A", "Z", "AA", "AZ", "BA"]


# --- the tool, inside the real harness --------------------------------------

class Done(BaseModel):
    ok: bool


def test_read_document_tool_in_agent_loop(bundle, events, tracer):
    tool = make_read_document_tool(bundle)
    model = ScriptedChatModel(script=[
        ai(tool_calls=[("read_document", {"file_name": "delivery_note_scan.pdf"}),
                       ("read_document", {"file_name": "../state.json"})]),
        ai(tool_calls=[("Done", {"ok": True})]),
    ], seen=[])
    handle = create_harness_agent(name="agent", model=model, tools=[tool], system_prompt="x",
                                  tracer=tracer, budget=RunBudget(), output_schema=Done)
    result = run_agent(handle, "read it")
    assert result.ok, result.error

    tool_msgs = [m for m in model.seen[1] if isinstance(m, ToolMessage)]
    ok_msg, err_msg = tool_msgs
    assert isinstance(ok_msg.content, list) and any(b.get("type") == "image" for b in ok_msg.content)
    assert err_msg.status == "error" and "plain file name" in err_msg.content

    # Tool calls run in parallel, so find each event by its argument, not its position.
    tool_events = [e for e in events if e.type == "tool_call"]
    assert len(tool_events) == 2
    assert all("base64" not in e.summary for e in tool_events)
    err_ev = next(e for e in tool_events if "(error)" in e.summary)
    ok_ev = next(e for e in tool_events if e is not err_ev)
    assert err_ev.summary.startswith("read_document (error)")
    ok_payload = ok_ev.payload or __import__("json").loads((tracer.run_dir / ok_ev.payload_ref).read_text())
    assert "[image image/png]" in ok_payload["result"] and "base64" not in ok_payload["result"]
