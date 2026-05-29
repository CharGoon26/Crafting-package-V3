from __future__ import annotations

import random
from typing import TYPE_CHECKING

import discord

from bd_models.models import BallInstance, Player, TradeObject
from settings.models import settings

from .logic import determine_ingredient_usage, find_matching_recipes
from .models import CraftingRecipe
from .session_manager import crafting_sessions

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


class CraftingView(discord.ui.View):
    def __init__(self, bot: "BallsDexBot", player: Player, session_data: dict):
        super().__init__(timeout=1200)
        self.bot = bot
        self.player = player
        self.session_data = session_data
        self.authorized_user_id = player.discord_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.authorized_user_id:
            await interaction.response.send_message(
                "❌ Only the person who started this crafting session can use these buttons!",
                ephemeral=True,
            )
            return False
        return True

    @discord.ui.button(label="🔨 Craft", style=discord.ButtonStyle.success)
    async def craft_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        if not self.session_data["ingredient_instances"]:
            await interaction.response.send_message(
                "You haven't added any ingredients yet!", ephemeral=True
            )
            return

        possible_recipes = await find_matching_recipes(self.session_data["ingredient_instances"])
        if not possible_recipes:
            await interaction.response.send_message(
                "Your current ingredients don't match any known recipes!", ephemeral=True
            )
            return

        if len(possible_recipes) > 1:
            await self.show_recipe_selection(interaction, possible_recipes)
        else:
            await self.execute_craft(interaction, possible_recipes[0])
        self.stop()

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.danger)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        crafting_sessions.pop(interaction.user.id, None)
        embed = discord.Embed(
            title="Crafting Cancelled",
            description="Your crafting session has been cancelled. All ingredients have been returned.",
            color=0xFF0000,
        )
        await interaction.response.edit_message(embed=embed, view=None)
        self.stop()

    async def on_timeout(self):
        crafting_sessions.pop(self.player.discord_id, None)
        try:
            if self.session_data.get("message"):
                await self.session_data["message"].edit(
                    embed=discord.Embed(
                        title="Crafting Timed Out",
                        description="Your crafting session expired after 20 minutes of inactivity.",
                        color=0x808080,
                    ),
                    view=None,
                )
        except (discord.HTTPException, discord.NotFound, discord.Forbidden):
            pass

    async def show_recipe_selection(self, interaction: discord.Interaction, possible_recipes: list):
        embed = discord.Embed(
            title="Multiple Recipes Available!",
            description="Your ingredients can craft multiple items. Choose which one:",
            color=0x00FF00,
        )
        options = []
        for i, recipe in enumerate(possible_recipes):
            emoji = self.bot.get_emoji(recipe.result.emoji_id)
            options.append(
                discord.SelectOption(
                    label=recipe.result.country,
                    description=f"Craft {recipe.result.country}",
                    value=str(i),
                    emoji=emoji,
                )
            )
        select = RecipeSelect(options, possible_recipes, self, self.authorized_user_id)
        view = discord.ui.View()
        view.add_item(select)
        await interaction.response.edit_message(embed=embed, view=view)

    async def execute_craft(self, interaction: discord.Interaction, recipe: CraftingRecipe):
        try:
            ingredients_to_use = await determine_ingredient_usage(
                recipe, self.session_data["ingredient_instances"]
            )
            if not ingredients_to_use:
                await interaction.response.send_message(
                    "Unable to determine ingredient usage. This shouldn't happen!", ephemeral=True
                )
                return

            ball_instances_to_delete = []
            async for instance in BallInstance.objects.filter(
                id__in=ingredients_to_use
            ).select_related("ball", "special"):
                ball_instances_to_delete.append(instance)

            instance_ids_to_delete = [b.id for b in ball_instances_to_delete]

            try:
                await TradeObject.objects.filter(ballinstance_id__in=instance_ids_to_delete).adelete()
            except Exception as e:
                print(f"Error cleaning up trade objects: {e}")
                crafting_sessions.pop(interaction.user.id, None)
                await interaction.response.send_message(
                    "Error cleaning up trade references. Crafting session ended for security.",
                    ephemeral=True,
                )
                return

            try:
                deleted_count, _ = await BallInstance.objects.filter(
                    id__in=instance_ids_to_delete
                ).adelete()
                if deleted_count != len(instance_ids_to_delete):
                    crafting_sessions.pop(interaction.user.id, None)
                    await interaction.response.send_message(
                        "Not all ingredients were properly consumed. Crafting session ended for security.",
                        ephemeral=True,
                    )
                    return
            except Exception as e:
                print(f"Error deleting ball instances: {e}")
                crafting_sessions.pop(interaction.user.id, None)
                await interaction.response.send_message(
                    "Error consuming ingredients. Crafting session ended for security.",
                    ephemeral=True,
                )
                return

            crafted_instance = await BallInstance.objects.acreate(
                player=self.player,
                ball=recipe.result,
                health_bonus=random.randint(-settings.max_attack_bonus, settings.max_attack_bonus),
                attack_bonus=random.randint(-settings.max_attack_bonus, settings.max_attack_bonus),
                server_id=interaction.guild_id,
            )

            total_sacrificed_attack = sum(b.attack_bonus for b in ball_instances_to_delete)
            total_sacrificed_health = sum(b.health_bonus for b in ball_instances_to_delete)

            ball_emoji = self.bot.get_emoji(recipe.result.emoji_id)
            name = f"{ball_emoji} {recipe.result.country}"

            embed = discord.Embed(
                title="✅ Crafting Successful!",
                description=f"Successfully crafted **{name}** (ID: #{crafted_instance.pk:0X})!",
                color=0x00FF00,
            )
            embed.add_field(
                name="New Instance Stats",
                value=f"**ATK:** {crafted_instance.attack_bonus:+d} | **HP:** {crafted_instance.health_bonus:+d}",
                inline=False,
            )

            used_summary = []
            for ball in ball_instances_to_delete:
                b_emoji = self.bot.get_emoji(ball.ball.emoji_id)
                special_text = f"{ball.special.emoji} " if ball.special_id else ""
                used_summary.append(f"{b_emoji} {special_text}{ball.ball.country} (#{ball.pk:0X})")

            embed.add_field(name="Ingredients Used", value="\n".join(used_summary), inline=False)
            embed.add_field(
                name="Total Stats of Ingredients",
                value=f"**ATK:** {total_sacrificed_attack:+d} | **HP:** {total_sacrificed_health:+d}",
                inline=False,
            )

            net_attack = crafted_instance.attack_bonus - total_sacrificed_attack
            net_health = crafted_instance.health_bonus - total_sacrificed_health
            if net_attack != 0 or net_health != 0:
                embed.add_field(
                    name="Net Change",
                    value=f"**ATK:** {net_attack:+d} | **HP:** {net_health:+d}",
                    inline=False,
                )

            await interaction.response.edit_message(embed=embed, view=None)

            for iid in ingredients_to_use:
                self.session_data["ingredient_instances"].discard(iid) if isinstance(
                    self.session_data["ingredient_instances"], set
                ) else (
                    self.session_data["ingredient_instances"].remove(iid)
                    if iid in self.session_data["ingredient_instances"]
                    else None
                )

            if not self.session_data["ingredient_instances"]:
                crafting_sessions.pop(interaction.user.id, None)

        except Exception as e:
            print(f"Unexpected error in execute_craft: {e}")
            crafting_sessions.pop(interaction.user.id, None)
            try:
                await interaction.response.send_message(
                    "An unexpected error occurred during crafting. Please try again.", ephemeral=True
                )
            except discord.InteractionResponded:
                await interaction.followup.send(
                    "An unexpected error occurred during crafting. Please try again.", ephemeral=True
                )


class RecipeSelect(discord.ui.Select):
    def __init__(self, options, recipes, parent_view: CraftingView, authorized_user_id: int):
        super().__init__(placeholder="Choose which item to craft...", options=options)
        self.recipes = recipes
        self.parent_view = parent_view
        self.authorized_user_id = authorized_user_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.authorized_user_id:
            await interaction.response.send_message(
                "❌ Only the person who started this crafting session can use this menu!",
                ephemeral=True,
            )
            return False
        return True

    async def callback(self, interaction: discord.Interaction):
        recipe_index = int(self.values[0])
        await self.parent_view.execute_craft(interaction, self.recipes[recipe_index])


class BulkCraftView(discord.ui.View):
    def __init__(
        self,
        bot: "BallsDexBot",
        session: dict,
        mode: str,
        candidates: list[BallInstance],
    ):
        super().__init__(timeout=None)
        self.bot = bot
        self.session = session
        self.mode = mode
        self.candidates = candidates
        self.candidates.sort(key=lambda x: (x.ball.country.lower(), x.pk))
        self.items_per_page = 10
        self.current_page = 0
        self.selected_ids: set[int] = set()
        self.message: discord.Message | None = None

        self._update_page()

    def _max_pages(self) -> int:
        return max(1, (len(self.candidates) - 1) // self.items_per_page + 1)

    def _page_slice(self):
        start = self.current_page * self.items_per_page
        end = start + self.items_per_page
        return self.candidates[start:end]

    def _resolve_event_emoji(self, inst: BallInstance) -> tuple[discord.PartialEmoji | discord.Emoji | None, str | None]:
        """Return (emoji_obj, unicode_fallback) for a special card."""
        if not inst.special_id or not inst.special:
            return None, None

        raw = inst.special.emoji
        if not raw:
            return None, None

        try:
            emoji_obj = self.bot.get_emoji(int(raw))
            if emoji_obj:
                return emoji_obj, None
        except (ValueError, TypeError):
            pass

        if isinstance(raw, str):
            r = raw.strip()
            if r.startswith("<") and r.endswith(">") and ":" in r:
                try:
                    pe = discord.PartialEmoji.from_str(r)
                    return pe, None
                except Exception:
                    pass

        return None, str(raw)

    def _update_page(self):
        self.clear_items()

        page_cards = self._page_slice()
        options = []
        for inst in page_cards:
            emoji = self.bot.get_emoji(inst.ball.emoji_id)
            label = f"{inst.ball.country} #{inst.pk:0X}"

            event_emoji_obj, event_unicode = self._resolve_event_emoji(inst)
            if event_emoji_obj:
                emoji = event_emoji_obj
            elif event_unicode:
                label = f"{event_unicode} {label}"

            desc = f"ATK:{inst.attack_bonus:+} HP:{inst.health_bonus:+}"
            options.append(
                discord.SelectOption(
                    label=label[:100],
                    value=str(inst.pk),
                    description=desc[:100],
                    emoji=emoji,
                    default=inst.pk in self.selected_ids,
                )
            )

        if not options:
            select = discord.ui.Select(
                placeholder="No cards on this page",
                min_values=0,
                max_values=0,
                options=[],
                disabled=True,
            )
        else:
            select = discord.ui.Select(
                placeholder="Select cards…",
                min_values=0,
                max_values=len(options),
                options=options,
                custom_id="bulk_craft_select",
            )
            select.callback = self._on_select

        self.add_item(select)

        prev_btn = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.current_page == 0,
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        next_btn = discord.ui.Button(
            label="Next ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=self.current_page >= self._max_pages() - 1,
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        page_btn = discord.ui.Button(
            label=f"Page {self.current_page + 1}/{self._max_pages()}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.add_item(page_btn)

        action_label = ("➕ Add Selected" if self.mode == "add" else "➖ Remove Selected") + f" ({len(self.selected_ids)})"
        action_btn = discord.ui.Button(
            label=action_label,
            style=discord.ButtonStyle.success if self.mode == "add" else discord.ButtonStyle.danger,
            disabled=len(self.selected_ids) == 0,
        )
        action_btn.callback = self._apply
        self.add_item(action_btn)

        cancel_btn = discord.ui.Button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
        cancel_btn.callback = self._cancel
        self.add_item(cancel_btn)

    async def _on_select(self, interaction: discord.Interaction):
        selected = set(int(x) for x in interaction.data["values"])
        page_ids = {inst.pk for inst in self._page_slice()}
        self.selected_ids = (self.selected_ids - page_ids) | selected
        self._update_page()
        await interaction.response.edit_message(view=self)

    async def _prev(self, interaction: discord.Interaction):
        self.current_page = max(0, self.current_page - 1)
        self._update_page()
        await interaction.response.edit_message(view=self)

    async def _next(self, interaction: discord.Interaction):
        self.current_page = min(self._max_pages() - 1, self.current_page + 1)
        self._update_page()
        await interaction.response.edit_message(view=self)

    async def _apply(self, interaction: discord.Interaction):
        if not self.selected_ids:
            await interaction.response.send_message("❌ No cards selected.", ephemeral=True)
            return

        await interaction.response.defer()

        if self.mode == "add":
            for pk in self.selected_ids:
                if pk not in self.session["ingredient_instances"]:
                    self.session["ingredient_instances"].append(pk)
        else:
            self.session["ingredient_instances"] = [
                pk for pk in self.session["ingredient_instances"] if pk not in self.selected_ids
            ]

        from .crafting_utils import update_crafting_display
        if self.session.get("message"):
            await update_crafting_display(interaction, interaction.user.id)
        else:
            await update_crafting_display(interaction, interaction.user.id, is_new=True)

        self.selected_ids.clear()
        self._update_page()
        await interaction.edit_original_response(view=self)
        await interaction.followup.send("✅ Selection applied. You can keep adding more.", ephemeral=True)

    async def _cancel(self, interaction: discord.Interaction):
        await interaction.response.edit_message(view=None)
        await interaction.followup.send("❌ Bulk action cancelled.", ephemeral=True)
