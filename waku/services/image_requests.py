"""Shared image captions and explicit opt-in to model generation/editing."""

import re
import unicodedata

GENERATED_IMAGE_CAPTION = "Tạo bởi waku meow~!"

_NEGATED = re.compile(
    r"\b(?:đừng|dung|không|khong|ko|chưa|chua|don't|dont|do not|never|without)\b(?:\s+\w+){0,4}\s*$",
    re.IGNORECASE,
)
_EXPLANATION = re.compile(
    r"\b(?:là gì|la gi|là sao|la sao|làm thế nào|lam the nao|how to|what is|explain)\b",
    re.IGNORECASE,
)
_REPORTED = re.compile(
    r"\b(?:đã|da|vừa|vua|từng|tung|already|previously)\b\s*$",
    re.IGNORECASE,
)
_IMAGE = (
    r"(?:ảnh|anh|hình|hinh|tranh|image|picture|photo|illustration|artwork|avatar|logo)"
)
_GENERATE = re.compile(
    rf"\b(?:tạo|generate|create|thiết kế|thiet ke)\b.{{0,60}}\b{_IMAGE}\b"
    rf"|\btao\s+(?:(?:cho|giup|minh|toi|t|mot|buc)\s+){{0,5}}{_IMAGE}\b"
    rf"|\bve\s+(?:(?:cho|giup|minh|toi|t|mot|buc)\s+){{0,5}}{_IMAGE}\b"
    r"|\b(?:vẽ|draw|paint|render)\b\s+\S",
    re.IGNORECASE,
)
_EDIT = re.compile(
    rf"\b(?:sửa|sua|chỉnh|chinh|edit|modify|transform|retouch)\b.{{0,60}}\b{_IMAGE}\b"
    rf"|\b{_IMAGE}\b.{{0,60}}\b(?:sửa|sua|chỉnh|chinh|edit|modify|transform|retouch)\b",
    re.IGNORECASE,
)
_EDIT_ACTION = re.compile(
    r"\b(?:sửa|sua|chỉnh|chinh|edit|modify|transform|retouch)\b",
    re.IGNORECASE,
)


def explicit_image_operation(text: str, operation: str) -> bool:
    """Inspect only the caller's current text, never an AI-supplied prompt."""
    if operation not in {"generate", "edit"}:
        return False
    pattern = _GENERATE if operation == "generate" else _EDIT
    text = unicodedata.normalize("NFC", text or "")
    for clause in re.split(r"[.!?;\n]", text):
        if _EXPLANATION.search(clause):
            continue
        for match in pattern.finditer(clause):
            action = _EDIT_ACTION.search(match.group()) if operation == "edit" else None
            start = match.start() + (action.start() if action else 0)
            prefix = clause[:start]
            if not _NEGATED.search(prefix) and not _REPORTED.search(prefix):
                return True
    return False
