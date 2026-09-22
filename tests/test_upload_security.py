import asyncio
import struct
import tempfile
import zlib
from typing import cast

import pytest
from fastapi import HTTPException, UploadFile
from starlette.requests import Request

from goldenage.web.upload_security import (
    UploadLimitMiddleware,
    UploadLimits,
    normalize_profile_png,
    read_upload_limited,
    validate_outlook_msg,
)


def test_read_upload_limited_accepts_at_limit_and_closes_file() -> None:
    file = _upload_file(b"1234")

    assert asyncio.run(read_upload_limited(file, limit=4, chunk_size=1)) == b"1234"
    assert file.file.closed


def test_read_upload_limited_rejects_first_byte_over_limit_and_closes_file() -> None:
    file = _upload_file(b"12345")

    with pytest.raises(HTTPException) as error:
        asyncio.run(read_upload_limited(file, limit=4, chunk_size=1))

    assert error.value.status_code == 413
    assert file.file.closed


def test_request_limit_counts_chunked_multipart_before_parser() -> None:
    consumed: list[bytes] = []
    sent: list[dict[str, object]] = []

    async def downstream(scope, receive, send) -> None:
        del scope, send
        while message := await receive():
            consumed.append(message.get("body", b""))
            if not message.get("more_body"):
                return

    chunks = iter(
        [
            {"type": "http.request", "body": b"a", "more_body": True},
            {"type": "http.request", "body": b"b", "more_body": True},
            {"type": "http.request", "body": b"c", "more_body": True},
            {"type": "http.request", "body": b"d", "more_body": True},
            {"type": "http.request", "body": b"e", "more_body": False},
        ]
    )

    async def receive() -> dict[str, object]:
        return cast(dict[str, object], next(chunks))

    async def send(message: object) -> None:
        sent.append(cast(dict[str, object], message))

    middleware = UploadLimitMiddleware(downstream, limit=4)
    asyncio.run(
        middleware(
            {"type": "http", "headers": [(b"content-type", b"multipart/form-data")]},
            receive,
            send,
        )
    )

    assert consumed == [b"a", b"b", b"c", b"d"]
    assert sent == [
        {"type": "http.response.start", "status": 413, "headers": [(b"content-type", b"text/plain; charset=utf-8")]},
        {"type": "http.response.body", "body": b"Request body is too large."},
    ]


def test_request_limit_returns_413_through_starlette_parser_without_content_length() -> None:
    boundary = b"goldenage-boundary"
    body = (
        b"--" + boundary + b"\r\n"
        b'Content-Disposition: form-data; name="file"; filename="mail.msg"\r\n'
        b"Content-Type: application/octet-stream\r\n\r\n"
        b"0123456789"
        b"\r\n--" + boundary + b"--\r\n"
    )
    chunks = iter(body[index : index + 7] for index in range(0, len(body), 7))
    sent: list[dict[str, object]] = []

    async def receive() -> dict[str, object]:
        try:
            chunk = next(chunks)
        except StopIteration:
            return {"type": "http.request", "body": b"", "more_body": False}
        return {"type": "http.request", "body": chunk, "more_body": True}

    async def downstream(scope, receive, send) -> None:
        request = Request(scope, receive)
        try:
            await request.form()
        except HTTPException as error:
            await send({"type": "http.response.start", "status": error.status_code, "headers": []})
            await send(
                {
                    "type": "http.response.body",
                    "body": b'{"detail":"' + error.detail.encode() + b'"}',
                }
            )

    async def send(message: object) -> None:
        sent.append(cast(dict[str, object], message))

    asyncio.run(
        UploadLimitMiddleware(downstream, limit=20)(
            {
                "type": "http",
                "app": object(),
                "headers": [(b"content-type", b"multipart/form-data; boundary=" + boundary)],
            },
            receive,
            send,
        )
    )

    assert sent[0]["status"] == 413
    assert sent[1]["body"] == b'{"detail":"Request body is too large."}'


def test_invalid_msg_is_rejected_even_with_msg_extension_and_mime_type() -> None:
    with pytest.raises(HTTPException, match="valid Outlook MSG"):
        validate_outlook_msg(b"not an OLE compound file")


def test_png_is_decoded_reencoded_without_text_metadata() -> None:
    source = _png_with_text_metadata()

    normalized = normalize_profile_png(source, limits=UploadLimits(image_pixels=1))

    assert normalized.startswith(b"\x89PNG\r\n\x1a\n")
    assert b"secret" not in normalized
    assert normalize_profile_png(normalized, limits=UploadLimits(image_pixels=1)) == normalized


def test_png_pixel_limit_and_bad_compressed_content_are_rejected() -> None:
    with pytest.raises(HTTPException) as oversized:
        normalize_profile_png(
            _png(width=2, height=1, scanlines=b"\0\0\0\0\0\0\0"),
            limits=UploadLimits(image_pixels=1),
        )
    assert oversized.value.status_code == 413

    with pytest.raises(HTTPException, match="valid PNG"):
        normalize_profile_png(_png(width=1, height=1, scanlines=b"broken"), limits=UploadLimits())


def test_animated_and_trailing_png_content_are_rejected() -> None:
    source = _png(width=1, height=1, scanlines=b"\0\0\0\0")
    animated = source[:33] + _chunk(b"acTL", struct.pack(">II", 2, 0)) + source[33:]

    with pytest.raises(HTTPException, match="Animated"):
        normalize_profile_png(animated, limits=UploadLimits())
    with pytest.raises(HTTPException, match="valid PNG"):
        normalize_profile_png(source + b"not a PNG chunk", limits=UploadLimits())


def _upload_file(content: bytes) -> UploadFile:
    spool = tempfile.SpooledTemporaryFile(max_size=len(content) + 1)
    spool.write(content)
    spool.seek(0)
    return UploadFile(filename="mail.msg", file=spool)  # ty:ignore[invalid-argument-type]


def _png_with_text_metadata() -> bytes:
    header = struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"tEXt", b"note\0secret")
        + _chunk(b"IDAT", zlib.compress(b"\0\x00\x00\x00"))
        + _chunk(b"IEND", b"")
    )


def _png(*, width: int, height: int, scanlines: bytes) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", header)
        + _chunk(b"IDAT", zlib.compress(scanlines))
        + _chunk(b"IEND", b"")
    )


def _chunk(kind: bytes, data: bytes) -> bytes:
    return (
        struct.pack(">I", len(data))
        + kind
        + data
        + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)
    )
