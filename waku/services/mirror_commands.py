"""Validated transfer commands matching the connected stock Mirrorz parser."""

from __future__ import annotations

import ast
import re
from urllib.parse import parse_qs, urlencode, urlsplit, urlunsplit

ALIASES = {
    "mirror": "mirror",
    "m": "mirror",
    "qbmirror": "qbmirror",
    "qm": "qbmirror",
    "qbm": "qbmirror",
    "jdmirror": "jdmirror",
    "jm": "jdmirror",
    "nzbmirror": "nzbmirror",
    "nm": "nzbmirror",
    "leech": "leech",
    "l": "leech",
    "qbleech": "qbleech",
    "ql": "qbleech",
    "qbl": "qbleech",
    "jdleech": "jdleech",
    "jl": "jdleech",
    "nzbleech": "nzbleech",
    "nl": "nzbleech",
    "ytdl": "ytdl",
    "y": "ytdl",
    "ytdlleech": "ytdlleech",
    "yl": "ytdlleech",
    "clone": "clone",
    "cl": "clone",
    "uphoster": "uphoster",
    "up": "uphoster",
}
LEECH_MODES = frozenset({"leech", "qbleech", "jdleech", "nzbleech", "ytdlleech"})
_COMMON = frozenset(
    {
        "-doc",
        "-med",
        "-z",
        "-sv",
        "-ss",
        "-f",
        "-fd",
        "-fu",
        "-hl",
        "-bt",
        "-ut",
        "-sp",
        "-n",
        "-m",
        "-meta",
        "-up",
        "-ud",
        "-gc",
        "-rcf",
        "-t",
        "-ca",
        "-cv",
        "-ns",
        "-tl",
        "-ff",
        "-s",
    }
)
_MIRROR = _COMMON | {"-d", "-j", "-e", "-ad", "-yt", "-seedr", "-au", "-ap", "-h"}
_YTDL = _COMMON | {"-opt"}
_CLONE = frozenset(
    {
        "-n",
        "-up",
        "-gc",
        "-rcf",
        "-sync",
        "-ud",
        "-ct",
        "-ex",
        "-mn",
        "-xn",
        "-mc",
        "-xc",
        "-fwd",
    }
)
_BOOL = frozenset(
    {
        "-doc",
        "-med",
        "-s",
        "-j",
        "-f",
        "-fd",
        "-fu",
        "-sync",
        "-hl",
        "-bt",
        "-ut",
        "-yt",
        "-ad",
        "-fwd",
        "-d",
        "-e",
        "-z",
        "-sv",
        "-ss",
        "-seedr",
    }
)
_VALUELESS = _BOOL - {"-d", "-e", "-z", "-sv", "-ss", "-seedr"}
MAX_BATCH = 20


def normalize_mode(mode):
    if not isinstance(mode, str) or mode.lower().lstrip("/") not in ALIASES:
        raise ValueError(
            "Unsupported transfer mode; choose a documented Mirrorz transfer command."
        )
    return ALIASES[mode.lower().lstrip("/")]


def validate_source(value, mode="mirror"):
    """A single source token, never a command or an argument list."""
    if (
        not isinstance(value, str)
        or not 1 <= len(value) <= 3500
        or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value)
    ):
        return None
    try:
        parsed = urlsplit(value)
        # These wrappers treat every non-HTTP/FTP token as a Hydra ID and
        # rewrite the complete message, including trailing metadata. Use a
        # direct source link (or stage a Telegram attachment) for reliable
        # correlation; do not accidentally send a magnet as a Hydra ID.
        if normalize_mode(mode) in {
            "nzbmirror",
            "nzbleech",
            "uphoster",
        } and parsed.scheme not in {"http", "https", "ftp"}:
            return None
        if parsed.scheme.lower() in {
            "http",
            "https",
            "ftp",
            "rtmp",
            "rtmps",
            "mms",
            "rtsp",
        }:
            parsed.port
            return (
                value
                if parsed.hostname
                and parsed.username is None
                and parsed.password is None
                else None
            )
        if parsed.scheme == "magnet":
            xt = parse_qs(parsed.query).get("xt", [])
            return (
                value
                if any(
                    re.fullmatch(
                        r"urn:(?:btih:[A-Za-z0-9]{32,40}|btmh:[A-Za-z0-9]+)", item
                    )
                    for item in xt
                )
                else None
            )
        if parsed.scheme == "tg" and parsed.netloc == "openmessage":
            query = parse_qs(parsed.query)
            return (
                value
                if set(query) == {"user_id", "message_id"}
                and all(
                    len(items) == 1 and re.fullmatch(r"[0-9]+(?:-[0-9]+)?", items[0])
                    for items in query.values()
                )
                else None
            )
        # Mirrorz accepts configured rclone remotes and Drive IDs directly.
        if re.fullmatch(
            r"(?:mrcc:)?[A-Za-z0-9_.-]+:(?!//)[^\s]*", value
        ) and parsed.scheme not in {"file", "javascript", "data", "magnet", "tg"}:
            return value
        if re.fullmatch(
            r"(?:(?:tp|sa|mtp):)?(?:[A-Za-z0-9_-]{19}|[A-Za-z0-9_-]{33}|root)|gdl|rcl",
            value,
        ):
            return value
    except ValueError:
        pass
    return None


def normalize_options(mode, options):
    if options is not None and not isinstance(options, dict):
        raise ValueError(
            "Options must map supported flags to true/false or their exact value."
        )
    result = dict(options or {})
    if normalize_mode(mode) in {"nzbmirror", "nzbleech"}:
        # NZB's stock wrapper auto-extracts by default. Put it on the first
        # command line, before our separate correlation marker.
        result.setdefault("-e", True)
    if normalize_mode(mode) in {"ytdl", "ytdlleech"} and not result.get("-s"):
        raw = result.get("-opt")
        try:
            yt_options = (
                ast.literal_eval(raw)
                if isinstance(raw, str)
                else {}
                if not raw
                else None
            )
        except (ValueError, SyntaxError):
            raise ValueError(
                "The -opt value must be a valid yt-dlp options dictionary."
            ) from None
        if not isinstance(yt_options, dict):
            raise ValueError("The -opt value must be a yt-dlp options dictionary.")
        # Avoid a bot-owned quality picker that Waku cannot click. Preserve
        # explicit requested formats; otherwise use yt-dlp's standard best.
        yt_options.setdefault("format", "bv*+ba/b")
        result["-opt"] = repr(yt_options)
    return result


def render_options(mode, options):
    mode = normalize_mode(mode)
    allowed = (
        _CLONE
        if mode == "clone"
        else _YTDL
        if mode in {"ytdl", "ytdlleech"}
        else _MIRROR
    )
    if options is None:
        return ""
    if not isinstance(options, dict):
        raise ValueError(
            "Options must map supported flags to true/false or their exact value."
        )
    parts = []
    for flag, value in sorted(options.items()):
        if flag in {"-i", "-b"}:
            raise ValueError(
                "For multiple links use urls; Waku tracks each task separately instead of native -i/-b."
            )
        if flag not in allowed:
            raise ValueError(f"Option {flag!r} is not supported for {mode}.")
        if value is False:
            continue
        if value is True:
            if flag not in _BOOL:
                raise ValueError(f"Option {flag} requires a value.")
            parts.append(flag)
            continue
        if not isinstance(value, str) or not value.strip() or flag in _VALUELESS:
            raise ValueError(f"Invalid value for option {flag}.")
        if (
            any(ord(char) < 32 or ord(char) == 127 for char in value)
            or "Waku-Request:" in value
        ):
            raise ValueError(
                "Option values must be single-line text without control markers."
            )
        # Mirrorz splits on literal spaces, not shell quoting. A recognised
        # flag inside a normal value would silently become another option.
        if any(token in allowed | {"-i", "-b"} for token in value.split(" ")):
            if flag != "-ff" or not (value.startswith("[") and value.endswith("]")):
                raise ValueError(
                    f"Option {flag} contains another flag token; pass options separately."
                )
        parts.extend((flag, value.strip()))
    rendered = " ".join(parts)
    if len(rendered) > 2000:
        raise ValueError("Transfer options are too long.")
    return rendered


def expand_sources(sources, mode="mirror"):
    """Expand Telegram ranges so native generated child tasks cannot be lost."""
    result = []
    for source in sources:
        if validate_source(source, mode) is None:
            raise ValueError("Invalid transfer source.")
        parsed = urlsplit(source)
        span = None
        if normalize_mode(mode) != "clone":
            if parsed.hostname in {
                "t.me",
                "telegram.me",
                "telegram.dog",
                "telegram.space",
            }:
                span = re.fullmatch(r"(.+/)(\d+)-(\d+)", parsed.path)
                if span:
                    lo, hi = int(span[2]), int(span[3])
                    make_source = lambda i: urlunsplit(
                        parsed._replace(path=f"{span[1]}{i}")
                    )
            elif parsed.scheme == "tg" and parsed.netloc == "openmessage":
                query = parse_qs(parsed.query)
                span = re.fullmatch(r"(\d+)-(\d+)", query.get("message_id", [""])[0])
                if span:
                    lo, hi = int(span[1]), int(span[2])
                    make_source = lambda i: urlunsplit(
                        parsed._replace(
                            query=urlencode(
                                {"user_id": query["user_id"][0], "message_id": i}
                            )
                        )
                    )
        if span:
            if lo <= 0 or hi < lo or len(result) + hi - lo + 1 > MAX_BATCH:
                raise ValueError(
                    "Telegram message ranges must contain 1–20 messages in ascending order."
                )
            result.extend(make_source(i) for i in range(lo, hi + 1))
        else:
            result.append(source)
        if len(result) > MAX_BATCH:
            raise ValueError("A transfer request can contain at most 20 sources.")
    return result


def build_command(mode, username, source, options, request_id):
    mode = normalize_mode(mode)
    if (
        re.fullmatch(r"[A-Za-z0-9_]{5,32}", username) is None
        or re.fullmatch(r"[0-9a-f]{32}", request_id) is None
    ):
        raise ValueError("Invalid transfer command identity.")
    if validate_source(source, mode) is None:
        raise ValueError("Invalid transfer source.")
    suffix = render_options(mode, normalize_options(mode, options))
    command = (
        f"/{mode}@{username} {source}"
        + (f" {suffix}" if suffix else "")
        + f"\nWaku-Request: {request_id}"
    )
    if len(command.encode("utf-16-le")) > 8000:
        raise ValueError("Transfer command exceeds Telegram's message length.")
    return command
