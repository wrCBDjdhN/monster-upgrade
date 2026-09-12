## F-1 — config.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `config.py:1`

### Evidence

config.py 有 405 行；medium 配置阈值为 500。

### Snippet

```python
# ... config values
```

### Consequence

文件过大，难以导航和维护。

## F-2 — init_db 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/database.py:22`

### Evidence

init_db 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cursor:
        if rows:
            if row:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-3 — init_db 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `db/database.py:22`

### Evidence

init_db 有 120 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def init_db():
    # ... 120 lines
```

### Consequence

函数过长，难以理解和维护。

## F-4 — upgrade_equipment 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:5`

### Evidence

upgrade_equipment 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-5 — equip_item 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:36`

### Evidence

equip_item 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def equip_item(player_id, item_id, slot, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-6 — equip_from_inventory 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:53`

### Evidence

equip_from_inventory 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-7 — add_equipment 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:94`

### Evidence

add_equipment 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-8 — add_equipment 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:94`

### Evidence

add_equipment 有 7 个参数；medium 配置阈值为 4。

### Snippet

```python
def add_equipment(player_id, name, defense, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-9 — get_equipment_materials 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:133`

### Evidence

get_equipment_materials 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def get_equipment_materials(equip_id, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-10 — upgrade_equipment 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:152`

### Evidence

upgrade_equipment 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-11 — upgrade_equipment 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:152`

### Evidence

upgrade_equipment 有 55 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def upgrade_equipment(equip_id):
    # ... 55 lines
```

### Consequence

函数过长，难以理解和维护。

## F-12 — sell_equipment 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:212`

### Evidence

sell_equipment 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-13 — delete_equipment 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/equipment.py:234`

### Evidence

delete_equipment 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-14 — get_or_create_player 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/players.py:5`

### Evidence

get_or_create_player 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-15 — spend_gold 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/players.py:34`

### Evidence

spend_gold 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-16 — add_potion 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/potions.py:5`

### Evidence

add_potion 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-17 — add_potion 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/potions.py:5`

### Evidence

add_potion 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def add_potion(player_id, potion_id, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-18 — use_potion 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/potions.py:35`

### Evidence

use_potion 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-19 — get_key_bindings 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/settings.py:39`

### Evidence

get_key_bindings 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-20 — add_warehouse_item 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/warehouse.py:5`

### Evidence

add_warehouse_item 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-21 — sell_warehouse_item 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/warehouse.py:34`

### Evidence

sell_warehouse_item 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-22 — create_weapon 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/weapons.py:5`

### Evidence

create_weapon 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def create_weapon(player_id, name, damage, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-23 — add_weapon 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `db/weapons.py:25`

### Evidence

add_weapon 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def add_weapon(player_id, weapon_id, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-24 — upgrade_weapon 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/weapons.py:46`

### Evidence

upgrade_weapon 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-25 — sell_weapon 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `db/weapons.py:97`

### Evidence

sell_weapon 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if conn:
    if cur:
        if row:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-26 — ShapeBatch.rect 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/batch_shapes.py:69`

### Evidence

ShapeBatch.rect 有 7 个参数；medium 配置阈值为 4。

### Snippet

```python
def rect(self, x, y, w, h, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-27 — ShapeBatch.circle 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/batch_shapes.py:88`

### Evidence

ShapeBatch.circle 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def circle(self, x, y, r, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-28 — ShapeBatch.line 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/batch_shapes.py:110`

### Evidence

ShapeBatch.line 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def line(self, x1, y1, x2, y2, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-29 — ShapeBatch.arc_outline 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/batch_shapes.py:139`

### Evidence

ShapeBatch.arc_outline 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def arc_outline(self, x, y, r, start, end, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-30 — use_skill 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/character_skills.py:41`

### Evidence

use_skill 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def use_skill(skill_id, player, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-31 — _arcane_blast 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/character_skills.py:76`

### Evidence

_arcane_blast 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _arcane_blast(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-32 — _shadow_strike 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/character_skills.py:117`

### Evidence

_shadow_strike 有 45 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _shadow_strike(self):
    # ... 45 lines
```

### Consequence

函数过长，难以理解和维护。

## F-33 — _shadow_strike 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/character_skills.py:117`

### Evidence

_shadow_strike 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def _shadow_strike(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-34 — CombatSystem.melee_attack 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:85`

### Evidence

melee_attack 有 10 个参数；medium 配置阈值为 4。

### Snippet

```python
def melee_attack(self, player, monsters, damage, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-35 — CombatSystem.ranged_attack 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:170`

### Evidence

ranged_attack 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def ranged_attack(self, player, target, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-36 — CombatSystem.check_monster_hits 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:221`

### Evidence

check_monster_hits 嵌套深度为 8 层；medium 配置阈值为 3。

### Snippet

```python
for m in monsters:
    if hit:
        for d in damage:
            if alive:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-37 — CombatSystem.emit_aoe_explosion 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:303`

### Evidence

emit_aoe_explosion 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def emit_aoe_explosion(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-38 — CombatSystem.spawn_laser 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:313`

### Evidence

spawn_laser 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def spawn_laser(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-39 — _boss_summon_minions 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:367`

### Evidence

_boss_summon_minions 有 50 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _boss_summon_minions(self):
    # ... 50 lines
```

### Consequence

函数过长，难以理解和维护。

## F-40 — LaserBeam.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:414`

### Evidence

LaserBeam.__init__ 有 7 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, angle, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-41 — LaserBeam._point_segment_distance 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/combat.py:490`

### Evidence

_point_segment_distance 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def _point_segment_distance(self, px, py, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-42 — FloatingText.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/effects.py:25`

### Evidence

FloatingText.__init__ 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, text, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-43 — ParticleSystem.emit 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/effects.py:111`

### Evidence

ParticleSystem.emit 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def emit(self, x, y, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-44 — ParticleSystem.emit_directional 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/effects.py:145`

### Evidence

emit_directional 有 7 个参数；medium 配置阈值为 4。

### Snippet

```python
def emit_directional(self, x, y, angle, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-45 — FloatingTextManager.add 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/effects.py:160`

### Evidence

FloatingTextManager.add 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def add(self, x, y, text, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-46 — Particle.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/effects.py:263`

### Evidence

Particle.__init__ 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, vx, vy, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-47 — handle_harvestable_combat 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/entity_callbacks.py:148`

### Evidence

handle_harvestable_combat 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
if hit:
    if alive:
        for d in drops:
            if valid:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-48 — handle_harvestable_combat 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/entity_callbacks.py:148`

### Evidence

handle_harvestable_combat 有 80 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def handle_harvestable_combat(self):
    # ... 80 lines
```

### Consequence

函数过长，难以理解和维护。

## F-49 — _place_drop_avoiding 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/entity_callbacks.py:524`

### Evidence

_place_drop_avoiding 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _place_drop_avoiding(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-50 — scatter_drops 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/entity_callbacks.py:535`

### Evidence

scatter_drops 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def scatter_drops(self, x, y, items, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-51 — commit_run_to_warehouse 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/evac.py:84`

### Evidence

commit_run_to_warehouse 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
if player:
    if items:
        for item:
            if valid:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-52 — spawn_harvestables 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/harvestable.py:64`

### Evidence

spawn_harvestables 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
for tile in tiles:
    if valid:
        for i in range(n):
            if pos:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-53 — handle_key_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:31`

### Evidence

handle_key_press 有 90 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def handle_key_press(self, key):
    # ... 90 lines
```

### Consequence

函数过长，难以理解和维护。

## F-54 — handle_mouse_motion 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:165`

### Evidence

handle_mouse_motion 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def handle_mouse_motion(self, x, y, dx, dy):
```

### Consequence

参数过多增加调用复杂度。

## F-55 — handle_mouse_press 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:199`

### Evidence

handle_mouse_press 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def handle_mouse_press(self, x, y, button, modifiers):
```

### Consequence

参数过多增加调用复杂度。

## F-56 — handle_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:209`

### Evidence

handle_mouse_press 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if button:
    if self:
        if state:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-57 — handle_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:209`

### Evidence

handle_mouse_press 有 45 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def handle_mouse_press(self, x, y, ...):
    # ... 45 lines
```

### Consequence

函数过长，难以理解和维护。

## F-58 — handle_mouse_release 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/input_handler.py:412`

### Evidence

handle_mouse_release 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def handle_mouse_release(self, x, y, button, modifiers):
```

### Consequence

参数过多增加调用复杂度。

## F-59 — DropItem.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/loot.py:30`

### Evidence

DropItem.__init__ 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, item, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-60 — roll_loot 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/loot.py:76`

### Evidence

roll_loot 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if roll < threshold:
    for item in items:
        if rare:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-61 — try_pickup 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/loot.py:147`

### Evidence

try_pickup 有 60 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def try_pickup(self):
    # ... 60 lines
```

### Consequence

函数过长，难以理解和维护。

## F-62 — try_pickup 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/loot.py:147`

### Evidence

try_pickup 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def try_pickup(self, player, items, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-63 — generate_map 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/map_gen.py:62`

### Evidence

generate_map 有 355 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def generate_map(seed=None, theme="forest"):
    # ... 355 lines
```

### Consequence

函数过长，难以理解和维护。

## F-64 — monster_utils.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `game/monster_utils.py:1`

### Evidence

game/monster_utils.py 有 496 行；medium 配置阈值为 500。

### Snippet

```python
# ... monster utils
```

### Consequence

文件过大，难以导航和维护。

## F-65 — _emit_skill_vfx 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/monster_utils.py:140`

### Evidence

_emit_skill_vfx 有 45 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _emit_skill_vfx(self):
    # ... 45 lines
```

### Consequence

函数过长，难以理解和维护。

## F-66 — apply_skill_effect 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/monster_utils.py:278`

### Evidence

apply_skill_effect 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if effect:
    if target:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-67 — apply_skill_effect 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/monster_utils.py:278`

### Evidence

apply_skill_effect 有 50 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def apply_skill_effect(self):
    # ... 50 lines
```

### Consequence

函数过长，难以理解和维护。

## F-68 — monsters.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:1`

### Evidence

game/monsters.py 有 1192 行；medium 配置阈值为 500。

### Snippet

```python
# ... monsters
```

### Consequence

文件过大，难以导航和维护。

## F-69 — _RangedMonsterBase.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:92`

### Evidence

_RangedMonsterBase.__init__ 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, speed, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-70 — _has_line_of_sight 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:119`

### Evidence

_has_line_of_sight 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _has_line_of_sight(self, x1, y1, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-71 — Projectile.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:200`

### Evidence

Projectile.__init__ 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, dx, dy, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-72 — _MeleeMonsterBase.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:235`

### Evidence

_MeleeMonsterBase.__init__ 有 8 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, speed, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-73 — _MeleeMonsterBase.update 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:282`

### Evidence

_MeleeMonsterBase.update 有 65 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def update(self, dt):
    # ... 65 lines
```

### Consequence

函数过长，难以理解和维护。

## F-74 — _MeleeMonsterBase.apply_debuff 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:427`

### Evidence

apply_debuff 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if debuff:
    if target:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-75 — _segment_intersects_rect 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:501`

### Evidence

_segment_intersects_rect 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def _segment_intersects_rect(self, x1, y1, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-76 — _RangedMonsterBase.update 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:551`

### Evidence

_RangedMonsterBase.update 有 55 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def update(self, dt):
    # ... 55 lines
```

### Consequence

函数过长，难以理解和维护。

## F-77 — _RangedMonsterBase.apply_debuff 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:692`

### Evidence

apply_debuff 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if debuff:
    if target:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-78 — _spawn_monster_group 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/monsters.py:792`

### Evidence

_spawn_monster_group 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _spawn_monster_group(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-79 — draw_harvestable 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/render_helpers.py:112`

### Evidence

draw_harvestable 有 45 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def draw_harvestable(self):
    # ... 45 lines
```

### Consequence

函数过长，难以理解和维护。

## F-80 — draw_player_weapon 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/render_helpers.py:199`

### Evidence

draw_player_weapon 有 50 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def draw_player_weapon(self):
    # ... 50 lines
```

### Consequence

函数过长，难以理解和维护。

## F-81 — draw_player_weapon 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/render_helpers.py:199`

### Evidence

draw_player_weapon 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def draw_player_weapon(self, player, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-82 — rendering.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `game/rendering.py:1`

### Evidence

game/rendering.py 有 879 行；medium 配置阈值为 500。

### Snippet

```python
# ... rendering
```

### Consequence

文件过大，难以导航和维护。

## F-83 — render_game 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/rendering.py:170`

### Evidence

render_game 有 490 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def render_game(view):
    # ... 490 lines
```

### Consequence

函数过长，难以理解和维护。

## F-84 — draw_minimap 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/rendering.py:885`

### Evidence

draw_minimap 有 80 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def draw_minimap(self):
    # ... 80 lines
```

### Consequence

函数过长，难以理解和维护。

## F-85 — _find_valid_spawn_position 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/respawn.py:52`

### Evidence

_find_valid_spawn_position 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _find_valid_spawn_position(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-86 — respawn_harvestables 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/respawn.py:94`

### Evidence

respawn_harvestables 有 50 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def respawn_harvestables(self):
    # ... 50 lines
```

### Consequence

函数过长，难以理解和维护。

## F-87 — _spawn_monster_group 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/respawn.py:167`

### Evidence

_spawn_monster_group 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _spawn_monster_group(self, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-88 — SoundManager._synth 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `game/sound_manager.py:37`

### Evidence

SoundManager._synth 有 45 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _synth(self):
    # ... 45 lines
```

### Consequence

函数过长，难以理解和维护。

## F-89 — SoundManager._synth 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/sound_manager.py:37`

### Evidence

SoundManager._synth 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _synth(self, wave, freq, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-90 — SoundManager._play 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `game/sound_manager.py:84`

### Evidence

_play 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if sound:
    if channel:
        if playing:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-91 — SoundManager._emit 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `game/sound_manager.py:108`

### Evidence

_emit 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def _emit(self, wave, freq, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-92 — backpack_view.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:1`

### Evidence

views/backpack_view.py 有 733 行；medium 配置阈值为 500。

### Snippet

```python
# ... backpack view
```

### Consequence

文件过大，难以导航和维护。

## F-93 — BackpackView.on_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:117`

### Evidence

on_mouse_press 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if button:
    if self:
        if state:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-94 — BackpackView._draw_discard_dialog 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:237`

### Evidence

_draw_discard_dialog 有 54 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _draw_discard_dialog(self):
    # ... 54 lines
```

### Consequence

函数过长，难以理解和维护。

## F-95 — BackpackView._discard_equipped 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:308`

### Evidence

_discard_equipped 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if item:
    if equipped:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-96 — BackpackView._discard_equipped 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:308`

### Evidence

_discard_equipped 有 80 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _discard_equipped(self):
    # ... 80 lines
```

### Consequence

函数过长，难以理解和维护。

## F-97 — BackpackView._discard_item 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:457`

### Evidence

_discard_item 有 70 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _discard_item(self):
    # ... 70 lines
```

### Consequence

函数过长，难以理解和维护。

## F-98 — BackpackView._discard_all_items 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:548`

### Evidence

_discard_all_items 有 57 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _discard_all_items(self):
    # ... 57 lines
```

### Consequence

函数过长，难以理解和维护。

## F-99 — BackpackView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/backpack_view.py:635`

### Evidence

on_draw 有 201 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 201 lines
```

### Consequence

函数过长，难以理解和维护。

## F-100 — CharacterSelectView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/character_select_view.py:94`

### Evidence

on_draw 有 136 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 136 lines
```

### Consequence

函数过长，难以理解和维护。
