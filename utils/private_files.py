"""Write secret files (signing keys, the dev secrets key, the bootstrap
password) so they are never readable by anyone but the owner.

``Path.write_bytes`` followed by ``chmod`` leaves a window where the file
exists with the umask's permissions (often world-readable), and a crash
between the two leaves it that way. ``write_private`` creates a temporary
file with mode 0600 from the start, writes and syncs it, then renames it
into place, so the target is either absent, the old file, or the new
file with mode 0600.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Union


def ensure_private_dir(path: Union[str, os.PathLike]) -> Path:
    """Create ``path`` (and parents) if needed; new directories get 0700."""
    p = Path(path)
    p.mkdir(mode=0o700, parents=True, exist_ok=True)
    return p


def write_private(path: Union[str, os.PathLike], data: bytes) -> None:
    target = Path(path)
    ensure_private_dir(target.parent)
    # mkstemp creates the file with mode 0600 (O_CREAT | O_EXCL).
    fd, tmp = tempfile.mkstemp(prefix=f".{target.name}.", dir=str(target.parent))
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
