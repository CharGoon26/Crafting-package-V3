from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Sequence

from bd_models.models import BallInstance, Player
from settings.models import settings

from ..models import CraftingRecipe


async def find_matching_recipes(ingredient_instance_ids: List[int]) -> List[CraftingRecipe]:
    """Find all recipes that can be crafted with the given ingredient instances."""
    if not ingredient_instance_ids:
        return []

    ball_counts: Dict[int, int] = {}
    async for instance in BallInstance.objects.filter(id__in=ingredient_instance_ids).select_related("ball"):
        ball_id = instance.ball_id
        ball_counts[ball_id] = ball_counts.get(ball_id, 0) + 1

    matching = []
    async for recipe in CraftingRecipe.objects.prefetch_related(
        "ingredients__ingredient",
        "ingredient_groups__options__ball",
        "result",
    ):
        if await can_craft_recipe(recipe, ball_counts):
            matching.append(recipe)

    return matching


async def can_craft_recipe(recipe: CraftingRecipe, available_ball_counts: Dict[int, int]) -> bool:
    """Check if a recipe can be crafted with available ball counts."""
    async for ingredient in recipe.ingredients.all():
        if ingredient.ingredient_id:
            if available_ball_counts.get(ingredient.ingredient_id, 0) < ingredient.quantity:
                return False

    async for group in recipe.ingredient_groups.all():
        available_from_group = 0
        async for option in group.options.all():
            available_from_group += available_ball_counts.get(option.ball_id, 0)
        if available_from_group < group.required_count:
            return False

    return True


async def determine_ingredient_usage(
    recipe: CraftingRecipe, ingredient_instance_ids: List[int]
) -> List[int]:
    """
    Determine which specific ball instances to use for a recipe.
    Returns a list of instance IDs to consume, or [] if requirements can't be met.
    """
    instances_by_ball: Dict[int, list] = {}
    async for instance in BallInstance.objects.filter(id__in=ingredient_instance_ids).select_related("ball"):
        ball_id = instance.ball_id
        instances_by_ball.setdefault(ball_id, []).append(instance)

    for ball_id in instances_by_ball:
        instances_by_ball[ball_id].sort(
            key=lambda x: (x.favorite, x.attack_bonus + x.health_bonus)
        )

    instances_to_use: List[int] = []

    async for ingredient in recipe.ingredients.all():
        if not ingredient.ingredient_id:
            continue
        ball_id = ingredient.ingredient_id
        needed = ingredient.quantity
        available = instances_by_ball.get(ball_id, [])
        if len(available) < needed:
            return []
        for _ in range(needed):
            instances_to_use.append(instances_by_ball[ball_id].pop(0).id)

    async for group in recipe.ingredient_groups.all():
        needed = group.required_count
        available_options = []
        async for option in group.options.all():
            qty = len(instances_by_ball.get(option.ball_id, []))
            if qty > 0:
                available_options.append((option.ball_id, qty))
        available_options.sort(key=lambda x: x[1], reverse=True)

        for ball_id, _ in available_options:
            if needed <= 0:
                break
            to_use = min(needed, len(instances_by_ball[ball_id]))
            for _ in range(to_use):
                instances_to_use.append(instances_by_ball[ball_id].pop(0).id)
            needed -= to_use

        if needed > 0:
            return []

    return instances_to_use


def instance_stat_score(instance: BallInstance) -> int:
    return instance.attack_bonus + instance.health_bonus


def compute_crafted_bonuses(instances: Sequence[BallInstance]) -> tuple[int, int, list[BallInstance]]:
    """
    Inherit ATK/HP *bonuses* (percentage modifiers), not raw combat stats.

    BallsDex stores attack_bonus/health_bonus as percents applied to the result
    card's own base ATK/HP. Averaging those percents keeps the result's 1:3
    (or any) base ratio intact and cannot turn 1000 ATK into a +1000% bonus.

    Top-heavy: average only the best 50% of consumed cards (rounded up) so a
    few dump cards cannot wipe a strong duplicate. The result is then clamped
    to the bot's normal catch bonus range.
    """
    if not instances:
        return 0, 0, []

    ranked = sorted(
        instances,
        key=lambda inst: (instance_stat_score(inst), inst.attack_bonus, inst.health_bonus),
        reverse=True,
    )
    keep = max(1, math.ceil(len(ranked) * 0.5))
    chosen = ranked[:keep]
    attack = int(round(sum(inst.attack_bonus for inst in chosen) / len(chosen)))
    health = int(round(sum(inst.health_bonus for inst in chosen) / len(chosen)))

    max_atk = int(settings.max_attack_bonus)
    max_hp = int(settings.max_health_bonus)
    attack = max(-max_atk, min(max_atk, attack))
    health = max(-max_hp, min(max_hp, health))
    return attack, health, chosen


async def load_craft_inventory(player: Player) -> list[BallInstance]:
    """Unlocked, non-special cards eligible for crafting."""
    items: list[BallInstance] = []
    async for inst in BallInstance.objects.filter(
        player=player,
        deleted=False,
        special__isnull=True,
    ).select_related("ball", "special"):
        if inst.locked:
            continue
        items.append(inst)
    return items


def inventory_counts(instances: Sequence[BallInstance]) -> Dict[int, int]:
    counts: Dict[int, int] = {}
    for inst in instances:
        counts[inst.ball_id] = counts.get(inst.ball_id, 0) + 1
    return counts


@dataclass
class IngredientNeed:
    emoji_id: int | None
    label: str


@dataclass
class RecipeStatus:
    recipe: CraftingRecipe
    ready: bool
    needs: list[IngredientNeed] = field(default_factory=list)

    def field_value(self, bot) -> str:
        lines = ["Requires:"]
        if self.needs:
            for need in self.needs:
                emoji = bot.get_emoji(need.emoji_id) if need.emoji_id else None
                prefix = f"{emoji} " if emoji else ""
                lines.append(f"{prefix}{need.label}")
        else:
            lines.append("*(no ingredients)*")
        lines.append("🟢 Ready to craft" if self.ready else "🔴 Missing ingredients")
        text = "\n".join(lines)
        if len(text) > 1024:
            # Truncate the ingredient list to fit within Discord's 1024 char limit
            status_line = lines[-1]
            header = "Requires:\n"
            max_ingredient_len = 1024 - len(header) - len(status_line) - 4  # 4 for "..." + newline
            truncated = []
            used = 0
            for need in self.needs:
                emoji = bot.get_emoji(need.emoji_id) if need.emoji_id else None
                prefix = f"{emoji} " if emoji else ""
                line = f"{prefix}{need.label}"
                if used + len(line) + 1 > max_ingredient_len:
                    remaining = len(self.needs) - len(truncated)
                    truncated.append(f"...and {remaining} more")
                    break
                truncated.append(line)
                used += len(line) + 1
            if not truncated:
                truncated = ["*(ingredients truncated)*"]
            text = header + "\n".join(truncated) + "\n" + status_line
        return text


async def recipe_requirement_lines(recipe: CraftingRecipe, counts: Dict[int, int]) -> list[IngredientNeed]:
    needs: list[IngredientNeed] = []
    async for ing in recipe.ingredients.all():
        if not ing.ingredient_id:
            continue
        have = counts.get(ing.ingredient_id, 0)
        needs.append(
            IngredientNeed(
                emoji_id=ing.ingredient.emoji_id,
                label=f"{ing.quantity}x {ing.ingredient.country} (You have {have})",
            )
        )
    async for group in recipe.ingredient_groups.all():
        have = 0
        first_emoji = None
        async for option in group.options.all():
            have += counts.get(option.ball_id, 0)
            if first_emoji is None:
                first_emoji = option.ball.emoji_id
        needs.append(
            IngredientNeed(
                emoji_id=first_emoji,
                label=f"{group.required_count} from {group.name} (You have {have})",
            )
        )
    return needs


async def build_recipe_statuses(
    recipes: Sequence[CraftingRecipe], counts: Dict[int, int]
) -> list[RecipeStatus]:
    statuses: list[RecipeStatus] = []
    for recipe in recipes:
        ready = await can_craft_recipe(recipe, counts)
        needs = await recipe_requirement_lines(recipe, counts)
        statuses.append(RecipeStatus(recipe=recipe, ready=ready, needs=needs))
    return statuses


def format_instance_line(bot, inst: BallInstance) -> str:
    emoji = bot.get_emoji(inst.ball.emoji_id)
    special_text = f"{inst.special.emoji} " if inst.special_id else ""
    return (
        f"{emoji} {special_text}{inst.ball.country} #{inst.pk:0X} "
        f"(ATK: {inst.attack_bonus:+d}, HP: {inst.health_bonus:+d})"
    )
