"""
Storage layer for the /nexclan and /nexrules embed system.

Two files are involved, with a clear split:
  - data/clan_tags.json    -> READ ONLY here. General clan-tag source used
                              across the whole bot. We never write to it.
  - data/clan_embeds.json  -> Owned entirely by this feature. Holds every
                              piece of state needed to render/post/edit the
                              showcase embeds.

All writes to clan_embeds.json are atomic (write to a temp file, then
os.replace) so a crash mid-write never corrupts the live file.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Optional

TAGS_PATH = "data/clan_tags.json"
EMBEDS_PATH = "data/clan_embeds.json"

VALID_TIERS = ("competitive", "semi-competitive")


# ---------------------------------------------------------------------------
# clan_tags.json (read-only)
# ---------------------------------------------------------------------------

def load_clan_tags() -> dict[str, str]:
    """
    Returns {clan_name: clan_tag} from data/clan_tags.json.

    ASSUMPTION: I don't have access to the live file, so this handles the
    three shapes that are most likely to exist. If none match your real
    file, tell me the actual structure and I'll fix this one function.

    Shape A (dict of name -> tag string):
        {"PINEAPPLES": "#ABC123", "KRYPTONITE": "#DEF456"}

    Shape B (dict of name -> object with a "tag" key):
        {"PINEAPPLES": {"tag": "#ABC123"}, ...}

    Shape C (list of objects):
        [{"name": "PINEAPPLES", "tag": "#ABC123"}, ...]
    """
    if not os.path.exists(TAGS_PATH):
        return {}

    with open(TAGS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result: dict[str, str] = {}

    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, str):
                result[name] = value  # Shape A
            elif isinstance(value, dict) and "tag" in value:
                result[name] = value["tag"]  # Shape B
    elif isinstance(raw, list):
        for entry in raw:
            if isinstance(entry, dict) and "name" in entry and "tag" in entry:
                result[entry["name"]] = entry["tag"]  # Shape C

    return result


# ---------------------------------------------------------------------------
# clan_embeds.json (owned by this feature)
# ---------------------------------------------------------------------------

@dataclass
class ClanLinks:
    war_rules: str = ""
    cwl_rules: str = ""
    clan_rules: str = ""


@dataclass
class ClanEmbedEntry:
    tag: str = ""
    tier: str = "competitive"
    emoji: str = ""
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None
    requirements: str = ""          # entry reqs / hit rate / wars required block
    description: str = ""           # free text, may include pasted markdown links
    last_updated: Optional[str] = None

    def touch(self) -> None:
        self.last_updated = datetime.now(timezone.utc).isoformat()

    @property
    def is_posted(self) -> bool:
        return self.message_id is not None and self.channel_id is not None


@dataclass
class IndexEmbedConfig:
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None

    @property
    def is_posted(self) -> bool:
        return self.message_id is not None and self.channel_id is not None


def _default_store() -> dict:
    return {"clans": {}, "index_embed": asdict(IndexEmbedConfig())}


def _atomic_write(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(dir=directory, prefix=".tmp_", suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


class ClanEmbedStore:
    """Loads the whole clan_embeds.json into memory and writes back atomically."""

    def __init__(self, path: str = EMBEDS_PATH):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            data = _default_store()
            _atomic_write(self.path, data)
            return data
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def save(self) -> None:
        _atomic_write(self.path, self._data)

    # -- clan entries --------------------------------------------------

    def get_clan(self, name: str) -> Optional[ClanEmbedEntry]:
        raw = self._data["clans"].get(name)
        if raw is None:
            return None
        return ClanEmbedEntry(**raw)

    def get_or_create_clan(self, name: str, tag: str = "") -> ClanEmbedEntry:
        existing = self.get_clan(name)
        if existing:
            return existing
        return ClanEmbedEntry(tag=tag)

    def upsert_clan(self, name: str, entry: ClanEmbedEntry) -> None:
        entry.touch()
        self._data["clans"][name] = asdict(entry)
        self.save()

    def remove_clan(self, name: str) -> None:
        self._data["clans"].pop(name, None)
        self.save()

    def all_clans(self) -> dict[str, ClanEmbedEntry]:
        return {name: ClanEmbedEntry(**raw) for name, raw in self._data["clans"].items()}

    # -- index embed -----------------------------------------------------

    def get_index_embed(self) -> IndexEmbedConfig:
        return IndexEmbedConfig(**self._data.get("index_embed", asdict(IndexEmbedConfig())))

    def set_index_embed(self, config: IndexEmbedConfig) -> None:
        self._data["index_embed"] = asdict(config)
        self.save()
