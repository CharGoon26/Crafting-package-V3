from django.db import models

from bd_models.models import Ball


class CraftingRecipe(models.Model):
    result = models.ForeignKey(Ball, on_delete=models.CASCADE, related_name="crafted_by")

    class Meta:
        db_table = "craftingrecipe"

    def __str__(self) -> str:
        return f"Crafting recipe for {self.result}"


class CraftingIngredient(models.Model):
    recipe = models.ForeignKey(CraftingRecipe, on_delete=models.CASCADE, related_name="ingredients")
    ingredient = models.ForeignKey(Ball, on_delete=models.CASCADE)
    amount = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "craftingingredient"
        constraints = [
            models.UniqueConstraint(fields=("recipe", "ingredient"), name="craftingingredient_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.amount}x {self.ingredient}"


class CraftingIngredientGroup(models.Model):
    recipe = models.ForeignKey(CraftingRecipe, on_delete=models.CASCADE, related_name="ingredient_groups")
    label = models.CharField(max_length=64, blank=True)
    minimum_choices = models.PositiveIntegerField(default=1)

    class Meta:
        db_table = "craftingingredientgroup"

    def __str__(self) -> str:
        return self.label or f"Group for recipe {self.recipe_id}"


class CraftingGroupOption(models.Model):
    group = models.ForeignKey(CraftingIngredientGroup, on_delete=models.CASCADE, related_name="options")
    ball = models.ForeignKey(Ball, on_delete=models.CASCADE)

    class Meta:
        db_table = "craftinggroupoption"
        constraints = [
            models.UniqueConstraint(fields=("group", "ball"), name="craftinggroupoption_unique"),
        ]

    def __str__(self) -> str:
        return str(self.ball)

