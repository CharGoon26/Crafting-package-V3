# BallsDex Crafting Package V3

This package adds a crafting system for BallsDex with:

- a `craft` slash-command group
- Django admin support for crafting recipes
- database models for recipes, ingredients, and ingredient groups

## Installation

Add this to `config/extra.toml` in a BallsDex install:

```toml
[[ballsdex.packages]]
location = "git+https://github.com/your-name/ballsdex-crafting-package.git==1.0.0"
path = "crafting_pkg"
enabled = true
```

For local development, point `location` at the local package directory and set `editable = true`.

