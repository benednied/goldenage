"""Bounded upload handling used by the web delivery layer.

The limits deliberately leave room for multipart framing: an Outlook message is
limited to 10 MiB and a profile picture to 5 MiB, while a multipart request is
limited to 12 MiB.  Fields are capped at 16 KiB, so they cannot turn the
multipart parser's in-memory field buffer into an unbounded allocation.
"""

from __future__ import annotations

import binascii
import io
import os
import struct
import zlib
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import olefile
from fastapi import HTTPException, UploadFile

MEBIBYTE = 1024 * 1024


@dataclass(frozen=True, slots=True)
class UploadLimits:
    """Resource budgets for the two upload workflows."""

    request_bytes: int = 12 * MEBIBYTE
    artifact_bytes: int = 10 * MEBIBYTE
    profile_image_bytes: int = 5 * MEBIBYTE
    multipart_fields: int = 8
    multipart_field_bytes: int = 16 * 1024
    image_pixels: int = 16_000_000
    image_frames: int = 1
    read_chunk_bytes: int = 64 * 1024

    @classmethod
    def from_environment(cls) -> UploadLimits:
        """Read positive byte budgets; invalid values keep the safe defaults."""
        defaults = cls()
        return cls(
            request_bytes=_positive_env("GOLDENAGE_UPLOAD_REQUEST_BYTES", defaults.request_bytes),
            artifact_bytes=_positive_env(
                "GOLDENAGE_UPLOAD_ARTIFACT_BYTES", defaults.artifact_bytes
            ),
            profile_image_bytes=_positive_env(
                "GOLDENAGE_UPLOAD_PROFILE_IMAGE_BYTES", defaults.profile_image_bytes
            ),
            multipart_fields=_positive_env(
                "GOLDENAGE_UPLOAD_MULTIPART_FIELDS", defaults.multipart_fields
            ),
            multipart_field_bytes=_positive_env(
                "GOLDENAGE_UPLOAD_MULTIPART_FIELD_BYTES", defaults.multipart_field_bytes
            ),
            image_pixels=_positive_env("GOLDENAGE_UPLOAD_IMAGE_PIXELS", defaults.image_pixels),
            image_frames=1,
            read_chunk_bytes=defaults.read_chunk_bytes,
        )


class UploadTooLarge(HTTPException):
    """The ASGI receive wrapper consumed more bytes than its budget."""

    def __init__(self) -> None:
        super().__init__(status_code=413, detail="Request body is too large.")


class UploadLimitMiddleware:
    """Count received multipart bytes before Starlette starts parsing them."""

    def __init__(self, app: Any, *, limit: int) -> None:
        self.app = app
        self.limit = limit

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[..., Awaitable[Any]],
        send: Callable[..., Awaitable[Any]],
    ) -> None:
        if scope["type"] != "http" or not _is_multipart(scope):
            await self.app(scope, receive, send)
            return
        content_length = _header(scope, b"content-length")
        if content_length is not None:
            try:
                if int(content_length) > self.limit:
                    await _send_413(send)
                    return
            except ValueError:
                pass

        received = 0

        async def bounded_receive() -> Any:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.limit:
                    raise UploadTooLarge
            return message

        await self.app(scope, bounded_receive, send)


async def read_upload_limited(file: UploadFile, *, limit: int, chunk_size: int) -> bytes:
    """Read an upload incrementally and close its spool on every outcome."""
    chunks: list[bytes] = []
    size = 0
    try:
        while chunk := await file.read(chunk_size):
            size += len(chunk)
            if size > limit:
                raise HTTPException(status_code=413, detail="Uploaded file is too large.")
            chunks.append(chunk)
        return b"".join(chunks)
    finally:
        await file.close()


def validate_outlook_msg(content: bytes) -> None:
    """Require a parseable OLE compound document, the container used by MSG."""
    if not content or not olefile.isOleFile(io.BytesIO(content)):
        raise HTTPException(status_code=400, detail="Upload is not a valid Outlook MSG file.")
    try:
        with olefile.OleFileIO(
            io.BytesIO(content), raise_defects=olefile.DEFECT_INCORRECT
        ) as document:
            if not document.listdir():
                raise ValueError("empty compound document")
    except (OSError, ValueError, struct.error) as exc:
        raise HTTPException(
            status_code=400, detail="Upload is not a valid Outlook MSG file."
        ) from exc


def normalize_profile_png(content: bytes, *, limits: UploadLimits) -> bytes:
    """Decode a non-interlaced PNG and serialize a metadata-free PNG.

    PNG is intentionally the sole accepted input and output format.  It makes
    static local-first serving safe from client-controlled extensions and lets
    us validate all decompressed scanlines without an optional image runtime.
    """
    if not content.startswith(b"\x89PNG\r\n\x1a\n"):
        raise HTTPException(status_code=400, detail="Profile picture must be a valid PNG image.")
    position = 8
    width = height = color_type = bit_depth = None
    idat: list[bytes] = []
    seen_iend = False
    while position < len(content):
        if position + 12 > len(content):
            break
        length = struct.unpack(">I", content[position : position + 4])[0]
        chunk_end = position + 12 + length
        if chunk_end > len(content):
            break
        kind = content[position + 4 : position + 8]
        data = content[position + 8 : position + 8 + length]
        checksum = struct.unpack(">I", content[position + 8 + length : chunk_end])[0]
        if binascii.crc32(kind + data) & 0xFFFFFFFF != checksum:
            break
        position = chunk_end
        if kind == b"IHDR" and length == 13 and width is None:
            width, height, bit_depth, color_type, compression, filtering, interlace = struct.unpack(
                ">IIBBBBB", data
            )
            if (
                compression
                or filtering
                or interlace
                or bit_depth != 8
                or color_type not in {0, 2, 4, 6}
            ):
                raise HTTPException(
                    status_code=400, detail="Profile picture PNG format is not supported."
                )
            if not width or not height or width * height > limits.image_pixels:
                raise HTTPException(
                    status_code=413, detail="Profile picture dimensions are too large."
                )
        elif kind == b"IDAT" and width is not None:
            idat.append(data)
        elif kind == b"IEND" and length == 0:
            seen_iend = True
            break
    if width is None or height is None or not idat or not seen_iend or position != len(content):
        raise HTTPException(status_code=400, detail="Profile picture must be a valid PNG image.")
    assert color_type is not None
    channels = {0: 1, 2: 3, 4: 2, 6: 4}[color_type]
    expected = height * (1 + width * channels)
    try:
        decompressor = zlib.decompressobj()
        decoded = decompressor.decompress(b"".join(idat), expected + 1)
        decoded += decompressor.flush(expected + 1 - len(decoded))
    except zlib.error as exc:
        raise HTTPException(
            status_code=400, detail="Profile picture must be a valid PNG image."
        ) from exc
    if (
        len(decoded) != expected
        or not decompressor.eof
        or decompressor.unused_data
        or decompressor.unconsumed_tail
        or not _valid_png_filters(decoded, width * channels)
    ):
        raise HTTPException(status_code=400, detail="Profile picture must be a valid PNG image.")
    return (
        _png_chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, color_type, 0, 0, 0))
        + _png_chunk(b"IDAT", zlib.compress(decoded, level=9))
        + _png_chunk(b"IEND", b"")
    )


def _valid_png_filters(decoded: bytes, row_bytes: int) -> bool:
    return all(decoded[offset] <= 4 for offset in range(0, len(decoded), row_bytes + 1))


def _png_chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
    )


def _positive_env(name: str, default: int) -> int:
    try:
        return max(1, int(os.environ.get(name, default)))
    except ValueError:
        return default


def _header(scope: dict[str, Any], target: bytes) -> str | None:
    for key, value in scope.get("headers", []):
        if key.lower() == target:
            return value.decode("latin-1")
    return None


def _is_multipart(scope: dict[str, Any]) -> bool:
    return (_header(scope, b"content-type") or "").lower().startswith("multipart/")


async def _send_413(send: Callable[..., Awaitable[Any]]) -> None:
    await send(
        {
            "type": "http.response.start",
            "status": 413,
            "headers": [(b"content-type", b"text/plain; charset=utf-8")],
        }
    )
    await send({"type": "http.response.body", "body": b"Request body is too large."})
