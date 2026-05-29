from django.contrib import admin

from .models import CraftingGroupOption, CraftingIngredient, CraftingIngredientGroup, CraftingRecipe


class CraftingIngredientInline(admin.TabularInline):
    model = CraftingIngredient
    extra = 0
    raw_id_fields = ("ingredient",)


class CraftingGroupOptionInline(admin.TabularInline):
    model = CraftingGroupOption
    extra = 0
    raw_id_fields = ("ball",)


class CraftingIngredientGroupInline(admin.StackedInline):
    model = CraftingIngredientGroup
    extra = 0


@admin.register(CraftingRecipe)
class CraftingRecipeAdmin(admin.ModelAdmin):
    list_display = ("id", "result")
    raw_id_fields = ("result",)
    inlines = [CraftingIngredientInline, CraftingIngredientGroupInline]


@admin.register(CraftingIngredientGroup)
class CraftingIngredientGroupAdmin(admin.ModelAdmin):
    list_display = ("id", "recipe", "label", "minimum_choices")
    raw_id_fields = ("recipe",)
    inlines = [CraftingGroupOptionInline]


@admin.register(CraftingIngredient)
class CraftingIngredientAdmin(admin.ModelAdmin):
    list_display = ("id", "recipe", "ingredient", "amount")
    raw_id_fields = ("recipe", "ingredient")


@admin.register(CraftingGroupOption)
class CraftingGroupOptionAdmin(admin.ModelAdmin):
    list_display = ("id", "group", "ball")
    raw_id_fields = ("group", "ball")

