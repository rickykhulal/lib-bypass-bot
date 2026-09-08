# LIB Bypass Bot

A Discord key/license-shop bot. Owners register users and hand out credits;
users spend credits to redeem time-limited keys from a stock the owner
maintains. Every redemption is logged and the whole database is backed up
automatically.

## What's included

| File | Purpose |
|---|---|
| `main.py` | Bot entrypoint — all commands, checks, error handling |
| `storage.py` | JSON persistence, rotating backups, optional off-site backup |
| `views.py` | The duration-picker buttons and the two popup forms (modals) |
| `config.py` | Owner IDs, prefix, tiers, colors — edit this first |
| `requirements.txt` | Python dependencies |
| `.env.example` | Environment variables template |

## 1. Discord Developer Portal setup

1. Go to https://discord.com/developers/applications → New Application → name it **LIB Bypass Bot**.
2. Bot tab → Reset Token → copy it (this is `DISCORD_TOKEN`).
3. Still on the Bot tab, turn ON **Message Content Intent** and **Server Members Intent** — the bot won't start without these, since it reads `!command` text and resolves `@mentions`.
4. OAuth2 → URL Generator → scopes: `bot`. Permissions: Send Messages, Embed Links, Read Message History, Use Slash Commands (not required but harmless), Manage Messages (optional). Use the generated URL to invite the bot to your server.

## 2. Configure

Open `config.py`:

- `OWNER_IDS` — already set to the two IDs you gave me. Add more any time.
- `DAY_TIERS` — the duration buttons (`1, 3, 7, 30` by default). Change freely.
- `CREDIT_COST_PER_KEY` — flat cost per redemption regardless of tier (default `1`).

Copy `.env.example` to `.env` and fill in `DISCORD_TOKEN`. Locally you can use
a package like `python-dotenv` if you want auto-loading, or just `export` the
variables in your shell — on a host like Render/Railway you'll set these as
dashboard environment variables instead.

## 3. Install & run locally

```bash
pip install -r requirements.txt
export DISCORD_TOKEN=your-token-here
python main.py
```

## 4. Deploying to Render (or similar) — read this part carefully

You mentioned the bot's data got wiped after a crash/restart on Render. Here's
why, and how this bot addresses it:

- **The problem:** Render's free web/worker instances use a disk that is not
  guaranteed to persist across redeploys or crash-restarts. A plain
  `data/store.json` file can vanish the moment the container is recreated.
- **What this bot does about it:**
  1. Every write is atomic and immediately followed by a rotating local
     backup (`data/backups/store-<timestamp>.json`, last 30 kept) — this
     protects against corruption, not host wipes.
  2. **Optional, recommended:** off-site backup to a private GitHub Gist.
     On every save, the bot pushes the full database to a Gist; on startup,
     if the local file is missing, it pulls the latest copy back down
     automatically. This is what actually survives a full Render wipe.

To enable the Gist backup:

1. Create a private Gist at https://gist.github.com with one file named
   `libbypass_store.json` containing `{}`. Copy the Gist ID from its URL
   (the long string after your username).
2. Create a GitHub personal access token (classic) with only the **gist**
   scope: https://github.com/settings/tokens
3. Set `GITHUB_TOKEN` and `GIST_ID` as environment variables on your host.

Without these two variables the bot still runs fine — it just relies on
whatever local disk persistence your host provides. For guaranteed
durability regardless of host, a small managed database (e.g. a free
MongoDB Atlas or Supabase/Postgres instance) would be a stronger long-term
option than any file-based approach; the Gist backup is a lightweight
middle ground that needs no extra service to run.

On Render specifically: use a **Background Worker** (not a Web Service,
since this bot doesn't serve HTTP) and add a **persistent disk** mounted at
`/opt/render/project/src/data` if you want local backups to survive too —
otherwise the Gist backup alone is enough to recover everything on restart.

## 5. First-time Discord setup (after inviting the bot)

Run these once, as an owner, in the server:

```
!setchannel #your-bypass-channel
!setlogchannel #bot-logs
!adduser @someone 10
!addkey            (pick a duration, then paste your keys)
```

## Command reference

### Everyone with access (owners + registered users)

| Command | What it does |
|---|---|
| `!createkey` | Pick a duration, add an optional note, redeem a key for 1 credit |
| `!mykeys` | See your own redemption history |
| `!credits` | Check your credit balance |
| `!stock` | See how many keys remain per duration |
| `!help` | Role-aware help menu |

### Owner only

| Command | What it does |
|---|---|
| `!adduser @user <credits>` | Register a user with starting credits |
| `!revokeuser @user` | Remove a user's access entirely |
| `!addcredits @user <amount>` | Top up a user's balance |
| `!removecredits @user <amount>` | Deduct from a user's balance |
| `!users` | List every registered user and their balance |
| `!addkey` | Pick a duration, then paste keys (comma or newline separated) into stock |
| `!setchannel [#channel]` | Allow the bot to respond in a channel (defaults to current) |
| `!removechannel [#channel]` | Disallow a channel |
| `!setlogchannel [#channel]` | Set where redemption activity is logged |
| `!backup` | Force an immediate backup right now |

Unregistered members typing any command that requires access get a plain
"you don't have access yet" message rather than a crash or a silent stall.

## Notes on the design

- **Channel lock:** once `!setchannel` has been used at least once, every
  command except `!setchannel`/`!removechannel`/`!help` only responds inside
  the allowed channel(s). Before any channel is set, the bot works anywhere
  so you can bootstrap it.
- **Credits:** owners bypass the credit check entirely (unlimited
  redemptions). Registered users are blocked from `!createkey` the moment
  their balance hits 0, with a clear error rather than a partial redemption.
- **Keys are supplied by you, not generated by the bot** — `!addkey` stores
  whatever key strings you paste in (from whatever system produces your
  actual keys) and hands them out first-in-first-out per duration tier.
- **Errors** are all routed through one handler in `main.py`
  (`on_command_error`) so users get a clean embed instead of a raw Python
  traceback, while the full traceback still goes to your console/host logs
  for debugging.
