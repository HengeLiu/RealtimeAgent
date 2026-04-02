"""共享枚举定义。

本文件集中定义三端运行时都会依赖的基础枚举，避免各端各自维护一套值域。
"""

from enum import Enum


class RuntimeType(str, Enum):
    """运行时类型。

    主要功能：
    - 标识当前消息、设备或模块属于眼镜端、手机端还是服务器端。

    主要取值：
    - glass：眼镜端
    - phone：手机端
    - server：服务器端
    """

    GLASS = "glass"
    PHONE = "phone"
    SERVER = "server"


class CapabilityType(str, Enum):
    """设备能力类型。

    主要功能：
    - 描述某个设备或运行时当前具备的能力，供任务创建和能力校验使用。
    """

    RGB_CAMERA = "rgb_camera"
    DEPTH_CAMERA = "depth_camera"
    IMU = "imu"
    MICROPHONE = "microphone"
    SPEAKER = "speaker"
    VIBRATOR = "vibrator"
    GPS = "gps"
    LOCAL_DETECTION = "local_detection"
    OCR = "ocr"
    MAP_NAVIGATION = "map_navigation"
    CLOUD_VLM = "cloud_vlm"
    TASK_ORCHESTRATION = "task_orchestration"


class TaskCategory(str, Enum):
    """任务类别。"""

    INTERACTIVE = "interactive"
    BACKGROUND = "background"
    SCHEDULED = "scheduled"
    SYSTEM = "system"


class TaskStatus(str, Enum):
    """任务整体状态。"""

    CREATED = "created"
    PENDING = "pending"
    STARTING = "starting"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class TaskPriority(str, Enum):
    """任务优先级。"""

    LOW = "low"
    NORMAL = "normal"
    HIGH = "high"
    URGENT = "urgent"


class ExecutorMode(str, Enum):
    """任务执行模式。"""

    GLASS_ONLY = "glass_only"
    PHONE_ONLY = "phone_only"
    SERVER_ONLY = "server_only"
    HYBRID = "hybrid"


class SensorType(str, Enum):
    """传感器类型。"""

    RGB_CAMERA = "rgb_camera"
    DEPTH_CAMERA = "depth_camera"
    IMU = "imu"
    MICROPHONE = "microphone"
    BATTERY = "battery"
    GPS = "gps"


class CaptureMode(str, Enum):
    """采集模式。"""

    SNAPSHOT = "snapshot"
    STREAM = "stream"
    CONTINUOUS_STATE = "continuous_state"


class EventType(str, Enum):
    """事件类型。"""

    VOICE_EVENT = "voice_event"
    CAPTURE_READY = "capture_ready"
    DEVICE_STATE_CHANGED = "device_state_changed"
    TASK_STATE_CHANGED = "task_state_changed"
    DETECTION_RESULT = "detection_result"
    EXECUTION_FEEDBACK = "execution_feedback"
    SYSTEM_WARNING = "system_warning"


class ExecutionType(str, Enum):
    """执行类型。"""

    SPEECH = "speech"
    BEEP = "beep"
    VIBRATION = "vibration"
    MIXED = "mixed"


class ChannelType(str, Enum):
    """协议通道类型。"""

    CONTROL = "control"
    DATA = "data"


class TransportMode(str, Enum):
    """数据传输模式。"""

    RELAY = "relay"
    PEER = "peer"
