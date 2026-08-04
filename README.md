# Monster Upgrade - 打怪升级

A 2D action RPG built with Python 3.14 + Arcade 2D engine + SQLite. Battle monsters across three distinct maps, loot weapons and gear, upgrade your equipment at the forge, and extract before time runs out.

## Screenshot

```
┌─────────────────────────────────────────────┐
│  ▓▓▓  Monster Upgrade  ▓▓▓                 │
│  "2D Action RPG"                           │
│                                             │
│  [ 开始游戏 ]                               │
│  [ 仓  库  ]                                │
│  [ 市  场  ]                                │
│  [ 锻造坊  ]                               │
└─────────────────────────────────────────────┘
```

## Features

### 3 Maps with Unique Themes

| Map | Theme | Monsters | Difficulty | Timer |
|-----|-------|----------|------------|-------|
| **Dark Forest** (幽暗森林) | `forest` | Zombies, Skeletons | Normal | 10 min |
| **Desert Wasteland** (沙漠荒地) | `desert` | Mummies, Camels + Forest mobs | Hard | 10 min |
| **Space Base** (航天基地) | `space` | Snipers, Assault, Bandits, Rocket Troops | Extreme | 8 min |

### 13 Monster Types + 4 Bosses

- **Forest**: Zombie (melee), Skeleton (ranged)
- **Desert**: Mummy Melee, Mummy Ranged, Camel (ranged)
- **Space Base**: Sniper, Assault, Bandit, Rocket Troop
- **Bosses**: Boss Zombie, Boss Skeleton, Boss Mummy, Boss Space (each with unique debuffs, require Lv5+ gear)

### Combat System

- **Melee**: 120° arc slash with knockback
- **Ranged**: Projectile-based with penetration and explosion variants
- **Debuffs**: Burn, Freeze, Poison, Stun — each boss applies a unique debuff
- **Monster AI**: Aggro range detection, pursuit, ranged kiting, wall collision

### Loot & Equipment

- **Weapons** (16 types): Fist, Wood/Iron Sword, Stone Mace, Short/Long Bow, Fire Staff, Pistol, Rifle, Sniper, Laser Gun, Rocket Launcher + more
- **Helmets** (5 tiers): Leather → Iron → Golden → Mummy → Space → Artifact (Strong Force)
- **Armor** (5 tiers): Cloth → Iron → Golden → Mummy → Space → Artifact (Nanotech)
- **Backpacks**: Expand inventory capacity
- **Potions**: Healing, Speed, Damage Boost, Shield
- **Resources**: Wood, Stone, Ore — used for crafting

### Progression Systems

- **Level Up**: Square-root sublinear scaling (Lv5 ≈ 1.9x, Lv50 ≈ 4.2x, Lv100 ≈ 5.5x)
- **Forge**: Upgrade weapons/armor to higher tiers, craft artifact gear
- **Market**: Buy gear and potions, open mystery chests
- **Warehouse**: Store loot between runs
- **Evacuation**: Reach the extraction point, channel for 3 seconds, and escape with your loot

### Procedural Generation

- Random room-and-corridor map layouts per seed
- Theme-specific wall colors, floor textures, and decorative elements
- Chest placement, harvestable resources (trees, ores, stones, cacti), and boss rooms

## Project Structure

```
monster-upgrade/
├── main.py              # Entry point: arcade.Window + GameState
├── config.py            # All game constants (window, player, monsters, combat, loot, upgrade formulas)
├── entities/            # Static data definitions
│   ├── weapon_defs.py   # 16 weapon templates (melee + ranged)
│   ├── equipment_defs.py# Helmets, armor, backpacks, potions
│   ├── resource_defs.py # Wood, stone, ore
│   └── effects_defs.py  # Debuff/effect rules and scaling
├── game/                # Core game logic
│   ├── monsters.py      # 13 monster types + 4 bosses
│   ├── combat.py        # Melee/ranged combat, debuff resolution
│   ├── map_gen.py       # Procedural room+corridor generation
│   ├── player.py        # Player sprite and movement
│   ├── loot.py          # Drop tables and loot logic
│   ├── evac.py          # Extraction point and evacuation
│   ├── effects.py       # Particles, floating text, sound triggers
│   ├── rendering.py     # Main render orchestration
│   ├── sound_manager.py # Procedural sound synthesis
│   ├── input_handler.py # Keyboard/mouse input mapping
│   └── respawn.py       # Monster respawn system
├── views/               # UI screens (all arcade.View subclasses)
│   ├── start_view.py    # Main menu with equipment display
│   ├── map_select_view.py # Map selection (3 maps)
│   ├── game_view.py     # Main gameplay HUD (~700 lines)
│   ├── warehouse_view.py# Item management and selling
│   ├── market_view.py   # Buy/sell/chest opening (~500 lines)
│   ├── forge_view.py    # Weapon/armor upgrading and artifact crafting
│   ├── backpack_view.py # Current run inventory
│   └── scroll_view.py   # Scrollable panel base class
└── db/                  # SQLite persistence layer
    ├── database.py      # Schema setup + CRUD re-exports
    ├── players.py       # Player data
    ├── weapons.py       # Weapon storage
    ├── equipment.py     # Equipment storage
    ├── warehouse.py     # Warehouse storage
    └── potions.py       # Potion storage
```

## Tech Stack

- **Python 3.14**
- **Arcade 2.7** — 2D game engine (rendering, physics, input)
- **SQLite** — Local database for persistence
- **No external dependencies** beyond `arcade`

## Getting Started

### Prerequisites

- Python 3.14+
- pip

### Installation

```bash
# Clone the repository
git clone https://github.com/wrCBDjdhN/monster-upgrade.git
cd monster-upgrade

# Install dependencies
pip install arcade

# Run the game
python main.py
```

### Controls

| Key | Action |
|-----|--------|
| WASD / Arrow Keys | Move |
| Mouse | Aim (ranged weapons) |
| Left Click | Attack |
| TAB | Open/close backpack |
| F11 | Toggle fullscreen |

## Game Flow

```
Start Menu
  ├── Select weapon to carry into battle
  ├── Warehouse (store/manage loot)
  ├── Market (buy gear, open chests)
  └── Forge (upgrade weapons/armor)
        │
        ▼
  Map Selection
  ├── Dark Forest (Normal)
  ├── Desert Wasteland (Hard)
  └── Space Base (Extreme)
        │
        ▼
  Gameplay (timed)
  ├── Explore procedural maps
  ├── Kill monsters → collect loot
  ├── Open chests → find gear
  ├── Harvest resources (trees, ores)
  ├── Fight bosses for rare equipment
  └── Reach extraction point → Evacuate
        │
        ▼
  Evacuation Result
  ├── Loot saved to warehouse
  ├── Gold deposited
  └── Return to Start Menu
```

## Architecture

The codebase follows a strict layered architecture:

- **`entities/`** — Pure data definitions (weapon/equipment/resource templates)
- **`db/`** — SQLite persistence layer (schema + CRUD)
- **`game/`** — Core logic (monsters, combat, map gen, effects, rendering)
- **`views/`** — UI screens (arcade.View subclasses)
- **`config.py`** — All tunable constants (no hardcoded values in game code)

Cross-layer data flow goes through `window.game_state` (main.GameState) — no global variables.

## License

This project is open source. See the repository for license details.
