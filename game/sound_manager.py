"""音效系统 - 使用 pyglet.media 播放程序化合成音效

无需任何外部音频文件，也不依赖 numpy：用标准库 wave 在内存中合成 WAV，
再通过 arcade/pyglet 自带的 pyglet.media 播放（非阻塞、跨平台）。
"""

import io
import wave
import math
import struct
import threading

# pyglet 已由 arcade 依赖安装，pyglet.media 可在 arcade 事件循环中播放
try:
    import pyglet
    from pyglet.media import Player
    HAS_PYGLET = True
except Exception:
    HAS_PYGLET = False


class SoundManager:
    """音效管理器 - 程序化合成并播放音效"""

    def __init__(self):
        self._enabled = True
        self._sample_rate = 22050
        self._volume = 0.4
        self._players = []          # 复用的 Player 池
        self._lock = threading.Lock()
        # 预生成常用音色，避免每次都即时合成造成卡顿
        self._cache = {}

    # ------------------------------------------------------------------ #
    # 合成工具
    # ------------------------------------------------------------------ #
    def _synth(self, freq, duration, volume=0.3, wave_type='sine',
               sweep_to=None, noise=0.0):
        """合成单段音效，返回 WAV 字节流（BytesIO）"""
        sr = self._sample_rate
        n = max(1, int(sr * duration))
        fade = int(sr * 0.008)
        buf = bytearray()
        f0 = float(freq)
        f1 = float(sweep_to) if sweep_to else f0
        for i in range(n):
            t = i / n
            # 频率扫频（用于嗖/下滑等效果）
            freq_i = f0 + (f1 - f0) * t
            if wave_type == 'square':
                s = 1.0 if math.sin(2 * math.pi * freq_i * (i / sr)) >= 0 else -1.0
            elif wave_type == 'sawtooth':
                s = 2.0 * ((freq_i * (i / sr)) % 1.0) - 1.0
            elif wave_type == 'triangle':
                ph = (freq_i * (i / sr)) % 1.0
                s = 4.0 * abs(ph - 0.5) - 1.0
            else:  # sine
                s = math.sin(2 * math.pi * freq_i * (i / sr))
            # 白噪声层（用于打击/爆破质感）
            if noise > 0.0:
                s = s * (1.0 - noise) + (2.0 * ((i * 1103515245 + 12345) % 65536) / 65536.0 - 1.0) * noise
            # 包络（淡入淡出，避免爆音）
            env = 1.0
            if i < fade:
                env = i / fade
            elif i > n - fade:
                env = (n - i) / fade
            v = int(32767 * volume * env * s)
            if v > 32767:
                v = 32767
            elif v < -32767:
                v = -32767
            buf += struct.pack('<h', v)
        w = io.BytesIO()
        ww = wave.open(w, 'wb')
        ww.setnchannels(1)
        ww.setsampwidth(2)
        ww.setframerate(sr)
        ww.writeframes(bytes(buf))
        ww.close()
        w.seek(0)
        return w

    def _play(self, wav_bytes):
        """用 pyglet.media 播放一段 WAV（非阻塞）"""
        if not self._enabled or not HAS_PYGLET:
            return
        try:
            src = pyglet.media.load('sfx.wav', file=wav_bytes, streaming=False)
            with self._lock:
                # 取一个空闲的 player，否则新建
                player = None
                for p in self._players:
                    if p.time >= (p.source.duration if p.source else 0) and not p.playing:
                        player = p
                        break
                if player is None:
                    if len(self._players) < 16:
                        player = pyglet.media.Player()
                        self._players.append(player)
                    else:
                        player = self._players[0]
                player.queue(src)
                player.play()
        except Exception:
            pass  # 音频故障不应影响游戏逻辑

    def _emit(self, key, freq, dur, vol=0.3, wave='sine', sweep_to=None, noise=0.0):
        """合成并播放（带缓存）"""
        if not self._enabled:
            return
        ck = (key, freq, dur, vol, wave, sweep_to, noise)
        wav = self._cache.get(ck)
        if wav is None:
            wav = self._synth(freq, dur, vol * self._volume, wave, sweep_to, noise)
            self._cache[ck] = wav
        else:
            wav.seek(0)
        self._play(wav)

    # ------------------------------------------------------------------ #
    # 公开音效 API（保持与原有调用一致）
    # ------------------------------------------------------------------ #
    def play_attack(self):
        """攻击音效 - 短促的刮擦声"""
        self._emit('attack1', 820, 0.05, 0.25, 'sawtooth')
        self._emit('attack2', 620, 0.08, 0.18, 'square')

    def play_ranged_attack(self):
        """远程攻击音效 - 嗖的一声（频率下滑）"""
        self._emit('ranged', 1300, 0.12, 0.2, 'sine', sweep_to=500)

    def play_pickup(self):
        """拾取音效 - 通用上升音调"""
        self._emit('pickup1', 523, 0.05, 0.22, 'sine')   # C5
        self._emit('pickup2', 784, 0.08, 0.22, 'sine')   # G5

    def play_heal(self):
        """治疗/护盾音效 - 柔和上升双音（骑士圣盾庇护开启）"""
        self._emit('heal1', 523, 0.08, 0.2, 'sine', sweep_to=784)    # C5 → G5 柔滑上扬
        self._emit('heal2', 784, 0.16, 0.2, 'sine', sweep_to=1047)   # G5 → C6 余韵

    def play_resource_pickup(self, kind=None):
        """资源拾取音效 - 木材/石头/矿石各有不同音色

        kind: 'wood'(木材) / 'stone'(石头) / 'ore'(矿石)，其余归为通用资源音
        """
        if kind == 'wood':
            # 木材：温润、低沉的木质敲击（钝、短促）
            self._emit('wood1', 392, 0.06, 0.24, 'triangle')   # G4
            self._emit('wood2', 523, 0.09, 0.18, 'sine')       # C5
        elif kind == 'stone':
            # 石头：坚硬、带噪声的清脆敲击
            self._emit('stone1', 300, 0.05, 0.28, 'square', noise=0.4)
            self._emit('stone2', 600, 0.07, 0.18, 'triangle')  # D5
        elif kind == 'ore':
            # 矿石：明亮金属泛音的叮当声
            self._emit('ore1', 988, 0.05, 0.24, 'sine')        # B5
            self._emit('ore2', 1319, 0.14, 0.2, 'triangle')    # E6 余韵
        else:
            # 通用资源音（与之前一致）
            self._emit('res1', 587, 0.05, 0.22, 'triangle')    # D5
            self._emit('res2', 880, 0.09, 0.2, 'triangle')     # A5

    def play_weapon_pickup(self):
        """武器拾取音效 - 金属上扬的双音（英雄感）"""
        self._emit('wpn1', 740, 0.06, 0.24, 'square')    # D#5
        self._emit('wpn2', 988, 0.12, 0.22, 'sine')      # B5

    def play_equip_pickup(self):
        """头盔/护甲拾取音效 - 金属铠甲铿锵声（带噪声敲击）"""
        self._emit('eqp1', 440, 0.07, 0.28, 'square', noise=0.35)
        self._emit('eqp2', 660, 0.1, 0.22, 'triangle')   # E5

    def play_evac(self):
        """撤离成功音效 - 胜利号角般的上升和弦"""
        self._emit('evac1', 523, 0.12, 0.25, 'sine')     # C5
        self._emit('evac2', 659, 0.12, 0.25, 'sine')     # E5
        self._emit('evac3', 784, 0.18, 0.28, 'sine')     # G5
        self._emit('evac4', 1047, 0.3, 0.26, 'triangle') # C6 收尾

    def play_gold_pickup(self):
        """金币拾取 - 清脆叮铃声"""
        self._emit('gold1', 1047, 0.05, 0.25, 'triangle')  # C6
        self._emit('gold2', 1568, 0.09, 0.2, 'triangle')   # G6

    def play_hurt(self):
        """受伤音效 - 低沉撞击 + 噪声"""
        self._emit('hurt1', 160, 0.15, 0.35, 'square', noise=0.25)
        self._emit('hurt2', 90, 0.12, 0.25, 'sawtooth', noise=0.2)

    def play_death(self):
        """死亡音效 - 下降音调"""
        self._emit('death1', 420, 0.12, 0.3, 'sine', sweep_to=180)
        self._emit('death2', 200, 0.25, 0.25, 'sawtooth', sweep_to=80)

    def play_projectile(self):
        """弹丸音效 - 轻微嗖声"""
        self._emit('proj', 1500, 0.04, 0.15, 'sine', sweep_to=900)

    def play_monster_hit(self):
        """怪物受击 - 短促碰撞 + 噪声"""
        self._emit('mhit', 320, 0.06, 0.25, 'square', noise=0.3)

    def play_level_up(self):
        """升级音效 - 上升和弦"""
        self._emit('lvl1', 523, 0.1, 0.22, 'sine')   # C5
        self._emit('lvl2', 659, 0.1, 0.22, 'sine')   # E5
        self._emit('lvl3', 784, 0.16, 0.26, 'sine')  # G5

    def play_upgrade(self):
        """装备升级音效 - 双音叮咚"""
        self._emit('upg1', 880, 0.08, 0.25, 'triangle')
        self._emit('upg2', 1175, 0.14, 0.25, 'triangle')  # D6

    def play_ui(self):
        """UI 点击/确认音效"""
        self._emit('ui', 660, 0.04, 0.18, 'sine')

    def set_enabled(self, enabled: bool):
        """启用/禁用音效"""
        self._enabled = enabled

    def set_volume(self, volume: float):
        """设置主音量 (0.0 - 1.0)"""
        self._volume = max(0.0, min(1.0, float(volume)))

    def play_rocket_launch(self):
        """火箭发射音效 - 低沉隆隆声 + 频率上升"""
        self._emit('rocket1', 80, 0.4, 0.35, 'sawtooth', sweep_to=200, noise=0.3)
        self._emit('rocket2', 120, 0.6, 0.25, 'square', sweep_to=400, noise=0.2)

    def play_explosion(self):
        """爆炸音效 - 撞击+噪声+低沉余波"""
        self._emit('explode1', 200, 0.12, 0.4, 'square', noise=0.5)
        self._emit('explode2', 80, 0.3, 0.3, 'sawtooth', sweep_to=40, noise=0.3)


# 全局实例
sound_manager = SoundManager()
