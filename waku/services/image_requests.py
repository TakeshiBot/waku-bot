"""Shared image captions and explicit opt-in to model generation/editing."""

import re
import unicodedata

GENERATED_IMAGE_CAPTION = "Tạo bởi waku meow~!"

_R18_REQUEST = re.compile(
    r"\b(?:r18|nsfw|hentai|ero|ecchi|nude|nudity|naked|porn|pornography|sex|sexual|lewd|khỏa thân|khoa than|trần truồng|tran truong)\b|18\+|🔞",
    re.IGNORECASE,
)
_R18_NEGATED = re.compile(
    r"\b(?:no|not|non|without|avoid|đừng|dung|không|khong|ko)\b(?:\s+\w+){0,4}[\s-]*$",
    re.IGNORECASE,
)
_NON_IMAGE_DRAW = re.compile(
    r"\b(?:draw\s+(?:conclusions?|comparisons?|parallels?|attention|lots|cards|money)|render\s+(?:this\s+|the\s+)?(?:webpage|web page|html|website|component|template)|vẽ\s+(?:đường đi|kết luận))\b",
    re.IGNORECASE,
)


def contains_r18_request(text: str) -> bool:
    text = unicodedata.normalize("NFC", text or "")
    return any(not _R18_NEGATED.search(text[:match.start()]) for match in _R18_REQUEST.finditer(text))


def safe_image_prompt(prompt: str, *, r18: bool) -> str:
    if r18:
        return prompt
    return (
        prompt
        + "\nKeep the resulting image safe for work: no nudity or sexually explicit content."
    )


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
            if operation == "generate" and _NON_IMAGE_DRAW.match(clause, match.start()):
                continue
            action = _EDIT_ACTION.search(match.group()) if operation == "edit" else None
            start = match.start() + (action.start() if action else 0)
            prefix = clause[:start]
            if not _NEGATED.search(prefix) and not _REPORTED.search(prefix):
                return True
    return False
