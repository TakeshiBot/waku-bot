"""Native Business drafts with streaming edits when Telegram rejects drafts."""

import asyncio
import copy
import html
import re
import secrets

import pyrogram

from waku.common.rich_message import rich_html_plain_text
from waku.logger import logger
from waku.plugins.agent.output import (
    DeliveryUncertain,
    StreamingOutput,
    _rich_output_enabled,
)
from waku.plugins.agent.rich_output import (
    edit_business_message_text,
    send_generation_message,
)
from waku.plugins.agent.styling import (
    _rich_html_within_limits,
    convert_md_chunks,
    convert_rich_md,
    split_plain_text,
)


class BusinessStreamingOutput:
    """Prefer native Thinking/drafts, falling back to coalesced message edits.

    The final reply replaces the preview, preserving rich formatting and sending
    only overflow as new messages. All writes recheck the connection permission.
    """

    UPDATE_INTERVAL = 2.0
    MAX_MESSAGE_LENGTH = 4000

    def __init__(self, client, message, should_send=None):
        self.client = client
        self.message = message
        self.should_send = should_send
        self.current_text = ""
        self.reply_message_id = None
        self.delivered = False
        self._preview_id = None
        self._last_preview = None
        self._task = None
        self._stopped = False
        self._finalized = False
        self._completion_success = False
        self._native = None
        from waku.services.business_context import connection_reply_prefix

        self.reply_prefix = connection_reply_prefix(message.business_connection_id)
        self._native_prefix_sent = False

    def _display_text(self):
        if not self.reply_prefix:
            return self.current_text
        body = self.current_text
        if body.startswith(self.reply_prefix):
            body = body[len(self.reply_prefix) :]
        return re.sub(r"([\\`*_{}\[\]()#+.!>~|=-])", r"\\\1", self.reply_prefix) + body

    def _body_text(self):
        return (
            self.current_text[len(self.reply_prefix) :]
            if self.reply_prefix and self.current_text.startswith(self.reply_prefix)
            else self.current_text
        )

    def _plain_chunks(self, text=None, *, plain=False):
        """Reserve the literal tag before splitting, preserving entity offsets."""
        text = self._body_text() if text is None else text
        if self.reply_prefix and text.startswith(self.reply_prefix):
            text = text[len(self.reply_prefix) :]
        offset = len(self.reply_prefix.encode("utf-16-le")) // 2
        limit = self.MAX_MESSAGE_LENGTH - offset
        chunks = (
            [(part, []) for part in split_plain_text(text, limit)]
            if plain
            else convert_md_chunks(text, limit)
        )
        tagged = []
        for body, entities in chunks:
            shifted = []
            for entity in entities:
                entity = copy.copy(entity)
                entity.offset += offset
                shifted.append(entity)
            tagged.append((self.reply_prefix + body, shifted))
        return tagged

    def _rich_payloads(self):
        payloads = convert_rich_md(self._body_text())
        if not self.reply_prefix:
            return payloads
        prefix = re.sub(r"([\\`*_{}\[\]()#+.!>~|=-])", r"\\\1", self.reply_prefix)
        result = []
        for payload in payloads:
            payload = copy.copy(payload)
            payload._waku_business_markdown = payload.markdown
            payload._waku_business_html = payload.html
            if payload.html:
                payload.html = (
                    "<p>"
                    + html.escape(self.reply_prefix.rstrip())
                    + "</p>"
                    + payload.html
                )
                if not _rich_html_within_limits(payload.html):
                    return []
            if payload.markdown:
                payload.markdown = prefix.rstrip() + "\n\n" + payload.markdown
                if len(payload.markdown.encode("utf-8")) > 32768:
                    return []
            result.append(payload)
        return result

    def _may_send(self):
        return not self._stopped and (self.should_send is None or self.should_send())

    async def start_thinking(self):
        if self._native is not None or self._finalized or not self._may_send():
            return
        self._native = StreamingOutput(
            self.client, self.message, should_send=self._may_send
        )
        await self._native.start_thinking()

    def _native_available(self):
        return (
            self._native is not None
            and self._native.official_draft is not None
            and self._native.official_draft.supported is not False
        )

    async def _write(
        self, text="", entities=None, rich_message=None, *, edit=False, random_id=None
    ):
        if not self._may_send():
            return None
        if edit:
            while self._may_send():
                try:
                    result = await edit_business_message_text(
                        self.client,
                        self.message,
                        self._preview_id,
                        text if rich_message is None else None,
                        entities=entities,
                        parse_mode=pyrogram.enums.ParseMode.DISABLED,
                        rich_message=rich_message,
                        should_send=self._may_send,
                    )
                    message_id = result.id if result is not None else None
                    break
                except pyrogram.errors.MessageNotModified:
                    message_id = self._preview_id
                    break
                except pyrogram.errors.FloodWait as error:
                    await asyncio.sleep(max(1, error.value))
            else:
                return None
        else:
            try:
                message_id = await send_generation_message(
                    self.client,
                    self.message,
                    random_id or secrets.randbits(63) or 1,
                    text=text,
                    entities=entities,
                    rich_message=rich_message,
                    should_send=self._may_send,
                )
            except pyrogram.errors.RPCError:
                raise
            except Exception as error:
                self._stopped = True
                raise DeliveryUncertain(
                    "Unconfirmed Business streaming send"
                ) from error
        if message_id is None:
            if not self._may_send():
                return None
            self._stopped = True
            raise DeliveryUncertain("Missing Business streaming receipt")
        self.delivered = True
        self.reply_message_id = message_id
        return message_id

    async def _preview(self):
        if not self.current_text.strip():
            return
        chunks = self._plain_chunks()
        if not chunks or not chunks[0][0].strip() or not self._may_send():
            return
        text, entities = chunks[0]
        if (text, entities) == self._last_preview:
            return
        try:
            message_id = await self._write(
                text, entities, edit=self._preview_id is not None
            )
        except (pyrogram.errors.EntityBoundsInvalid, pyrogram.errors.EntitiesTooLong):
            message_id = await self._write(text, [], edit=self._preview_id is not None)
        if message_id is not None:
            self._preview_id = message_id
            self._last_preview = (text, entities)

    async def _loop(self):
        try:
            while self._may_send():
                await asyncio.sleep(self.UPDATE_INTERVAL)
                if self._may_send():
                    await self._preview()
        except asyncio.CancelledError:
            raise
        except Exception as error:
            # The preview exists already. Finalization may edit its known ID,
            # but must never resend it as another message after a failed edit.
            logger.warning(f"Business streaming edit failed: {type(error).__name__}")

    async def append_delta(self, delta):
        if not delta or self._finalized or not self._may_send():
            return
        self.current_text += delta
        if self._native_available():
            prefix = (
                re.sub(r"([\\`*_{}\[\]()#+.!>~|=-])", r"\\\1", self.reply_prefix)
                if not self._native_prefix_sent
                else ""
            )
            self._native_prefix_sent = True
            await self._native.append_delta(prefix + delta)
            if self._native_available():
                return
        if self._native is not None:
            # No persistent message was sent by a draft. Falling back to edits
            # uses the accumulated text once, without creating two answers.
            await self._native.abort()
            self._native = None
        if self._preview_id is None:
            await self._preview()
        if self._preview_id is not None and self._task is None:
            self._task = asyncio.create_task(self._loop(), name="business-stream-edit")

    async def _stop_updates(self):
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def finalize(self):
        if self._finalized:
            return self._completion_success
        self._finalized = True
        await self._stop_updates()
        native_id = None
        if self._native_available() and not self.reply_prefix:
            self._native.current_text = self._display_text()
            self._completion_success = await self._native.finalize()
            self.reply_message_id = self._native.reply_message_id
            self.delivered = self._native.delivered
            return self._completion_success
        if self._native is not None:
            native_id = self._native.random_id
            await self._native.abort()
            self._native = None
        if not self._may_send() or not self.current_text.strip():
            return False
        payloads = (
            self._rich_payloads() if await _rich_output_enabled(self.message) else []
        )
        chunks = (
            [("", None, payload) for payload in payloads]
            if payloads
            else [(text, entities, None) for text, entities in self._plain_chunks()]
        )
        for index, (text, entities, payload) in enumerate(chunks):
            edit = index == 0 and self._preview_id is not None
            try:
                if (
                    await self._write(
                        text,
                        entities,
                        payload,
                        edit=edit,
                        random_id=native_id if index == 0 else None,
                    )
                    is None
                ):
                    return False
            except (
                pyrogram.errors.RichMessageUnsupported,
                pyrogram.errors.EntityBoundsInvalid,
                pyrogram.errors.EntitiesTooLong,
            ):
                if payload is not None:
                    original_markdown = getattr(
                        payload, "_waku_business_markdown", payload.markdown
                    )
                    original_html = getattr(
                        payload, "_waku_business_html", payload.html
                    )
                    plain_chunks = (
                        self._plain_chunks(original_markdown)
                        if original_markdown
                        else [
                            (part, entities)
                            for part, entities in self._plain_chunks(
                                rich_html_plain_text(original_html or ""),
                                plain=True,
                            )
                        ]
                    )
                else:
                    plain_chunks = [(text, [])]
                for tail_index, (plain, plain_entities) in enumerate(plain_chunks):
                    if (
                        await self._write(
                            plain,
                            plain_entities,
                            edit=edit and tail_index == 0,
                            random_id=native_id
                            if index == 0 and tail_index == 0
                            else None,
                        )
                        is None
                    ):
                        return False
        self._completion_success = True
        return True

    async def abort(self):
        self._stopped = True
        await self._stop_updates()
        if self._native is not None:
            await self._native.abort()
