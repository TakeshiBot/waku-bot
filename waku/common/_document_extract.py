"""Isolated document parser: bounded memory/CPU, no Telegram or bot imports."""

import io
import json
import sys
import xml.etree.ElementTree as ET
import zipfile

MAX_BYTES = 10 * 1024 * 1024
MAX_CHARS = 12000


def parse(data, kind):
    truncated = False
    if kind == "text":
        encoding = (
            "utf-16" if data.startswith((b"\xff\xfe", b"\xfe\xff")) else "utf-8-sig"
        )
        text = data.decode(encoding)
        if "\x00" in text:
            return {"content_status": "binary_content_metadata_only"}
    elif kind == "docx":
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            info = archive.getinfo("word/document.xml")
            if info.file_size > 2 * 1024 * 1024:
                return {"content_status": "document_xml_too_large"}
            root = ET.fromstring(archive.read(info))
        ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
        lines = []
        size = 0
        for paragraph in root.iter(ns + "p"):
            line = "".join(node.text or "" for node in paragraph.iter(ns + "t"))
            lines.append(line)
            size += len(line) + 1
            if size > MAX_CHARS:
                truncated = True
                break
        text = "\n".join(lines)
    elif kind == "pdf":
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(data), strict=False)
        if reader.is_encrypted and not reader.decrypt(""):
            return {"content_status": "encrypted_metadata_only"}
        lines = []
        size = 0
        truncated = len(reader.pages) > 30
        for page in reader.pages[:30]:
            stream = page.get_contents()
            if stream is not None and len(stream.get_data()) > 2 * 1024 * 1024:
                return {"content_status": "pdf_page_too_large"}
            line = page.extract_text() or ""
            lines.append(line)
            size += len(line) + 1
            if size > MAX_CHARS:
                truncated = True
                break
        text = "\n".join(lines)
    else:
        return {"content_status": "unsupported_format_metadata_only"}
    text = text.strip()
    if not text:
        return {"content_status": "no_extractable_text_metadata_only"}
    return {
        "content_status": "truncated" if truncated or len(text) > MAX_CHARS else "read",
        "text": text[:MAX_CHARS],
    }


if __name__ == "__main__":
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_AS, (512 * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
    except ImportError:
        pass
    try:
        data = sys.stdin.buffer.read(MAX_BYTES + 1)
        result = (
            parse(data, sys.argv[1])
            if len(data) <= MAX_BYTES
            else {"content_status": "too_large"}
        )
    except Exception:
        result = {"content_status": "read_failed_metadata_only"}
    sys.stdout.buffer.write(json.dumps(result, ensure_ascii=False).encode("utf-8"))
