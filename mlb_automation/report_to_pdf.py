#!/usr/bin/env python3
import argparse
from pathlib import Path

PAGE_WIDTH = 612
PAGE_HEIGHT = 792
LEFT_MARGIN = 40
TOP_Y = 760
LINE_HEIGHT = 14
MAX_LINES_PER_PAGE = 48


def escape_pdf_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def normalize_lines(text: str) -> list[str]:
    out = []
    for raw in text.splitlines():
        line = raw.replace("\t", "    ")
        if not line:
            out.append("")
            continue
        while len(line) > 95:
            cut = line[:95]
            split_at = cut.rfind(" ")
            if split_at < 40:
                split_at = 95
            out.append(line[:split_at].rstrip())
            line = line[split_at:].lstrip()
        out.append(line)
    return out


def chunk_pages(lines: list[str]) -> list[list[str]]:
    if not lines:
        return [["MLB Daily Model Report"]]
    return [lines[i:i + MAX_LINES_PER_PAGE] for i in range(0, len(lines), MAX_LINES_PER_PAGE)]


def build_pdf_bytes(lines: list[str]) -> bytes:
    pages = chunk_pages(lines)
    objects: list[bytes | None] = []

    def add_obj(data: bytes | None) -> int:
        objects.append(data)
        return len(objects)

    font_id = add_obj(b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>")
    pages_id = add_obj(None)
    page_ids = []

    for page_lines in pages:
        content_lines = [
            b"BT",
            b"/F1 11 Tf",
            f"{LEFT_MARGIN} {TOP_Y} Td".encode(),
            f"{LINE_HEIGHT} TL".encode(),
        ]
        for line in page_lines:
            content_lines.append(f"({escape_pdf_text(line)}) Tj".encode())
            content_lines.append(b"T*")
        content_lines.append(b"ET")
        stream = b"\n".join(content_lines)
        content_id = add_obj(b"<< /Length " + str(len(stream)).encode() + b" >>\nstream\n" + stream + b"\nendstream")
        page_id = add_obj(
            f"<< /Type /Page /Parent {pages_id} 0 R /MediaBox [0 0 {PAGE_WIDTH} {PAGE_HEIGHT}] /Resources << /Font << /F1 {font_id} 0 R >> >> /Contents {content_id} 0 R >>".encode()
        )
        page_ids.append(page_id)

    kids = " ".join(f"{pid} 0 R" for pid in page_ids)
    objects[pages_id - 1] = f"<< /Type /Pages /Kids [{kids}] /Count {len(page_ids)} >>".encode()
    catalog_id = add_obj(f"<< /Type /Catalog /Pages {pages_id} 0 R >>".encode())

    output = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for idx, obj in enumerate(objects, start=1):
        offsets.append(len(output))
        output.extend(f"{idx} 0 obj\n".encode())
        output.extend(obj or b"")
        output.extend(b"\nendobj\n")

    xref_start = len(output)
    output.extend(f"xref\n0 {len(objects)+1}\n".encode())
    output.extend(b"0000000000 65535 f \n")
    for off in offsets[1:]:
        output.extend(f"{off:010d} 00000 n \n".encode())
    output.extend(
        f"trailer\n<< /Size {len(objects)+1} /Root {catalog_id} 0 R >>\nstartxref\n{xref_start}\n%%EOF\n".encode()
    )
    return bytes(output)


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert the markdown report into a simple PDF.")
    parser.add_argument("input", help="Input markdown/text report")
    parser.add_argument("output", help="Output PDF path")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)
    text = input_path.read_text(encoding="utf-8")
    lines = normalize_lines(text)
    pdf_bytes = build_pdf_bytes(lines)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_bytes(pdf_bytes)
    print(f"PDF written to {output_path}")


if __name__ == "__main__":
    main()
