"""Match natural target names only against Telegram-sourced identities."""

from __future__ import annotations

import re
import unicodedata


def normalized_name(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).casefold().replace("đ", "d")
    return " ".join("".join(c for c in value if not unicodedata.combining(c)).split())


def _contains(text: str, token: str, *, allow_at: bool = False) -> bool:
    if not token:
        return False
    prefix = r"(?<!\w)" if allow_at else r"(?<![\w@])"
    return re.search(prefix + re.escape(token) + r"(?!\w)", text) is not None


def matching_candidate_ids(candidates, target: str) -> set[int]:
    value = normalized_name(target.lstrip("@").strip())
    if not value:
        return set()
    return {
        candidate.user_id
        for candidate in candidates
        if value in {
            normalized_name(candidate.full_name),
            normalized_name(candidate.username or ""),
        }
    }


def named_candidate_ids(text: str, candidates, *, allow_display_at=False) -> set[int]:
    text = normalized_name(text)
    names: dict[str, set[int]] = {}
    usernames: dict[str, set[int]] = {}
    for candidate in candidates:
        name = normalized_name(candidate.full_name)
        username = normalized_name(candidate.username or "")
        if name:
            names.setdefault(name, set()).add(candidate.user_id)
        if username:
            usernames.setdefault(username, set()).add(candidate.user_id)
    found = set()
    for mapping, allow_at in ((names, allow_display_at), (usernames, True)):
        for name, ids in mapping.items():
            if len(ids) == 1 and _contains(text, name, allow_at=allow_at):
                found.update(ids)
    return found


