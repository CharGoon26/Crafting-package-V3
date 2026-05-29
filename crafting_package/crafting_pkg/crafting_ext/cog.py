from __future__ import annotations

from typing import TYPE_CHECKING

import discord
from discord import app_commands
from discord.ext import commands

from ..models import CraftingRecipe

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class Craft(commands.GroupCog, group_name="craft"):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command(name="recipes", description="Show configured crafting recipes.")
    async def recipes(self, interaction: discord.Interaction):
        await interaction.response.defer(ephemeral=True)

        lines: list[str] = []
        async for recipe in CraftingRecipe.objects.select_related("result").all():
            lines.append(f"• {recipe.result}")

        if not lines:
            await interaction.followup.send("No crafting recipes have been configured yet.", ephemeral=True)
            return

        await interaction.followup.send("\n".join(lines), ephemeral=True)

