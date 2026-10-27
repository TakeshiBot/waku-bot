"""Prepare raster uploads for Telegram photos, independently of source URLs."""

from io import BytesIO
from math import ceil

from PIL import Image, ImageOps

_MAX_PHOTO_BYTES = 10 * 1024 * 1024
_MAX_SOURCE_PIXELS = 25_000_000


def prepare_photo_upload(photo: BytesIO) -> BytesIO:
    """Keep valid JPEG/PNG bytes; decode and encode other rasters as real JPEG.

    Merely renaming WEBP bytes to .jpg does not make a valid photo upload.
    Call in a worker thread so decoding large images cannot block bot updates.
    Documents bypass this helper and retain the original file.
    """
    raw = photo.getvalue()
    stem = photo.name.rsplit(".", 1)[0] or "photo"
    with Image.open(BytesIO(raw)) as source:
        width, height = source.size
        if width * height > _MAX_SOURCE_PIXELS:
            raise ValueError("Photo exceeds the 25 megapixel decoding limit.")
        compatible = (
            source.format in {"JPEG", "PNG"}
            and source.mode in {"RGB", "RGBA", "L", "LA", "P"}
            and len(raw) <= _MAX_PHOTO_BYTES
            and width + height <= 10_000
            and max(width, height) <= 20 * min(width, height)
        )
        if compatible:
            extension = ".jpg" if source.format == "JPEG" else ".png"
            source.verify()
            result = BytesIO(raw)
        else:
            source.seek(0)
            image = ImageOps.exif_transpose(source).convert("RGBA")
            image.thumbnail((4096, 4096), Image.Resampling.LANCZOS)
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            width, height = background.size
            if max(width, height) > 20 * min(width, height):
                background = ImageOps.pad(
                    background,
                    (max(width, ceil(height / 20)), max(height, ceil(width / 20))),
                    color="white",
                )
            result = BytesIO()
            for quality in (90, 80, 65):
                result.seek(0)
                result.truncate()
                background.save(result, format="JPEG", quality=quality)
                if result.tell() <= _MAX_PHOTO_BYTES:
                    break
            if result.tell() > _MAX_PHOTO_BYTES:
                raise ValueError("Converted photo exceeds the 10 MB upload limit.")
            extension = ".jpg"
    result.name = stem + extension
    result.seek(0)
    return result
