# BallsDex Crafting Package V3

This package was originally written by Mitoooooooopo or @An Unknown Guy on discord.

Minor changes by Chargoon26 were added:
- /craft addbulk query:<query> - Add cards in bulk with query to specify
- /craft removebulk query:<query> - Remove cards in bulk with query to specify
- specials cannot be crafted with this package

Other than that, these remain as in the original package:
- /craft begin
- /craft add and /craft remove
- /craft recipes

## Installation

Add this to `config/extra.toml`

```toml
[[ballsdex.packages]]
location = "git+https://github.com/CharGoon26/Crafting-package-V3"
path = "crafting_pkg"
enabled = true
```

For local development, point `location` at the local package directory and set `editable = true`.

