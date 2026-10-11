"""Guest Thinking and streaming bound to the returned inline message ID."""

import time

from pyrogram import errors, raw

from waku.config import app_config
from waku.logger import logger
from waku.plugins.agent.styling import convert_rich_md


def _clip(text):
    encoded = text.encode("utf-16-le")
    if len(encoded) <= 8192:
        return text
    return encoded[:8190].decode("utf-16-le", errors="ignore") + "…"


def _format_rejected(error):
    return isinstance(error, errors.RichMessageUnsupported) or (
        isinstance(error, errors.BadRequest)
        and any(
            code in str(error.value)
            for code in (
                "RICH_MESSAGE",
                "PAGE_BLOCK",
                "BLOCK_INVALID",
                "MESSAGE_EMPTY",
                "INPUT_CONSTRUCTOR_INVALID",
                "TYPE_CONSTRUCTOR_INVALID",
            )
        )
    )


class GuestStreamingOutput:
    UPDATE_INTERVAL = 2.0

    def __init__(self, client, message):
        self.client = client
        self.query_id = int(message.guest_query_id)
        self.inline_id = None
        self.current_text = ""
        self._last_text = None
        self._last_edit = 0.0
        self._rich = app_config.agent_rich_output

    async def _post(self, content):
        if not app_config.telegram_enabled:
            return False
        result = await self.client.invoke(
            raw.functions.messages.SetBotGuestChatResult(
                query_id=self.query_id,
                result=raw.types.InputBotInlineResult(
                    id=str(self.query_id),
                    type="article",
                    title=app_config.nickname or "Waku",
                    send_message=content,
                ),
            ),
            retries=1,
            sleep_threshold=0,
        )
        if not isinstance(
            result,
            (raw.types.InputBotInlineMessageID, raw.types.InputBotInlineMessageID64),
        ):
            # Never recover by using the Guest chat ID as an ordinary peer.
            raise RuntimeError("Guest result did not return an inline message ID")
        self.inline_id = result
        return True

    async def start_thinking(self):
        # Try the native block through the Guest result transport. On a definite
        # format rejection, use a placeholder and replace that same message.
        try:
            delivered = await self._post(
                raw.types.InputBotInlineMessageRichMessage(
                    rich_message=raw.types.InputRichMessage(
                        blocks=[
                            raw.types.PageBlockThinking(
                                text=raw.types.TextPlain(text="Thinking…")
                            )
                        ]
                    )
                )
            )
            if delivered:
                logger.info("Telegram Guest Thinking block delivered")
            return delivered
        except errors.BadRequest:
            # A 400 response explicitly rejected this result; the query has
            # not produced a message. Retrying as text is safe. Never do this
            # for timeouts or 5xx responses with an uncertain receipt.
            logger.info(
                "Telegram Guest Thinking block unavailable; using editable placeholder"
            )
            return await self._post(
                raw.types.InputBotInlineMessageText(
                    message="✨ Thinking…", no_webpage=True
                )
            )

    async def _edit(self, text, *, final=False):
        if self.inline_id is None or not app_config.telegram_enabled:
            return False
        clipped = _clip(text)
        if clipped == self._last_text:
            return True
        rich = convert_rich_md(clipped) if final and self._rich else []
        # Resolve the DC from the returned inline ID, never from Guest.chat.id.
        session = await self.client.get_session(self.inline_id.dc_id, is_media=True)
        if not app_config.telegram_enabled:
            return False
        try:
            await session.invoke(
                raw.functions.messages.EditInlineBotMessage(
                    id=self.inline_id,
                    no_webpage=True,
                    message="" if rich else clipped,
                    rich_message=rich[0].write() if rich else None,
                ),
                retries=1,
                sleep_threshold=0,
            )
        except errors.MessageNotModified:
            pass
        except errors.BadRequest as error:
            if not rich or not _format_rejected(error):
                raise
            self._rich = False
            return await self._edit(text, final=final)
        self._last_text = clipped
        self._last_edit = time.monotonic()
        return True

    async def append_delta(self, delta):
        self.current_text += delta
        if time.monotonic() - self._last_edit >= self.UPDATE_INTERVAL:
            try:
                await self._edit(self.current_text)
            except errors.FloodWait as error:
                self._last_edit = time.monotonic() + max(1, error.value)
            except Exception as error:
                # Preview edits are optional. Keep generating and try the
                # complete answer; a failed preview must not strand Thinking.
                self._last_edit = time.monotonic()
                logger.debug("Telegram Guest preview skipped: {}", type(error).__name__)

    async def finalize(self, text):
        self.current_text = text
        # Final formatting must still run when the last preview has this text.
        self._last_text = None
        return await self._edit(text, final=True)
