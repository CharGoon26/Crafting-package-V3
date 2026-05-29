from __future__ import annotations

import discord

from bd_models.models import BallInstance

from .crafting_views import CraftingView
from .logic import find_matching_recipes
from .session_manager import crafting_sessions


def _chunk_embed_lines(lines: list[str], *, max_length: int = 900, max_chunks: int = 10) -> list[str]:
    chunks: list[str] = []
    current: list[str] = []
    current_length = 0
    processed_lines = 0

    for line in lines:
        line_length = len(line) + 1
        if current and current_length + line_length > max_length:
            chunks.append("\n".join(current))
            processed_lines += len(current)
            if len(chunks) >= max_chunks:
                remaining = len(lines) - processed_lines
                if remaining > 0:
                    chunks[-1] = f"{chunks[-1]}\n...and {remaining} more"
                return chunks
            current = [line]
            current_length = line_length
        else:
            current.append(line)
            current_length += line_length

    if current and len(chunks) < max_chunks:
        chunks.append("\n".join(current))

    return chunks


async def update_crafting_display(interaction: discord.Interaction, user_id: int, is_new: bool = False):
    """Update the crafting session embed."""
    session = crafting_sessions[user_id]

    ball_instances = []
    if session["ingredient_instances"]:
        try:
            async for inst in BallInstance.objects.filter(
                id__in=session["ingredient_instances"]
            ).select_related("ball", "special"):
                ball_instances.append(inst)
        except Exception as e:
            print(f"Error fetching ball instances: {e}")
            return

    possible_recipes = await find_matching_recipes(session["ingredient_instances"])

    embed = discord.Embed(title="🔨 Crafting Session", color=0x0099FF)

    if possible_recipes:
        results = []
        for recipe in possible_recipes[:5]:
            emoji = interaction.client.get_emoji(recipe.result.emoji_id)
            results.append(f"{emoji} {recipe.result.country}")
        extra = f"\n*+{len(possible_recipes) - 5} more*" if len(possible_recipes) > 5 else ""
        embed.add_field(name="✅ Can Craft", value="\n".join(results) + extra, inline=False)
    else:
        embed.add_field(
            name="❓ Can Craft",
            value="*Add ingredients to see possible recipes*\nUse `/craft recipes` to view all available recipes",
            inline=False,
        )

    if ball_instances:
        lines = []
        for inst in ball_instances:
            emoji = interaction.client.get_emoji(inst.ball.emoji_id)
            special_text = f"{inst.special.emoji} " if inst.special_id else ""
            lines.append(
                f"{emoji} {special_text}{inst.ball.country} #{inst.pk:0X} "
                f"(ATK: {inst.attack_bonus:+d}, HP: {inst.health_bonus:+d})"
            )
        for index, chunk in enumerate(_chunk_embed_lines(lines), start=1):
            name = "Current Ingredients" if index == 1 else f"Current Ingredients (continued {index})"
            embed.add_field(name=name, value=chunk, inline=False)
        total_atk = sum(i.attack_bonus for i in ball_instances)
        total_hp = sum(i.health_bonus for i in ball_instances)
        embed.add_field(
            name="Total Stats of All Ingredients",
            value=f"**ATK:** {total_atk:+d} | **HP:** {total_hp:+d}",
            inline=False,
        )
    else:
        embed.add_field(
            name="Current Ingredients",
            value="*No ingredients added yet*\nUse `/craft add` to add ingredients",
            inline=False,
        )

    embed.add_field(
        name="Commands",
        value=(
            "`/craft add` - Add specific cards\n"
            "`/craft addbulk query:<query>` - Add cards in bulk with query to specify\n"
            "`/craft remove` - Remove specific cards\n"
            "`/craft removebulk query:<query>` - Remove cards in bulk with query to specify\n"
            "`/craft clear` - Clear all ingredients\n"
            "`/craft recipes` - View available recipes"
        ),
        inline=False,
    )

    embed.set_footer(text="Session expires after 20 minutes of inactivity")

    view = CraftingView(interaction.client, session["player"], session)

    if is_new:
        message = await interaction.followup.send("Crafting session:", embed=embed, view=view)
        session["message"] = message
        return

    try:
        if session.get("message"):
            await session["message"].edit(embed=embed, view=view)
        else:
            new_message = await interaction.followup.send("Updated crafting session:", embed=embed, view=view)
            session["message"] = new_message
    except Exception as e:
        print(f"Error updating crafting display: {e}")
        try:
            new_message = await interaction.followup.send("Updated crafting session:", embed=embed, view=view)
            session["message"] = new_message
        except Exception as e2:
            print(f"Error sending followup: {e2}")
