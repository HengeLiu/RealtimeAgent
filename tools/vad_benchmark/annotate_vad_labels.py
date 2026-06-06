#!/usr/bin/env python3
"""打开小窗口标注音频真实说话边界。

主要功能：读取 WAV 音频，显示波形和 RMS 音量强度曲线，支持人工标记
`speech_start_ms` 和 `speech_end_ms`，并保存为 sidecar JSON 标签文件。

主要逻辑：
1. 读取 PCM WAV，转换为 mono float waveform。
2. 计算 20ms frame / 10ms hop 的 RMS 强度，用橙色曲线显示。
3. 在 Tk canvas 中绘制当前可见窗口内的波形、RMS、游标和 start/end 标记。
4. 用户通过点击移动游标，用按钮或快捷键 `s` / `e` 设置开始和结束时间。
5. 保存 `<音频文件>.vad-label.json`，供后续 benchmark 使用人工标签。

参数：
    可选位置参数：需要打开的 WAV 文件路径。
返回值：命令行退出码。窗口正常关闭时为 0。
异常情况：不支持的 WAV 格式或文件读取失败会在界面状态栏和命令行中显示。
"""

from __future__ import annotations

import argparse
import json
import math
import tempfile
import subprocess
import sys
import time
import wave
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import numpy as np
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


CANVAS_WIDTH = 1100
CANVAS_HEIGHT = 420
DEFAULT_WINDOW_MS = 6000
MIN_WINDOW_MS = 800
MAX_WINDOW_MS = 60000


@dataclass
class AudioData:
    """保存标注工具当前打开的音频数据。

    主要功能：集中保存音频路径、采样率、波形、RMS 曲线和时长。
    主要属性：samples 是 mono float32 波形；rms_values 是归一化 RMS 强度数组。
    """

    path: Path
    sample_rate: int
    samples: np.ndarray
    duration_ms: int
    rms_values: np.ndarray
    rms_hop_ms: int


@dataclass
class LabelState:
    """保存当前人工标注状态。

    主要功能：记录游标、说话开始、说话结束和备注。
    主要属性：cursor_ms 是当前点击位置；start_ms / end_ms 是人工标签。
    """

    cursor_ms: int = 0
    start_ms: int | None = None
    end_ms: int | None = None
    notes: str = ""


class VadLabelApp:
    """VAD 人工标签小工具。

    主要功能：提供一个小窗口显示音频强度，并保存真实说话起止时间。
    主要方法：open_audio() 读取音频；redraw() 绘图；save_label() 保存标签。
    主要属性：audio 保存当前音频；label 保存人工标注；visible_start_ms 控制横向浏览。
    """

    def __init__(self, root: tk.Tk, initial_path: Path | None = None) -> None:
        """初始化界面。

        参数：
            root：Tk 根窗口。
            initial_path：可选初始音频路径。
        返回值：无。
        异常情况：初始音频打开失败时显示错误，但窗口继续保留。
        """

        self.root = root
        self.root.title("VAD Label Tool")
        self.audio: AudioData | None = None
        self.label = LabelState()
        self.visible_start_ms = 0
        self.window_ms = DEFAULT_WINDOW_MS
        self.play_process: subprocess.Popen[bytes] | None = None
        self.play_started_perf: float | None = None
        self.playhead_ms: int | None = None
        self.play_start_offset_ms = 0
        self.play_end_ms: int | None = None
        self.playback_latency_ms = 550

        self._build_ui()
        self._bind_keys()
        if initial_path:
            self.open_audio(initial_path)

    def _build_ui(self) -> None:
        """创建 Tk 界面控件。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        toolbar = ttk.Frame(self.root, padding=6)
        toolbar.pack(fill=tk.X)

        ttk.Button(toolbar, text="Open", command=self.open_dialog).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Play", command=self.play_audio).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Play Around Cursor", command=self.play_around_cursor).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Stop", command=self.stop_audio).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Offset -50", command=lambda: self.adjust_playback_latency(-50)).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Offset +50", command=lambda: self.adjust_playback_latency(50)).pack(side=tk.LEFT, padx=3)
        ttk.Separator(toolbar, orient=tk.VERTICAL).pack(side=tk.LEFT, fill=tk.Y, padx=8)
        ttk.Button(toolbar, text="Set Start (S)", command=self.set_start).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Set End (E)", command=self.set_end).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Clear", command=self.clear_label).pack(side=tk.LEFT, padx=3)
        ttk.Button(toolbar, text="Save", command=self.save_label).pack(side=tk.LEFT, padx=3)

        self.info_var = tk.StringVar(value="Open a WAV file to start.")
        ttk.Label(self.root, textvariable=self.info_var, padding=(8, 0)).pack(fill=tk.X)

        self.canvas = tk.Canvas(self.root, width=CANVAS_WIDTH, height=CANVAS_HEIGHT, bg="#101418", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True, padx=8, pady=6)
        self.canvas.bind("<Button-1>", self.on_canvas_click)
        self.canvas.bind("<B1-Motion>", self.on_canvas_click)

        notes_frame = ttk.Frame(self.root, padding=(8, 0, 8, 8))
        notes_frame.pack(fill=tk.X)
        ttk.Label(notes_frame, text="Notes").pack(side=tk.LEFT)
        self.notes_var = tk.StringVar()
        ttk.Entry(notes_frame, textvariable=self.notes_var).pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)

        self.status_var = tk.StringVar(
            value="快捷键：S=start, E=end, Cmd/Ctrl+S=save, Space=play around cursor, Shift+Space=play full"
        )
        ttk.Label(self.root, textvariable=self.status_var, padding=(8, 0, 8, 8)).pack(fill=tk.X)

    def _bind_keys(self) -> None:
        """绑定快捷键。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        self.root.bind("s", lambda _: self.set_start())
        self.root.bind("e", lambda _: self.set_end())
        self.root.bind("S", lambda _: self.set_start())
        self.root.bind("E", lambda _: self.set_end())
        self.root.bind("<Command-s>", lambda _: self.save_label())
        self.root.bind("<Control-s>", lambda _: self.save_label())
        self.root.bind("<Left>", lambda _: self.move_cursor(-50))
        self.root.bind("<Right>", lambda _: self.move_cursor(50))
        self.root.bind("<Shift-Left>", lambda _: self.move_cursor(-500))
        self.root.bind("<Shift-Right>", lambda _: self.move_cursor(500))
        self.root.bind("<space>", lambda _: self.play_around_cursor())
        self.root.bind("<Shift-space>", lambda _: self.play_audio())
        self.root.bind("[", lambda _: self.adjust_playback_latency(-50))
        self.root.bind("]", lambda _: self.adjust_playback_latency(50))

    def open_dialog(self) -> None:
        """打开文件选择框。

        参数：无。
        返回值：无。
        异常情况：用户取消时不做处理。
        """

        filename = filedialog.askopenfilename(
            title="选择 WAV 音频",
            filetypes=[("WAV files", "*.wav"), ("All files", "*.*")],
        )
        if filename:
            self.open_audio(Path(filename))

    def open_audio(self, path: Path) -> None:
        """读取音频并加载已有标签。

        参数：path，WAV 音频路径。
        返回值：无。
        异常情况：读取失败时弹出错误提示。
        """

        try:
            self.audio = read_audio(path)
            self.label = LabelState()
            self.visible_start_ms = 0
            self.window_ms = self.audio.duration_ms
            self.load_existing_label()
            self.redraw()
            self.status_var.set(f"Loaded {path}")
        except Exception as exc:  # noqa: BLE001 - GUI 工具需要显示具体错误
            self.status_var.set(f"Open failed: {exc}")
            messagebox.showerror("Open failed", str(exc))

    def load_existing_label(self) -> None:
        """读取同名 sidecar 标签。

        参数：无。
        返回值：无。
        异常情况：标签 JSON 解析失败时忽略并提示。
        """

        if not self.audio:
            return
        path = label_path_for_audio(self.audio.path)
        if not path.exists():
            return
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            self.label.start_ms = data.get("speech_start_ms")
            self.label.end_ms = data.get("speech_end_ms")
            self.label.cursor_ms = self.label.start_ms or 0
            self.label.notes = data.get("notes", "")
            self.notes_var.set(self.label.notes)
        except Exception as exc:  # noqa: BLE001
            self.status_var.set(f"Label load skipped: {exc}")

    def bring_to_front(self) -> None:
        """把窗口短暂置顶并聚焦。

        参数：无。
        返回值：无。
        异常情况：窗口管理器不支持置顶时忽略。
        """

        try:
            self.root.deiconify()
            self.root.lift()
            self.root.focus_force()
            self.root.attributes("-topmost", True)
            self.root.after(600, lambda: self.root.attributes("-topmost", False))
        except tk.TclError:
            pass

    def on_canvas_click(self, event: tk.Event[tk.Canvas]) -> None:
        """点击画布移动游标。

        参数：event，Tk 鼠标事件。
        返回值：无。
        异常情况：无。
        """

        if not self.audio:
            return
        width = max(1, self.canvas.winfo_width())
        ratio = min(1.0, max(0.0, event.x / width))
        self.label.cursor_ms = clamp_ms(int(self.visible_start_ms + ratio * self.window_ms), self.audio.duration_ms)
        self.redraw()

    def move_cursor(self, delta_ms: int) -> None:
        """按键移动游标。

        参数：delta_ms，移动毫秒数。
        返回值：无。
        异常情况：无。
        """

        if not self.audio:
            return
        self.label.cursor_ms = clamp_ms(self.label.cursor_ms + delta_ms, self.audio.duration_ms)
        self.redraw()

    def set_start(self) -> None:
        """把当前游标设置为 speech_start_ms。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        if not self.audio:
            return
        self.label.start_ms = self.label.cursor_ms
        if self.label.end_ms is not None and self.label.end_ms < self.label.start_ms:
            self.label.end_ms = None
        self.redraw()

    def set_end(self) -> None:
        """把当前游标设置为 speech_end_ms。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        if not self.audio:
            return
        self.label.end_ms = self.label.cursor_ms
        if self.label.start_ms is not None and self.label.end_ms < self.label.start_ms:
            self.status_var.set("End is earlier than start; please adjust.")
        self.redraw()

    def clear_label(self) -> None:
        """清空当前标签。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        self.label.start_ms = None
        self.label.end_ms = None
        self.redraw()

    def play_audio(self) -> None:
        """播放当前音频。

        参数：无。
        返回值：无。
        异常情况：macOS 上使用 afplay；其他平台提示未实现。
        """

        if not self.audio:
            return
        if sys.platform != "darwin":
            self.status_var.set("Playback currently uses macOS afplay only.")
            return
        self.stop_audio()
        self.play_start_offset_ms = 0
        self.play_end_ms = self.audio.duration_ms
        self.playhead_ms = 0
        self.play_started_perf = time.perf_counter()
        self.play_process = subprocess.Popen(["afplay", str(self.audio.path)])
        self.status_var.set(
            "Playing full audio. Space plays around white cursor; Shift+Space plays full audio."
        )
        self.schedule_playhead_update()

    def play_around_cursor(self) -> None:
        """播放白色游标附近的一小段音频。

        参数：无。
        返回值：无。
        异常情况：macOS 上使用 afplay；其他平台提示未实现。
        """

        if not self.audio:
            return
        if sys.platform != "darwin":
            self.status_var.set("Playback currently uses macOS afplay only.")
            return
        self.stop_audio()
        before_ms = 350
        after_ms = 1400
        start_ms = clamp_ms(self.label.cursor_ms - before_ms, self.audio.duration_ms)
        end_ms = clamp_ms(self.label.cursor_ms + after_ms, self.audio.duration_ms)
        if end_ms <= start_ms:
            return
        snippet_path = write_temp_snippet(self.audio, start_ms, end_ms)
        self.play_start_offset_ms = start_ms
        self.play_end_ms = end_ms
        self.playhead_ms = start_ms
        self.play_started_perf = time.perf_counter()
        self.play_process = subprocess.Popen(["afplay", str(snippet_path)])
        self.status_var.set(
            f"Playing around cursor: {format_ms(start_ms)} - {format_ms(end_ms)}. "
            "White cursor remains the label marker."
        )
        self.schedule_playhead_update()

    def stop_audio(self) -> None:
        """停止播放。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        if self.play_process and self.play_process.poll() is None:
            self.play_process.terminate()
        self.play_process = None
        self.play_started_perf = None
        self.play_end_ms = None

    def schedule_playhead_update(self) -> None:
        """更新播放进度线。

        参数：无。
        返回值：无。
        异常情况：播放进程结束或音频缺失时停止更新。
        """

        if not self.audio or not self.play_process or self.play_started_perf is None:
            return
        audible_elapsed_ms = max(0, int((time.perf_counter() - self.play_started_perf) * 1000) - self.playback_latency_ms)
        play_end_ms = self.play_end_ms if self.play_end_ms is not None else self.audio.duration_ms
        self.playhead_ms = min(play_end_ms, clamp_ms(self.play_start_offset_ms + audible_elapsed_ms, self.audio.duration_ms))
        self.redraw()
        if self.play_process.poll() is None and self.playhead_ms < play_end_ms:
            self.root.after(30, self.schedule_playhead_update)
        else:
            self.status_var.set("Playback finished. White line remains the draggable label cursor.")
            self.play_process = None
            self.play_started_perf = None
            self.play_end_ms = None

    def adjust_playback_latency(self, delta_ms: int) -> None:
        """调整播放进度线延迟校准值。

        参数：delta_ms，正数表示蓝线更晚走，负数表示蓝线更早走。
        返回值：无。
        异常情况：无。
        """

        self.playback_latency_ms = max(0, min(2000, self.playback_latency_ms + delta_ms))
        self.status_var.set(
            f"Playback offset={self.playback_latency_ms}ms. Use [ and ] or buttons to calibrate blue playhead."
        )
        self.redraw()

    def save_label(self) -> None:
        """保存 sidecar JSON 标签。

        参数：无。
        返回值：无。
        异常情况：标签不完整或 end 早于 start 时提示错误。
        """

        if not self.audio:
            return
        self.label.notes = self.notes_var.get()
        if self.label.start_ms is None or self.label.end_ms is None:
            messagebox.showwarning("Label incomplete", "Please set both start and end before saving.")
            return
        if self.label.end_ms <= self.label.start_ms:
            messagebox.showwarning("Label invalid", "speech_end_ms must be later than speech_start_ms.")
            return
        data = {
            "schema": "realtime-agent.vad-label.v1",
            "source_path": str(self.audio.path),
            "source_name": self.audio.path.name,
            "sample_rate": self.audio.sample_rate,
            "duration_ms": self.audio.duration_ms,
            "speech_start_ms": self.label.start_ms,
            "speech_end_ms": self.label.end_ms,
            "notes": self.label.notes,
            "created_at": datetime.now().isoformat(timespec="seconds"),
        }
        path = label_path_for_audio(self.audio.path)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        self.status_var.set(f"Saved label: {path}")

    def redraw(self) -> None:
        """重绘画布和状态信息。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        if not hasattr(self, "canvas"):
            return
        self.canvas.delete("all")
        if not self.audio:
            self.canvas.create_text(
                CANVAS_WIDTH // 2,
                CANVAS_HEIGHT // 2,
                text="Open a WAV file",
                fill="#d0d7de",
                font=("Helvetica", 18),
            )
            return
        width = max(1, self.canvas.winfo_width())
        height = max(1, self.canvas.winfo_height())
        start_ms = self.visible_start_ms
        end_ms = min(self.audio.duration_ms, start_ms + self.window_ms)
        self.draw_grid(width, height, start_ms, end_ms)
        if self.play_process is not None and self.play_end_ms is not None:
            self.draw_play_range(width, height, start_ms, end_ms)
        self.draw_waveform(width, height, start_ms, end_ms)
        self.draw_rms(width, height, start_ms, end_ms)
        if self.playhead_ms is not None:
            self.draw_marker("Play", self.playhead_ms, "#58a6ff", width, height, start_ms, end_ms)
        self.draw_marker("Cursor", self.label.cursor_ms, "#e6edf3", width, height, start_ms, end_ms)
        if self.label.start_ms is not None:
            self.draw_marker("Start", self.label.start_ms, "#2ea043", width, height, start_ms, end_ms)
        if self.label.end_ms is not None:
            self.draw_marker("End", self.label.end_ms, "#f85149", width, height, start_ms, end_ms)
        self.update_info()

    def draw_grid(self, width: int, height: int, start_ms: int, end_ms: int) -> None:
        """绘制时间网格。

        参数：画布宽高和可见时间范围。
        返回值：无。
        异常情况：无。
        """

        self.canvas.create_rectangle(0, 0, width, height, fill="#101418", outline="")
        duration = max(1, end_ms - start_ms)
        step_ms = choose_grid_step(duration)
        first_tick = (start_ms // step_ms) * step_ms
        tick = first_tick
        while tick <= end_ms:
            x = time_to_x(tick, start_ms, end_ms, width)
            self.canvas.create_line(x, 0, x, height, fill="#26313a")
            self.canvas.create_text(x + 4, 12, text=format_ms(tick), fill="#8b949e", anchor=tk.NW)
            tick += step_ms
        mid = int(height * 0.42)
        self.canvas.create_line(0, mid, width, mid, fill="#30363d")
        rms_base = int(height * 0.88)
        self.canvas.create_line(0, rms_base, width, rms_base, fill="#30363d")
        self.canvas.create_text(8, mid - 24, text="Waveform", fill="#8b949e", anchor=tk.NW)
        self.canvas.create_text(8, rms_base - 110, text="RMS intensity", fill="#f2cc60", anchor=tk.NW)

    def draw_waveform(self, width: int, height: int, start_ms: int, end_ms: int) -> None:
        """绘制当前窗口的波形包络。

        参数：画布宽高和可见时间范围。
        返回值：无。
        异常情况：无。
        """

        assert self.audio is not None
        start_sample = ms_to_sample(start_ms, self.audio.sample_rate)
        end_sample = ms_to_sample(end_ms, self.audio.sample_rate)
        visible = self.audio.samples[start_sample:end_sample]
        if len(visible) == 0:
            return
        center = int(height * 0.42)
        scale = int(height * 0.33)
        samples_per_pixel = max(1, int(math.ceil(len(visible) / width)))
        for x in range(width):
            begin = x * samples_per_pixel
            chunk = visible[begin : begin + samples_per_pixel]
            if len(chunk) == 0:
                continue
            low = float(np.min(chunk))
            high = float(np.max(chunk))
            y1 = center - int(high * scale)
            y2 = center - int(low * scale)
            self.canvas.create_line(x, y1, x, y2, fill="#8b949e")

    def draw_play_range(self, width: int, height: int, start_ms: int, end_ms: int) -> None:
        """绘制当前播放片段范围。

        参数：画布宽高和可见时间范围。
        返回值：无。
        异常情况：无。
        """

        if self.play_end_ms is None:
            return
        x1 = time_to_x(max(start_ms, self.play_start_offset_ms), start_ms, end_ms, width)
        x2 = time_to_x(min(end_ms, self.play_end_ms), start_ms, end_ms, width)
        if x2 <= x1:
            return
        self.canvas.create_rectangle(x1, 0, x2, height, fill="#0d2d4d", outline="")
        self.canvas.create_text(x1 + 6, 34, text="play range", fill="#58a6ff", anchor=tk.NW)

    def draw_rms(self, width: int, height: int, start_ms: int, end_ms: int) -> None:
        """绘制 RMS 音量强度曲线。

        参数：画布宽高和可见时间范围。
        返回值：无。
        异常情况：无。
        """

        assert self.audio is not None
        base = int(height * 0.88)
        scale = int(height * 0.23)
        start_index = max(0, start_ms // self.audio.rms_hop_ms)
        end_index = min(len(self.audio.rms_values), int(math.ceil(end_ms / self.audio.rms_hop_ms)))
        points = []
        for index in range(start_index, end_index):
            t_ms = index * self.audio.rms_hop_ms
            x = time_to_x(t_ms, start_ms, end_ms, width)
            y = base - int(min(1.0, self.audio.rms_values[index] * 8.0) * scale)
            points.append((x, y))
        if len(points) < 2:
            return
        for (x1, y1), (x2, y2) in zip(points, points[1:]):
            self.canvas.create_line(x1, y1, x2, y2, fill="#f2cc60", width=2)

    def draw_marker(self, label: str, marker_ms: int, color: str, width: int, height: int, start_ms: int, end_ms: int) -> None:
        """绘制游标或标签线。

        参数：标签名、时间、颜色、画布大小和可见范围。
        返回值：无。
        异常情况：无。
        """

        if marker_ms < start_ms or marker_ms > end_ms:
            return
        x = time_to_x(marker_ms, start_ms, end_ms, width)
        self.canvas.create_line(x, 0, x, height, fill=color, width=2)
        self.canvas.create_text(x + 4, height - 22, text=f"{label} {format_ms(marker_ms)}", fill=color, anchor=tk.NW)

    def update_info(self) -> None:
        """更新顶部信息栏。

        参数：无。
        返回值：无。
        异常情况：无。
        """

        assert self.audio is not None
        self.info_var.set(
            f"{self.audio.path.name} | duration={format_ms(self.audio.duration_ms)} | "
            f"cursor={format_ms(self.label.cursor_ms)} | "
            f"start={format_optional_ms(self.label.start_ms)} | end={format_optional_ms(self.label.end_ms)} | "
            f"play_offset={self.playback_latency_ms}ms"
        )


def read_audio(path: Path) -> AudioData:
    """读取 WAV 并计算 RMS。

    参数：path，WAV 路径。
    返回值：AudioData。
    异常情况：不支持压缩 WAV、非 8/16/24/32-bit PCM 或空音频时抛出 ValueError。
    """

    with wave.open(str(path), "rb") as wav:
        channels = wav.getnchannels()
        sample_rate = wav.getframerate()
        sample_width = wav.getsampwidth()
        frames = wav.getnframes()
        if wav.getcomptype() != "NONE":
            raise ValueError(f"compressed WAV is not supported: {wav.getcomptype()}")
        if sample_width not in {1, 2, 3, 4}:
            raise ValueError(f"unsupported sample width: {sample_width}")
        raw = wav.readframes(frames)
    samples = decode_pcm(raw, sample_width, channels)
    if len(samples) == 0:
        raise ValueError("empty WAV")
    duration_ms = int(round(len(samples) * 1000 / sample_rate))
    rms_values = compute_rms(samples, sample_rate)
    return AudioData(path=path, sample_rate=sample_rate, samples=samples, duration_ms=duration_ms, rms_values=rms_values, rms_hop_ms=10)


def decode_pcm(raw: bytes, sample_width: int, channels: int) -> np.ndarray:
    """解码 PCM 字节为 mono float32。

    参数：raw 原始 PCM 字节；sample_width 单样本字节数；channels 声道数。
    返回值：范围约为 -1 到 1 的 mono float32。
    异常情况：24-bit PCM 会走手动符号扩展。
    """

    if sample_width == 1:
        data = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    elif sample_width == 2:
        data = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif sample_width == 3:
        data = decode_int24(raw).astype(np.float32) / 8388608.0
    else:
        data = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    if channels > 1:
        data = data.reshape(-1, channels).mean(axis=1)
    return np.clip(data, -1.0, 1.0).astype(np.float32)


def decode_int24(raw: bytes) -> np.ndarray:
    """解码 little-endian 24-bit PCM。

    参数：raw 原始 PCM 字节。
    返回值：int32 numpy 数组。
    异常情况：raw 长度不是 3 的整数倍时尾部字节会被忽略。
    """

    byte_data = np.frombuffer(raw, dtype=np.uint8)
    byte_data = byte_data[: len(byte_data) // 3 * 3].reshape(-1, 3)
    values = byte_data[:, 0].astype(np.int32) | (byte_data[:, 1].astype(np.int32) << 8) | (byte_data[:, 2].astype(np.int32) << 16)
    sign_bit = 1 << 23
    return (values ^ sign_bit) - sign_bit


def compute_rms(samples: np.ndarray, sample_rate: int, frame_ms: int = 20, hop_ms: int = 10) -> np.ndarray:
    """计算归一化 RMS 强度。

    参数：samples mono float；sample_rate 采样率；frame_ms 和 hop_ms 控制窗口。
    返回值：RMS 数组。
    异常情况：无。
    """

    frame = max(1, ms_to_sample(frame_ms, sample_rate))
    hop = max(1, ms_to_sample(hop_ms, sample_rate))
    values = []
    for start in range(0, max(1, len(samples) - frame + 1), hop):
        chunk = samples[start : start + frame]
        values.append(float(np.sqrt(np.mean(chunk * chunk))) if len(chunk) else 0.0)
    return np.array(values, dtype=np.float32)


def label_path_for_audio(path: Path) -> Path:
    """计算 sidecar 标签路径。

    参数：音频路径。
    返回值：同目录 `<stem>.vad-label.json`。
    异常情况：无。
    """

    return path.with_suffix(".vad-label.json")


def write_temp_snippet(audio: AudioData, start_ms: int, end_ms: int) -> Path:
    """把游标附近片段写成临时 WAV。

    参数：audio 当前音频；start_ms / end_ms 是片段边界。
    返回值：临时 WAV 路径。
    异常情况：文件写入失败时抛出异常。
    """

    start_sample = ms_to_sample(start_ms, audio.sample_rate)
    end_sample = ms_to_sample(end_ms, audio.sample_rate)
    snippet = audio.samples[start_sample:end_sample]
    pcm = np.clip(snippet * 32767.0, -32768, 32767).astype("<i2")
    temp = tempfile.NamedTemporaryFile(prefix="vad-label-snippet-", suffix=".wav", delete=False)
    temp_path = Path(temp.name)
    temp.close()
    with wave.open(str(temp_path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(audio.sample_rate)
        wav.writeframes(pcm.tobytes())
    return temp_path


def time_to_x(value_ms: int, start_ms: int, end_ms: int, width: int) -> int:
    """把毫秒时间映射到画布 x 坐标。

    参数：时间、可见范围和画布宽度。
    返回值：x 坐标。
    异常情况：无。
    """

    return int(round((value_ms - start_ms) / max(1, end_ms - start_ms) * width))


def ms_to_sample(ms: int, sample_rate: int) -> int:
    """把毫秒转换为 sample index。

    参数：毫秒和采样率。
    返回值：sample index。
    异常情况：无。
    """

    return int(round(ms * sample_rate / 1000))


def clamp_ms(value: int, duration_ms: int) -> int:
    """把时间限制在音频范围内。

    参数：value 和 duration_ms。
    返回值：限制后的毫秒。
    异常情况：无。
    """

    return min(duration_ms, max(0, int(value)))


def choose_grid_step(duration_ms: int) -> int:
    """根据窗口时长选择网格间隔。

    参数：duration_ms 可见窗口长度。
    返回值：网格毫秒间隔。
    异常情况：无。
    """

    if duration_ms <= 3000:
        return 250
    if duration_ms <= 8000:
        return 500
    if duration_ms <= 20000:
        return 1000
    return 5000


def format_ms(value_ms: int) -> str:
    """把毫秒格式化成秒字符串。

    参数：value_ms。
    返回值：例如 `1.230s`。
    异常情况：无。
    """

    return f"{value_ms / 1000:.3f}s"


def format_optional_ms(value_ms: int | None) -> str:
    """格式化可空毫秒。

    参数：value_ms。
    返回值：空值返回 `-`。
    异常情况：无。
    """

    return "-" if value_ms is None else format_ms(value_ms)


def parse_args() -> argparse.Namespace:
    """解析命令行参数。

    参数：无。
    返回值：argparse.Namespace。
    异常情况：参数非法时 argparse 退出。
    """

    parser = argparse.ArgumentParser(description="VAD label annotation tool.")
    parser.add_argument("audio", nargs="?", help="可选 WAV 音频路径。")
    return parser.parse_args()


def main() -> None:
    """启动标注窗口。

    参数：无。
    返回值：无。
    异常情况：Tk 初始化失败时向外抛出。
    """

    args = parse_args()
    root = tk.Tk()
    root.geometry(f"{CANVAS_WIDTH}x650")
    initial_path = Path(args.audio).expanduser().resolve() if args.audio else None
    app = VadLabelApp(root, initial_path=initial_path)
    print("VAD label window opened. If you do not see it, check behind other windows or Mission Control.", file=sys.stderr)
    root.after(100, app.bring_to_front)
    root.mainloop()


if __name__ == "__main__":
    main()
