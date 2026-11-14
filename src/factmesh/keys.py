"""The installation's signing key (DESIGN.md principle 6)."""

from pathlib import Path

from .event import Signer
from .home import home, write_atomic


def key_path() -> Path:
    return home() / "key"


def load_signer(create=True) -> Signer:
    path = key_path()
    if path.exists():
        return Signer(bytes.fromhex(path.read_text().strip()))
    if not create:
        raise FileNotFoundError(path)
    return rotate()


def rotate() -> Signer:
    """Replace the key with a new one. Earlier claims stay signed by the old key."""
    signer = Signer()
    write_atomic(key_path(), signer.secret.hex().encode() + b"\n", mode=0o600)
    return signer
