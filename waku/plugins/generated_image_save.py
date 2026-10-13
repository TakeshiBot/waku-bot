"""Save an already generated Telegram photo to the clicking user's bot DM."""

import asyncio
import secrets
from weakref import WeakValueDictionary

from pyrogram import Client, enums, filters
from pyrogram.errors import BadRequest, FloodWait, Forbidden, Unauthorized
from pyrogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from waku.common import memttlcache
from waku.common.telegram_runtime import prepare_telegram_output
from waku.i18n import i18n
from waku.logger import logger
from waku.services.image_requests import GENERATED_IMAGE_CAPTION

_PREFIX = "img_save:"
_TTL = 7 * 24 * 60 * 60
_DELIVERY_TIMEOUT = 25
_CAPTION = GENERATED_IMAGE_CAPTION
_locks: WeakValueDictionary[str, asyncio.Lock] = WeakValueDictionary()


def _tr(key, locale):
    return i18n.t(f"bot.agent_i18n.image_save_{key}", locale=locale)


def _markup(token, locale, *, saved=False):
    return InlineKeyboardMarkup(
        [
            [
                InlineKeyboardButton(
                    _tr("saved_button" if saved else "button", locale),
                    callback_data=_PREFIX + token,
                    style=enums.ButtonStyle.SUCCESS,
                )
            ]
        ]
    )


async def send_generated_photo(
    client, *, owner_id, chat_id, photo, reply_parameters, locale=None, native=True
):
    """Upload once; retain the returned Telegram file ID for the Save callback."""
    kwargs = dict(
        chat_id=chat_id,
        photo=photo,
        reply_parameters=reply_parameters,
        caption=_CAPTION,
        parse_mode=enums.ParseMode.DISABLED,
    )
    if not native:
        return await client.send_photo(**kwargs)
    token = secrets.token_urlsafe(16)
    sent = await client.send_photo(**kwargs, reply_markup=_markup(token, locale))
    if sent and getattr(sent, "photo", None) and isinstance(sent.photo.file_id, str):
        record = dict(
            chat_id=chat_id,
            message_id=sent.id,
            file_id=sent.photo.file_id,
            locale=locale,
            caption=_CAPTION,
        )
        try:
            await memttlcache.set(_PREFIX + token, record, ttl=_TTL)
        except Exception as error:
            # Saving is optional; a cache failure must not report a successful
            # image upload as a failed generation or trigger another API call.
            logger.warning(
                "Generated image Save registration failed: {}", type(error).__name__
            )
            try:
                await client.edit_message_reply_markup(
                    chat_id, sent.id, reply_markup=None
                )
            except Exception:
                pass
    return sent


async def _mark_saved(client, query, token, record):
    if record["chat_id"] == query.from_user.id:
        try:
            await client.edit_message_reply_markup(
                record["chat_id"],
                record["message_id"],
                reply_markup=_markup(token, record.get("locale"), saved=True),
            )
        except Exception:
            pass
    await query.answer(_tr("saved", record.get("locale")), show_alert=True)


async def _save_generated_image(client, callback_query):
    query = callback_query
    message, user = query.message, query.from_user
    if (
        message is None
        or user is None
        or getattr(user, "is_bot", False)
        or not isinstance(user.id, int)
        or user.id <= 0
    ):
        await query.answer()
        return
    if not await prepare_telegram_output(message, user):
        return
    token = query.data[len(_PREFIX) :]
    source_key = _PREFIX + token
    key = source_key + ":" + str(user.id)
    lock = _locks.setdefault(key, asyncio.Lock())
    async with lock:
        record = await memttlcache.get(source_key)
        locale = record.get("locale") if isinstance(record, dict) else None
        if not isinstance(record, dict):
            await query.answer(_tr("expired", locale), show_alert=True)
            return
        if (
            message.chat.id != record["chat_id"]
            or message.id != record["message_id"]
            or not getattr(message, "photo", None)
            or message.photo.file_id != record["file_id"]
        ):
            await query.answer(_tr("expired", locale), show_alert=True)
            return
        status = await memttlcache.get(key, default="ready")
        if status == "sent" or message.chat.id == user.id:
            await memttlcache.set(key, "sent", ttl=_TTL)
            await _mark_saved(client, query, token, record)
            return
        if status != "ready":
            await query.answer(_tr("uncertain", locale), show_alert=True)
            return
        # Persist the claim before sending. An interrupted request must not
        # silently resend an image that Telegram may already have delivered.
        await memttlcache.set(key, "sending", ttl=_TTL)
        if not await prepare_telegram_output(message, user):
            await memttlcache.set(key, "ready", ttl=_TTL)
            return
        try:
            sent = await asyncio.wait_for(
                client.send_photo(
                    chat_id=user.id,
                    photo=record["file_id"],
                    caption=record.get("caption", _CAPTION),
                    parse_mode=enums.ParseMode.DISABLED,
                ),
                timeout=_DELIVERY_TIMEOUT,
            )
            if (
                not sent
                or not isinstance(getattr(sent, "id", None), int)
                or sent.id <= 0
            ):
                status = "uncertain"
            else:
                status = "sent"
        except (Forbidden, Unauthorized, BadRequest):
            await memttlcache.set(key, "ready", ttl=_TTL)
            await query.answer(_tr("dm_unavailable", locale), show_alert=True)
            return
        except FloodWait as error:
            await memttlcache.set(key, "ready", ttl=_TTL)
            await query.answer(
                _tr("flood_wait", locale).format(seconds=error.value), show_alert=True
            )
            return
        except asyncio.CancelledError:
            await memttlcache.set(key, "uncertain", ttl=_TTL)
            raise
        except Exception as error:
            status = "uncertain"
            logger.warning(
                "Generated image Save delivery uncertain: {}", type(error).__name__
            )
        await memttlcache.set(key, status, ttl=_TTL)
        if status == "sent":
            await _mark_saved(client, query, token, record)
        else:
            await query.answer(_tr("uncertain", locale), show_alert=True)


@Client.on_callback_query(filters.regex(r"^img_save:[A-Za-z0-9_-]{22}$"))
async def save_generated_image(client, callback_query):
    try:
        await _save_generated_image(client, callback_query)
    except asyncio.CancelledError:
        raise
    except Exception as error:
        logger.warning(
            "Generated image Save callback unavailable: {}", type(error).__name__
        )
        try:
            await callback_query.answer(_tr("unavailable", None), show_alert=True)
        except Exception:
            pass
