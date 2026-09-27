# BallsDex Crafting Package V3

This package was originally written by Mitoooooooopo or @An Unknown Guy on discord.

Minor changes by Chargoon26 were added:
- `/craft addbulk query:<query`> - Add cards in bulk with query to specify
- `/craft removebulk query:<query>` - Remove cards in bulk with query to specify
- specials cannot be crafted with this package
- `/craft recipes` now gives a list of craftable cards, a dropdown will appear to choose a craftable card, and only then you will see the ingredients needed.
  ^ Includes a quick craft feature while browsing the recipes.

> [!IMPORTANT]
> Contact @bigfatbrimmy on discord for any bugs or errors or if the original package owner wants to take it down, please notify me.

## Installation

Add this to `config/extra.toml`

```toml
[[ballsdex.packages]]
location = "git+https://github.com/CharGoon26/Crafting-package-V3"
path = "crafting_pkg"
enabled = true
```

For local development, point `location` at the local package directory and set `editable = true`.

