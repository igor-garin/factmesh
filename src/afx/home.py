"""Where the client keeps its files, and how it writes them."""

import os
import tempfile
from pathlib import Path


def home() -> Path:
    if os.environ.get("AFX_HOME"):
        return Path(os.environ["AFX_HOME"])
    base = os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share"
    return Path(base) / "afx"


def write_atomic(path: Path, data: bytes, mode=0o644):
    """Write via a temp file and rename, so readers never see a partial file."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        os.replace(tmp, path)
    except BaseException:
        os.unlink(tmp)
        raise
