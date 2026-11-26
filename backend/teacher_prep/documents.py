"""Text extraction shared by lesson sources and the teacher knowledge base."""
from __future__ import annotations

from pathlib import Path


def extract_text(path):
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8-sig")
    elif suffix == ".docx":
        from docx import Document
        document = Document(path)
        text = "\n".join(
            [p.text for p in document.paragraphs] +
            [" | ".join(c.text for c in row.cells) for table in document.tables for row in table.rows]
        )
    elif suffix == ".pdf":
        from pypdf import PdfReader
        document = PdfReader(path)
        if document.is_encrypted:
            raise ValueError("暂不支持加密 PDF")
        text = "\n".join(f"[第{index}页]\n{page.extract_text() or ''}" for index, page in enumerate(document.pages, 1))
        if not any((page.extract_text() or "").strip() for page in document.pages):
            raise ValueError("PDF没有可提取文本，图片型教材尚未验证支持")
    else:
        raise ValueError("首版支持 TXT、MD、DOCX 和可提取文本的 PDF")
    if not text.strip():
        raise ValueError("资料没有可提取内容")
    return text
