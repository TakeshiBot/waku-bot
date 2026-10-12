import asyncio
import base64
import re
from dataclasses import dataclass
from io import BytesIO
from typing import Literal, cast

import httpx
from openai import AsyncOpenAI
from openai.types import ImagesResponse
from PIL import Image

from waku.config import app_config
from waku.logger import logger


@dataclass
class ImageGenResult:
    success: bool
    data: bytes | None = None
    revised_prompt: str | None = None
    error: str | None = None
    media_type: str = "image/png"
    # True means the provider definitively rejected/failed this request, so a
    # user can safely submit a new one. Transport timeouts stay False because
    # the provider may have completed and billed the original request.
    retry_safe: bool = False


@dataclass
class ImageEditResult:
    success: bool
    data: bytes | None = None
    revised_prompt: str | None = None
    error: str | None = None
    media_type: str = "image/png"
    retry_safe: bool = False


def _validate_image(data: bytes) -> str:
    if not data or len(data) > 20 * 1024 * 1024:
        raise ValueError("Empty or oversized image response")
    with Image.open(BytesIO(data)) as image:
        media_type = Image.MIME.get(image.format)
        if media_type not in {"image/png", "image/jpeg", "image/webp"}:
            raise ValueError("The provider returned an unsupported image format")
        image.verify()
    return media_type


async def _image_source(source: str, proxy: str | None) -> tuple[bytes, str]:
    if source.startswith("data:image/"):
        header, encoded = source.split(",", 1)
        if not header.endswith(";base64"):
            raise ValueError("Image data URI must contain base64")
        data = await asyncio.to_thread(base64.b64decode, encoded, validate=True)
    elif source.startswith(("https://", "http://")):
        data = await _url_to_bytes(source, proxy)
    else:
        # The Images API supplies plain b64_json without a data URI header.
        data = await asyncio.to_thread(base64.b64decode, source, validate=True)
    return data, await asyncio.to_thread(_validate_image, data)


async def _chat_image(response, proxy: str | None) -> tuple[bytes, str]:
    """Accept actual Gemini/OpenAI-compatible image parts, never text sketches."""
    if not response.choices:
        raise ValueError("The model returned no image")
    message = response.choices[0].message.model_dump(exclude_none=True, warnings=False)
    parts = list(message.get("images") or [])
    content = message.get("content")
    if isinstance(content, list):
        parts.extend(content)
    for part in parts:
        if not isinstance(part, dict):
            continue
        image = part.get("image_url")
        source = image.get("url") if isinstance(image, dict) else image
        inline = part.get("inline_data") or part.get("inlineData")
        if not source and isinstance(inline, dict):
            source = inline.get("data")
        if source:
            return await _image_source(source, proxy)
    if isinstance(content, str):
        match = re.search(r"data:image/[\w.+-]+;base64,[A-Za-z0-9+/=]+", content)
        if match:
            return await _image_source(match.group(), proxy)
        match = re.search(r"!\[[^\]]*\]\((https?://[^\s)]+)\)", content)
        if match:
            return await _image_source(match.group(1), proxy)
    raise ValueError(
        "The model returned text instead of an image; an image-capable model is required"
    )


def _public_error(error: Exception) -> str:
    # SDK errors can echo credentials, input images and provider URLs. Expose
    # only our own validation errors or the error class/status to the chat AI.
    if isinstance(error, ValueError):
        return "Invalid image response or input. An image-capable model is required."
    status = getattr(error, "status_code", None)
    return type(error).__name__ + (f" (HTTP {status})" if status else "")


def _provider_definitively_rejected(error: Exception) -> bool:
    """Whether the provider sent an HTTP response for this failed request."""
    status = getattr(error, "status_code", None)
    return isinstance(status, int) and 400 <= status < 600


def _output_is_retry_safe(error: Exception) -> bool:
    """Classify errors after a valid provider response was received.

    A failed download of a result URL is intentionally uncertain: an image was
    already generated remotely. Local decoding/validation errors and text-only
    responses are definite unusable results and can be retried immediately.
    """
    return not isinstance(error, httpx.HTTPError)


async def _url_to_bytes(url: str, proxy: str | None = None) -> bytes:
    # Image result URLs (when b64_json is absent) are fetched via httpx.
    # Reuse the agent proxy when the URL belongs to the provider's domain;
    # otherwise direct fetch is fine. Minimal closed loop: honour explicit
    # image-model provider proxy, fallback to global agent_proxy.
    http_client: httpx.AsyncClient | None = None
    if proxy is None:
        try:
            from waku.common.http import get_agent_http_client

            http_client = get_agent_http_client(None)
        except Exception:
            http_client = None
    else:
        try:
            from waku.common.http import get_agent_http_client

            http_client = get_agent_http_client(proxy)
        except Exception:
            http_client = None
    if http_client is not None:
        resp = await http_client.get(url)
        resp.raise_for_status()
        return resp.content
    async with httpx.AsyncClient(timeout=60.0) as client:
        resp = await client.get(url)
        resp.raise_for_status()
        return resp.content


def _proxy_for_spec(spec: str) -> str | None:
    """Resolve proxy for an image model spec (provider proxy > global)."""
    provider_name, _ = _parse_model_spec(spec)
    providers = app_config.agent_providers
    cfg = providers.get(provider_name)
    if cfg is not None and cfg.proxy:
        return cfg.proxy
    return app_config.agent_proxy


class _ImageGenerationClient:
    def __init__(
        self, api_key: str, base_url: str, model: str, proxy: str | None = None
    ):
        self.model = model
        http_client: httpx.AsyncClient | None = None
        if proxy is not None:
            try:
                from waku.common.http import get_agent_http_client

                http_client = get_agent_http_client(proxy)
            except Exception:
                http_client = None
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            timeout=180,
            max_retries=0,
        )
        self._proxy = proxy

    async def generate(
        self,
        prompt: str,
        size: str = "1024x1024",
        quality: str = "standard",
        n: int = 1,
    ) -> ImageGenResult:
        try:
            response = await self._client.images.generate(
                model=self.model,
                prompt=prompt,
                size=size,
                quality=cast(
                    Literal["standard", "hd", "low", "medium", "high", "auto"],
                    quality,
                ),
                n=n,
                response_format="b64_json",
            )
        except Exception as e:
            logger.error("ImageGen error: {}", _public_error(e))
            return ImageGenResult(
                success=False,
                error=_public_error(e),
                retry_safe=_provider_definitively_rejected(e),
            )
        if not response.data:
            return ImageGenResult(
                success=False,
                error="No image data returned by the model.",
                retry_safe=True,
            )
        item = response.data[0]
        source = item.b64_json or item.url
        if not source:
            return ImageGenResult(
                success=False,
                error="No image data returned by the model.",
                retry_safe=True,
            )
        try:
            image_bytes, media_type = await _image_source(source, self._proxy)
        except Exception as e:
            logger.error("ImageGen output error: {}", _public_error(e))
            return ImageGenResult(
                success=False,
                error=_public_error(e),
                retry_safe=_output_is_retry_safe(e),
            )
        return ImageGenResult(
            success=True,
            data=image_bytes,
            revised_prompt=item.revised_prompt,
            media_type=media_type,
        )


class _ImageEditClient:
    def __init__(
        self, api_key: str, base_url: str, model: str, proxy: str | None = None
    ):
        self.model = model
        http_client: httpx.AsyncClient | None = None
        if proxy is not None:
            try:
                from waku.common.http import get_agent_http_client

                http_client = get_agent_http_client(proxy)
            except Exception:
                http_client = None
        self._client = AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            http_client=http_client,
            timeout=180,
            max_retries=0,
        )
        self._proxy = proxy

    async def edit(
        self,
        prompt: str,
        image_data: bytes,
        image_filename: str = "image.png",
        image_mime_type: str = "image/png",
        mask_data: bytes | None = None,
        size: str = "auto",
        n: int = 1,
    ) -> ImageEditResult:
        if "gemini" in self.model.lower():
            if mask_data is not None:
                return ImageEditResult(
                    success=False,
                    error="This Gemini editing endpoint does not support masks.",
                    retry_safe=True,
                )
            try:
                actual_mime = await asyncio.to_thread(_validate_image, image_data)
                encoded = await asyncio.to_thread(base64.b64encode, image_data)
                response = await self._client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {
                            "role": "user",
                            "content": [
                                {
                                    "type": "text",
                                    "text": prompt
                                    + "\nReturn the actual edited image, not a description or code.",
                                },
                                {
                                    "type": "image_url",
                                    "image_url": {
                                        "url": f"data:{actual_mime};base64,{encoded.decode('ascii')}"
                                    },
                                },
                            ],
                        }
                    ],
                    stream=False,
                    extra_body={"modalities": ["text", "image"]},
                )
            except Exception as e:
                logger.error("ImageEdit error: {}", _public_error(e))
                return ImageEditResult(
                    success=False,
                    error=_public_error(e),
                    retry_safe=_provider_definitively_rejected(e)
                    or isinstance(e, ValueError),
                )
            try:
                data, media_type = await _chat_image(response, self._proxy)
            except Exception as e:
                logger.error("ImageEdit output error: {}", _public_error(e))
                return ImageEditResult(
                    success=False,
                    error=_public_error(e),
                    retry_safe=_output_is_retry_safe(e),
                )
            return ImageEditResult(success=True, data=data, media_type=media_type)
        try:
            image_file = (image_filename, BytesIO(image_data), image_mime_type)
            kwargs: dict = dict(
                model=self.model,
                image=image_file,
                prompt=prompt,
                size=size,  # type: ignore[arg-type]
                n=n,
                response_format="b64_json",
            )
            if mask_data is not None:
                kwargs["mask"] = ("mask.png", BytesIO(mask_data), "image/png")
            response = cast(ImagesResponse, await self._client.images.edit(**kwargs))
        except Exception as e:
            logger.error("ImageEdit error: {}", _public_error(e))
            return ImageEditResult(
                success=False,
                error=_public_error(e),
                retry_safe=_provider_definitively_rejected(e),
            )
        if not response.data:
            return ImageEditResult(
                success=False,
                error="No image data returned by the model.",
                retry_safe=True,
            )
        item = response.data[0]
        source = item.b64_json or item.url
        if not source:
            return ImageEditResult(
                success=False,
                error="No image data returned by the model.",
                retry_safe=True,
            )
        try:
            result_bytes, media_type = await _image_source(source, self._proxy)
        except Exception as e:
            logger.error("ImageEdit output error: {}", _public_error(e))
            return ImageEditResult(
                success=False,
                error=_public_error(e),
                retry_safe=_output_is_retry_safe(e),
            )
        return ImageEditResult(
            success=True,
            data=result_bytes,
            revised_prompt=item.revised_prompt,
            media_type=media_type,
        )


def _parse_model_spec(spec: str) -> tuple[str, str]:
    if "/" in spec:
        provider_name, _, model_name = spec.partition("/")
        return provider_name.strip(), model_name.strip()
    return "default", spec.strip()


def _make_openai_client_args(spec: str) -> dict[str, str]:
    provider_name, model_name = _parse_model_spec(spec)
    providers = app_config.agent_providers
    if provider_name not in providers:
        raise ValueError(
            f"Provider {provider_name!r} not found in agent_providers. "
            f"Available: {list(providers.keys())}"
        )
    provider_cfg = providers[provider_name]
    return {
        "api_key": provider_cfg.key,
        "base_url": provider_cfg.url,
        "model": model_name,
    }


image_gen_client: _ImageGenerationClient | None = None
image_edit_client: _ImageEditClient | None = None

if app_config.agent and app_config.agent_image_gen_model:
    _gen_args = _make_openai_client_args(app_config.agent_image_gen_model)
    image_gen_client = _ImageGenerationClient(
        api_key=_gen_args["api_key"],
        base_url=_gen_args["base_url"],
        model=_gen_args["model"],
        proxy=_proxy_for_spec(app_config.agent_image_gen_model),
    )

if app_config.agent and (
    app_config.agent_image_edit_model or app_config.agent_image_gen_model
):
    _edit_spec = app_config.agent_image_edit_model or app_config.agent_image_gen_model
    _edit_args = _make_openai_client_args(_edit_spec)
    image_edit_client = _ImageEditClient(
        api_key=_edit_args["api_key"],
        base_url=_edit_args["base_url"],
        model=_edit_args["model"],
        proxy=_proxy_for_spec(_edit_spec),
    )
__all__ = [
    "image_gen_client",
    "image_edit_client",
    "ImageGenResult",
    "ImageEditResult",
]
