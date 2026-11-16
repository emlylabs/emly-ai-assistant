"""G13c: check what an uploaded file really is, from its bytes.

Uploads used to be accepted on the type the browser declared
(``Content-Type``) and their file name. A program renamed ``report.pdf``
and sent as ``application/pdf`` passed every check, was stored, and, for
widget form attachments, was emailed on to the bot's recipients.

``check_upload`` reads the file's first bytes (and, for zip-based office
files, the zip's table of contents) and refuses:

- programs and scripts (Windows, Linux and macOS executables, and file
  names such as ``.exe``, ``.js``, ``.bat``), whatever they claim to be;
- archives (zip, gzip, 7z, rar, tar, …). Word, Excel, PowerPoint and
  OpenDocument files are zips inside, and are accepted when they look
  like one (``[Content_Types].xml`` or an ODF ``mimetype`` entry) and
  unpack to no more than ``MAX_UNPACKED_BYTES`` (a zip bomb guard; Python
  never unpacks more than an entry's declared size);
- a file whose content is none of the types its name and declared type
  stand for (a zip named ``notes.txt``, a PDF sent as ``text/plain``).

Types it doesn't know (an image format the admin allowed, say) pass when
they are neither a program nor an archive. This is a type check, not a
malware scan: a PDF with a malicious payload is still a PDF.
"""
from __future__ import annotations

import os
import zipfile
from typing import IO, FrozenSet, Optional

HEAD_BYTES = 8192
MAX_UNPACKED_BYTES = 512 * 1024 * 1024
MAX_ZIP_ENTRIES = 10_000

# Kinds sniff() can return.
PDF, OOXML, ODF, OLE2, TEXT = "pdf", "ooxml", "odf", "ole2", "text"
PNG, JPEG, GIF, WEBP = "png", "jpeg", "gif", "webp"
ZIP, ARCHIVE, EXECUTABLE, BINARY = "zip", "archive", "executable", "binary"

_BY_EXTENSION = {
    ".pdf": {PDF},
    ".docx": {OOXML}, ".xlsx": {OOXML}, ".pptx": {OOXML},
    ".odt": {ODF}, ".ods": {ODF}, ".odp": {ODF},
    ".doc": {OLE2}, ".xls": {OLE2}, ".ppt": {OLE2}, ".msg": {OLE2},
    ".png": {PNG}, ".jpg": {JPEG}, ".jpeg": {JPEG}, ".gif": {GIF}, ".webp": {WEBP},
    **{ext: {TEXT} for ext in (".txt", ".md", ".markdown", ".csv", ".tsv", ".html",
                               ".htm", ".json", ".xml", ".log")},
}

_BY_MIME = {
    "application/pdf": {PDF},
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": {OOXML},
    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet": {OOXML},
    "application/vnd.openxmlformats-officedocument.presentationml.presentation": {OOXML},
    "application/vnd.oasis.opendocument.text": {ODF},
    "application/vnd.oasis.opendocument.spreadsheet": {ODF},
    "application/vnd.oasis.opendocument.presentation": {ODF},
    "application/msword": {OLE2},
    # Windows browsers send .csv files as application/vnd.ms-excel.
    "application/vnd.ms-excel": {OLE2, TEXT},
    "application/vnd.ms-powerpoint": {OLE2},
    "application/json": {TEXT},
    "application/xml": {TEXT},
    "image/png": {PNG},
    "image/jpeg": {JPEG},
    "image/gif": {GIF},
    "image/webp": {WEBP},
}

# Refused by name, whatever the content looks like.
BLOCKED_EXTENSIONS: FrozenSet[str] = frozenset({
    ".exe", ".dll", ".com", ".scr", ".msi", ".msp", ".bat", ".cmd", ".ps1", ".psm1",
    ".vbs", ".vbe", ".js", ".jse", ".wsf", ".wsh", ".hta", ".lnk", ".jar", ".apk",
    ".app", ".dmg", ".pkg", ".sh", ".bash", ".cpl", ".reg", ".iso", ".img",
})

_EXECUTABLE_MAGIC = (
    b"MZ",                      # Windows PE
    b"\x7fELF",                 # Linux
    b"\xfe\xed\xfa\xce", b"\xfe\xed\xfa\xcf",  # Mach-O
    b"\xce\xfa\xed\xfe", b"\xcf\xfa\xed\xfe",
    b"\xca\xfe\xba\xbe",        # Mach-O universal / Java class
)
_ARCHIVE_MAGIC = (
    b"\x1f\x8b",                # gzip
    b"BZh",                     # bzip2
    b"\xfd7zXZ\x00",            # xz
    b"7z\xbc\xaf\x27\x1c",      # 7z
    b"Rar!\x1a\x07",            # rar
    b"\x28\xb5\x2f\xfd",        # zstd
    b"MSCF",                    # cab
)


class UploadRejected(ValueError):
    """The file can't be accepted; ``str(exc)`` is safe to show the uploader."""


def _looks_like_text(head: bytes) -> bool:
    if head.startswith((b"\xef\xbb\xbf", b"\xff\xfe", b"\xfe\xff")):
        return True  # byte-order mark: UTF-8/16 text
    if b"\x00" in head:
        return False
    control = sum(1 for b in head if b < 0x20 and b not in (0x09, 0x0A, 0x0C, 0x0D, 0x1B))
    return control <= len(head) // 100


def sniff(head: bytes) -> str:
    """The kind of file ``head`` (its first bytes) starts."""
    if head.startswith(_EXECUTABLE_MAGIC):
        return EXECUTABLE
    if head.startswith(_ARCHIVE_MAGIC) or head[257:262] == b"ustar":
        return ARCHIVE
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        return ZIP
    if b"%PDF-" in head[:1024]:
        return PDF
    if head.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
        return OLE2
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return PNG
    if head.startswith(b"\xff\xd8\xff"):
        return JPEG
    if head.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return WEBP
    if _looks_like_text(head):
        return TEXT  # including an empty file
    return BINARY


def _office_kind(fileobj: IO[bytes]) -> str:
    """OOXML or ODF for an office zip; raises UploadRejected otherwise."""
    try:
        with zipfile.ZipFile(fileobj) as zf:
            infos = zf.infolist()
            names = {i.filename for i in infos}
            if len(infos) > MAX_ZIP_ENTRIES or sum(i.file_size for i in infos) > MAX_UNPACKED_BYTES:
                raise UploadRejected("This document unpacks to more than this server accepts.")
            if "[Content_Types].xml" in names:
                return OOXML
            if "mimetype" in names:
                return ODF
    except zipfile.BadZipFile:
        raise UploadRejected("This file is a damaged zip archive.") from None
    raise UploadRejected("Zip archives can't be uploaded. Upload the files inside instead.")


def expected_kinds(filename: Optional[str], content_type: Optional[str]) -> FrozenSet[str]:
    """Kinds the name and declared type stand for; empty when neither is known."""
    ext = os.path.splitext(filename or "")[1].lower()
    mime = (content_type or "").split(";", 1)[0].strip().lower()
    kinds = set(_BY_EXTENSION.get(ext, ()))
    kinds |= _BY_MIME.get(mime, set())
    if mime.startswith("text/"):
        kinds.add(TEXT)
    return frozenset(kinds)


def check_upload(fileobj: IO[bytes], filename: Optional[str], content_type: Optional[str]) -> str:
    """Return the file's kind, or raise UploadRejected.

    ``fileobj`` must be seekable; it is left at position 0.
    """
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in BLOCKED_EXTENSIONS:
        raise UploadRejected(f"Files of type {ext} can't be uploaded.")

    fileobj.seek(0)
    head = fileobj.read(HEAD_BYTES)
    fileobj.seek(0)
    kind = sniff(head)
    try:
        if kind == EXECUTABLE:
            raise UploadRejected("Programs can't be uploaded.")
        if kind == ARCHIVE:
            raise UploadRejected("Archives can't be uploaded. Upload the files inside instead.")
        if kind == ZIP:
            kind = _office_kind(fileobj)
    finally:
        fileobj.seek(0)

    expected = expected_kinds(filename, content_type)
    if expected and kind not in expected:
        raise UploadRejected(
            "This file's content doesn't match its name or type. "
            "Save it in its real format and upload it again."
        )
    return kind
