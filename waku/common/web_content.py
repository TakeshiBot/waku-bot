"""HTML reduction shared by Telegram and Discord, retaining real media URLs."""

from urllib.parse import urljoin, urlparse


def web_page_text(raw: bytes, url: str = "") -> str:
    try:
        from bs4 import BeautifulSoup

        soup = BeautifulSoup(raw, "html.parser")
        if not soup.find():
            return raw.decode("utf-8", errors="replace")

        def web_url(value: object) -> str | None:
            if not isinstance(value, str) or not value.strip():
                return None
            resolved = urljoin(url, value.strip())
            return resolved if urlparse(resolved).scheme in ("http", "https") else None

        base = soup.find("base", href=True)
        if base is not None:
            url = web_url(base.get("href")) or url
        for tag in soup(["script", "style", "nav", "footer", "aside"]):
            tag.decompose()
        for tag in soup.find_all(["img", "source"]):
            if tag.name == "source" and tag.find_parent("picture") is None:
                continue
            sources: list[str] = []
            for attr in ("data-src", "data-lazy-src", "data-original", "src"):
                source = web_url(tag.get(attr))
                if source and source not in sources:
                    sources.append(source)
            for attr in ("data-srcset", "srcset"):
                value = tag.get(attr)
                if isinstance(value, str):
                    for candidate in value.split(","):
                        parts = candidate.strip().split()
                        source = web_url(parts[0]) if parts else None
                        if source and source not in sources:
                            sources.append(source)
            label = str(tag.get("alt") or "image").replace("\n", " ")
            tag.replace_with(
                "\n" + "\n".join(f"![{label}]({src})" for src in sources) + "\n"
            )
        for tag in soup.find_all("a", href=True):
            target = web_url(tag.get("href"))
            if target:
                label = tag.get_text(" ", strip=True) or target
                tag.replace_with(f"[{label}]({target})")
        for tag in soup.find_all("meta"):
            if (
                tag.get("property") in ("og:image", "og:image:url")
                or tag.get("name") == "twitter:image"
            ):
                source = web_url(tag.get("content"))
                if source:
                    tag.replace_with(f"\n![preview image]({source})\n")
        return soup.get_text("\n", strip=True)
    except Exception:
        return raw.decode("utf-8", errors="replace")
