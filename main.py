"""
LIB Bypass Bot
==============
A Discord key/license-shop bot: owners register users and hand out
credits, users spend credits to redeem time-limited keys from a stock
the owner maintains, and everything is logged and backed up.

Run:  python main.py
Env:  DISCORD_TOKEN must be set. See .env.example / README.md.
"""

from __future__ import annotations

import logging
import os
import sys
import traceback
from datetime import datetime, timezone

import discord
from discord.ext import commands, tasks

try:
    from dotenv import load_dotenv
    load_dotenv()  # reads a local .env file into the environment, if present
except ImportError:
    pass  # python-dotenv is optional; env vars can be set another way instead

import config
from storage import Storage, StorageError
from views import TierSelectView, NoteModal, BulkKeysModal
from keep_alive import keep_alive

# --------------------------------------------------------------------------- #
# Logging
# --------------------------------------------------------------------------- #

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)],
)
logger = logging.getLogger("libbypass")

# --------------------------------------------------------------------------- #
# Bot setup
# --------------------------------------------------------------------------- #

intents = discord.Intents.default()
intents.message_content = True  # required to read "!command" text
intents.members = True          # required to resolve @mentions to members reliably

bot = commands.Bot(command_prefix=config.BOT_PREFIX, intents=intents, help_command=None)
storage = Storage()

# Commands that stay usable in any channel so the bot can be configured /
# explained even before (or outside of) an allowed channel.
CHANNEL_CHECK_EXEMPT = {"setchannel", "removechannel", "help"}


# --------------------------------------------------------------------------- #
# Custom exceptions -> friendly messages
# --------------------------------------------------------------------------- #

class NotAuthorized(commands.CheckFailure):
    pass


class WrongChannel(commands.CheckFailure):
    def __init__(self, allowed_ids):
        self.allowed_ids = allowed_ids
        super().__init__("Wrong channel.")


# --------------------------------------------------------------------------- #
# Checks
# --------------------------------------------------------------------------- #

def owner_only():
    async def predicate(ctx: commands.Context) -> bool:
        if not storage.is_owner(ctx.author.id):
            raise NotAuthorized("This command is restricted to bot owners.")
        return True
    return commands.check(predicate)


def authorized_only():
    """Owner OR a registered user."""
    async def predicate(ctx: commands.Context) -> bool:
        if not storage.is_authorized(ctx.author.id):
            raise NotAuthorized(
                "You don't have access to this bot yet. Ask an owner to add you."
            )
        return True
    return commands.check(predicate)


@bot.check
async def global_channel_lock(ctx: commands.Context) -> bool:
    if ctx.guild is None:
        raise NotAuthorized("This bot only works inside a server, not in DMs.")
    if ctx.command and ctx.command.name in CHANNEL_CHECK_EXEMPT:
        return True
    if not storage.is_allowed_channel(ctx.channel.id):
        raise WrongChannel(storage.data.get("allowed_channels", []))
    return True


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #

def make_embed(title: str, description: str = "", color: int = config.EMBED_COLOR) -> discord.Embed:
    embed = discord.Embed(title=title, description=description, color=color)
    embed.set_footer(text=config.FOOTER_TEXT)
    embed.timestamp = datetime.now(timezone.utc)
    return embed


async def post_log(guild: discord.Guild, embed: discord.Embed):
    channel_id = storage.get_log_channel()
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel is None:
        return
    try:
        await channel.send(embed=embed)
    except discord.HTTPException as exc:
        logger.warning("Failed to post to log channel: %s", exc)


def parse_member(ctx: commands.Context, raw: str) -> discord.Member | None:
    """Resolve a mention / raw ID / name to a Member without requiring the
    full MemberConverter machinery in every command signature."""
    raw = raw.strip().strip("<@!>")
    if raw.isdigit():
        return ctx.guild.get_member(int(raw))
    return discord.utils.find(lambda m: m.name == raw or m.display_name == raw, ctx.guild.members)


# --------------------------------------------------------------------------- #
# Lifecycle
# --------------------------------------------------------------------------- #

@bot.event
async def on_ready():
    await storage.load()
    if not auto_backup_loop.is_running():
        auto_backup_loop.start()
    logger.info("%s is online as %s (id: %s)", config.BOT_NAME, bot.user, bot.user.id)
    await bot.change_presence(activity=discord.Game(name=f"{config.BOT_PREFIX}help"))


@tasks.loop(minutes=config.AUTO_BACKUP_MINUTES)
async def auto_backup_loop():
    try:
        await storage.save()
        logger.info("Auto-backup completed.")
    except Exception:  # noqa: BLE001 - a failed backup must never crash the bot
        logger.exception("Auto-backup failed")


# --------------------------------------------------------------------------- #
# Error handling
# --------------------------------------------------------------------------- #

@bot.event
async def on_command_error(ctx: commands.Context, error: Exception):
    error = getattr(error, "original", error)

    if isinstance(error, commands.CommandNotFound):
        return  # stay quiet on typos rather than spamming the channel

    if isinstance(error, WrongChannel):
        if error.allowed_ids:
            mentions = " ".join(f"<#{cid}>" for cid in error.allowed_ids)
            desc = f"This bot only responds in: {mentions}"
        else:
            desc = "No allowed channel has been configured yet."
        await ctx.send(embed=make_embed("Wrong Channel", desc, config.WARNING_COLOR))
        return

    if isinstance(error, (NotAuthorized, commands.CheckFailure)):
        await ctx.send(embed=make_embed("Not Authorized", str(error) or "You can't use this command.", config.ERROR_COLOR))
        return

    if isinstance(error, StorageError):
        await ctx.send(embed=make_embed("Error", str(error), config.ERROR_COLOR))
        return

    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(embed=make_embed(
            "Missing Argument",
            f"Usage: `{config.BOT_PREFIX}{ctx.command.qualified_name} {ctx.command.signature}`",
            config.WARNING_COLOR,
        ))
        return

    if isinstance(error, commands.BadArgument):
        await ctx.send(embed=make_embed("Invalid Input", str(error), config.WARNING_COLOR))
        return

    # Anything unexpected: log the full traceback, keep the user-facing
    # message generic and non-leaky.
    logger.error("Unhandled error in command %s:\n%s", getattr(ctx.command, "qualified_name", "?"),
                 "".join(traceback.format_exception(type(error), error, error.__traceback__)))
    await ctx.send(embed=make_embed(
        "Something Went Wrong",
        "An unexpected error occurred and has been logged. Please try again or contact an owner.",
        config.ERROR_COLOR,
    ))


# --------------------------------------------------------------------------- #
# Key redemption flow (owner + registered users)
# --------------------------------------------------------------------------- #

@bot.command(name="createkey", help="Redeem a credit for a key of your chosen duration.")
@authorized_only()
async def createkey(ctx: commands.Context):
    user = storage.get_user(ctx.author.id)
    is_owner = storage.is_owner(ctx.author.id)

    if not is_owner and (not user or user["credits"] < config.CREDIT_COST_PER_KEY):
        raise StorageError("You don't have enough credits to redeem a key.")

    async def on_tier_pick(interaction: discord.Interaction, tier: int):
        if storage.stock_counts().get(tier, 0) <= 0:
            await interaction.response.send_message(
                embed=make_embed("Out of Stock", f"There are no {tier}-day keys left.", config.WARNING_COLOR),
                ephemeral=True,
            )
            return

        async def on_note(modal_interaction: discord.Interaction, note: str):
            try:
                key = storage.pop_key(tier)
                if not is_owner:
                    storage.adjust_credits(ctx.author.id, -config.CREDIT_COST_PER_KEY)
                storage.record_redemption(ctx.author.id, tier, key, note)
                await storage.save()
            except StorageError as exc:
                await modal_interaction.response.send_message(
                    embed=make_embed("Error", str(exc), config.ERROR_COLOR), ephemeral=True
                )
                return

            embed = make_embed("Key Redeemed", color=config.SUCCESS_COLOR)
            embed.add_field(name="Duration", value=f"{tier} day{'s' if tier != 1 else ''}", inline=True)
            embed.add_field(name="Key", value=f"`{key}`", inline=False)
            if note:
                embed.add_field(name="Note", value=note, inline=False)
            remaining = "Unlimited (owner)" if is_owner else str(storage.get_user(ctx.author.id)["credits"])
            embed.add_field(name="Credits Remaining", value=remaining, inline=True)
            await modal_interaction.response.send_message(embed=embed, ephemeral=True)

            log_embed = make_embed("Key Created", color=config.INFO_COLOR)
            log_embed.add_field(name="User", value=f"{ctx.author.mention} (`{ctx.author.id}`)", inline=False)
            log_embed.add_field(name="Duration", value=f"{tier} day{'s' if tier != 1 else ''}", inline=True)
            log_embed.add_field(name="Key", value=f"`{key}`", inline=True)
            if note:
                log_embed.add_field(name="Note", value=note, inline=False)
            await post_log(ctx.guild, log_embed)

        await interaction.response.send_modal(NoteModal(on_note))

    view = TierSelectView(on_tier_pick, author_id=ctx.author.id)
    msg = await ctx.send(
        embed=make_embed("Create Key", "Choose a duration for the key you'd like to redeem.", config.INFO_COLOR),
        view=view,
    )
    view.message = msg


@bot.command(name="mykeys", help="View your own key redemption history.")
@authorized_only()
async def mykeys(ctx: commands.Context):
    history = storage.user_history(ctx.author.id)
    if not history:
        await ctx.send(embed=make_embed("Your Keys", "You haven't redeemed any keys yet.", config.INFO_COLOR))
        return
    embed = make_embed("Your Key History", color=config.INFO_COLOR)
    for entry in history[-10:]:
        ts = entry["timestamp"].split("T")[0]
        label = f"{entry['tier']} day(s) — {ts}"
        value = f"`{entry['key']}`"
        if entry.get("note"):
            value += f"\nNote: {entry['note']}"
        embed.add_field(name=label, value=value, inline=False)
    if len(history) > 10:
        embed.set_footer(text=f"{config.FOOTER_TEXT} • showing last 10 of {len(history)}")
    await ctx.send(embed=embed)


@bot.command(name="credits", aliases=["mycredits"], help="Check your credit balance.")
@authorized_only()
async def credits_cmd(ctx: commands.Context):
    if storage.is_owner(ctx.author.id):
        await ctx.send(embed=make_embed("Credits", "You're an owner — unlimited redemptions.", config.INFO_COLOR))
        return
    user = storage.get_user(ctx.author.id)
    await ctx.send(embed=make_embed("Credits", f"Balance: **{user['credits']}**", config.INFO_COLOR))


# --------------------------------------------------------------------------- #
# Owner: user / credit management
# --------------------------------------------------------------------------- #

@bot.command(name="adduser", help="Register a user and give them starting credits. Usage: !adduser @user 10")
@owner_only()
async def adduser(ctx: commands.Context, member: discord.Member, credits: int):
    storage.add_user(member.id, credits, ctx.author.id)
    await storage.save()
    await ctx.send(embed=make_embed(
        "User Added",
        f"{member.mention} registered with **{credits}** credit(s).",
        config.SUCCESS_COLOR,
    ))


@bot.command(name="revokeuser", aliases=["removeuser"], help="Revoke a user's access. Usage: !revokeuser @user")
@owner_only()
async def revokeuser(ctx: commands.Context, member: discord.Member):
    storage.revoke_user(member.id)
    await storage.save()
    await ctx.send(embed=make_embed("Access Revoked", f"{member.mention} no longer has access.", config.SUCCESS_COLOR))


@bot.command(name="addcredits", help="Add credits to a user. Usage: !addcredits @user 5")
@owner_only()
async def addcredits(ctx: commands.Context, member: discord.Member, amount: int):
    if amount <= 0:
        raise StorageError("Amount must be positive.")
    new_balance = storage.adjust_credits(member.id, amount)
    await storage.save()
    await ctx.send(embed=make_embed(
        "Credits Added", f"{member.mention} now has **{new_balance}** credit(s).", config.SUCCESS_COLOR
    ))


@bot.command(name="removecredits", help="Remove credits from a user. Usage: !removecredits @user 5")
@owner_only()
async def removecredits(ctx: commands.Context, member: discord.Member, amount: int):
    if amount <= 0:
        raise StorageError("Amount must be positive.")
    new_balance = storage.adjust_credits(member.id, -amount)
    await storage.save()
    await ctx.send(embed=make_embed(
        "Credits Removed", f"{member.mention} now has **{new_balance}** credit(s).", config.SUCCESS_COLOR
    ))


@bot.command(name="users", help="List all registered users and their credit balances.")
@owner_only()
async def list_users(ctx: commands.Context):
    users = storage.all_users()
    if not users:
        await ctx.send(embed=make_embed("Users", "No users registered yet.", config.INFO_COLOR))
        return
    embed = make_embed("Registered Users", color=config.INFO_COLOR)
    for uid, info in users.items():
        member = ctx.guild.get_member(int(uid))
        name = member.mention if member else f"`{uid}`"
        embed.add_field(
            name=name,
            value=f"Credits: **{info['credits']}** • Redemptions: {len(info['redemptions'])}",
            inline=False,
        )
    await ctx.send(embed=embed)


# --------------------------------------------------------------------------- #
# Owner: key stock management
# --------------------------------------------------------------------------- #

@bot.command(name="addkey", help="Add keys to stock for a chosen duration.")
@owner_only()
async def addkey(ctx: commands.Context):
    async def on_tier_pick(interaction: discord.Interaction, tier: int):
        async def on_keys(modal_interaction: discord.Interaction, keys: list):
            added = storage.add_keys(tier, keys)
            await storage.save()
            duplicates = len(keys) - added
            desc = f"Added **{added}** new {tier}-day key(s) to stock."
            if duplicates > 0:
                desc += f"\n{duplicates} duplicate/blank entr{'y was' if duplicates == 1 else 'ies were'} skipped."
            await modal_interaction.response.send_message(
                embed=make_embed("Keys Added", desc, config.SUCCESS_COLOR), ephemeral=True
            )

        await interaction.response.send_modal(BulkKeysModal(on_keys))

    view = TierSelectView(on_tier_pick, author_id=ctx.author.id)
    msg = await ctx.send(
        embed=make_embed("Add Keys", "Choose which duration these keys belong to.", config.INFO_COLOR),
        view=view,
    )
    view.message = msg


@bot.command(name="stock", help="View how many keys are left per duration.")
@authorized_only()
async def stock(ctx: commands.Context):
    counts = storage.stock_counts()
    embed = make_embed("Key Stock", color=config.INFO_COLOR)
    for tier, count in counts.items():
        embed.add_field(name=f"{tier} day{'s' if tier != 1 else ''}", value=str(count), inline=True)
    await ctx.send(embed=embed)


# --------------------------------------------------------------------------- #
# Owner: channel / log configuration
# --------------------------------------------------------------------------- #

@bot.command(name="setchannel", help="Allow the bot to operate in this (or a mentioned) channel.")
@owner_only()
async def setchannel(ctx: commands.Context, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    storage.add_channel(channel.id)
    await storage.save()
    await ctx.send(embed=make_embed("Channel Allowed", f"The bot will now respond in {channel.mention}.", config.SUCCESS_COLOR))


@bot.command(name="removechannel", help="Stop the bot from operating in a channel.")
@owner_only()
async def removechannel(ctx: commands.Context, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    storage.remove_channel(channel.id)
    await storage.save()
    await ctx.send(embed=make_embed("Channel Removed", f"The bot will no longer respond in {channel.mention}.", config.SUCCESS_COLOR))


@bot.command(name="setlogchannel", help="Set the channel where key/redemption activity is logged.")
@owner_only()
async def setlogchannel(ctx: commands.Context, channel: discord.TextChannel = None):
    channel = channel or ctx.channel
    storage.set_log_channel(channel.id)
    await storage.save()
    await ctx.send(embed=make_embed("Log Channel Set", f"Activity will be logged in {channel.mention}.", config.SUCCESS_COLOR))


# --------------------------------------------------------------------------- #
# Owner: manual backup
# --------------------------------------------------------------------------- #

@bot.command(name="backup", help="Force an immediate backup (local + off-site if configured).")
@owner_only()
async def backup_cmd(ctx: commands.Context):
    await storage.save()
    remote_status = "configured" if (config.GITHUB_TOKEN and config.GIST_ID) else "not configured"
    await ctx.send(embed=make_embed(
        "Backup Complete", f"Local backup written. Off-site backup: {remote_status}.", config.SUCCESS_COLOR
    ))


# --------------------------------------------------------------------------- #
# Help
# --------------------------------------------------------------------------- #

USER_COMMANDS = [
    ("!createkey", "Redeem a credit for a key of your chosen duration."),
    ("!mykeys", "View your own key redemption history."),
    ("!credits", "Check your credit balance."),
    ("!stock", "View how many keys are left per duration."),
    ("!help", "Show this menu."),
]

OWNER_COMMANDS = [
    ("!adduser @user <credits>", "Register a user with starting credits."),
    ("!revokeuser @user", "Revoke a user's access."),
    ("!addcredits @user <amount>", "Add credits to a user."),
    ("!removecredits @user <amount>", "Remove credits from a user."),
    ("!users", "List all registered users and balances."),
    ("!addkey", "Add keys to stock (pick duration, then paste keys)."),
    ("!setchannel [#channel]", "Allow the bot to respond in a channel."),
    ("!removechannel [#channel]", "Disallow a channel."),
    ("!setlogchannel [#channel]", "Set the audit-log channel."),
    ("!backup", "Force an immediate backup."),
]


@bot.command(name="help", help="Show available commands for your role.")
async def help_cmd(ctx: commands.Context):
    is_owner = storage.is_owner(ctx.author.id)
    is_user = storage.is_registered(ctx.author.id)

    if not is_owner and not is_user:
        await ctx.send(embed=make_embed(
            config.BOT_NAME,
            "You don't have access to this bot yet. Ask an owner to add you with `!adduser`.",
            config.WARNING_COLOR,
        ))
        return

    embed = make_embed(config.BOT_NAME, "Here's what you can do:", config.INFO_COLOR)
    user_lines = "\n".join(f"**{cmd}** — {desc}" for cmd, desc in USER_COMMANDS)
    embed.add_field(name="Commands", value=user_lines, inline=False)

    if is_owner:
        owner_lines = "\n".join(f"**{cmd}** — {desc}" for cmd, desc in OWNER_COMMANDS)
        embed.add_field(name="Owner Commands", value=owner_lines, inline=False)

    await ctx.send(embed=embed)


# --------------------------------------------------------------------------- #
# Entrypoint
# --------------------------------------------------------------------------- #

def main():
    token = os.getenv("DISCORD_TOKEN")
    if not token:
        logger.error("DISCORD_TOKEN environment variable is not set. See .env.example.")
        sys.exit(1)
    if os.getenv("RENDER") or os.getenv("KEEP_ALIVE") == "1":
        keep_alive()
    bot.run(token)


if __name__ == "__main__":
    main()
