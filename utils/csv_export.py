"""CSV download helpers shared by the audit-log and analytics exports.

Exported cells can hold attacker-influenced text (user agents, emails,
topic names, file names). Spreadsheet apps treat a cell starting with
``= + - @`` (or a tab / carriage return) as a formula, so such cells get a
leading apostrophe (OWASP "CSV injection"). Numbers are written as
numbers, so a negative value stays a number.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Sequence

from fastapi.responses import StreamingResponse

FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def csv_cell(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, datetime):
        value = value.isoformat()
    elif isinstance(value, (dict, list)):
        value = json.dumps(value, sort_keys=True, default=str)
    text = str(value)
    if text.startswith(FORMULA_PREFIXES):
        text = "'" + text
    return text


def csv_lines(rows: Iterable[Sequence[Any]], *, chunk_bytes: int = 64 * 1024) -> Iterator[str]:
    """Encode rows (already-ordered cell sequences) in chunks."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    for row in rows:
        writer.writerow([csv_cell(v) for v in row])
        if buf.tell() > chunk_bytes:
            yield buf.getvalue()
            buf.seek(0)
            buf.truncate(0)
    yield buf.getvalue()


def csv_download(chunks: Iterable[str], name: str) -> StreamingResponse:
    """A no-store CSV attachment named ``<name>-<UTC stamp>.csv``."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return StreamingResponse(
        chunks,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="{name}-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )
