"""
Reusable Discord UI pieces: the "pick a duration" button row used by both
!createkey and !addkey, and the two modal popups that follow it.
"""

from __future__ import annotations

import discord
from discord import ui

import config


class TierSelectView(ui.View):
    """A row of buttons, one per configured day tier (1/3/7/30 by default).

    `on_pick(interaction, tier)` is awaited when a button is pressed.
    The view disables itself after a pick or on timeout so it can't be
    reused twice by accident.
    """

    def __init__(self, on_pick, *, author_id: int, timeout: float = 60):
        super().__init__(timeout=timeout)
        self.on_pick = on_pick
        self.author_id = author_id
        self.message: discord.Message | None = None
        for tier in config.DAY_TIERS:
            self.add_item(self._make_button(tier))

    def _make_button(self, tier: int) -> ui.Button:
        button = ui.Button(
            label=f"{tier} Day{'s' if tier != 1 else ''}",
            style=discord.ButtonStyle.blurple,
            custom_id=f"tier_{tier}",
        )

        async def callback(interaction: discord.Interaction):
            if interaction.user.id != self.author_id:
                await interaction.response.send_message(
                    "This menu isn't for you.", ephemeral=True
                )
                return
            for child in self.children:
                child.disabled = True
            if self.message:
                try:
                    await self.message.edit(view=self)
                except discord.HTTPException:
                    pass
            self.stop()
            await self.on_pick(interaction, tier)

        button.callback = callback
        return button

    async def on_timeout(self):
        for child in self.children:
            child.disabled = True
        if self.message:
            try:
                await self.message.edit(view=self)
            except discord.HTTPException:
                pass


class NoteModal(ui.Modal, title="Create Key — Optional Note"):
    note = ui.TextInput(
        label="Note (e.g. which client this is for)",
        required=False,
        max_length=200,
        style=discord.TextStyle.short,
        placeholder="Leave blank if not needed",
    )

    def __init__(self, on_submit_cb):
        super().__init__()
        self.on_submit_cb = on_submit_cb

    async def on_submit(self, interaction: discord.Interaction):
        await self.on_submit_cb(interaction, str(self.note.value or "").strip())


class BulkKeysModal(ui.Modal, title="Add Keys to Stock"):
    keys_field = ui.TextInput(
        label="Keys — comma or newline separated",
        required=True,
        style=discord.TextStyle.paragraph,
        placeholder="key-aaaa\nkey-bbbb, key-cccc",
        max_length=4000,
    )

    def __init__(self, on_submit_cb):
        super().__init__()
        self.on_submit_cb = on_submit_cb

    async def on_submit(self, interaction: discord.Interaction):
        raw = str(self.keys_field.value)
        parts = [piece.strip() for line in raw.splitlines() for piece in line.split(",")]
        keys = [p for p in parts if p]
        await self.on_submit_cb(interaction, keys)
