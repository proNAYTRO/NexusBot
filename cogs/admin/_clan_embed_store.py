
"""
Storage layer for the /nexclan and /nexrules embed system.

data/clan_tags.json
    READ ONLY. General clan-tag source used across the whole bot.

data/clan_embeds.json
    Owned by this feature. Stores all state needed to manage
    clan showcase embeds.

Clan showcase entries additionally store:
    - webhook_url
    - channel_id
    - thread_id
    - message_id

The same webhook can be used by multiple clans, with each clan
using a different thread in the webhook's parent channel.

All writes are atomic.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Optional


TAGS_PATH = "data/clan_tags.json"
EMBEDS_PATH = "data/clan_embeds.json"


# ---------------------------------------------------------------------------
# clan_tags.json
# ---------------------------------------------------------------------------

def load_clan_tags() -> dict[str, str]:
    """
    Returns:
        {clan_name: clan_tag}

    Supported source formats:

    A:
        {
            "PINEAPPLES": "#ABC123"
        }

    B:
        {
            "PINEAPPLES": {
                "tag": "#ABC123"
            }
        }

    C:
        [
            {
                "name": "PINEAPPLES",
                "tag": "#ABC123"
            }
        ]
    """

    if not os.path.exists(TAGS_PATH):
        return {}

    with open(TAGS_PATH, "r", encoding="utf-8") as f:
        raw = json.load(f)

    result: dict[str, str] = {}

    if isinstance(raw, dict):
        for name, value in raw.items():
            if isinstance(value, str):
                result[str(name)] = value

            elif isinstance(value, dict) and "tag" in value:
                result[str(name)] = str(value["tag"])

    elif isinstance(raw, list):
        for entry in raw:
            if (
                isinstance(entry, dict)
                and "name" in entry
                and "tag" in entry
            ):
                result[str(entry["name"])] = str(entry["tag"])

    return result


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

@dataclass
class ClanEmbedEntry:
    tag: str = ""

    # User-editable content
    requirements: str = ""
    description: str = ""

    # Discord webhook configuration
    webhook_url: str = ""

    # Where the webhook message lives
    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None

    last_updated: Optional[str] = None

    def touch(self) -> None:
        self.last_updated = datetime.now(timezone.utc).isoformat()

    @property
    def is_posted(self) -> bool:
        return (
            self.message_id is not None
            and self.channel_id is not None
            and bool(self.webhook_url)
        )


@dataclass
class IndexEmbedConfig:
    """
    The /nexrules index embed remains a normal bot message.

    It is intentionally kept separate from clan showcase webhooks.
    """

    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None

    @property
    def is_posted(self) -> bool:
        return (
            self.message_id is not None
            and self.channel_id is not None
        )


# ---------------------------------------------------------------------------
# Atomic JSON storage
# ---------------------------------------------------------------------------

def _default_store() -> dict:
    return {
        "clans": {},
        "index_embed": asdict(IndexEmbedConfig()),
    }


def atomic_write(path: str, data: dict) -> None:
    directory = os.path.dirname(path) or "."
    os.makedirs(directory, exist_ok=True)

    fd, tmp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".tmp",
        suffix=".json",
    )

    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False,
            )

        os.replace(tmp_path, path)

    except Exception:
        if os.path.exists(tmp_path):
            os.remove(tmp_path)
        raise


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ClanEmbedStore:
    """
    Loads clan_embeds.json into memory and writes changes atomically.
    """

    def __init__(self, path: str = EMBEDS_PATH):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:
        if not os.path.exists(self.path):
            data = _default_store()
            atomic_write(self.path, data)
            return data

        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)

        # Basic migration protection for older files.
        data.setdefault("clans", {})
        data.setdefault(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        # Existing clan entries may have been created by the
        # previous version. Make sure the new fields exist.
        for raw in data["clans"].values():
            raw.setdefault("tag", "")
            raw.setdefault("requirements", "")
            raw.setdefault("description", "")
            raw.setdefault("webhook_url", "")
            raw.setdefault("channel_id", None)
            raw.setdefault("thread_id", None)
            raw.setdefault("message_id", None)
            raw.setdefault("last_updated", None)

        return data

    def save(self) -> None:
        atomic_write(self.path, self._data)

    # ------------------------------------------------------------------
    # Clan entries
    # ------------------------------------------------------------------

    def get_clan(self, name: str) -> Optional[ClanEmbedEntry]:
        raw = self._data["clans"].get(name)

        if raw is None:
            return None

        # Ignore old fields such as emoji/tier if they still exist.
        allowed = {
            "tag",
            "requirements",
            "description",
            "webhook_url",
            "channel_id",
            "thread_id",
            "message_id",
            "last_updated",
        }

        cleaned = {
            key: value
            for key, value in raw.items()
            if key in allowed
        }

        return ClanEmbedEntry(**cleaned)

    def get_or_create_clan(
        self,
        name: str,
        tag: str = "",
    ) -> ClanEmbedEntry:

        existing = self.get_clan(name)

        if existing:
            # Keep the current stored tag if one already exists.
            if not existing.tag and tag:
                existing.tag = tag

            return existing

        return ClanEmbedEntry(tag=tag)

    def upsert_clan(
        self,
        name: str,
        entry: ClanEmbedEntry,
    ) -> None:

        entry.touch()

        self._data["clans"][name] = asdict(entry)

        self.save()

    def remove_clan(self, name: str) -> None:
        self._data["clans"].pop(name, None)
        self.save()

    def all_clans(self) -> dict[str, ClanEmbedEntry]:
        return {
            name: self.get_clan(name)
            for name in self._data["clans"]
            if self.get_clan(name) is not None
        }

    # ------------------------------------------------------------------
    # Index embed
    # ------------------------------------------------------------------

    def get_index_embed(self) -> IndexEmbedConfig:
        raw = self._data.get(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        return IndexEmbedConfig(**raw)

    def set_index_embed(
        self,
        config: IndexEmbedConfig,
    ) -> None:

        self._data["index_embed"] = asdict(config)
        self.save()

