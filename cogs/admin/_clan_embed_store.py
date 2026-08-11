"""
Storage layer for the /nexclanembed and /nexrules embed system.

data/clan_tags.json
    READ ONLY. General clan-tag source used across the whole bot.

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
Each clan can have a different thread ID.

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

        {
            "CLAN NAME": "#CLANTAG"
        }

    Supports:

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


# ---------------------------------------------------------------------------
# Data models
# ---------------------------------------------------------------------------

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
        This is displayed WITHOUT a "Requirements" heading.

    webhook_url:
        Discord webhook used to publish the embed.

    channel_id:
        Parent channel containing the webhook.

    thread_id:
        Optional thread where this clan's embed lives.

    message_id:
        ID of the webhook-created message.

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


# ---------------------------------------------------------------------------
# Atomic storage
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Store
# ---------------------------------------------------------------------------

class ClanEmbedStore:
    """
    Loads clan_embeds.json into memory and writes changes atomically.
    """

    def __init__(
        self,
        path: str = EMBEDS_PATH,
    ):
        self.path = path
        self._data = self._load()

    def _load(self) -> dict:

        if not os.path.exists(self.path):

            data = _default_store()

            atomic_write(
                self.path,
                data,
            )

            return data

        with open(
            self.path,
            "r",
            encoding="utf-8",
        ) as f:

            data = json.load(f)

        data.setdefault(
            "clans",
            {},
        )

        data.setdefault(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        # ---------------------------------------------------------------
        # Migrate older clan entries.
        #
        # Old versions may contain:
        #   emoji
        #   tier
        #
        # We intentionally don't remove them here because leaving unknown
        # legacy fields in the JSON is harmless and protects old data.
        # The loader simply ignores them.
        # ---------------------------------------------------------------

        for raw in data["clans"].values():

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

        return data

    def save(self) -> None:
        atomic_write(
            self.path,
            self._data,
        )

    # ------------------------------------------------------------------
    # Clan entries
    # ------------------------------------------------------------------

    def get_clan(
        self,
        name: str,
    ) -> Optional[ClanEmbedEntry]:

        raw = self._data["clans"].get(name)

        if raw is None:
            return None

        # Only load fields used by the current version.
        # This safely ignores legacy emoji/tier fields.
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

        result = {}

        for name in self._data["clans"]:

            entry = self.get_clan(name)

            if entry is not None:
                result[name] = entry

        return result

    # ------------------------------------------------------------------
    # Index embed
    # ------------------------------------------------------------------

    def get_index_embed(
        self,
    ) -> IndexEmbedConfig:

        raw = self._data.get(
            "index_embed",
            asdict(IndexEmbedConfig()),
        )

        return IndexEmbedConfig(
            **raw
        )

    def set_index_embed(
        self,
        config: IndexEmbedConfig,
    ) -> None:

        self._data["index_embed"] = (
            asdict(config)
        )

        self.save()