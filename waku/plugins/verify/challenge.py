"""Pure verification challenge builders for questions and keyboards; question text comes from i18n."""

from __future__ import annotations

import math
import random
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from pyrogram.enums import ButtonStyle
from pyrogram.types import (
    CallbackQuery,
    Chat,
    ChatPermissions,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    User,
)

from waku.database.models import ChatConfig, VerificationSession
from waku.i18n import i18n

EMOJI_POOL = ["🍎", "🍌", "🍇", "🍓", "🍒", "🍑", "🥝", "🍍", "🥥", "🍉", "🍊", "🥭"]

RESTORE_PERMISSIONS_KEY = "_restore_permissions"
PERMISSION_FIELDS = (
    "can_send_messages",
    "can_send_audios",
    "can_send_documents",
    "can_send_photos",
    "can_send_videos",
    "can_send_video_notes",
    "can_send_voice_notes",
    "can_send_polls",
    "can_send_other_messages",
    "can_add_web_page_previews",
    "can_react_to_messages",
    "can_edit_tag",
    "can_change_info",
    "can_invite_users",
    "can_pin_messages",
    "can_manage_topics",
)


def restrict_permissions(method: str) -> ChatPermissions | None:
    """Permissions restricted for new members; sticker verification returns None (unrestricted)."""
    if method == "sticker":
        return None
    return ChatPermissions()


# --------------------------------------------------------------------------- Pure functions.


@dataclass
class VerifyContext:
    """Full candidate-event context for policy checks; handlers construct it and pass it to maybe_verify."""

    chat_id: int
    user: User | Chat
    is_join: bool
    text: str = ""
    is_verified: bool = False
    has_active_session: bool = False

    @property
    def is_bot(self) -> bool:
        return bool(getattr(self.user, "is_bot", False))

    @property
    def user_id(self) -> int:
        user_id = self.user.id
        if user_id is None:
            raise ValueError("user must have an ID")
        return user_id


def strategy_matches(strategy: str, ctx: VerifyContext) -> bool:
    """Check whether the policy matches this event; unknown policies skip verification. Add new policy branches here."""
    match strategy:
        case "all":
            return ctx.is_join
        case "first_message":
            return (
                not ctx.is_join and not ctx.is_verified and not ctx.has_active_session
            )
        case _:
            return False


def make_math_challenge() -> dict:
    """Arithmetic question: add two numbers from 2..9, with the answer among four options."""
    a, b = random.randint(2, 9), random.randint(2, 9)
    answer = a + b
    pool = list(range(max(0, answer - 6), answer + 7))
    pool.remove(answer)
    distractors = random.sample(pool, 3)
    options = random.sample([answer] + distractors, 4)
    return {"a": a, "b": b, "answer": answer, "options": options}


def _tpl(key: str, lang: str, **kwargs: object) -> str:
    """Format an i18n template; return the key unchanged when missing, matching i18n.t."""
    tpl = i18n.get_raw(key, locale=lang)
    return tpl.format(**kwargs) if isinstance(tpl, str) else key


def _math_data(key: str, lang: str) -> Any:
    """Get math_hard question data (template/phrasing pool) by the complete i18n key."""
    return i18n.get_raw(key, locale=lang)


def _default_questions(lang: str = "") -> list[dict]:
    """Read default questions from i18n using the same format as panel-stored verify_questions."""
    questions = i18n.get_raw("bot.msg.verify.default_questions", locale=lang)
    if not isinstance(questions, list):
        return []
    return [q for q in questions if isinstance(q, dict)]


def _fraction(numerator: int, denominator: int) -> str:
    divisor = math.gcd(numerator, denominator)
    numerator //= divisor
    denominator //= divisor
    return str(numerator) if denominator == 1 else f"{numerator}/{denominator}"


def _distractors(correct: int | str, candidates: Iterable[int | str]) -> list[str]:
    """Pick three random distractors from candidates excluding the correct answer."""
    pool = [c for c in candidates if str(c) != str(correct)]
    random.shuffle(pool)
    return [str(c) for c in pool[:3]]


def _math_html(question: str) -> str:
    """Convert inline $...$ formulas to rich-message <tg-math> tags."""
    return re.sub(r"\$([^$]+)\$", r"<tg-math>\1</tg-math>", question)


def _calculus_challenge(lang: str = "") -> dict:
    kind = random.choice(("derivative", "integral", "limit"))
    if kind == "derivative":
        a = random.randint(2, 5)
        n = random.randint(2, 4)
        x = random.choice((1, 2))
        answer = a * n * (x ** (n - 1))
        question = _tpl(
            f"bot.msg.verify.math_hard.calculus.{kind}", lang, a=a, n=n, x=x
        )
        candidates = {answer + d for d in (-3, -2, -1, 1, 2, 3, n, -n)} | {a * (x**n)}
    elif kind == "integral":
        n = random.randint(1, 3)
        k = random.randint(1, 3)
        answer = k
        question = _tpl(
            f"bot.msg.verify.math_hard.calculus.{kind}", lang, coef=k * (n + 1), n=n
        )
        candidates = {k + d for d in (-3, -2, -1, 1, 2, 3)} | {
            k * (n + 1),
            k + n,
        }
    else:  # limit
        k = random.randint(2, 5)
        answer = k
        question = _tpl(f"bot.msg.verify.math_hard.calculus.{kind}", lang, k=k)
        candidates = {k + d for d in (-3, -2, -1, 1, 2, 3)} | {k * 2}
    options = [str(answer), *_distractors(answer, candidates)]
    random.shuffle(options)
    return {"question": question, "answer": str(answer), "options": options}


def _linear_algebra_challenge(lang: str = "") -> dict:
    kind = random.choice(
        (
            "invertible_criterion",
            "rank_nullity",
            "det_scale",
            "symmetric_eigen",
            "diagonalizable",
            "trace_eigen",
            "transpose_inverse",
            "null_space_domain",
            "positive_definite",
        )
    )
    la = "bot.msg.verify.math_hard.linear_algebra"
    if kind == "invertible_criterion":
        n = random.randint(2, 4)
        data = _math_data(f"{la}.invertible_criterion", lang)
        answer = random.choice(data["answers"])
        question = data["question"].format(n=n)
        wrong = data["wrong"]
        candidates = {answer, *wrong}
    elif kind == "rank_nullity":
        n = random.randint(4, 6)
        nullity = random.choice((2, 3))
        rank = n - nullity
        answer = str(nullity)
        question = _tpl(f"{la}.rank_nullity", lang, n=n, rank=rank)
        candidates = {str(nullity + d) for d in range(-2, 3)} | {str(n), str(rank)}
    elif kind == "det_scale":
        n = random.randint(2, 4)
        c = random.randint(2, 3)
        answer = f"{c**n} det(A)"
        question = _tpl(f"{la}.det_scale", lang, n=n, c=c)
        candidates = {f"{m} det(A)" for m in (c * n, c, 1, c + 1, c**n + 1)} | {answer}
    elif kind in (
        "symmetric_eigen",
        "transpose_inverse",
        "null_space_domain",
        "positive_definite",
    ):
        frame = random.choice(_math_data(f"{la}.{kind}", lang))
        question, answer, wrong = frame["question"], frame["answer"], frame["wrong"]
        candidates = {answer, *wrong}
    elif kind == "diagonalizable":
        n = random.randint(2, 4)
        data = _math_data(f"{la}.diagonalizable", lang)
        answer = random.choice(data["answers"])
        question = data["question"].format(n=n)
        wrong = data["wrong"]
        candidates = {answer, *wrong}
    else:  # trace_eigen
        data = _math_data(f"{la}.trace_eigen", lang)
        answer = random.choice(data["answers"])
        question = data["question"]
        wrong = data["wrong"]
        candidates = {answer, *wrong}
    options = [answer, *_distractors(answer, candidates)]
    random.shuffle(options)
    return {"question": question, "answer": answer, "options": options}


def _probability_challenge(lang: str = "") -> dict:
    kind = random.choice(
        (
            "uniform_e",
            "uniform_var",
            "exp_e",
            "normal_prob",
            "binomial_e",
            "poisson_var",
            "var_scale",
            "monty_hall",
            "max_two_uniform",
            "chebyshev",
            "sum_expectation",
            "second_moment",
        )
    )
    prob = "bot.msg.verify.math_hard.probability"
    if kind == "uniform_e":
        a = random.randint(1, 4)
        b = a + 2 * random.randint(2, 4)
        answer = (a + b) // 2
        question = _tpl(f"{prob}.{kind}", lang, a=a, b=b)
        candidates = {str(answer + d) for d in range(-3, 4)} | {str(a), str(b)}
    elif kind == "uniform_var":
        b = random.choice((4, 6, 12))
        answer = _fraction(b * b, 12)
        question = _tpl(f"{prob}.{kind}", lang, b=b)
        candidates = {"4/3", "3", "12", "1/3", "1/2", "2/3", "4", "9", "16"}
    elif kind == "exp_e":
        lam = random.choice((2, 3, 4))
        answer = _fraction(1, lam)
        question = _tpl(f"{prob}.{kind}", lang, lam=lam)
        candidates = {"1/2", "1/3", "1/4", "1/5", "2/3", "1"}
    elif kind == "normal_prob":
        mu = random.randint(1, 5)
        sigma2 = random.choice((1, 4, 9))
        answer = "1/2"
        question = _tpl(f"{prob}.{kind}", lang, mu=mu, sigma2=sigma2)
        candidates = {"1/2", "1/3", "1/4", "2/3", "3/4"}
    elif kind == "binomial_e":
        n = random.choice((8, 10, 12))
        p = random.choice(("0.25", "0.5"))
        answer = _fraction(n * (5 if p == "0.25" else 10), 20)  # n*p
        question = _tpl(f"{prob}.{kind}", lang, n=n, p=p)
        candidates = {_fraction(2 * n + i, 4) for i in range(-8, 9, 2)} | {"2", "3"}
    elif kind == "poisson_var":
        lam = random.randint(2, 5)
        answer = str(lam)
        question = _tpl(f"{prob}.{kind}", lang, lam=lam)
        candidates = {str(lam + d) for d in range(-2, 3)} | {str(lam * 2), "1"}
    elif kind == "var_scale":
        c = random.choice((2, 3))
        answer = str(3 * c * c)  # X~U(0,6), Var=3
        question = _tpl(f"{prob}.{kind}", lang, c=c)
        candidates = {"3", "6", "9", "12", "18", "27", "36"}
    elif kind == "monty_hall":
        n = random.choice((3, 4, 5))
        answer = _fraction(n - 1, n * (n - 2))
        question = _tpl(f"{prob}.{kind}", lang, n=n)
        candidates = {_fraction(m - 1, m * (m - 2)) for m in (3, 4, 5)} | {
            "1/2",
            "1/3",
            "1/4",
        }
    elif kind == "max_two_uniform":
        k = random.choice((2, 3))
        answer = _fraction(k, k + 1)
        question = _tpl(f"{prob}.{kind}", lang, k=k)
        candidates = {_fraction(m, m + 1) for m in (2, 3, 4)} | {"1/2", "1/3", "5/6"}
    elif kind == "chebyshev":
        mu = 5
        sigma = random.choice((2, 3))
        k = random.choice((2, 3))
        answer = _fraction(1, k * k)
        question = _tpl(
            f"{prob}.{kind}",
            lang,
            mu=mu,
            var=sigma * sigma,
            bound=k * sigma,
        )
        candidates = {"1/2", "1/3", "1/4", "1/8", "1/9", "2/3", "1/16"}
    elif kind == "sum_expectation":
        a = random.choice((0, 2, 4))
        b = a + random.choice((4, 6))
        lam = random.choice((2, 3))
        mean = (a + b) // 2  # Mean of U(a,b); matching parity makes this an integer.
        answer = _fraction(mean * lam + 1, lam)  # Add 1/λ for Exp(λ).
        question = _tpl(f"{prob}.{kind}", lang, a=a, b=b, lam=lam)
        candidates = {"2", "7/3", "5/2", "8/3", "3", "4", "9/2", "11/3"}
    else:  # second_moment
        b = random.choice((3, 4, 6))
        answer = _fraction(b * b, 3)  # X~U(0,b): E[X^2] = b^2/3
        question = _tpl(f"{prob}.{kind}", lang, b=b)
        candidates = {"3", "16/3", "12", "9", "6", "18", "27", "32/3", "4"}
    options = [answer, *_distractors(answer, candidates)]
    random.shuffle(options)
    return {"question": question, "answer": answer, "options": options}


def make_math_hard_challenge(lang: str = "") -> dict:
    generator = random.choice(
        (_calculus_challenge, _linear_algebra_challenge, _probability_challenge)
    )
    return generator(lang)


def make_challenge_payload(method: str, questions: list[dict], lang: str = "") -> dict:
    """Build the challenge payload by method; unknown methods fall back to custom_qa."""
    if method == "math_easy":
        return make_math_challenge()
    if method == "math_hard":
        return make_math_hard_challenge(lang)
    if method == "emoji":
        return make_emoji_challenge()
    if method == "sticker":
        return make_sticker_challenge()
    return make_qa_challenge(questions, lang)


def make_emoji_challenge() -> dict:
    """Emoji selection: include the target emoji among six options."""
    target = random.choice(EMOJI_POOL)
    rest = [e for e in EMOJI_POOL if e != target]
    options = [target] + random.sample(rest, 5)
    random.shuffle(options)
    return {"target": target, "options": options}


def make_sticker_challenge() -> dict:
    """Sticker verification: any sticker passes, no payload needed."""
    return {}


def make_qa_challenge(questions: list[dict], lang: str = "") -> dict:
    """Pick a valid random question, shuffle options and retain all answers; use defaults if none are valid."""
    valid = [
        q
        for q in questions
        if isinstance(q, dict)
        and q.get("question")
        and isinstance(q.get("options"), list)
        and len(q["options"]) >= 2
        and isinstance(q.get("answers"), list)
        and len(q["answers"]) >= 1
        and all(answer in q["options"] for answer in q["answers"])
    ]
    if not valid:
        valid = _default_questions(lang)
    question = random.choice(valid)
    options = list(question["options"])
    random.shuffle(options)
    return {
        "question": question["question"],
        "options": options,
        "answers": list(question["answers"]),
        "select": question.get("select", "all"),
    }


def _is_multi_answer(session_row: VerificationSession) -> bool:
    """custom_qa all-select mode: multiple correct answers must all be selected."""
    if session_row.method != "custom_qa":
        return False
    return _multi_payload(session_row.payload or {})


def _multi_payload(payload: dict) -> bool:
    """Whether this payload requires all correct answers: select=all and more than one answer."""
    return (
        payload.get("select", "all") == "all" and len(payload.get("answers") or []) > 1
    )


def _callback_data(callback_query: CallbackQuery) -> list[str]:
    """Split callback data on ':'; accept str/bytes/None (custom buttons use ASCII strings)."""
    data = callback_query.data or ""
    if isinstance(data, bytes):
        data = data.decode(errors="replace")
    return data.split(":")


def _is_correct_option(session_row: VerificationSession, index: int) -> bool:
    """Check the selected option against target for emoji, answer for math, or answers for custom_qa."""
    payload = session_row.payload or {}
    options = payload.get("options") or []
    if index < 0 or index >= len(options):
        return False
    if session_row.method == "emoji":
        return options[index] == payload.get("target")
    answers = payload.get("answers")
    if isinstance(answers, list):
        return options[index] in answers
    return options[index] == payload.get("answer")


def build_challenge_text(
    config: ChatConfig,
    method: str,
    payload: dict,
    attempts_left: int,
    *,
    wrong_prefix: bool,
    lang: str,
    user_mention: str = "",
) -> str:
    """Challenge body; prepend a nonempty user_mention as the first line."""
    prefix = i18n.t("bot.msg.verify.wrong_prefix", locale=lang) if wrong_prefix else ""
    if method == "math_easy":
        body = i18n.t("bot.msg.verify.challenge_math_easy", locale=lang).format(
            a=payload["a"],
            b=payload["b"],
            attempts=attempts_left,
            max=config.verify_max_attempts,
        )
    elif method == "math_hard":
        body = i18n.t("bot.msg.verify.challenge_math_hard", locale=lang).format(
            question=_math_html(payload["question"]),
            attempts=attempts_left,
            max=config.verify_max_attempts,
        )
    elif method == "emoji":
        body = i18n.t("bot.msg.verify.challenge_emoji", locale=lang).format(
            emoji=payload["target"],
            attempts=attempts_left,
            max=config.verify_max_attempts,
        )
    elif method == "sticker":
        # Sticker verification cannot have wrong answers, only timeouts.
        body = i18n.t("bot.msg.verify.challenge_sticker", locale=lang)
    else:  # custom_qa
        key = (
            "bot.msg.verify.challenge_qa_multi"
            if _multi_payload(payload)
            else "bot.msg.verify.challenge_qa"
        )
        body = i18n.t(key, locale=lang).format(
            question=payload["question"],
            attempts=attempts_left,
            max=config.verify_max_attempts,
        )
    timeout_hint = i18n.t("bot.msg.verify.timeout_hint", locale=lang).format(
        timeout=config.verify_timeout_seconds
    )
    mention_line = f"{user_mention}\n" if user_mention else ""
    # Separate the question and attempts/timeout notices with a blank line.
    return mention_line + prefix + body + "\n\n" + timeout_hint


# --------------------------------------------------------------------------- Keyboards.


def _admin_row(session_id: int, lang: str) -> list[InlineKeyboardButton]:
    """The same approve/ban administrator buttons for every verification method."""
    return [
        InlineKeyboardButton(
            i18n.t("bot.button.verify.approve", locale=lang),
            callback_data=f"verify_admin:{session_id}:approve",
            style=ButtonStyle.SUCCESS,
        ),
        InlineKeyboardButton(
            i18n.t("bot.button.verify.ban", locale=lang),
            callback_data=f"verify_admin:{session_id}:ban",
            style=ButtonStyle.DANGER,
        ),
    ]


def _challenge_markup(
    session_row: VerificationSession, lang: str
) -> InlineKeyboardMarkup:
    """Answer rows (math 2x2, emoji 2x3, custom_qa 2xN; confirmation for multi-select) plus admin row."""
    rows: list[list[InlineKeyboardButton]] = []
    payload = session_row.payload or {}
    options = payload.get("options") or []
    if session_row.method in ("math_easy", "math_hard", "custom_qa"):
        selected = set(payload.get("selected") or [])
        buttons = [
            InlineKeyboardButton(
                (
                    f"{option} {i18n.t('bot.button.verify.selected', locale=lang)}"
                    if index in selected
                    else str(option)
                ),
                callback_data=f"verify:{session_row.id}:{index}",
            )
            for index, option in enumerate(options)
        ]
        for start in range(0, len(buttons), 2):
            row = buttons[start : start + 2]
            if row:
                rows.append(row)
        if _is_multi_answer(session_row):
            rows.append(
                [
                    InlineKeyboardButton(
                        i18n.t("bot.button.verify.submit", locale=lang),
                        callback_data=f"verify:{session_row.id}:submit",
                    )
                ]
            )
    elif session_row.method == "emoji":
        buttons = [
            InlineKeyboardButton(
                str(option), callback_data=f"verify:{session_row.id}:{index}"
            )
            for index, option in enumerate(options)
        ]
        rows.append(buttons[0:3])
        rows.append(buttons[3:6])
    rows.append(_admin_row(session_row.id, lang))
    return InlineKeyboardMarkup(rows)
