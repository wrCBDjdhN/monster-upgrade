---
schema_version: 1
repo: "monster_upgrade"
commit: "598dce3"
date: "2026-09-06T12:00:00Z"
scope: "whole repo (66 Python files)"
status: "complete"
language: "zh-CN"
active: 164
dismissed: 0
profile_name: "medium"
profile_source: "auto"
profile_lines: 20282
profile_basis: "15000–74999 → medium"
---

# monster_upgrade smell-check

## Rule summary

| rule | active | dismissed | evidence rank |
| --- | ---: | ---: | --- |
| code.deep-nesting | 44 | 0 | mechanical |
| code.god-class | 1 | 0 | semantic |
| code.large-file | 8 | 0 | mechanical |
| code.long-function | 61 | 0 | mechanical |
| code.long-parameter-list | 50 | 0 | mechanical |

## Synthesis

- **F-1** (code.god-class): `GameView` 承担了过多职责（3322行），包括地图管理、玩家状态、怪物AI、战斗系统、掉落物处理、撤离逻辑、网络同步、UI渲染等。这导致任何功能修改都可能影响其他模块，增加了维护成本。建议将网络同步、战斗逻辑、渲染逻辑分别提取到独立模块。
- **F-2** (code.long-function): `render_game` 函数长达490行，`on_update` 长达636行，`setup` 长达296行。这些超长函数难以理解和测试。建议按职责拆分为更小的辅助函数。
- **F-3** (code.deep-nesting): 多个函数嵌套深度达到6-8层（如 `CombatSystem.check_monster_hits` 嵌套8层），增加了认知负担和出错概率。建议使用提前返回（guard clauses）减少嵌套层级。

Inference — verify by rescanning after the fix

## Shard Inventory

| Shard File | Rule Group | File Pattern | Count |
|-----------|------------|--------------|-------|
| `game-active-001.md` | `code.large-file`, `code.long-function`, `code.deep-nesting`, `code.long-parameter-list` | `game/`, `db/`, `config.py` | 100 |
| `views-active-001.md` | `code.large-file`, `code.god-class`, `code.long-function`, `code.deep-nesting`, `code.long-parameter-list` | `views/` | 64 |
| **Total** | | | **164** |

## Finding reports

| status | area | report | count |
| --- | --- | --- | ---: |
| active | game | [findings/game-active-001.md](findings/game-active-001.md) | 100 |
| active | views | [findings/views-active-001.md](findings/views-active-001.md) | 64 |

## Environment

- time_utc: 2026-09-06T12:00:00Z
- skill_version: 1.0.0
- execution_model: Sisyphus
- partial: false
- tools:
  - python3: 3.11.15
  - node: absent
  - typescript_subject: absent
  - lizard: absent
  - jscpd: absent
- commands_run:
  - Get-ChildItem -Recurse -Filter "*.py" | Where-Object { $_.FullName -notlike "*\__pycache__\*" -and $_.FullName -notlike "*\.git\*" } | ForEach-Object { $lines = (Get-Content $_.FullName | Measure-Object -Line).Lines; if ($lines -gt 200) { Write-Output "$($_.FullName):$lines" } }
  - python "C:\Users\pc\.config\opencode\skills\smell-check\scripts\measure_python.py" $files
- degradations:
  - code.duplicate-code: jscpd absent → skipped
