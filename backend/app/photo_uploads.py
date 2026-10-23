from __future__ import annotations

import asyncio
from io import BytesIO

from fastapi import HTTPException, Request
from PIL import Image, ImageOps, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool

MAX_UPLOAD_BYTES = 5 * 1024 * 1024
MAX_PHOTO_BYTES = 1024 * 1024
MAX_PIXELS = 12_000_000
ALLOWED_TYPES = {"image/jpeg", "image/png", "image/webp"}
_processing = asyncio.Semaphore(1)


def normalize_photo(content: bytes) -> bytes:
    try:
        with Image.open(BytesIO(content), formats=["JPEG", "PNG", "WEBP"]) as source:
            if source.width * source.height > MAX_PIXELS or max(source.size) > 8000:
                raise HTTPException(422, "照片解析度過大，請縮小至 1200 萬像素以下")
            if getattr(source, "is_animated", False):
                raise HTTPException(422, "請上傳靜態照片")
            image = ImageOps.exif_transpose(source)
            image.thumbnail((1600, 1600))
            clean = Image.new("RGB", image.size, "white")
            if image.mode in {"RGBA", "LA"} or "transparency" in image.info:
                rgba = image.convert("RGBA")
                clean.paste(rgba, mask=rgba.getchannel("A"))
            else:
                clean.paste(image.convert("RGB"))
            output = BytesIO()
            clean.save(output, format="WEBP", quality=85, method=4)
            result = output.getvalue()
            if len(result) > MAX_PHOTO_BYTES:
                raise HTTPException(422, "照片內容過大，請降低解析度後重新上傳")
            return result
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as exc:
        raise HTTPException(422, "照片無法讀取，請使用有效的 JPG、PNG 或 WebP") from exc


async def read_photo_upload(request: Request) -> bytes:
    if request.headers.get("content-type", "").split(";")[0] not in ALLOWED_TYPES:
        raise HTTPException(415, "僅支援 JPG、PNG 或 WebP 照片")
    length = request.headers.get("content-length", "0")
    if not length.isdecimal():
        raise HTTPException(400, "照片上傳長度無效")
    if int(length) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "照片不可超過 5 MB")
    content = bytearray()
    async for chunk in request.stream():
        if len(content) + len(chunk) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "照片不可超過 5 MB")
        content.extend(chunk)
    async with _processing:
        normalized = await run_in_threadpool(normalize_photo, bytes(content))
    return normalized
