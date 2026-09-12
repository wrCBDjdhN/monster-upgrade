"""EvacManager：撤离/失败结算逻辑管理器（从 GameView 抽取）

将 GameView 中撤离相关方法（撤离成功入库/失败清装/药水折叠/装备并入携带物）
独立成管理器，GameView 通过 self.evac_manager 持有本管理器。
所有方法保持与 GameView 原实现完全一致，仅将 GameView 实例属性引用改为 self.gv。
"""

import arcade
from game.evac import commit_run_to_warehouse, clear_run
from game.entity_callbacks import _award_exp
from game.effects import particle_system, floating_texts
from game.sound_manager import sound_manager
from config import EXP_EVAC, LIFESTEAL_DEFAULT, SPREAD_COUNT_DEFAULT, SPREAD_ANGLE_DEFAULT
from net.protocol import MsgType
from db.database import clear_run_equipment
from entities.equipment_defs import POTIONS


class EvacManager:
    """撤离/失败结算管理器：封装 GameView 中全部撤离相关逻辑

    构造时保存 GameView 引用（self.gv），方法内对 GameView 实例属性的访问
    一律经 self.gv 转发（self.player → self.gv.player 等）。
    """

    def __init__(self, game_view):
        """保存 GameView 引用，供各撤离方法转发实例属性访问"""
        self.gv = game_view

    def _fold_run_potions(self, gs: "GameState") -> None:
        """撤离前将本局药水槽（run_potions）并入 run_carried["potion"] 以便入库

        单机/主机本局拾取的药水存于 run_potions（不占容量、上限 RUN_POTION_SLOTS），
        撤离时并入 run_carried 的 potion 键走统一入库口径（commit_run_to_warehouse）。
        （联机客户端不走本函数：客户端药水拾取已被主机记录进 EVAC_RESULT 载荷，
        本地 run_potions 直接清空即可，避免重复入库。）
        """
        run_potions = getattr(gs, "run_potions", None)
        if not run_potions:
            return
        from entities.equipment_defs import POTIONS
        potion_slot = gs.run_carried.setdefault("potion", {})
        for item_id, qty in run_potions.items():
            if item_id in POTIONS and qty > 0:
                potion_slot[item_id] = potion_slot.get(item_id, 0) + qty
        run_potions.clear()

    def _clear_run_equipment(self, gs: "GameState") -> None:
        """清空本次携带数据并删除数据库中的已装备物品（死亡/撤离失败 = 丢失所有装备）

        与单机失败结算同口径；联机主机/客户端死亡与撤离失败均复用（回房等待或观战前清装）。
        """
        # 清空本次携带数据
        if hasattr(gs, 'run_carried'):
            gs.run_carried = {}
        # 本局药水槽一并清空（死亡/撤离失败 = 药水随携带物丢失）
        if hasattr(gs, 'run_potions'):
            gs.run_potions = {}
        # 从数据库中删除装备（撤离失败 = 死亡，丢失所有装备）
        pid = gs.player_id
        if pid:
            weapon_db_id = getattr(gs, 'equipped_weapon_id', None)
            clear_run_equipment(pid, weapon_db_id)
        # 重置游戏状态中的装备引用
        gs.equipped_weapon_id = None
        gs.current_weapon_kind = "melee"
        gs.current_weapon_id = None
        gs.current_weapon_item_id = None
        gs.equipped_helmet_id = None
        gs.equipped_armor_id = None
        gs.equipped_backpack_id = None
        # 重置武器扩展机制字段（死亡/清装后避免光环/吸血残留作用于观战或无武器状态）
        gs.weapon_lifesteal = LIFESTEAL_DEFAULT
        gs.weapon_spread_count = SPREAD_COUNT_DEFAULT
        gs.weapon_spread_angle = SPREAD_ANGLE_DEFAULT
        gs.weapon_aura_slow = False
        gs.weapon_aura_radius = 0

    def _fail_run(self, reason: str) -> None:
        """行动失败统一处理：超时/死亡 → 清空携带物 → 删除数据库中的装备

        单机：清装备后展示失败结算页；
        联机主机：清装备后进入观战模式（房间保留、后台模拟，等待全员结束回房）；
        联机客户端：清装备后进入观战模式（与撤离观战同路径，连接保留、等待全员结束回房）。
        """
        # 防御性检查：窗口关闭时 player 可能尚未初始化（setup 未完成），直接跳过
        if self.gv.player is None:
            return
        gs = self.gv.window.game_state
        if gs.net_mode == "host":
            # 联机主机死亡/超时：清装备 → 观战模式（不关房、不广播 ROOM_ENDED，
            # 房间生命周期与单局解耦：剩余客户端继续玩，全员结束后回房等待）
            self._clear_run_equipment(gs)
            self.gv._enter_spectate("dead")
            return
        if gs.net_mode == "client":
            # 联机客户端死亡/超时（理论经 _apply_player_death 处理，此处兜底）：
            # 清装备 → 通知主机放弃行动 → 进入观战模式（与撤离观战同路径，连接保留、等待全员结束回房）
            self._clear_run_equipment(gs)
            # 通知主机：更新 _player_status → 触发全员结束判定（修复观战者卡住）
            if gs.net_client is not None:
                gs.net_client.send((MsgType.PLAYER_ABANDON, {
                    "player_id": getattr(gs, "net_player_id", 0),
                    "reason": reason,
                }))
            # 客户端放弃 → 进入观战模式（与撤离观战同路径）：
            # - _spectating=True：渲染隐藏本体/武器/读条，input_handler 屏蔽操作，相机跟随观战目标；
            # - _spectate_target_id=None：由 _spectate_camera_target 自动回退第一个存活幽灵；
            # - 连接保留、poll 继续运行，主机 ROOM_ENDED(all_finished) 广播时经 _apply_room_ended 回房。
            gs.net_wait_reason = "dead"
            self.gv._spectating = True
            # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
            # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
            # 双保险防止幽灵持续攻击/拾取（配合 on_update 的观战守卫）
            self.gv._left_mouse_held = False
            self.gv._chest_key_pressed = False
            # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
            # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点）
            self.gv.controller.follow_player = False
            self.gv._spectate_target_id = None
            # 观战提示
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                               "你已放弃行动，进入观战模式（V 键切换视角）",
                               arcade.color.RED, life=3.0, font_size=16)
            return
        # 单机：清装备 + 失败结算页
        self._clear_run_equipment(gs)
        # 修复：不再在地图内弹出失败文字，统一使用撤离结果页面提示
        # 增强提示：粒子效果
        particle_system.emit(self.gv.player.center_x, self.gv.player.center_y, 20,
                            (255, 50, 50), speed=80, life=0.8, size=4)
        sound_manager.play_hurt()  # 使用受伤音效作为失败音效
        # 跳转到撤离结果页面
        from views.evac_result_view import EvacResultView
        self.gv.window.show_view(EvacResultView(self.gv.window_ref, success=False))