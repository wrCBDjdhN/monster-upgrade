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
from config import DOWNED_TIMEOUT, PLAYER_SIZE, RESCUE_DISTANCE, RESCUE_DURATION, WINDOW_WIDTH


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
          但"等待"不会无限持续：主机每帧在本判定**之前**先跑 `_settle_no_rescuer`
          （口径 A：场上无可救援者 → 倒地者立即真死、status 转 dead），
          60 秒超时真死由 `_update_downed_timers` 负责，两条路径共用
          `_settle_downed_true_death`。故走到这里的 downed 只可能是"确实还有队友
          可救"的正常等待态。
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
            # 客户端倒地：不再主动上报 PLAYER_DOWNED——主机入站分发（game_view.py 主机
            # inbound 段）无该分支，只会打印「未接线消息」；主机实际靠 20Hz 幽灵轮询
            # （game_view.py 主机权威幽灵倒地检测段）在 HP 归零时自行置
            # _player_status[pid]="downed" 并广播 PLAYER_DOWNED，故客户端发送纯冗余
            # （联机审计 2026-10-01）。本地倒地副作用（downed 标志/_downed_players[0]/
            # _spectating/提示文字）均在上方本地完成，不依赖该消息。
            pass  # 保留空分支以维持 net_mode 分流结构（无待发送消息）
        floating_texts.add(self.gv.player.center_x, self.gv.player.center_y + 60,
                           f"你已倒地！等待队友救援（{DOWNED_TIMEOUT}秒超时）",
                           arcade.color.ORANGE, life=3.0, font_size=16)

    # ── 倒地真死结算（口径 A：场上无可救援者 → 不等 60 秒，立即按正常死亡结算）──

    def _has_rescuer_for(self, pid: int) -> bool:
        """场上是否存在能救 pid 的玩家（"无可救援者"判定的唯一口径）

        可救援者 = 非 pid 本人、且本局状态为 "alive"（未倒地/未撤离/未阵亡/未离开）。
        候选全集 = 主机自己(0) + 房间内在线连接玩家（net_server.player_ids）；
        未登记进 `_player_status` 的玩家按 "alive" 默认口径计算（与 `_check_all_finished`
        的 `.get(pid, "alive")` 同口径）——在线但状态未登记者仍视为可实施救援。

        关键推论（决定了本检查放在主机 on_update 的哪个位置）：
        主机自己存活时恒是任意客户端的救援者，故「主机活着 + 队友倒地」永远判为
        有救援者（不会误杀队友）；只有主机自己也结束/倒地，或其他人全部撤离/阵亡/
        离开时，才会判为无救援者。
        """
        gs = self.gv.window.game_state
        candidates: set[int] = {0}
        server = gs.net_server
        if server is not None:
            candidates.update(server.player_ids)
        for other in candidates:
            if other == pid:
                continue
            if self.gv._player_status.get(other, "alive") == "alive":
                return True
        return False

    def _settle_downed_true_death(self, pid: int, reason: str) -> None:
        """倒地真死统一结算：60 秒超时 / 无可救援者 两条路径共用（消除重复实现）

        结算口径（三段，各端等价）：
        - 清倒地账本 `_downed_players[pid]` + 置 `_player_status[pid] = "dead"`
          （"dead" 即 `_check_all_finished` 认定的"已结束"，主机据此收口广播 ROOM_ENDED）；
        - pid != 0 且当前为主机 → 单播 PLAYER_DEATH，客户端走 `_apply_player_death`
          落地（清本局携带物 + 进观战）；
        - pid == 0（主机自己）→ **不依赖 send_to(0)**：net/server.py `_enqueue_send_to`
          对 player_id 0 恒丢弃（主机不在 room.players 内），该调用恒无效。改为直接
          执行本地等效结算：清本局携带物 + 清倒地标志 + `_enter_spectate("dead")`
          （与 `_fail_run` 主机分支同一落地口径），随后由 `_check_all_finished` 收口。
        """
        gs = self.gv.window.game_state
        self.gv._downed_players.pop(pid, None)
        self.gv._player_status[pid] = "dead"
        if pid == 0:
            # 主机自己真死：本地等效结算（send_to(0) 恒无效，见 docstring）
            if self.gv.player is not None:
                self.gv.player.downed = False
                self.gv.player.downed_timer = 0.0
            self.gv._clear_run_equipment(gs)
            self.gv._enter_spectate("dead")
            print(f"[GameView] 玩家 {pid} 倒地真死（{reason}），已本地结算进观战")
            return
        if gs.net_mode == "host" and gs.net_server is not None:
            gs.net_server.send_to(pid, MsgType.PLAYER_DEATH, {
                "player_id": pid, "killer_id": None,
            })
        print(f"[GameView] 玩家 {pid} 倒地真死（{reason}）")

    def _settle_no_rescuer(self) -> None:
        """无可救援者立即结算（用户口径 A 方案）：不等 60 秒超时，按正常死亡收口

        主机每帧调用（on_update 紧邻 `_check_all_finished` 之前）：任一倒地玩家若场上
        已不存在任何"非本人且存活"的玩家（他人全部撤离/阵亡/离开，或**倒地瞬间场上
        就没人**，如主机单人开局）→ 立即走与 60 秒超时真死完全相同的结算路径，
        丢失本局携带物并进观战；随后由既有 `_check_all_finished` 判定全员结束 →
        广播 ROOM_ENDED 回房。已撤离玩家 status 保持 "evac"，不受影响。

        结算后本方法的剩余循环项由 `_has_rescuer_for` 重新判定（该倒地者已转 dead，
        不再是任何人的救援者），故同一帧内可连续收口多名倒地者。
        """
        if not self.gv._downed_players:
            return
        for pid in list(self.gv._downed_players.keys()):
            # 账本残留兜底：账本与状态不一致时（断线感知把玩家标 left 但未清账本等）
            # 只清账本、不走真死结算——避免向已离线玩家单播 PLAYER_DEATH（server 会告警
            # 丢弃）、避免把 left 覆写成 dead、避免残留一条永不超时的"需要救援"渲染标记。
            if self.gv._player_status.get(pid) != "downed":
                self.gv._downed_players.pop(pid, None)
                continue
            if self._has_rescuer_for(pid):
                continue
            self._settle_downed_true_death(pid, reason="场上无可救援者")

    def _update_downed_timers(self, dt: float) -> None:
        """倒地倒计时推进（联机模式每帧调用）：逐倒地玩家递减，超时真死

        **倒计时的唯一实现处**（原 GameView.on_update 内联循环 + 主机幽灵轮询内联
        循环 + 本方法三处重复，现全部收口到本方法）：
        - 主机：账本 `_downed_players` 即权威倒计时（含主机自己 pid 0），超时即真死；
        - 客户端：账本只有远端幽灵，仅供本端表现层递减（渲染的倒计时/透明度）；
          客户端本地玩家不在账本内（`_player_downed` 的 client 分支不建本地账本），
          其倒计时完全由主机裁决——主机结算后单播 PLAYER_DEATH 落地；
        - 真死结算统一走 `_settle_downed_true_death`（与"无可救援者立即结算"共用）。
        """
        for pid in list(self.gv._downed_players.keys()):
            dp = self.gv._downed_players[pid]
            dp["timer"] -= dt
            if dp["timer"] <= 0:
                self._settle_downed_true_death(pid, reason=f"倒地超时（{int(DOWNED_TIMEOUT)}秒）")
        self._sync_local_downed_timer(dt)

    def _sync_local_downed_timer(self, dt: float) -> None:
        """把本机倒地倒计时同步到 `player.downed_timer`（该字段的表现层唯一读点）

        `player.downed_timer` 此前只写不读（写点在 `_player_downed` 与两个复活回执），
        本方法给它接上唯一的读出口——本机头顶倒计时标签：
        - 主机：自身倒地账本 `_downed_players[0]` 存在 → 直接取权威值（**不再二次递减**，
          避免双计时）；且此时 rendering 的倒地渲染已在同一坐标画了倒计时，故不重复画；
        - 客户端：自身不在倒地账本内 → 按同一 dt 递减本地镜像（仅表现，真死以主机为准），
          并补一条头顶倒计时标签——此前客户端本机倒地完全没有倒计时显示
          （账本内无自身条目，rendering 画不到自己）。
        标签复用既有 `_world_labels` 世界坐标→屏幕坐标通道（rendering.py 末尾统一
        绘制并做屏幕裁剪），不新增 HUD 布局。
        """
        player = self.gv.player
        if player is None or not player.downed:
            return
        gs = self.gv.window.game_state
        # 本机在倒地账本内的键：主机恒为 0（大厅约定），客户端为自己 id
        own_pid = 0 if gs.net_mode != "client" else getattr(gs, "net_player_id", 0)
        dp = self.gv._downed_players.get(own_pid)
        if dp is not None:
            # 主机：账本为权威值，同步过来（账本已在 _update_downed_timers 递减）
            player.downed_timer = max(0.0, float(dp["timer"]))
            return
        # 客户端：本地镜像递减 + 头顶倒计时标签
        player.downed_timer = max(0.0, player.downed_timer - dt)
        timer = player.downed_timer
        timer_color = (arcade.color.GREEN if timer > 20
                       else (arcade.color.ORANGE if timer > 10 else arcade.color.RED))
        self.gv._world_labels.append((
            player.center_x, player.center_y + PLAYER_SIZE + 18,
            f"救援 {int(timer)}s", timer_color, 10,
        ))

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
            # 救援完成：先把目标 id 缓存到局部变量，再清空 _rescue_target
            # 修复（P0 救援链路 100% 失效，联机审计 2026-10-01）：原实现先把
            # _rescue_target 置 None，随后主机路径（target_id）与客户端路径（target_id）
            # 才读取该属性 → target_id 恒为 None → 主机 _handle_rescue_request 内
            # `_player_status.get(None) != "downed"` 直接 return，RESCUE_RESULT /
            # PLAYER_REVIVED 永不产生，队友永远救不起来。
            # 注：dp 是倒地玩家信息（仅 x/y/timer 键），`dp.get("target_id")` 兜底恒为
            # None，故直接用 _downed_players 的键（倒地玩家 id）作为 target_id。
            target_id = self.gv._rescue_target
            self.gv._rescuing = False
            self.gv._rescue_target = None
            if gs.net_mode == "host":
                # 主机直接执行救援
                self.gv._handle_rescue_request(0, {
                    "rescuer_id": 0,
                    "target_id": target_id,
                })
            elif gs.net_mode == "client":
                gs.net_client.send((MsgType.RESCUE_REQUEST, {
                    "rescuer_id": gs.net_player_id,
                    "target_id": target_id,
                }))