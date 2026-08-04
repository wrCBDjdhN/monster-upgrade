"""可破坏环境物：树、矿石、石头、仙人掌

环境物机制：
1. 分布在野外，不与房间重叠
2. 拥有血量，可被攻击摧毁
3. 摧毁后掉落对应资源
4. 被摧毁后从障碍物列表移除

环境物类型：
- tree(树木): HP=50, 掉落木材 2-4 个
- ore(矿石): HP=80, 掉落矿石 1-3 个
- stone(石头): HP=60, 掉落石材 2-3 个
- cactus(仙人掌): HP=50, 攻击者自身受到反弹伤害，掉落果实药水 1 个（沙漠主题专属）
"""

import random
import arcade
from config import MAP_WIDTH, MAP_HEIGHT, TILE_SIZE, CACTUS_HP


RESOURCE_DEFS = {
    "tree":  {"name": "树木", "color": (34, 100, 34),  "hp": 50,  "drop_type": "wood",  "drop_qty": (2, 4)},
    "ore":   {"name": "矿石", "color": (160, 120, 60), "hp": 80,  "drop_type": "ore",   "drop_qty": (1, 3)},
    "stone": {"name": "石头", "color": (130, 130, 130), "hp": 60,  "drop_type": "stone", "drop_qty": (2, 3)},
    "cactus": {"name": "仙人掌", "color": (60, 150, 70), "hp": CACTUS_HP, "drop_type": "fruit", "drop_qty": (1, 1)},
}


class HarvestableEntity(arcade.SpriteSolidColor):
    """可破坏环境物，拥有血量，被击杀后掉落资源"""

    def __init__(self, center_x: float, center_y: float, resource_type: str):
        info = RESOURCE_DEFS[resource_type]
        super().__init__(28, 28, color=info["color"])
        self.center_x = center_x
        self.center_y = center_y
        self.resource_type = resource_type
        self.hp = info["hp"]
        self.max_hp = info["hp"]
        self._hit_flash = 0.0

    def take_damage(self, amount: int):
        """受到伤害，返回实际伤害值"""
        actual = max(1, amount)
        self.hp -= actual
        self._hit_flash = 0.15
        return actual

    def update(self, delta_time: float = 0):
        if self._hit_flash > 0:
            self._hit_flash = max(0, self._hit_flash - delta_time)

    def get_drops(self) -> list[tuple]:
        """返回掉落资源列表 [(resource_type, quantity)]"""
        info = RESOURCE_DEFS[self.resource_type]
        qty = random.randint(info["drop_qty"][0], info["drop_qty"][1])
        return [(info["drop_type"], qty)]

    @property
    def alive(self) -> bool:
        return self.hp > 0


def spawn_harvestables(rooms, rng, count: int = 25, min_spacing: int = 50) -> list[tuple]:
    """在野外随机生成环境物，返回 [(x, y, resource_type)]，避免与房间和其他环境物重合"""
    result = []
    resource_types = list(RESOURCE_DEFS.keys())
    for _ in range(count):
        rtype = rng.choice(resource_types)
        for _attempt in range(30):
            x = rng.randint(TILE_SIZE * 3, MAP_WIDTH - TILE_SIZE * 3)
            y = rng.randint(TILE_SIZE * 3, MAP_HEIGHT - TILE_SIZE * 3)
            # 不在房间内
            in_room = False
            for room in rooms:
                if (room.x - TILE_SIZE <= x <= room.x + room.w + TILE_SIZE and
                    room.y - TILE_SIZE <= y <= room.y + room.h + TILE_SIZE):
                    in_room = True
                    break
            if in_room:
                continue
            # 不与其他环境物重合
            too_close = False
            for ex, ey, _ in result:
                if abs(ex - x) < min_spacing and abs(ey - y) < min_spacing:
                    too_close = True
                    break
            if not too_close:
                result.append((x, y, rtype))
                break
    return result
