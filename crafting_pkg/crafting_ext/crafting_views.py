from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Sequence

import discord

from bd_models.models import BallInstance, Player, TradeObject

from .logic import (
    RecipeStatus,
    build_recipe_statuses,
    compute_crafted_bonuses,
    determine_ingredient_usage,
    find_matching_recipes,
    format_instance_line,
    inventory_counts,
    load_craft_inventory,
)
from ..models import CraftingRecipe
from .session_manager import crafting_sessions

if TYPE_CHECKING:
    from ballsdex.core.bot import BallsDexBot


async def perform_craft(
    bot: "BallsDexBot",
    player: Player,
    interaction: discord.Interaction,
    recipe: CraftingRecipe,
    instance_ids: Sequence[int],
) -> discord.Embed | str:
    """Consume the given instances and create the crafted card. Returns an embed or an error."""
    found: list[BallInstance] = []
    async for instance in BallInstance.objects.filter(
        id__in=list(instance_ids),
        player=player,
        deleted=False,
    ).select_related("ball", "special"):
        if instance.locked:
            return "One of the selected cards is locked in a trade. Craft cancelled."
        found.append(instance)

    if len(found) != len(set(instance_ids)):
        return "Some ingredients are no longer in your inventory. Craft cancelled."

    instance_ids_to_delete = [b.id for b in found]
    try:
        await TradeObject.objects.filter(ballinstance_id__in=instance_ids_to_delete).adelete()
    except Exception as e:
        print(f"Error cleaning up trade objects: {e}")
        return "Error cleaning up trade references. Craft cancelled."

    try:
        deleted_count, _ = await BallInstance.objects.filter(id__in=instance_ids_to_delete).adelete()
        if deleted_count != len(instance_ids_to_delete):
            return "Not all ingredients were properly consumed. Craft cancelled."
    except Exception as e:
        print(f"Error deleting ball instances: {e}")
        return "Error consuming ingredients. Craft cancelled."

    attack_bonus, health_bonus, inherited_from = compute_crafted_bonuses(found)
    crafted_instance = await BallInstance.objects.acreate(
        player=player,
        ball=recipe.result,
        health_bonus=health_bonus,
        attack_bonus=attack_bonus,
        server_id=interaction.guild_id,
    )

    total_sacrificed_attack = sum(b.attack_bonus for b in found)
    total_sacrificed_health = sum(b.health_bonus for b in found)
    ball_emoji = bot.get_emoji(recipe.result.emoji_id)
    name = f"{ball_emoji} {recipe.result.country}"
    inherit_note = (
        f"top {len(inherited_from)}/{len(found)} ingredient"
        f"{'s' if len(found) != 1 else ''} (best 50%)"
    )

    embed = discord.Embed(
        title="✅ Crafting Successful!",
        description=f"Successfully crafted **{name}** (ID: #{crafted_instance.pk:0X})!",
        color=0x00FF00,
    )
    embed.add_field(
        name="New Instance Stats",
        value=(
            f"**ATK:** {crafted_instance.attack_bonus:+d} | "
            f"**HP:** {crafted_instance.health_bonus:+d}\n"
            f"*Inherited from {inherit_note}*"
        ),
        inline=False,
    )

    embed.add_field(
        name="Ingredients Used",
        value="\n".join(format_instance_line(bot, ball) for ball in found),
        inline=False,
    )
    embed.add_field(
        name="Total Stats of Ingredients",
        value=f"**ATK:** {total_sacrificed_attack:+d} | **HP:** {total_sacrificed_health:+d}",
        inline=False,
    )

    session = crafting_sessions.get(interaction.user.id)
    if isinstance(session, dict) and session.get("ingredient_instances"):
        remaining = [
            iid for iid in session["ingredient_instances"] if iid not in instance_ids_to_delete
        ]
        session["ingredient_instances"] = remaining
        if not remaining:
            crafting_sessions.pop(interaction.user.id, None)

    return embed


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

            result = await perform_craft(
                self.bot, self.player, interaction, recipe, ingredients_to_use
            )
            if isinstance(result, str):
                crafting_sessions.pop(interaction.user.id, None)
                await interaction.response.send_message(result, ephemeral=True)
                return

            await interaction.response.edit_message(embed=result, view=None)
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


class QuickCraftConfirmView(discord.ui.View):
    def __init__(
        self,
        bot: "BallsDexBot",
        player: Player,
        recipe: CraftingRecipe,
        instances: list[BallInstance],
        browser: "RecipeBrowserView",
    ):
        super().__init__(timeout=90)
        self.bot = bot
        self.player = player
        self.recipe = recipe
        self.instances = instances
        self.browser = browser
        self.authorized_user_id = player.discord_id
        self._busy = False
        self._lock = asyncio.Lock()

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.authorized_user_id:
            await interaction.response.send_message(
                "❌ Only you can confirm this craft.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="Confirm", style=discord.ButtonStyle.success)
    async def confirm(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self._lock:
            if self._busy:
                await interaction.response.defer()
                return
            self._busy = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)

        result = await perform_craft(
            self.bot,
            self.player,
            interaction,
            self.recipe,
            [inst.pk for inst in self.instances],
        )
        if isinstance(result, str):
            await interaction.edit_original_response(
                embed=discord.Embed(title="❌ Craft Cancelled", description=result, color=0xFF0000),
                view=None,
            )
            return
        await interaction.edit_original_response(embed=result, view=None)
        await self.browser.refresh_after_craft()

    @discord.ui.button(label="Cancel", style=discord.ButtonStyle.secondary)
    async def cancel(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)
        await interaction.followup.send("Quick craft cancelled.", ephemeral=True)


class RecipeConfirmView(discord.ui.View):
    """Shown after a recipe is selected from the dropdown. Shows requirements and confirm/cancel."""

    def __init__(
        self,
        bot: "BallsDexBot",
        player: Player,
        recipe: CraftingRecipe,
        ready: bool,
        browser: "RecipeBrowserView",
    ):
        super().__init__(timeout=90)
        self.bot = bot
        self.player = player
        self.recipe = recipe
        self.ready = ready
        self.browser = browser
        self.authorized_user_id = player.discord_id
        self._busy = False
        self._lock = asyncio.Lock()
        # Enable the Craft button only if the recipe is ready
        for item in self.children:
            if getattr(item, "label", None) == "🔨 Craft":
                item.disabled = not ready
                break

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.authorized_user_id:
            await interaction.response.send_message(
                "❌ Only you can interact with this recipe.", ephemeral=True
            )
            return False
        return True

    @discord.ui.button(label="🔨 Craft", style=discord.ButtonStyle.success)
    async def craft_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        async with self._lock:
            if self._busy:
                await interaction.response.defer()
                return
            self._busy = True
        self.stop()
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        await interaction.response.edit_message(view=self)

        inventory = await load_craft_inventory(self.player)
        chosen_ids = await determine_ingredient_usage(self.recipe, [inst.pk for inst in inventory])
        if not chosen_ids:
            await interaction.edit_original_response(
                embed=discord.Embed(
                    title="❌ Craft Failed",
                    description="You no longer have the required ingredients.",
                    color=0xFF0000,
                ),
                view=None,
            )
            return

        result = await perform_craft(
            self.bot,
            self.player,
            interaction,
            self.recipe,
            chosen_ids,
        )
        if isinstance(result, str):
            await interaction.edit_original_response(
                embed=discord.Embed(title="❌ Craft Failed", description=result, color=0xFF0000),
                view=None,
            )
            return
        await interaction.edit_original_response(embed=result, view=None)
        await self.browser.refresh_after_craft()

    @discord.ui.button(label="◀️ Back", style=discord.ButtonStyle.secondary)
    async def back_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(
            embed=self.browser.build_embed(),
            view=self.browser,
        )

    @discord.ui.button(label="❌ Cancel", style=discord.ButtonStyle.secondary)
    async def cancel_button(self, interaction: discord.Interaction, button: discord.ui.Button):
        self.stop()
        await interaction.response.edit_message(view=None)
        await interaction.followup.send("Crafting cancelled.", ephemeral=True)


class RecipeBrowserView(discord.ui.View):
    recipes_per_page = 5

    def __init__(
        self,
        bot: "BallsDexBot",
        player: Player,
        statuses: list[RecipeStatus],
        title: str,
    ):
        super().__init__(timeout=600)
        self.bot = bot
        self.player = player
        self.statuses = statuses
        self.title = title
        self.page = 0
        self.message: discord.WebhookMessage | discord.Message | None = None
        self._rebuild()

    def _max_page(self) -> int:
        return max(0, (len(self.statuses) - 1) // self.recipes_per_page)

    def _page_statuses(self) -> list[RecipeStatus]:
        start = self.page * self.recipes_per_page
        return self.statuses[start : start + self.recipes_per_page]

    def build_embed(self) -> discord.Embed:
        embed = discord.Embed(
            title=self.title,
            description=(
                "Select a recipe from the dropdown below to see its requirements. "
                "Ready recipes will have a 🟢 indicator."
            ),
            color=0x0099FF,
        )
        if not self.statuses:
            embed.description = "No recipes found."
            return embed

        lines = []
        for status in self._page_statuses():
            emoji = self.bot.get_emoji(status.recipe.result.emoji_id) or ""
            ready_mark = "🟢" if status.ready else "🔴"
            lines.append(f"{ready_mark} {emoji} {status.recipe.result.country}")
        embed.add_field(
            name="Recipes on this page",
            value="\n".join(lines) if lines else "None",
            inline=False,
        )
        embed.set_footer(
            text=f"Page {self.page + 1}/{self._max_page() + 1} • Select a recipe to view requirements"
        )
        return embed

    def _rebuild(self) -> None:
        self.clear_items()
        page = self._page_statuses()

        options = []
        for status in page:
            emoji = self.bot.get_emoji(status.recipe.result.emoji_id)
            ready_mark = "🟢 " if status.ready else "🔴 "
            options.append(
                discord.SelectOption(
                    label=f"{ready_mark}{status.recipe.result.country}"[:100],
                    value=str(status.recipe.pk),
                    emoji=emoji,
                )
            )
        select = discord.ui.Select(
            placeholder="Choose a recipe to view requirements…",
            options=options,
            min_values=1,
            max_values=1,
        )
        select.callback = self._recipe_select
        self.add_item(select)

        prev_btn = discord.ui.Button(
            label="◀️ Previous",
            style=discord.ButtonStyle.secondary,
            disabled=self.page == 0,
        )
        prev_btn.callback = self._prev
        self.add_item(prev_btn)

        page_btn = discord.ui.Button(
            label=f"Page {self.page + 1}/{self._max_page() + 1}",
            style=discord.ButtonStyle.secondary,
            disabled=True,
        )
        self.add_item(page_btn)

        next_btn = discord.ui.Button(
            label="Next ▶️",
            style=discord.ButtonStyle.secondary,
            disabled=self.page >= self._max_page(),
        )
        next_btn.callback = self._next
        self.add_item(next_btn)

        cancel_btn = discord.ui.Button(label="❌ Close", style=discord.ButtonStyle.danger)
        cancel_btn.callback = self._close
        self.add_item(cancel_btn)

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.player.discord_id:
            await interaction.response.send_message(
                "❌ This recipe browser belongs to someone else.", ephemeral=True
            )
            return False
        return True

    async def _close(self, interaction: discord.Interaction):
        self.stop()
        await interaction.response.edit_message(
            embed=discord.Embed(
                title="Recipe Browser Closed",
                description="The recipe browser has been closed.",
                color=0x808080,
            ),
            view=None,
        )

    async def _prev(self, interaction: discord.Interaction):
        self.page = max(0, self.page - 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _next(self, interaction: discord.Interaction):
        self.page = min(self._max_page(), self.page + 1)
        self._rebuild()
        await interaction.response.edit_message(embed=self.build_embed(), view=self)

    async def _recipe_select(self, interaction: discord.Interaction):
        values = (interaction.data or {}).get("values") or []
        if not values:
            await interaction.response.send_message("No recipe selected.", ephemeral=True)
            return
        recipe_id = int(values[0])
        status = next((s for s in self.statuses if s.recipe.pk == recipe_id), None)
        if not status:
            await interaction.response.send_message("That recipe no longer exists.", ephemeral=True)
            return

        emoji = self.bot.get_emoji(status.recipe.result.emoji_id) or ""
        ready_mark = "🟢 Ready to craft" if status.ready else "🔴 Missing ingredients"
        embed = discord.Embed(
            title=f"{emoji} {status.recipe.result.country}",
            description=ready_mark,
            color=0x00FF00 if status.ready else 0xFF0000,
        )
        lines = []
        if status.needs:
            for need in status.needs:
                emoji = self.bot.get_emoji(need.emoji_id) if need.emoji_id else None
                prefix = f"{emoji} " if emoji else ""
                lines.append(f"{prefix}{need.label}")
        else:
            lines.append("*(no ingredients)*")
        embed.add_field(name="Requires", value="\n".join(lines), inline=False)
        if status.ready:
            embed.add_field(
                name="What happens",
                value="Your lowest-stat copies of the required ingredients will be consumed.",
                inline=False,
            )

        view = RecipeConfirmView(self.bot, self.player, status.recipe, status.ready, self)
        await interaction.response.edit_message(embed=embed, view=view)

    async def _quick_craft_select(self, interaction: discord.Interaction):
        values = (interaction.data or {}).get("values") or []
        if not values:
            await interaction.response.send_message("No recipe selected.", ephemeral=True)
            return
        recipe_id = int(values[0])
        status = next((s for s in self.statuses if s.recipe.pk == recipe_id), None)
        if not status or not status.ready:
            await interaction.response.send_message("That recipe is no longer ready.", ephemeral=True)
            return
        await self._start_quick_craft(interaction, status.recipe)

    async def _start_quick_craft(self, interaction: discord.Interaction, recipe: CraftingRecipe):
        inventory = await load_craft_inventory(self.player)
        chosen_ids = await determine_ingredient_usage(recipe, [inst.pk for inst in inventory])
        if not chosen_ids:
            await interaction.response.send_message(
                "You no longer have the ingredients for this recipe.", ephemeral=True
            )
            return

        chosen = [inst for inst in inventory if inst.pk in chosen_ids]
        chosen.sort(key=lambda inst: chosen_ids.index(inst.pk))
        predicted_atk, predicted_hp, inherited = compute_crafted_bonuses(chosen)
        result_name = recipe.result.country
        embed = discord.Embed(
            title=f"Confirm Quick Craft: {result_name}",
            description=f"Consume the following to craft **{result_name}**?",
            color=0xF1C40F,
        )
        used_lines = [format_instance_line(self.bot, inst) for inst in chosen]
        used_text = "\n".join(used_lines) if used_lines else "*none*"
        if len(used_text) > 1024:
            status_line = f"*Using {len(inherited)} of {len(chosen)} consumed cards*"
            max_len = 1024 - len(status_line) - 4
            truncated = []
            used = 0
            for line in used_lines:
                if used + len(line) + 1 > max_len:
                    remaining = len(used_lines) - len(truncated)
                    truncated.append(f"...and {remaining} more")
                    break
                truncated.append(line)
                used += len(line) + 1
            if not truncated:
                truncated = ["*(ingredients truncated)*"]
            used_text = "\n".join(truncated) + "\n" + status_line
        embed.add_field(
            name="Ingredients Used",
            value=used_text,
            inline=False,
        )
        embed.add_field(
            name="Predicted stats (best 50% average)",
            value=(
                f"**ATK:** {predicted_atk:+d} | **HP:** {predicted_hp:+d}\n"
                f"*Using {len(inherited)} of {len(chosen)} consumed cards*"
            ),
            inline=False,
        )
        view = QuickCraftConfirmView(self.bot, self.player, recipe, chosen, self)
        await interaction.response.send_message(embed=embed, view=view, ephemeral=True)

    async def refresh_after_craft(self) -> None:
        inventory = await load_craft_inventory(self.player)
        counts = inventory_counts(inventory)
        recipes = [s.recipe for s in self.statuses]
        self.statuses = await build_recipe_statuses(recipes, counts)
        self.page = min(self.page, self._max_page())
        self._rebuild()
        if self.message:
            try:
                await self.message.edit(embed=self.build_embed(), view=self)
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                pass

    async def on_timeout(self):
        for item in self.children:
            item.disabled = True  # type: ignore[attr-defined]
        if self.message:
            try:
                await self.message.edit(view=self)
            except (discord.HTTPException, discord.NotFound, discord.Forbidden):
                pass


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
