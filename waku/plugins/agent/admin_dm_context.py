"""An independent AGENTS.md context layer before the main bot instructions."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from pydantic_ai import RunContext
from pydantic_ai.capabilities import AbstractCapability
from pydantic_ai.messages import ModelRequest, SystemPromptPart
from pydantic_ai.models import ModelRequestContext

from waku.services.admin_dm_instructions import load_for_admin_dm

_LAYER_MARKER = "waku_admin_dm_context"


class AdminDMContextLayer(AbstractCapability[Any]):
    async def before_model_request(
        self, ctx: RunContext[Any], request_context: ModelRequestContext
    ) -> ModelRequestContext:
        # Always remove historical copies, including after deletion or demotion.
        # The file is never written into deps.instructions or the main prompt.
        messages = []
        for message in request_context.messages:
            if isinstance(message, ModelRequest):
                parts = [
                    part
                    for part in message.parts
                    if not (
                        isinstance(part, SystemPromptPart)
                        and part.dynamic_ref == _LAYER_MARKER
                    )
                ]
                if len(parts) != len(message.parts):
                    # The SDK may merge this request with a user/tool request.
                    # Remove only our part, preserving all conversation data.
                    if not parts and not message.instructions:
                        continue
                    message = replace(message, parts=parts)
            messages.append(message)
        content = await load_for_admin_dm(ctx.deps)
        if content:
            messages.insert(
                0,
                ModelRequest(
                    parts=[SystemPromptPart(content, dynamic_ref=_LAYER_MARKER)],
                    metadata={_LAYER_MARKER: True},
                ),
            )
        return replace(request_context, messages=messages)
