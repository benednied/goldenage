import asyncio
import io
import struct
import zlib

import pytest
from fastapi import HTTPException, UploadFile

from goldenage.web.upload_security import (
    UploadLimits,
    normalize_profile_png,
    read_upload_limited,
    validate_outlook_msg,
)


def test_read_upload_limited_accepts_at_limit_and_closes_file() -> None:
    file = UploadFile(filename="mail.msg", file=io.BytesIO(b"1234"))

    assert asyncio.run(read_upload_limited(file, limit=4, chunk_size=1)) == b"1234"
    assert file.file.closed


def test_read_upload_limited_rejects_first_byte_over_limit_and_closes_file() -> None:
    file = UploadFile(filename="mail.msg", file=io.BytesIO(b"12345"))

    with pytest.raises(HTTPException) as error:
        asyncio.run(read_upload_limited(file, limit=4, chunk_size=1))

    assert error.value.status_code == 413
    assert file.file.closed


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
