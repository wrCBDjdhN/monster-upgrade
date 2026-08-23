"""生成简单的启动音效（纯Python实现，无需额外依赖）"""

import struct
import wave
import math
import os


def generate_splash_sound(output_path: str, duration: float = 3.0, sample_rate: int = 44100):
    """生成一个丰富的启动音效
    
    音效描述：
    - 前1秒：柔和的上升音调（品牌展示）
    - 1-2秒：清脆的叮当声（装饰元素出现）
    - 2-3秒：宏大的和弦结束（光效过渡）
    """
    num_samples = int(duration * sample_rate)
    
    # 生成音频数据
    samples = []
    for i in range(num_samples):
        t = i / sample_rate
        sample = 0.0
        
        # 阶段1：0-1秒，柔和上升音调
        if t < 1.0:
            freq = 330 + (330 * t)  # 从330Hz上升到660Hz
            envelope = t * 0.5  # 线性淡入
            sample = math.sin(2 * math.pi * freq * t) * envelope * 0.4
            # 添加泛音
            sample += math.sin(2 * math.pi * freq * 1.5 * t) * envelope * 0.2
        
        # 阶段2：1-2秒，清脆叮当声
        elif t < 2.0:
            # 多个频率叠加产生叮当声
            freq1 = 880
            freq2 = 1100
            freq3 = 1320
            envelope = 0.7 * (1 - (t - 1.0))  # 逐渐衰减
            sample = math.sin(2 * math.pi * freq1 * t) * envelope * 0.3
            sample += math.sin(2 * math.pi * freq2 * t) * envelope * 0.2
            sample += math.sin(2 * math.pi * freq3 * t) * envelope * 0.15
            # 添加一些"闪烁"效果
            shimmer = math.sin(2 * math.pi * 20 * t) * 0.5 + 0.5
            sample *= (0.8 + shimmer * 0.4)
        
        # 阶段3：2-3秒，宏大的和弦结束
        else:
            # C大调和弦：C4(262Hz) + E4(330Hz) + G4(392Hz)
            progress = (t - 2.0) / 1.0
            envelope = 0.8 * (1 - progress * 0.7)  # 衰减
            sample = math.sin(2 * math.pi * 262 * t) * envelope * 0.3
            sample += math.sin(2 * math.pi * 330 * t) * envelope * 0.25
            sample += math.sin(2 * math.pi * 392 * t) * envelope * 0.2
            # 添加高八度泛音增加亮度
            sample += math.sin(2 * math.pi * 524 * t) * envelope * 0.15
        
        # 全局淡入淡出
        fade_in = min(1.0, t * 5)  # 0.2秒淡入
        fade_out = min(1.0, (duration - t) * 3)  # 0.33秒淡出
        sample *= fade_in * fade_out
        
        # 限制范围
        sample = max(-1.0, min(1.0, sample))
        samples.append(sample)
    
    # 写入WAV文件
    with wave.open(output_path, 'w') as wav_file:
        wav_file.setnchannels(1)  # 单声道
        wav_file.setsampwidth(2)  # 16位
        wav_file.setframerate(sample_rate)
        
        # 将浮点样本转换为16位整数
        for sample in samples:
            # 限制范围并转换
            value = int(sample * 32767)
            value = max(-32768, min(32767, value))
            wav_file.writeframes(struct.pack('<h', value))
    
    print(f"音效已生成: {output_path}")


if __name__ == "__main__":
    # 生成启动音效
    output_dir = os.path.join(os.path.dirname(__file__), "..", "assets", "sounds")
    os.makedirs(output_dir, exist_ok=True)
    
    output_path = os.path.join(output_dir, "splash.wav")
    generate_splash_sound(output_path, duration=2.0)
