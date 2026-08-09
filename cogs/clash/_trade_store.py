"""
Simple JSON-file storage for the trading system.

Good enough for a limited-time event on one or a few servers. If you
outgrow this later, the same method names can be re-implemented on
top of SQLite without touching trading_cog.py.
"""

import json
import asyncio
import time
import uuid
from pathlib import Path

DATA_PATH = Path(__file__).parent / "_trade_data.json"


class TradeStore:
    def __init__(self):
        self._lock = asyncio.Lock()
        self._data = self._load()

    def _load(self):
        if DATA_PATH.exists():
            with open(DATA_PATH, "r") as f:
                return json.load(f)
        return {"guilds": {}}

    def _save(self):
        with open(DATA_PATH, "w") as f:
            json.dump(self._data, f, indent=2)

    def _guild(self, guild_id):
        gid = str(guild_id)
        if gid not in self._data["guilds"]:
            self._data["guilds"][gid] = {
                "board_channel": None,
                "board_message": None,
                "notify": {},
                "trades": [],
            }
        return self._data["guilds"][gid]

    # ---------- board ----------

    async def set_board(self, guild_id, channel_id, message_id):
        async with self._lock:
            g = self._guild(guild_id)
            g["board_channel"] = channel_id
            g["board_message"] = message_id
            self._save()

    async def get_board(self, guild_id):
        g = self._guild(guild_id)
        return g["board_channel"], g["board_message"]

    # ---------- notify ----------

    async def set_notify(self, guild_id, user_id, value: bool):
        async with self._lock:
            g = self._guild(guild_id)
            g["notify"][str(user_id)] = value
            self._save()

    def get_notify(self, guild_id, user_id) -> bool:
        g = self._guild(guild_id)
        return g["notify"].get(str(user_id), False)  # off by default

    # ---------- trades ----------

    async def add_trade(self, guild_id, user_id, category, have, have_qty, want, want_qty):
        async with self._lock:
            g = self._guild(guild_id)
            trade = {
                "id": uuid.uuid4().hex[:8],
                "user_id": user_id,
                "category": category,
                "have": have,
                "have_qty": have_qty,
                "want": want,
                "want_qty": want_qty,
                "ts": time.time(),
            }
            g["trades"].append(trade)
            self._save()
            return trade

    def get_trades(self, guild_id, category=None):
        g = self._guild(guild_id)
        trades = g["trades"]
        if category:
            trades = [t for t in trades if t["category"] == category]
        return trades

    def get_user_trades(self, guild_id, user_id):
        g = self._guild(guild_id)
        return [t for t in g["trades"] if t["user_id"] == user_id]

    async def remove_trades(self, guild_id, trade_ids):
        async with self._lock:
            g = self._guild(guild_id)
            g["trades"] = [t for t in g["trades"] if t["id"] not in trade_ids]
            self._save()

    def find_matches(self, guild_id, trade):
        """Trades in the same category where have/want line up in both directions."""
        matches = []
        for other in self.get_trades(guild_id, trade["category"]):
            if other["id"] == trade["id"] or other["user_id"] == trade["user_id"]:
                continue
            have_matches = other["have"] == trade["want"] or trade["want"] == "Any troop"
            want_matches = other["want"] == trade["have"] or other["want"] == "Any troop"
            if have_matches and want_matches:
                matches.append(other)
        return matches
