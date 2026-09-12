"""SpectateManager：观战/倒地/救援系统逻辑管理器（从 GameView 抽取）

将 views/game_view.py 中观战模式（进入观战/切换目标/相机跟随/全员结束判定）
与倒地/救援系统（倒地状态/倒地超时计时/救援读条）相关方法独立成管理器，
GameView 通过持有本管理器实例（self.spectate）调用，降低 God-class 复杂度。

所有方法保持与 GameView 原实现完全一致，仅将 GameView 实例属性引用改为 self.gv。
"""

import math
import arcade
from net.protocol import MsgType
from game.effects import floating_texts
from config import DOWNED_TIMEOUT, RESCUE_DISTANCE, RESCUE_DURATION, WINDOW_WIDTH


class SpectateManager:
    """观战/倒地/救援系统管理器：封装 GameView 中观战与倒地救援相关逻辑

    构造时保存 GameView 引用（self.gv），方法内对 GameView 实例属性的访问
    一律经 self.gv 转发（self.player → self.gv.player 等）。
    """

    def __init__(self, game_view):
        """保存 GameView 引用，供各观战/倒地救援方法转发实例属性访问"""
        self.gv = game_view

    def _enter_spectate(self, outcome: str) -> None:
        """主机撤离/死亡后进入观战模式：房间保留、后台继续模拟，禁操作禁结算

        - outcome: "evac"（撤离成功）/ "dead"（死亡/撤离失败/超时）——写入 _player_status[0]，
          供全员结束判定使用；
        - 观战期间世界继续模拟（怪物 AI/掉落/快照照常），主机玩家不再受攻击、不再结算；
        - 相机默认跟随观战目标（V 键循环切换，见 input_handler）。
        """
        self.gv._spectating = True
        # 观战期 input_handler 早退（handle_key_release/handle_mouse_release 被观战守卫拦截）
        # 导致 _left_mouse_held/_chest_key_pressed 无法复位，这里手动清零：
        # 双保险防止幽灵持续攻击/拾取（修复客户端观战崩溃，配合 on_update 的观战守卫）
        self.gv._left_mouse_held = False
        self.gv._chest_key_pressed = False
        # 防御性加固（主机观战崩溃排查）：观战期不再攻击，清空残留攻击状态——
        # _attack_debuffs 残留会导致观战期间意外广播陈旧 debuff（引用已释放的装备效果），
        # _attack_this_frame 残留会让观战期多结算一帧近战伤害
        self.gv._attack_debuffs = []
        self.gv._attack_this_frame = False
        # 观战模式：相机改由观战段控制跟随幽灵，禁止 controller.update() 每帧拉回
        # 已撤离/阵亡的静止玩家（否则观战视角卡死在撤离点，修复场景2）
        self.gv.controller.follow_player = False
        # 默认观战目标 = 第一个存活客户端幽灵（修复：之前默认 0（自己玩家）导致相机跟随
        # 撤离点静止的玩家，视角卡死在撤离点无法移动）
        first_ghost = next(
            (pid for pid, g in self.gv.remote_players.items()
             if getattr(g, "alive", True) and self.gv._player_status.get(pid, "alive") == "alive"),
            None,
        )
        self.gv._spectate_target_id = first_ghost if first_ghost is not None else None
        self.gv._player_status[0] = outcome
        print(f"[GameView] 主机进入观战模式: {outcome}")
        # 观战提示（撤离成功/阵亡分流文案）
        tip = ("你已撤离，进入观战模式（V 键切换视角）" if outcome == "evac"
               else "你已阵亡，进入观战模式（V 键切换视角）")
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60, tip,
                           arcade.color.GOLD if outcome == "evac" else arcade.color.RED,
                           life=3.0, font_size=16)

    def _cycle_spectate_target(self) -> None:
        """V 键：循环切换观战跟随目标（仅存活客户端幽灵，不包含自己玩家）

        修复：原候选含 id=0（自己玩家），观战时自己已撤离/阵亡且不在幽灵表，
        跟随自己会卡在撤离点视角；观战目标应始终为存活客户端玩家。
        """
        if not self.gv._spectating:
            return
        # 候选：所有存活幽灵（alive 且状态为 alive）
        alive_ids = []
        for pid, ghost in self.gv.remote_players.items():
            if (getattr(ghost, "alive", True)
                    and self.gv._player_status.get(pid, "alive") == "alive"):
                alive_ids.append(pid)
        if not alive_ids:
            return
        cur = self.gv._spectate_target_id if self.gv._spectate_target_id in alive_ids else alive_ids[0]
        idx = alive_ids.index(cur)
        self.gv._spectate_target_id = alive_ids[(idx + 1) % len(alive_ids)]

    def _spectate_camera_target(self) -> "Player | None":
        """观战跟随目标实体：目标 id 对应的幽灵（存活优先），无效则回退第一个存活幽灵

        修复：之前无效时回退 self.player（自己玩家），观战时自己已撤离停在撤离点，
        相机跟随静止玩家导致视角卡死；观战应始终跟随客户端幽灵（其位置由
        PLAYER_SNAPSHOT 20Hz 同步，跟随即同步该玩家实时视角）。
        修复2：回退循环补 _player_status==alive 过滤（与 _enter_spectate/_cycle_spectate_target
        一致）——否则会跟随「已撤离/已阵亡」的冻结幽灵（alive 属性仍为 True，
        但位置已停在撤离点不再移动），导致视角再次卡死；且无存活幽灵时返回
        None 而非 self.player（自己已撤离静止，跟随自己仍是卡死）。
        """
        tid = self.gv._spectate_target_id
        if tid is not None and tid in self.gv.remote_players:
            ghost = self.gv.remote_players[tid]
            if (getattr(ghost, "alive", True)
                    and self.gv._player_status.get(tid, "alive") == "alive"):
                return ghost
        # 目标缺失/死亡/已撤离：回退到第一个存活且未结束的幽灵（无则返回 None，相机保持原位）
        for pid, ghost in self.gv.remote_players.items():
            if (getattr(ghost, "alive", True)
                    and self.gv._player_status.get(pid, "alive") == "alive"):
                return ghost
        return None

    def _check_all_finished(self) -> bool:
        """主机观战期间全员结束判定：主机已结束（evac/dead）+ 全部客户端结束/离开

        - 满足条件时广播 ROOM_ENDED(all_finished)（房间保留，回房等待再次开局）；
        - 断线客户端：server.player_ids 不再包含 → 标记 left（此处按在线玩家判定）；
        - 注意：downed 玩家不视为"已结束"——游戏继续等待其被救或超时真死。
        """
        gs = self.gv.window.game_state
        if not self.gv._spectating or gs.net_mode != "host" or gs.net_server is None:
            return False
        if self.gv._player_status.get(0, "alive") not in ("evac", "dead"):
            return False  # 主机本局未结束：不判定
        # 在线玩家全部结束（evac/dead/left 之一）即全员结束
        # 注意：downed 玩家不计入"已结束"，游戏继续等待救援或超时
        for pid in gs.net_server.player_ids:
            if pid == 0:
                continue  # 主机自己已在上方判定
            status = self.gv._player_status.get(pid, "alive")
            if status not in ("evac", "dead", "left"):
                return False  # 还有玩家存活或倒地：不结束
        return True

    def _player_downed(self) -> None:
        """本地玩家进入倒地状态（联机模式）：HP=0 但保留装备，等待队友救援

        倒地期间：不能移动/攻击/拾取，头顶显示倒计时，队友靠近按 E 可救援。
        超时未被救或主动退出观战 → 真死（清装备 + 观战）。
        """
        gs = self.gv.window.game_state
        if self.gv._spectating:
            return
        # 标记倒地状态
        self.gv.player.downed = True
        self.gv.player.downed_timer = DOWNED_TIMEOUT
        self.gv._spectating = True  # 复用观战标志：屏蔽操作/渲染本体
        self.gv.controller.follow_player = False
        self.gv._left_mouse_held = False
        self.gv._chest_key_pressed = False
        # 通知主机（主机模式下直接处理，客户端模式下发给主机）
        if gs.net_mode == "host":
            # 主机本地玩家倒地：广播给所有客户端
            self.gv._player_status[0] = "downed"
            self.gv._downed_players[0] = {
                "x": self.gv.player.center_x, "y": self.gv.player.center_y,
                "timer": DOWNED_TIMEOUT,
            }
            gs.net_server.broadcast(MsgType.PLAYER_DOWNED, {
                "player_id": 0,
                "x": self.gv.player.center_x, "y": self.gv.player.center_y,
            })
        elif gs.net_mode == "client":
            # 客户端倒地：通知主机（主机权威裁决）
            gs.net_client.send((MsgType.PLAYER_DOWNED, {
                "player_id": gs.net_player_id,
                "x": self.gv.player.center_x, "y": self.gv.player.center_y,
            }))
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           f"你已倒地！等待队友救援（{DOWNED_TIMEOUT}秒超时）",
                           arcade.color.ORANGE, life=3.0, font_size=16)

    def _update_downed_timers(self, dt: float) -> None:
        """更新倒地玩家计时器（远程玩家倒地超时，每帧调用）

        从 GameView.on_update 的倒地/救援系统更新段抽取：逐倒地玩家递减超时计时器，
        超时未获救 → 真死（标记 dead，主机单播 PLAYER_DEATH 通知该玩家）。
        """
        gs = self.gv.window.game_state
        for pid in list(self.gv._downed_players.keys()):
            dp = self.gv._downed_players[pid]
            dp["timer"] -= dt
            if dp["timer"] <= 0:
                # 超时真死
                del self.gv._downed_players[pid]
                self.gv._player_status[pid] = "dead"
                if gs.net_mode == "host":
                    gs.net_server.send_to(pid, MsgType.PLAYER_DEATH, {
                        "player_id": pid, "killer_id": None,
                    })
                print(f"[GameView] 玩家 {pid} 倒地超时，已阵亡")

    def _update_rescue(self, dt: float) -> None:
        """更新救援读条（每帧调用）"""
        if not self.gv._rescuing or self.gv._rescue_target is None:
            return
        gs = self.gv.window.game_state
        # 检查目标是否仍在倒地状态
        if self.gv._rescue_target not in self.gv._downed_players:
            self.gv._rescuing = False
            self.gv._rescue_target = None
            return
        # 检查距离是否仍在范围内
        dp = self.gv._downed_players[self.gv._rescue_target]
        dist = math.hypot(
            self.gv.player.center_x - dp["x"],
            self.gv.player.center_y - dp["y"],
        )
        if dist > RESCUE_DISTANCE:
            self.gv._rescuing = False
            self.gv._rescue_target = None
            floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 40,
                               "救援中断：距离太远",
                               arcade.color.ORANGE, life=1.0, font_size=12)
            return
        # 递增读条
        self.gv._rescue_timer += dt
        self.gv._rescue_progress = min(1.0, self.gv._rescue_timer / RESCUE_DURATION)
        if self.gv._rescue_timer >= RESCUE_DURATION:
            # 救援完成：发送请求给主机
            self.gv._rescuing = False
            self.gv._rescue_target = None
            if gs.net_mode == "host":
                # 主机直接执行救援
                self.gv._handle_rescue_request(0, {
                    "rescuer_id": 0,
                    "target_id": self.gv._rescue_target or dp.get("target_id"),
                })
            elif gs.net_mode == "client":
                gs.net_client.send((MsgType.RESCUE_REQUEST, {
                    "rescuer_id": gs.net_player_id,
                    "target_id": self.gv._rescue_target,
                }))