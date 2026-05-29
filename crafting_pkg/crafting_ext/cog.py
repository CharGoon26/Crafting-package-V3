from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Optional

import discord
from asgiref.sync import sync_to_async
from discord import app_commands
from discord.ext import commands

from bd_models.models import BallInstance, Player
from ballsdex.core.utils.transformers import BallEnabledTransform, BallInstanceTransform

from .crafting_utils import update_crafting_display
from .crafting_views import CraftingView, RecipeSelect, BulkCraftView
from .logic import can_craft_recipe, determine_ingredient_usage, find_matching_recipes
from ..models import CraftingGroupOption, CraftingIngredient, CraftingIngredientGroup, CraftingRecipe
from .session_manager import crafting_sessions

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot

log = logging.getLogger("ballsdex.packages.crafting")


class Craft(commands.GroupCog, group_name="craft"):
    def __init__(self, bot: "BallsDexBot"):
        self.bot = bot

    @app_commands.command(name="begin", description="Start a crafting session.")
    async def craft_begin(
        self,
        interaction: discord.Interaction,
    ):
        await interaction.response.defer()
        user_id = interaction.user.id

        if user_id in crafting_sessions:
            await interaction.followup.send(
                "You already have an active crafting session. Please finish or cancel it first.",
                ephemeral=True,
            )
            return

        player, _ = await Player.objects.aget_or_create(discord_id=user_id)

        crafting_sessions[user_id] = {
            "player": player,
            "ingredient_instances": [],
            "started_at": discord.utils.utcnow(),
            "message": None,
        }

        await update_crafting_display(interaction, user_id, is_new=True)

    @app_commands.command(name="addbulk", description="Bulk add cards to your crafting session.")
    async def craft_addbulk(self, interaction: discord.Interaction, query: str | None = None):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        if user_id not in crafting_sessions:
            await interaction.followup.send("❌ Start a crafting session first with `/craft begin`.", ephemeral=True)
            return

        session = crafting_sessions[user_id]
        player = session["player"]

        candidates = []
        async for inst in BallInstance.objects.filter(player=player, deleted=False).select_related("ball", "special"):
            if inst.locked:
                continue
            if inst.pk in session["ingredient_instances"]:
                continue
            candidates.append(inst)

        if query:
            q = query.lower().strip()
            candidates = [c for c in candidates if q in c.ball.country.lower()]

        if not candidates:
            await interaction.followup.send("❌ No eligible cards to add.", ephemeral=True)
            return

        view = BulkCraftView(self.bot, session, "add", candidates)
        message = await interaction.followup.send("Select cards to add:", view=view, ephemeral=True)
        view.message = message

    @app_commands.command(name="removebulk", description="Bulk remove cards from your crafting session.")
    async def craft_removebulk(self, interaction: discord.Interaction, query: str | None = None):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        if user_id not in crafting_sessions:
            await interaction.followup.send("❌ No active crafting session!", ephemeral=True)
            return

        session = crafting_sessions[user_id]
        if not session["ingredient_instances"]:
            await interaction.followup.send("❌ No ingredients to remove.", ephemeral=True)
            return

        candidates = []
        async for inst in BallInstance.objects.filter(id__in=session["ingredient_instances"]).select_related("ball", "special"):
            candidates.append(inst)

        if query:
            q = query.lower().strip()
            candidates = [c for c in candidates if q in c.ball.country.lower()]

        if not candidates:
            await interaction.followup.send("❌ No matching ingredients to remove.", ephemeral=True)
            return

        view = BulkCraftView(self.bot, session, "remove", candidates)
        message = await interaction.followup.send("Select cards to remove:", view=view, ephemeral=True)
        view.message = message

    @app_commands.command(name="add", description="Add a card to your crafting session.")
    async def craft_add(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        if user_id not in crafting_sessions:
            await interaction.followup.send(
                "❌ Start a crafting session first with `/craft begin`.", ephemeral=True
            )
            return

        session = crafting_sessions[user_id]
        player = session["player"]

        if countryball.player_id != player.pk:
            await interaction.followup.send("❌ You don't own this card!", ephemeral=True)
            return

        if countryball.locked:
            await interaction.followup.send(
                "❌ This card is currently reserved in a trade and can't be used for crafting.",
                ephemeral=True,
            )
            return

        if countryball.pk in session["ingredient_instances"]:
            await interaction.followup.send(
                f"❌ Already added #{countryball.pk:0X}!", ephemeral=True
            )
            return

        await countryball.arefresh_from_db()
        ball = await sync_to_async(lambda: countryball.ball)()  # type: ignore

        session["ingredient_instances"].append(countryball.pk)
        await interaction.followup.send(
            f"Added {ball.country} #{countryball.pk:0X} to crafting session!", ephemeral=True
        )
        await update_crafting_display(interaction, user_id)

    @app_commands.command(name="remove", description="Remove a card from your crafting session.")
    async def craft_remove(
        self,
        interaction: discord.Interaction,
        countryball: BallInstanceTransform,
    ):
        await interaction.response.defer(ephemeral=True)
        user_id = interaction.user.id

        if user_id not in crafting_sessions:
            await interaction.followup.send("❌ No active crafting session!", ephemeral=True)
            return

        session = crafting_sessions[user_id]
        if countryball.pk not in session["ingredient_instances"]:
            await interaction.followup.send(
                f"❌ Instance #{countryball.pk:0X} not in your session!", ephemeral=True
            )
            return

        ball = await countryball.ball  # type: ignore
        session["ingredient_instances"].remove(countryball.pk)
        await interaction.followup.send(
            f"Removed {ball.country} #{countryball.pk:0X} from crafting session!", ephemeral=True
        )
        await update_crafting_display(interaction, user_id)

    @app_commands.command(name="clear", description="Clear all ingredients from your crafting session.")
    async def craft_clear(self, interaction: discord.Interaction):
        user_id = interaction.user.id

        if user_id not in crafting_sessions:
            await interaction.response.send_message("❌ No active crafting session!", ephemeral=True)
            return

        crafting_sessions[user_id]["ingredient_instances"] = []
        await interaction.response.defer()
        await update_crafting_display(interaction, user_id)

    @app_commands.command(name="recipes", description="Show available crafting recipes.")
    async def craft_recipes(
        self,
        interaction: discord.Interaction,
        countryball: Optional[BallEnabledTransform] = None,
    ):
        await interaction.response.defer()

        if countryball:
            recipes = [r async for r in CraftingRecipe.objects.filter(result=countryball).select_related("result")]
            title = f"🔨 Recipes for {countryball.country}"
        else:
            recipes = [r async for r in CraftingRecipe.objects.all().select_related("result")[:10]]
            title = "🔨 Available Recipes (Top 10)"

        if not recipes:
            await interaction.followup.send("❌ No recipes found.", ephemeral=True)
            return

        embed = discord.Embed(title=title, color=0x0099FF)

        for recipe in recipes:
            desc = []
            async for ing in recipe.ingredients.select_related("ingredient"):
                if ing.ingredient_id:
                    emoji = interaction.client.get_emoji(ing.ingredient.emoji_id)
                    desc.append(f"{emoji} {ing.ingredient.country} x{ing.quantity}")
            async for group in recipe.ingredient_groups.prefetch_related("options__ball"):
                options_text = []
                async for opt in group.options.select_related("ball"):
                    emoji = interaction.client.get_emoji(opt.ball.emoji_id)
                    options_text.append(f"{emoji} {opt.ball.country}")
                desc.append(
                    f"**{group.name}** (choose {group.required_count}): {' | '.join(options_text[:5])}"
                )

            result = recipe.result
            result_emoji = interaction.client.get_emoji(result.emoji_id)
            embed.add_field(
                name=f"{result_emoji} {result.country}",
                value="\n".join(desc) or "*(no ingredients)*",
                inline=False,
            )

        await interaction.followup.send(embed=embed)
