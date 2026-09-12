## F-101 — CharacterSelectView.on_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/character_select_view.py:297`

### Evidence

on_mouse_press 有 43 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_mouse_press(self, x, y, ...):
    # ... 43 lines
```

### Consequence

函数过长，难以理解和维护。

## F-102 — EvacResultView.on_draw 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/evac_result_view.py:62`

### Evidence

on_draw 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
for item in items:
    if rare:
        for stat in stats:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-103 — EvacResultView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/evac_result_view.py:62`

### Evidence

on_draw 有 113 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 113 lines
```

### Consequence

函数过长，难以理解和维护。

## F-104 — EvacResultView.on_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/evac_result_view.py:224`

### Evidence

on_mouse_press 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
if button:
    if self:
        if state:
            if mode:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-105 — ForgeView._build_content 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/forge_view.py:88`

### Evidence

_build_content 有 53 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _build_content(self):
    # ... 53 lines
```

### Consequence

函数过长，难以理解和维护。

## F-106 — ForgeView.on_draw 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/forge_view.py:184`

### Evidence

on_draw 嵌套深度为 3 层；medium 配置阈值为 3。

### Snippet

```python
for item in items:
    if valid:
        pass
```

### Consequence

嵌套深度达到阈值。

## F-107 — ForgeView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/forge_view.py:184`

### Evidence

on_draw 有 90 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 90 lines
```

### Consequence

函数过长，难以理解和维护。

## F-108 — ForgeView.on_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/forge_view.py:305`

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

## F-109 — ForgeView._do_forge 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/forge_view.py:339`

### Evidence

_do_forge 有 57 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _do_forge(self):
    # ... 57 lines
```

### Consequence

函数过长，难以理解和维护。

## F-110 — game_view.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1`

### Evidence

views/game_view.py 有 3322 行；medium 配置阈值为 500。

### Snippet

```python
# ... game view
```

### Consequence

文件过大，难以导航和维护。

## F-111 — GameView 类职责过多（上帝类）

- `status`: `active`
- `rule`: `code.god-class`
- `evidence_rank`: `semantic`
- `location`: `views/game_view.py:120`

### Evidence

GameView 类有 3322 行，承担了地图管理、玩家状态、怪物AI、战斗系统、掉落物处理、撤离逻辑、网络同步、UI渲染等过多职责。

### Snippet

```python
class GameView(arcade.View):
    def __init__(self, window):
```

### Consequence

任何功能修改都可能影响其他模块，增加维护成本。

## F-112 — GameView.__init__ 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:121`

### Evidence

__init__ 有 101 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def __init__(self, window):
    # ... 101 lines
```

### Consequence

构造函数过长，难以理解和维护。

## F-113 — GameView.setup 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:311`

### Evidence

setup 嵌套深度为 6 层；medium 配置阈值为 3。

### Snippet

```python
if seed:
    if theme:
        if tiles:
            for tile:
                if valid:
                    pass
```

### Consequence

嵌套过深增加认知负担。

## F-114 — GameView.setup 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:311`

### Evidence

setup 有 296 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def setup(self, ...):
    # ... 296 lines
```

### Consequence

函数过长，难以理解和维护。

## F-115 — GameView._broadcast_damage 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:739`

### Evidence

_broadcast_damage 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _broadcast_damage(self, target_type, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-116 — GameView._resolve_attack_event 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:760`

### Evidence

_resolve_attack_event 嵌套深度为 5 层；medium 配置阈值为 3。

### Snippet

```python
if hit:
    if target:
        if alive:
            for d in damage:
                pass
```

### Consequence

嵌套过深增加认知负担。

## F-117 — GameView._resolve_attack_event 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:760`

### Evidence

_resolve_attack_event 有 94 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _resolve_attack_event(self, ...):
    # ... 94 lines
```

### Consequence

函数过长，难以理解和维护。

## F-118 — GameView._broadcast_player_hurt 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1024`

### Evidence

_broadcast_player_hurt 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def _broadcast_player_hurt(self, player_id, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-119 — GameView._handle_potion_use 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1281`

### Evidence

_handle_potion_use 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if potion:
    if player:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-120 — GameView._handle_potion_use 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1281`

### Evidence

_handle_potion_use 有 61 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _handle_potion_use(self, ...):
    # ... 61 lines
```

### Consequence

函数过长，难以理解和维护。

## F-121 — GameView._apply_potion_ack 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1372`

### Evidence

_apply_potion_ack 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if ack:
    if player:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-122 — GameView._apply_potion_ack 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1372`

### Evidence

_apply_potion_ack 有 43 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _apply_potion_ack(self, ...):
    # ... 43 lines
```

### Consequence

函数过长，难以理解和维护。

## F-123 — GameView._apply_map_change 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:1571`

### Evidence

_apply_map_change 有 54 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _apply_map_change(self, ...):
    # ... 54 lines
```

### Consequence

函数过长，难以理解和维护。

## F-124 — GameView._client_visual_collisions 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2125`

### Evidence

_client_visual_collisions 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
for p in projectiles:
    for m in monsters:
        if hit:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-125 — GameView._handle_interaction_request 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2244`

### Evidence

_handle_interaction_request 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if request:
    if target:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-126 — GameView._handle_interaction_request 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2244`

### Evidence

_handle_interaction_request 有 69 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _handle_interaction_request(self, ...):
    # ... 69 lines
```

### Consequence

函数过长，难以理解和维护。

## F-127 — GameView._serialize_projectiles 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2460`

### Evidence

_serialize_projectiles 有 56 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _serialize_projectiles(self):
    # ... 56 lines
```

### Consequence

函数过长，难以理解和维护。

## F-128 — GameView._apply_projectile_snapshot 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2582`

### Evidence

_apply_projectile_snapshot 有 52 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _apply_projectile_snapshot(self, ...):
    # ... 52 lines
```

### Consequence

函数过长，难以理解和维护。

## F-129 — GameView._hud_text 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2755`

### Evidence

_hud_text 有 9 个参数；medium 配置阈值为 4。

### Snippet

```python
def _hud_text(self, text, x, y, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-130 — GameView._apply_free_equip 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2791`

### Evidence

_apply_free_equip 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if equip:
    if player:
        if valid:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-131 — GameView._apply_free_equip 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2791`

### Evidence

_apply_free_equip 有 91 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _apply_free_equip(self, ...):
    # ... 91 lines
```

### Consequence

函数过长，难以理解和维护。

## F-132 — GameView.on_update 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2961`

### Evidence

on_update 嵌套深度为 7 层；medium 配置阈值为 3。

### Snippet

```python
for entity in entities:
    if active:
        for event in events:
            if valid:
                for action in actions:
                    if done:
                        pass
```

### Consequence

嵌套过深增加认知负担。

## F-133 — GameView.on_update 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/game_view.py:2961`

### Evidence

on_update 有 636 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_update(self, dt):
    # ... 636 lines
```

### Consequence

函数过长，难以理解和维护。

## F-134 — LevelUpView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/level_up_view.py:54`

### Evidence

on_draw 有 46 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 46 lines
```

### Consequence

函数过长，难以理解和维护。

## F-135 — lobby_view.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:1`

### Evidence

views/lobby_view.py 有 1093 行；medium 配置阈值为 500。

### Snippet

```python
# ... lobby view
```

### Consequence

文件过大，难以导航和维护。

## F-136 — LobbyView.__init__ 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:38`

### Evidence

__init__ 有 70 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def __init__(self, window):
    # ... 70 lines
```

### Consequence

构造函数过长，难以理解和维护。

## F-137 — LobbyView._build_tutorial_pages 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:152`

### Evidence

_build_tutorial_pages 有 79 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _build_tutorial_pages(self):
    # ... 79 lines
```

### Consequence

函数过长，难以理解和维护。

## F-138 — LobbyView.on_update 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:560`

### Evidence

on_update 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
if state:
    if mode:
        if active:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-139 — LobbyView.on_update 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:560`

### Evidence

on_update 有 91 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_update(self, dt):
    # ... 91 lines
```

### Consequence

函数过长，难以理解和维护。

## F-140 — LobbyView._draw_host_wait 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:738`

### Evidence

_draw_host_wait 有 66 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _draw_host_wait(self):
    # ... 66 lines
```

### Consequence

函数过长，难以理解和维护。

## F-141 — LobbyView._draw_join 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:816`

### Evidence

_draw_join 有 75 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _draw_join(self):
    # ... 75 lines
```

### Consequence

函数过长，难以理解和维护。

## F-142 — LobbyView._draw_client_wait 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:949`

### Evidence

_draw_client_wait 有 55 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _draw_client_wait(self):
    # ... 55 lines
```

### Consequence

函数过长，难以理解和维护。

## F-143 — LobbyView.on_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:1085`

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

## F-144 — LobbyView.on_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/lobby_view.py:1085`

### Evidence

on_mouse_press 有 94 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_mouse_press(self, x, y, ...):
    # ... 94 lines
```

### Consequence

函数过长，难以理解和维护。

## F-145 — MapSelectView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/map_select_view.py:207`

### Evidence

on_draw 有 84 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 84 lines
```

### Consequence

函数过长，难以理解和维护。

## F-146 — market_view.py 文件过大

- `status`: `active`
- `rule`: `code.large-file`
- `evidence_rank`: `mechanical`
- `location`: `views/market_view.py:1`

### Evidence

views/market_view.py 有 850 行；medium 配置阈值为 500。

### Snippet

```python
# ... market view
```

### Consequence

文件过大，难以导航和维护。

## F-147 — MarketView._build_content 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/market_view.py:73`

### Evidence

_build_content 有 184 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _build_content(self):
    # ... 184 lines
```

### Consequence

函数过长，难以理解和维护。

## F-148 — MarketView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/market_view.py:363`

### Evidence

on_draw 有 182 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 182 lines
```

### Consequence

函数过长，难以理解和维护。

## F-149 — MarketView.on_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/market_view.py:585`

### Evidence

on_mouse_press 有 79 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_mouse_press(self, x, y, ...):
    # ... 79 lines
```

### Consequence

函数过长，难以理解和维护。

## F-150 — SettingsView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/settings_view.py:151`

### Evidence

on_draw 有 77 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 77 lines
```

### Consequence

函数过长，难以理解和维护。

## F-151 — SettingsView.on_mouse_drag 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/settings_view.py:305`

### Evidence

on_mouse_drag 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def on_mouse_drag(self, x, y, dx, dy, button, modifiers):
```

### Consequence

参数过多增加调用复杂度。

## F-152 — Particle.__init__ 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/splash_view.py:20`

### Evidence

Particle.__init__ 有 5 个参数；medium 配置阈值为 4。

### Snippet

```python
def __init__(self, x, y, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-153 — SplashView.on_update 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/splash_view.py:146`

### Evidence

on_update 有 58 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_update(self, dt):
    # ... 58 lines
```

### Consequence

函数过长，难以理解和维护。

## F-154 — SplashView.on_draw 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/splash_view.py:270`

### Evidence

on_draw 嵌套深度为 4 层；medium 配置阈值为 3。

### Snippet

```python
for p in particles:
    if alive:
        for layer in layers:
            pass
```

### Consequence

嵌套过深增加认知负担。

## F-155 — SplashView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/splash_view.py:270`

### Evidence

on_draw 有 60 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 60 lines
```

### Consequence

函数过长，难以理解和维护。

## F-156 — SplashView._draw_decorations 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/splash_view.py:355`

### Evidence

_draw_decorations 有 53 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def _draw_decorations(self):
    # ... 53 lines
```

### Consequence

函数过长，难以理解和维护。

## F-157 — StartView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/start_view.py:77`

### Evidence

on_draw 有 95 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 95 lines
```

### Consequence

函数过长，难以理解和维护。

## F-158 — StartView.on_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/start_view.py:236`

### Evidence

on_mouse_press 有 52 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_mouse_press(self, x, y, ...):
    # ... 52 lines
```

### Consequence

函数过长，难以理解和维护。

## F-159 — TextCache.text 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/text_cache.py:23`

### Evidence

TextCache.text 有 9 个参数；medium 配置阈值为 4。

### Snippet

```python
def text(self, text_str, x, y, color, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-160 — draw_tutorial_page 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/tutorial.py:60`

### Evidence

draw_tutorial_page 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def draw_tutorial_page(page, x, y, width, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-161 — tut_banner 参数过多

- `status`: `active`
- `rule`: `code.long-parameter-list`
- `evidence_rank`: `mechanical`
- `location`: `views/tutorial.py:110`

### Evidence

tut_banner 有 6 个参数；medium 配置阈值为 4。

### Snippet

```python
def tut_banner(text, x, y, width, ...):
```

### Consequence

参数过多增加调用复杂度。

## F-162 — WarehouseView.on_draw 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/warehouse_view.py:95`

### Evidence

on_draw 有 134 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_draw(self):
    # ... 134 lines
```

### Consequence

函数过长，难以理解和维护。

## F-163 — WarehouseView.on_mouse_press 嵌套过深

- `status`: `active`
- `rule`: `code.deep-nesting`
- `evidence_rank`: `mechanical`
- `location`: `views/warehouse_view.py:330`

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

## F-164 — WarehouseView.on_mouse_press 函数过长

- `status`: `active`
- `rule`: `code.long-function`
- `evidence_rank`: `mechanical`
- `location`: `views/warehouse_view.py:330`

### Evidence

on_mouse_press 有 73 行非空非注释代码；medium 配置阈值为 40。

### Snippet

```python
def on_mouse_press(self, x, y, ...):
    # ... 73 lines
```

### Consequence

函数过长，难以理解和维护。
