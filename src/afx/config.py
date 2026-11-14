"""User settings, in <home>/config.toml. Created with defaults on first use so
the user can find and edit it."""

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from . import __version__
from .home import home, write_atomic
from .relay import DEFAULT_RELAYS

DEFAULT_TEXT = f'''# afx client settings.

# Let your agent publish claims to the network. Off by default: with it off,
# the agent can look up and verify facts but never sends anything.
publish = false

# Relays to read from and publish to.
relays = [
{"".join(f'    "{r}",{chr(10)}' for r in DEFAULT_RELAYS)}]

# How published claims name their author. Name the software, never a person.
agent = "afx-mcp/{__version__}"
'''


@dataclass
class Config:
    path: Path
    publish: bool = False
    relays: list = field(default_factory=lambda: list(DEFAULT_RELAYS))
    agent: str = f"afx-mcp/{__version__}"


def load(path: Path | None = None) -> Config:
    path = Path(path) if path else home() / "config.toml"
    if not path.exists():
        write_atomic(path, DEFAULT_TEXT.encode())
    data = tomllib.loads(path.read_text())
    cfg = Config(path=path)
    if isinstance(data.get("publish"), bool):
        cfg.publish = data["publish"]
    if isinstance(data.get("relays"), list) and data["relays"]:
        cfg.relays = [r for r in data["relays"]
                      if isinstance(r, str) and r.startswith(("wss://", "ws://"))]
    if isinstance(data.get("agent"), str) and data["agent"].strip():
        cfg.agent = data["agent"].strip()
    return cfg
