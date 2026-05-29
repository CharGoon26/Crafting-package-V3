from __future__ import annotations

from typing import Dict, List

from bd_models.models import Ball, BallInstance

from .models import CraftingGroupOption, CraftingIngredient, CraftingIngredientGroup, CraftingRecipe


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
        instances_by_ball[ball_id].sort(key=lambda x: x.attack_bonus + x.health_bonus)

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
