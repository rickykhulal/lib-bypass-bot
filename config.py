"""
Central configuration for LIB Bypass Bot.
Edit the constants below (or override via environment variables) before running.
"""

import os

# ---------------------------------------------------------------------------
# Identity
# ---------------------------------------------------------------------------
BOT_NAME = "LIB Bypass Bot"
BOT_PREFIX = "!"

# Discord user IDs that always have full owner access, regardless of the
# stored user database. These are hardcoded on purpose so an owner can never
# accidentally revoke their own access.
OWNER_IDS = {
    768020734231969793,
    910733488255270942,
}

# ---------------------------------------------------------------------------
# Key economy
# ---------------------------------------------------------------------------
# Available key durations, in days. Buttons are generated from this list, so
# adding/removing a tier here is enough to change what shows up in Discord.
DAY_TIERS = [1, 3, 7, 30]

# Flat credit cost to redeem one key, no matter which tier it comes from.
CREDIT_COST_PER_KEY = 1

# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------
DATA_FILE = os.getenv("DATA_FILE", os.path.join("data", "store.json"))
BACKUP_DIR = os.getenv("BACKUP_DIR", os.path.join("data", "backups"))
MAX_BACKUPS = int(os.getenv("MAX_BACKUPS", "30"))
AUTO_BACKUP_MINUTES = int(os.getenv("AUTO_BACKUP_MINUTES", "10"))

# Optional off-site backup so data survives a full host wipe (e.g. Render
# free-tier redeploys, which reset the local disk). Create a private GitHub
# Gist with one file in it, then set these two environment variables:
#   GITHUB_TOKEN = a personal access token with the "gist" scope
#   GIST_ID      = the id of that gist (from its URL)
# Leave both unset to run with local-disk-only backups.
GITHUB_TOKEN = os.getenv("GITHUB_TOKEN")
GIST_ID = os.getenv("GIST_ID")

# ---------------------------------------------------------------------------
# Appearance
# ---------------------------------------------------------------------------
EMBED_COLOR = 0x2B2D31
SUCCESS_COLOR = 0x57F287
ERROR_COLOR = 0xED4245
WARNING_COLOR = 0xFEE75C
INFO_COLOR = 0x5865F2

FOOTER_TEXT = f"{BOT_NAME}"
