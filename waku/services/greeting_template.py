"""HTML greetings with current {user} and legacy $user placeholders."""

import html
import re

_FIELDS = "user_html|user|name|chat|id"
_TOKEN = re.compile(
    r"\$\$(?:\{[a-zA-Z_]\w*\}|[a-zA-Z_]\w*)?"
    rf"|\$\{{({_FIELDS})\}}|\$({_FIELDS})\b|\{{({_FIELDS})\}}"
)


def render_greeting(template: str, *, user, chat_title: str) -> str:
    name = html.escape(user.full_name or user.first_name or str(user.id))
    uid = str(int(user.id))
    mention = f'<a href="tg://user?id={uid}">{name}</a>'
    values = {
        "user": mention,
        "user_html": mention,
        "name": name,
        "chat": html.escape(chat_title or ""),
        "id": uid,
    }

    def replace(match):
        if match[0].startswith("$$"):
            return match[0][1:]
        return values[next(group for group in match.groups() if group is not None)]

    # One pass: placeholders inside member names/group titles remain literal.
    return _TOKEN.sub(replace, template)
