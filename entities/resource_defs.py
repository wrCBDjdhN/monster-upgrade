"""资源类型定义

定义游戏中所有可采集资源的属性模板：
- wood(木材): 基础建材，价值最低
- stone(石材): 中级建材，价值中等
- ore(矿石): 高级材料，价值最高

每种资源包含：
- name: 中文显示名
- sell_price: 每个资源的售卖价格（金币）
- color: 渲染颜色 RGB
"""

# 资源模板：name(显示名), sell_price(售卖单价/个)
RESOURCES = {
    "wood": {
        "name": "木材",
        "sell_price": 3,
        "color": (139, 90, 43),
    },
    "stone": {
        "name": "石材",
        "sell_price": 5,
        "color": (160, 160, 160),
    },
    "ore": {
        "name": "矿石",
        "sell_price": 10,
        "color": (200, 180, 50),
    },
}
