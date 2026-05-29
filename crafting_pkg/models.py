from __future__ import annotations

from django.db import models

from bd_models.models import Ball


class CraftingRecipe(models.Model):
    result = models.ForeignKey(Ball, on_delete=models.CASCADE, related_name="crafted_by")

    class Meta:
        managed = True
        db_table = "craftingrecipe"

    def __str__(self) -> str:
        return f"{self.result} Recipe" if self.result_id else "Unnamed Recipe"


class CraftingIngredient(models.Model):
    recipe = models.ForeignKey(CraftingRecipe, on_delete=models.CASCADE, related_name="ingredients")
    ingredient = models.ForeignKey(Ball, on_delete=models.CASCADE, related_name="+")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        managed = True
        db_table = "craftingingredient"
        unique_together = ("recipe", "ingredient")

    def __str__(self) -> str:
        return f"{self.ingredient} x{self.quantity}"


class CraftingIngredientGroup(models.Model):
    recipe = models.ForeignKey(CraftingRecipe, on_delete=models.CASCADE, related_name="ingredient_groups")
    name = models.CharField(max_length=100)
    required_count = models.PositiveIntegerField(default=1)

    class Meta:
        managed = True
        db_table = "craftingingredientgroup"

    def __str__(self) -> str:
        return f"{self.name} (need {self.required_count})"


class CraftingGroupOption(models.Model):
    group = models.ForeignKey(CraftingIngredientGroup, on_delete=models.CASCADE, related_name="options")
    ball = models.ForeignKey(Ball, on_delete=models.CASCADE, related_name="group_memberships")

    class Meta:
        managed = True
        db_table = "craftinggroupoption"
        unique_together = ("group", "ball")

    def __str__(self) -> str:
        return f"{self.ball} in {self.group.name}"
