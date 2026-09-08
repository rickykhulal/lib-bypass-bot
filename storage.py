"""
Storage layer for LIB Bypass Bot.

Everything is kept in a single JSON document on disk (data/store.json),
written atomically, plus:
  - a rotating set of timestamped local backups (data/backups/*.json)
  - an optional off-site copy in a GitHub Gist, so the bot can recover
    its full state (users, credits, key stock, history) even if the
    host wipes local disk on restart/redeploy.

All mutating methods are plain synchronous dict operations; call
`await storage.save()` after any of them to persist + back up.
"""

import asyncio
import json
import logging
import os
import shutil
from datetime import datetime, timezone

import config

logger = logging.getLogger("libbypass.storage")

try:
    import aiohttp
except ImportError:  # aiohttp ships with discord.py, but guard anyway
    aiohttp = None


DEFAULT_DATA = {
    "users": {},            # user_id(str) -> {credits, added_by, added_at, redemptions:[]}
    "keys": {},              # tier(str) -> [unused key strings]
    "used_keys": [],         # full redemption history, newest last
    "allowed_channels": [],  # channel ids the bot will operate in
    "log_channel": None,     # channel id for audit log posts
    "meta": {
        "created_at": None,
        "last_local_backup": None,
        "last_remote_backup": None,
    },
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class StorageError(Exception):
    """Raised for expected, user-facing storage problems (bad input etc.)."""


class Storage:
    def __init__(self):
        self._lock = asyncio.Lock()
        self.data = None
        os.makedirs(os.path.dirname(config.DATA_FILE) or ".", exist_ok=True)
        os.makedirs(config.BACKUP_DIR, exist_ok=True)

    # ------------------------------------------------------------------ #
    # Loading / persistence
    # ------------------------------------------------------------------ #

    async def load(self):
        if os.path.exists(config.DATA_FILE):
            try:
                with open(config.DATA_FILE, "r", encoding="utf-8") as f:
                    self.data = json.load(f)
                self._ensure_schema()
                logger.info("Loaded existing local data file.")
                return
            except (json.JSONDecodeError, OSError) as exc:
                logger.warning("Local data file unreadable (%s); trying remote backup.", exc)

        remote = await self._download_from_gist()
        if remote is not None:
            self.data = remote
            self._ensure_schema()
            await self._write_local()
            logger.info("Restored data from GitHub Gist backup.")
            return

        logger.info("No existing data found anywhere; starting a fresh store.")
        self.data = json.loads(json.dumps(DEFAULT_DATA))
        self.data["meta"]["created_at"] = _now()
        await self._write_local()

    def _ensure_schema(self):
        """Fill in any keys missing from an older/partial data file."""
        for key, value in DEFAULT_DATA.items():
            if key not in self.data:
                self.data[key] = json.loads(json.dumps(value))
        for tier in config.DAY_TIERS:
            self.data["keys"].setdefault(str(tier), [])

    async def _write_local(self):
        tmp_path = config.DATA_FILE + ".tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=2)
        os.replace(tmp_path, config.DATA_FILE)  # atomic on same filesystem

    async def save(self, push_remote: bool = True):
        """Persist current state to disk, rotate a backup, and optionally
        push an off-site copy. Safe to call often."""
        async with self._lock:
            await self._write_local()
            self._rotate_local_backup()
            if push_remote:
                await self._upload_to_gist()

    def _rotate_local_backup(self):
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        dest = os.path.join(config.BACKUP_DIR, f"store-{stamp}.json")
        try:
            shutil.copyfile(config.DATA_FILE, dest)
            self.data["meta"]["last_local_backup"] = _now()
        except OSError as exc:
            logger.warning("Could not write rotating local backup: %s", exc)
            return

        backups = sorted(os.listdir(config.BACKUP_DIR))
        excess = len(backups) - config.MAX_BACKUPS
        for old in backups[:max(excess, 0)]:
            try:
                os.remove(os.path.join(config.BACKUP_DIR, old))
            except OSError:
                pass

    # ------------------------------------------------------------------ #
    # Optional off-site (GitHub Gist) backup
    # ------------------------------------------------------------------ #

    async def _upload_to_gist(self):
        if not (config.GITHUB_TOKEN and config.GIST_ID and aiohttp):
            return
        try:
            headers = {
                "Authorization": f"token {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            }
            payload = {
                "files": {"libbypass_store.json": {"content": json.dumps(self.data, indent=2)}}
            }
            url = f"https://api.github.com/gists/{config.GIST_ID}"
            async with aiohttp.ClientSession() as session:
                async with session.patch(url, headers=headers, json=payload, timeout=15) as resp:
                    if resp.status == 200:
                        self.data["meta"]["last_remote_backup"] = _now()
                    else:
                        logger.warning("Gist backup failed: HTTP %s", resp.status)
        except Exception as exc:  # noqa: BLE001 - backups must never crash the bot
            logger.warning("Gist backup error: %s", exc)

    async def _download_from_gist(self):
        if not (config.GITHUB_TOKEN and config.GIST_ID and aiohttp):
            return None
        try:
            headers = {
                "Authorization": f"token {config.GITHUB_TOKEN}",
                "Accept": "application/vnd.github+json",
            }
            url = f"https://api.github.com/gists/{config.GIST_ID}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=15) as resp:
                    if resp.status != 200:
                        return None
                    body = await resp.json()
                    file_obj = body.get("files", {}).get("libbypass_store.json")
                    if not file_obj:
                        return None
                    return json.loads(file_obj["content"])
        except Exception as exc:  # noqa: BLE001
            logger.warning("Gist restore error: %s", exc)
            return None

    # ------------------------------------------------------------------ #
    # Users / credits
    # ------------------------------------------------------------------ #

    def is_owner(self, user_id: int) -> bool:
        return user_id in config.OWNER_IDS

    def is_registered(self, user_id: int) -> bool:
        return str(user_id) in self.data["users"]

    def is_authorized(self, user_id: int) -> bool:
        return self.is_owner(user_id) or self.is_registered(user_id)

    def get_user(self, user_id: int):
        return self.data["users"].get(str(user_id))

    def add_user(self, user_id: int, credits: int, added_by: int):
        uid = str(user_id)
        if uid in self.data["users"]:
            raise StorageError("That user is already registered.")
        if credits < 0:
            raise StorageError("Starting credits can't be negative.")
        self.data["users"][uid] = {
            "credits": credits,
            "added_by": added_by,
            "added_at": _now(),
            "redemptions": [],
        }

    def revoke_user(self, user_id: int):
        uid = str(user_id)
        if uid not in self.data["users"]:
            raise StorageError("That user is not registered.")
        del self.data["users"][uid]

    def adjust_credits(self, user_id: int, amount: int) -> int:
        uid = str(user_id)
        user = self.data["users"].get(uid)
        if not user:
            raise StorageError("That user is not registered.")
        new_balance = user["credits"] + amount
        if new_balance < 0:
            raise StorageError("That would take the user's balance below zero.")
        user["credits"] = new_balance
        return new_balance

    def all_users(self):
        return self.data["users"]

    # ------------------------------------------------------------------ #
    # Keys
    # ------------------------------------------------------------------ #

    def add_keys(self, tier: int, keys: list) -> int:
        bucket = self.data["keys"].setdefault(str(tier), [])
        existing = set(bucket)
        added = 0
        for raw in keys:
            k = raw.strip()
            if k and k not in existing:
                bucket.append(k)
                existing.add(k)
                added += 1
        return added

    def stock_counts(self) -> dict:
        return {tier: len(self.data["keys"].get(str(tier), [])) for tier in config.DAY_TIERS}

    def pop_key(self, tier: int) -> str:
        bucket = self.data["keys"].get(str(tier), [])
        if not bucket:
            raise StorageError(f"No {tier}-day keys left in stock.")
        return bucket.pop(0)

    def record_redemption(self, user_id: int, tier: int, key: str, note: str) -> dict:
        entry = {
            "user_id": user_id,
            "tier": tier,
            "key": key,
            "note": note or "",
            "timestamp": _now(),
        }
        self.data["used_keys"].append(entry)
        uid = str(user_id)
        if uid in self.data["users"]:
            self.data["users"][uid]["redemptions"].append(entry)
        return entry

    def user_history(self, user_id: int) -> list:
        user = self.data["users"].get(str(user_id))
        return user["redemptions"] if user else []

    # ------------------------------------------------------------------ #
    # Channel / log configuration
    # ------------------------------------------------------------------ #

    def add_channel(self, channel_id: int):
        if channel_id not in self.data["allowed_channels"]:
            self.data["allowed_channels"].append(channel_id)

    def remove_channel(self, channel_id: int):
        if channel_id in self.data["allowed_channels"]:
            self.data["allowed_channels"].remove(channel_id)

    def is_allowed_channel(self, channel_id: int) -> bool:
        allowed = self.data["allowed_channels"]
        if not allowed:
            # Not configured yet -> stay open so the owner can bootstrap
            # with !setchannel from anywhere.
            return True
        return channel_id in allowed

    def set_log_channel(self, channel_id: int):
        self.data["log_channel"] = channel_id

    def get_log_channel(self):
        return self.data.get("log_channel")
