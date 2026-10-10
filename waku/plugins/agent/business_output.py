"""Business streaming through edits, independent of draft rendering support."""

import asyncio
import secrets

import pyrogram

from waku.common.rich_message import rich_html_plain_text
from waku.logger import logger
from waku.plugins.agent.output import DeliveryUncertain, _rich_output_enabled
from waku.plugins.agent.rich_output import (
    edit_business_message_text,
    send_generation_message,
)
from waku.plugins.agent.styling import (
    convert_md_chunks,
    convert_rich_md,
    split_plain_text,
)


class BusinessStreamingOutput:
    """Publish the first text immediately, then coalesce edits of that message.

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

    def _may_send(self):
        return not self._stopped and (self.should_send is None or self.should_send())

    async def _write(self, text="", entities=None, rich_message=None, *, edit=False):
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
                    secrets.randbits(63) or 1,
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
        chunks = convert_md_chunks(self.current_text, self.MAX_MESSAGE_LENGTH)
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
        if not self._may_send() or not self.current_text.strip():
            return False
        payloads = (
            convert_rich_md(self.current_text)
            if await _rich_output_enabled(self.message)
            else []
        )
        chunks = (
            [("", None, payload) for payload in payloads]
            if payloads
            else [
                (text, entities, None)
                for text, entities in convert_md_chunks(
                    self.current_text, self.MAX_MESSAGE_LENGTH
                )
            ]
        )
        for index, (text, entities, payload) in enumerate(chunks):
            edit = index == 0 and self._preview_id is not None
            try:
                if await self._write(text, entities, payload, edit=edit) is None:
                    return False
            except (
                pyrogram.errors.RichMessageUnsupported,
                pyrogram.errors.EntityBoundsInvalid,
                pyrogram.errors.EntitiesTooLong,
            ):
                if payload is not None:
                    plain_chunks = (
                        convert_md_chunks(payload.markdown, self.MAX_MESSAGE_LENGTH)
                        if payload.markdown
                        else [
                            (part, [])
                            for part in split_plain_text(
                                rich_html_plain_text(payload.html or ""),
                                self.MAX_MESSAGE_LENGTH,
                            )
                        ]
                    )
                else:
                    plain_chunks = [(text, [])]
                for tail_index, (plain, plain_entities) in enumerate(plain_chunks):
                    if (
                        await self._write(
                            plain, plain_entities, edit=edit and tail_index == 0
                        )
                        is None
                    ):
                        return False
        self._completion_success = True
        return True

    async def abort(self):
        self._stopped = True
        await self._stop_updates()
