from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class GuiLogEntry:
    """GUI 日志条目。

    主要功能：把 phone 端运行日志整理成窗口和测试都能消费的结构。
    主要属性：`level` 为日志级别，`message` 为展示文本，`created_at` 为记录时间。
    """

    level: str
    message: str
    created_at: float = field(default_factory=time.time)


@dataclass(frozen=True)
class GuiFrameSummary:
    """GUI 视频帧摘要。

    主要功能：记录最近一次收到的视频帧关键信息，避免 UI 层依赖协议对象。
    主要属性：`stream_id`、`seq`、`width`、`height` 用于窗口状态栏和测试断言。
    """

    stream_id: str
    stream_type: str
    seq: int
    codec: str
    width: int
    height: int
    received_at: float = field(default_factory=time.time)


class GuiEventBridge:
    """Python 手机 GUI 事件桥。

    主要功能：在网络线程、测试和真实 PySide6 窗口之间传递状态、日志和视频帧。
    主要方法：`emit_status()`、`emit_log()`、`emit_frame()`、`snapshot()`。
    异常情况：监听器异常会被记录为 ERROR 日志，不会中断协议链路。
    """

    def __init__(self, *, log_limit: int = 200, show_debug_events: bool = False) -> None:
        self.log_limit = max(1, int(log_limit or 200))
        self.show_debug_events = bool(show_debug_events)
        self.status: dict[str, Any] = {
            "control": "idle",
            "stream": "idle",
            "registered": False,
            "frame_count": 0,
            "last_error": "",
        }
        self.logs: list[GuiLogEntry] = []
        self.latest_frame: GuiFrameSummary | None = None
        self._status_listeners: list[Callable[[dict[str, Any]], None]] = []
        self._log_listeners: list[Callable[[GuiLogEntry], None]] = []
        self._frame_listeners: list[Callable[[GuiFrameSummary, Any], None]] = []

    def on_status(self, listener: Callable[[dict[str, Any]], None]) -> None:
        """注册状态监听器。"""

        self._status_listeners.append(listener)

    def on_log(self, listener: Callable[[GuiLogEntry], None]) -> None:
        """注册日志监听器。"""

        self._log_listeners.append(listener)

    def on_frame(self, listener: Callable[[GuiFrameSummary, Any], None]) -> None:
        """注册视频帧监听器。"""

        self._frame_listeners.append(listener)

    def emit_status(self, **patch: Any) -> None:
        """发布状态更新。

        参数：`patch` 为待合并状态字段。
        返回值：无。
        异常情况：监听器异常会被记录并继续通知其他监听器。
        """

        self.status.update(patch)
        snapshot = dict(self.status)
        for listener in list(self._status_listeners):
            self._safe_call(listener, snapshot)

    def emit_log(self, level: str, message: str, *, debug: bool = False) -> None:
        """发布一条窗口日志。"""

        if debug and not self.show_debug_events:
            return
        entry = GuiLogEntry(level=str(level or "INFO").upper(), message=str(message))
        self.logs.append(entry)
        if len(self.logs) > self.log_limit:
            self.logs = self.logs[-self.log_limit :]
        for listener in list(self._log_listeners):
            self._safe_call(listener, entry)

    def emit_frame(self, frame: Any) -> GuiFrameSummary:
        """发布一帧已解码图像。

        参数：`frame` 为 phone mock 解码后的 `DecodedVideoFrame`。
        返回值：窗口可展示的摘要。
        异常情况：监听器异常会被记录并继续通知其他监听器。
        """

        summary = GuiFrameSummary(
            stream_id=str(frame.stream_id),
            stream_type=str(frame.stream_type),
            seq=int(frame.seq),
            codec=str(frame.codec),
            width=int(frame.width),
            height=int(frame.height),
        )
        self.latest_frame = summary
        self.emit_status(frame_count=int(self.status.get("frame_count") or 0) + 1)
        for listener in list(self._frame_listeners):
            self._safe_call(listener, summary, frame.image)
        return summary

    def snapshot(self) -> dict[str, Any]:
        """返回当前 GUI 状态快照。"""

        return {
            "status": dict(self.status),
            "log_count": len(self.logs),
            "latest_frame": None if self.latest_frame is None else dict(self.latest_frame.__dict__),
            "logs": [dict(entry.__dict__) for entry in self.logs],
        }

    def _safe_call(self, listener: Callable[..., None], *args: Any) -> None:
        try:
            listener(*args)
        except Exception as exc:  # noqa: BLE001 - GUI 监听器不能拖垮设备协议
            entry = GuiLogEntry("ERROR", f"GUI listener failed: {type(exc).__name__}: {exc}")
            self.logs.append(entry)
            if len(self.logs) > self.log_limit:
                self.logs = self.logs[-self.log_limit :]


class VideoPanel:
    """PySide6 视频显示面板。

    主要功能：把 OpenCV BGR 图像转换成 Qt 图片并显示到窗口中。
    主要方法：`show_frame()`。
    """

    def __init__(self, qt_widgets: Any, qt_gui: Any, qt_core: Any) -> None:
        self._qt_gui = qt_gui
        self._qt_core = qt_core
        self.widget = qt_widgets.QLabel("等待 sensor.rgb 视频帧")
        self.widget.setMinimumSize(640, 360)
        self.widget.setAlignment(qt_core.Qt.AlignmentFlag.AlignCenter)
        self.widget.setStyleSheet("background:#0f1722;color:#dbe7ff;border:1px solid #253047;")

    def show_frame(self, image: Any) -> None:
        """显示一帧 OpenCV BGR 图像。"""

        height, width, _channels = image.shape
        rgb = image[:, :, ::-1].copy()
        q_image = self._qt_gui.QImage(rgb.data, width, height, width * 3, self._qt_gui.QImage.Format.Format_RGB888)
        pixmap = self._qt_gui.QPixmap.fromImage(q_image).scaled(
            self.widget.size(),
            self._qt_core.Qt.AspectRatioMode.KeepAspectRatio,
            self._qt_core.Qt.TransformationMode.SmoothTransformation,
        )
        self.widget.setPixmap(pixmap)


class StatusPanel:
    """PySide6 状态面板。

    主要功能：展示连接状态、注册状态、帧计数和最近错误。
    主要方法：`update_status()`。
    """

    def __init__(self, qt_widgets: Any) -> None:
        self.widget = qt_widgets.QLabel("control=idle stream=idle frame=0")
        self.widget.setWordWrap(True)
        self.widget.setStyleSheet("font:13px Menlo;color:#172033;padding:6px;")

    def update_status(self, status: dict[str, Any]) -> None:
        """刷新状态文本。"""

        self.widget.setText(
            "control={control} stream={stream} registered={registered} frame={frame_count} error={last_error}".format(
                control=status.get("control", "idle"),
                stream=status.get("stream", "idle"),
                registered=status.get("registered", False),
                frame_count=status.get("frame_count", 0),
                last_error=status.get("last_error", "") or "-",
            )
        )


class EventLogPanel:
    """PySide6 事件日志面板。

    主要功能：滚动展示 phone 端关键协议事件和错误。
    主要方法：`append_log()`。
    """

    def __init__(self, qt_widgets: Any) -> None:
        self.widget = qt_widgets.QPlainTextEdit()
        self.widget.setReadOnly(True)
        self.widget.setMaximumBlockCount(300)
        self.widget.setStyleSheet("font:12px Menlo;background:#0f1722;color:#dbe7ff;")

    def append_log(self, entry: GuiLogEntry) -> None:
        """追加一条日志。"""

        timestamp = time.strftime("%H:%M:%S", time.localtime(entry.created_at))
        self.widget.appendPlainText(f"[{timestamp}] {entry.level} {entry.message}")


class PhonePreviewWindow:
    """Python 手机 PySide6 预览窗口。

    主要功能：组合视频、状态和日志三个面板，用于本地观察 browser-glass 回传画面。
    主要方法：`show()`。
    异常情况：未安装 PySide6 时抛出 RuntimeError，并给出安装提示。
    """

    def __init__(self, *, bridge: GuiEventBridge, title: str = "audio-chat Python Phone") -> None:
        try:
            from PySide6 import QtCore, QtGui, QtWidgets  # type: ignore
        except Exception as exc:  # noqa: BLE001 - 需要把可选依赖缺失说明清楚
            raise RuntimeError("Python 手机 GUI 需要安装 PySide6：uv sync --group dev 或 uv add --dev pyside6") from exc
        self.bridge = bridge
        self._qt_core = QtCore
        self._qt_widgets = QtWidgets
        self.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
        self.window = QtWidgets.QMainWindow()
        self.window.setWindowTitle(title)
        self.video_panel = VideoPanel(QtWidgets, QtGui, QtCore)
        self.status_panel = StatusPanel(QtWidgets)
        self.log_panel = EventLogPanel(QtWidgets)
        self.dispatcher = self._build_dispatcher(QtCore)
        self.dispatcher.status.connect(self.status_panel.update_status, QtCore.Qt.ConnectionType.QueuedConnection)
        self.dispatcher.log.connect(self.log_panel.append_log, QtCore.Qt.ConnectionType.QueuedConnection)
        self.dispatcher.frame.connect(self.video_panel.show_frame, QtCore.Qt.ConnectionType.QueuedConnection)
        layout = QtWidgets.QVBoxLayout()
        layout.addWidget(self.video_panel.widget, 3)
        layout.addWidget(self.status_panel.widget)
        layout.addWidget(self.log_panel.widget, 1)
        root = QtWidgets.QWidget()
        root.setLayout(layout)
        self.window.setCentralWidget(root)
        self.window.resize(900, 720)
        self._connect_bridge()

    def _build_dispatcher(self, qt_core: Any) -> Any:
        """创建 Qt 主线程事件转发器。

        主要逻辑：网络循环在后台线程收到视频帧，不能直接调用 Qt 控件；这里用
        queued signal 把状态、日志和图像投递回 Qt 主线程，避免预览窗口偶发不刷新。
        参数：`qt_core` 为动态导入的 PySide6.QtCore。
        返回值：带有 `status/log/frame` 三个信号的 QObject。
        异常情况：无。
        """

        class PreviewDispatcher(qt_core.QObject):  # type: ignore[misc, valid-type]
            status = qt_core.Signal(object)
            log = qt_core.Signal(object)
            frame = qt_core.Signal(object)

        return PreviewDispatcher()

    def _connect_bridge(self) -> None:
        """把纯 Python 事件桥连接到 Qt 主线程。"""

        self.bridge.on_status(lambda status: self.dispatcher.status.emit(dict(status)))
        self.bridge.on_log(lambda entry: self.dispatcher.log.emit(entry))
        self.bridge.on_frame(lambda _summary, image: self.dispatcher.frame.emit(_copy_image(image)))

    def show(self) -> int:
        """显示窗口并进入 Qt 事件循环。"""

        self.window.show()
        return int(self.app.exec())


def _copy_image(image: Any) -> Any:
    """复制跨线程投递的视频帧。

    主要逻辑：OpenCV 图像通常是 numpy array，网络线程继续处理下一帧时不应影响
    已投递给 Qt 主线程的对象；如果对象不支持 `copy()`，则按原样返回。
    参数：`image` 为待显示图像。
    返回值：可安全投递给 GUI 的图像对象。
    异常情况：复制失败时返回原对象，避免显示链路中断。
    """

    try:
        copy = getattr(image, "copy", None)
        if callable(copy):
            return copy()
    except Exception:  # noqa: BLE001 - 显示辅助不能影响主链路
        return image
    return image
