# Upload resource limits

Manual artifact uploads are limited to 10 MiB and profile pictures to 5 MiB.
Every multipart request is capped at 12 MiB before multipart parsing, including
chunked requests without a usable `Content-Length`. Multipart forms allow one
file, at most eight fields, and fields no larger than 16 KiB. These defaults
bound request memory, Python multipart spool usage, and local temporary-disk
use while leaving 2 MiB for multipart framing around the largest accepted file.

The request, artifact, profile-image, field-count, field-size and image-pixel
budgets can be adjusted with `GOLDENAGE_UPLOAD_REQUEST_BYTES`,
`GOLDENAGE_UPLOAD_ARTIFACT_BYTES`, `GOLDENAGE_UPLOAD_PROFILE_IMAGE_BYTES`,
`GOLDENAGE_UPLOAD_MULTIPART_FIELDS`, `GOLDENAGE_UPLOAD_MULTIPART_FIELD_BYTES`
and `GOLDENAGE_UPLOAD_IMAGE_PIXELS`. Values must be positive integers.

Profile pictures accept only non-interlaced, 8-bit PNG variants (grayscale,
RGB, grayscale+alpha, RGBA), with one image frame and at most 16 million
pixels. Their scanlines are zlib-decoded under the calculated pixel budget and
then written as a new metadata-free PNG using a server-generated `.png` name.
Outlook uploads require a parseable OLE compound-file container in addition to
the `.msg` filename; declared MIME types are not trusted.
