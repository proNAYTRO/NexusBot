"""
Storage layer for the /nexclanembed and /nexrules embed system.

data/clan_tags.json
READ ONLY.
General clan-tag source used across the whole bot.

data/clan_embeds.json
Owned entirely by this feature.

Clan showcase entries store:
- Clash clan tag
- description
- entry information
- Discord webhook URL
- channel ID
- thread ID
- webhook message ID
- last updated timestamp

The same webhook can be used by multiple clans.
Each clan can have a different thread.

All writes are atomic.

The webhook itself is NOT created by this storage layer.

The Discord cog is responsible for:
- finding an existing NAYTRO NEXUS webhook
- creating one if none exists
- detecting a deleted webhook
- recreating/reusing a replacement webhook

This file only stores the resulting webhook URL.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from typing import Optional


TAGS_PATH = "data/clan_tags.json"
EMBEDS_PATH = "data/clan_embeds.json"


# ============================================================================
# clan_tags.json
# ============================================================================

def load_clan_tags() -> dict[str, str]:
    """
    Load clan names and Clash of Clans tags.

    Supported formats:

    Shape A:
    {
        "PINEAPPLES": "#ABC123"
    }

    Shape B:
    {
        "PINEAPPLES": {
            "tag": "#ABC123"
        }
    }

    Shape C:
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


# ============================================================================
# Data models
# ============================================================================

@dataclass
class ClanEmbedEntry:
    """
    Stored configuration for one clan.

    tag:
        Clash of Clans tag used for API lookups.

    description:
        Main manually-written description.

    requirements:
        Entry requirements / hit rate / wars required etc.
        Displayed without a "Requirements" heading.

    webhook_url:
        Discord webhook used to publish the embed.

        This is automatically discovered/created by the Discord cog.
        The user does NOT manually configure this.

    channel_id:
        Parent channel containing the webhook.

    thread_id:
        Optional thread where this clan's embed lives.

    message_id:
        ID of the webhook-created message.

    last_updated:
        UTC timestamp of the last store update.
    """

    tag: str = ""

    description: str = ""
    requirements: str = ""

    webhook_url: str = ""

    channel_id: Optional[int] = None
    thread_id: Optional[int] = None
    message_id: Optional[int] = None

    last_updated: Optional[str] = None

    def touch(self) -> None:
        self.last_updated = datetime.now(
            timezone.utc
        ).isoformat()

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
    /nexrules embed configuration.

    This remains a normal bot message and is intentionally
    separate from the clan showcase webhook system.
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


# ============================================================================
# Atomic storage
# ============================================================================

def _default_store() -> dict:
    return {
        "clans": {},
        "index_embed": asdict(
            IndexEmbedConfig()
        ),
    }


def atomic_write(
    path: str,
    data: dict,
) -> None:

    directory = (
        os.path.dirname(path)
        or "."
    )

    os.makedirs(
        directory,
        exist_ok=True,
    )

    fd, tmp_path = tempfile.mkstemp(
        dir=directory,
        prefix=".tmp",
        suffix=".json",
    )

    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf-8",
        ) as f:

            json.dump(
                data,
                f,
                indent=2,
                ensure_ascii=False,
            )

        os.replace(
            tmp_path,
            path,
        )

    except Exception:

        if os.path.exists(tmp_path):
            os.remove(tmp_path)

        raise


# ============================================================================
# Store
# ============================================================================

class ClanEmbedStore:
    """
    Loads clan_embeds.json into memory and writes changes atomically.

    clan_embeds.json is automatically created only when it does not exist.
    """

    def __init__(
        self,
        path: str = EMBEDS_PATH,
    ):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:

        # --------------------------------------------------------------
        # File does not exist.
        #
        # Create it once.
        # --------------------------------------------------------------

        if not os.path.exists(self.path):

            data = _default_store()

            atomic_write(
                self.path,
                data,
            )

            return data

        # --------------------------------------------------------------
        # Existing file.
        #
        # Load it. Do NOT recreate it.
        # --------------------------------------------------------------

        with open(
            self.path,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        if not isinstance(data, dict):
            data = _default_store()

        data.setdefault(
            "clans",
            {},
        )

        data.setdefault(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        # --------------------------------------------------------------
        # Ensure index fields exist.
        # --------------------------------------------------------------

        index = data["index_embed"]

        if not isinstance(index, dict):
            index = {}

        index.setdefault(
            "channel_id",
            None,
        )

        index.setdefault(
            "thread_id",
            None,
        )

        index.setdefault(
            "message_id",
            None,
        )

        data["index_embed"] = index

        # --------------------------------------------------------------
        # Migrate older clan entries.
        #
        # Unknown legacy fields are intentionally preserved in the
        # JSON but ignored by get_clan().
        # --------------------------------------------------------------

        clans = data["clans"]

        if not isinstance(clans, dict):
            clans = {}

        for raw in clans.values():

            if not isinstance(raw, dict):
                continue

            raw.setdefault(
                "tag",
                "",
            )

            raw.setdefault(
                "description",
                "",
            )

            raw.setdefault(
                "requirements",
                "",
            )

            raw.setdefault(
                "webhook_url",
                "",
            )

            raw.setdefault(
                "channel_id",
                None,
            )

            raw.setdefault(
                "thread_id",
                None,
            )

            raw.setdefault(
                "message_id",
                None,
            )

            raw.setdefault(
                "last_updated",
                None,
            )

        data["clans"] = clans

        return data

    def save(self) -> None:
        atomic_write(
            self.path,
            self._data,
        )

    # ========================================================================
    # Clan entries
    # ========================================================================

    def get_clan(
        self,
        name: str,
    ) -> Optional[ClanEmbedEntry]:

        raw = self._data["clans"].get(name)

        if raw is None:
            return None

        # Only load fields used by the current version.
        # Legacy fields such as emoji/tier are ignored safely.
        allowed = {
            "tag",
            "description",
            "requirements",
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

        return ClanEmbedEntry(
            **cleaned
        )

    def get_or_create_clan(
        self,
        name: str,
        tag: str = "",
    ) -> ClanEmbedEntry:

        existing = self.get_clan(name)

        if existing:

            if not existing.tag and tag:
                existing.tag = tag

            return existing

        return ClanEmbedEntry(
            tag=tag
        )

    def upsert_clan(
        self,
        name: str,
        entry: ClanEmbedEntry,
    ) -> None:

        entry.touch()

        self._data["clans"][name] = (
            asdict(entry)
        )

        self.save()

    def remove_clan(
        self,
        name: str,
    ) -> None:

        self._data["clans"].pop(
            name,
            None,
        )

        self.save()

    def all_clans(
        self,
    ) -> dict[str, ClanEmbedEntry]:

        result: dict[str, ClanEmbedEntry] = {}

        for name in self._data["clans"]:

            entry = self.get_clan(name)

            if entry is not None:
                result[name] = entry

        return result

    # ========================================================================
    # Index embed
    # ========================================================================

    def get_index_embed(
        self,
    ) -> IndexEmbedConfig:

        raw = self._data.get(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        if not isinstance(raw, dict):
            raw = asdict(IndexEmbedConfig())

        return IndexEmbedConfig(
            channel_id=raw.get(
                "channel_id"
            ),
            thread_id=raw.get(
                "thread_id"
            ),
            message_id=raw.get(
                "message_id"
            ),
        )

    def set_index_embed(
        self,
        config: IndexEmbedConfig,
    ) -> None:

        self._data["index_embed"] = (
            asdict(config)
        )

        self.save()