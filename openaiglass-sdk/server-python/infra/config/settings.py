"""服务端配置加载与校验模块。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime

from infra.errors import ErrorCode, build_error


@dataclass(slots=True)
class ServerSettings:
    """服务端配置对象。

    主要功能：
    1. 集中保存服务端运行参数。
    2. 提供环境变量读取与配置合法性校验。

    主要属性：
    1. `host`：监听地址。
    2. `port`：监听端口。
    3. `environment`：运行环境名称，例如 `dev`。
    4. `log_level`：日志级别。
    5. `log_file`：日志文件路径，留空表示只输出到标准输出。
    5. `device_token_map`：设备与配对令牌映射，格式为 `device_id=token,device2=token2`。
    6. `heartbeat_interval_ms`：服务端下发给设备的心跳建议间隔。
    7. `heartbeat_timeout_ms`：服务端判定设备离线的心跳超时时间。
    8. `server_device_id`：服务端在控制消息中的设备编号。
    9. `dashscope_api_key`：百炼兼容接口 API Key。
    10. `voice_model_base_url`：百炼兼容接口基础地址。
    11. `agent_model_name`：agent-core 文本与图片理解模型名称。
    12. `voice_model_name`：兼容旧链路的语音模型名称。
    13. `voice_model_voice`：兼容旧链路的语音音色。
    14. `voice_reply_mode`：语音回复模式，当前默认走 Omni 音频输入。
    15. `voice_omni_realtime_model_name`：Omni Realtime 实验性直连模型名称。
    16. `voice_omni_realtime_url`：Omni Realtime WebSocket 地址。
    17. `voice_omni_photo_wait_ms`：Omni 分支等待自动照片就绪的最长时间。
    18. `voice_conversation_mode`：语音对话模式，稳定分段提交或实验性 Omni 语义连续对话。
    19. `voice_realtime_turn_detection_type`：实时对话 turn detection 类型。
    20. `voice_realtime_semantic_vad_threshold`：Omni 语义 VAD 阈值。
    21. `voice_realtime_silence_duration_ms`：服务端 VAD 判定句尾的静音时长。
    22. `voice_realtime_prefix_padding_ms`：服务端 VAD 句首保留音频时长。
    23. `voice_input_mode`：旧版语音输入模式，仅用于兼容配置。
    24. `tts_model_name`：专用流式 TTS 模型名称。
    25. `tts_voice`：专用流式 TTS 音色。
    26. `tts_websocket_api_url`：专用流式 TTS 的 WebSocket 地址。
    27. `tts_sample_rate_hz`：TTS 原始输出采样率。
    28. `tool_progress_audio_mode`：工具前置播报音频来源，`cached` 或 `realtime`。
    29. `voice_model_timeout_ms`：模型请求超时时间。
    30. `voice_runs_root`：语音运行时资产落盘目录。
    31. `voice_asr_model_name`：批量语音转写模型名称。
    32. `voice_asr_mode`：ASR 模式，`realtime` 表示边收音频边送 ASR。
    33. `voice_asr_realtime_model_name`：实时 ASR 模型名称。
    34. `voice_asr_realtime_timeout_ms`：语音结束后等待实时 ASR 最终文本的时间。
    35. `voice_asr_realtime_max_sentence_silence_ms`：实时 ASR VAD 断句静音阈值。
    36. `voice_session_mode`：设备注册后默认打开的语音会话模式。
    37. `voice_system_prompt`：默认系统提示词。
    38. `max_segment_audio_bytes`：单轮上行音频最大字节数。
    39. `agent_memory_enabled`：是否启用 Agent 长期记忆。
    40. `agent_memory_store_path`：长期记忆本地持久化文件路径。
    41. `agent_memory_max_prompt_items`：每轮最多注入多少条长期记忆。
    """

    host: str = "0.0.0.0"
    port: int = 8765
    environment: str = "dev"
    log_level: str = "INFO"
    log_file: str = ""
    device_token_map: str = "glass-001=pair-demo-token"
    heartbeat_interval_ms: int = 5000
    heartbeat_timeout_ms: int = 15000
    server_device_id: str = "server-main"
    dashscope_api_key: str = ""
    voice_model_base_url: str = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    agent_model_name: str = "qwen3.5-omni-plus"
    voice_model_name: str = "qwen3.5-omni-plus"
    voice_model_voice: str = "Tina"
    voice_reply_mode: str = "omni_realtime"
    voice_omni_realtime_model_name: str = "qwen3.5-omni-plus-realtime"
    voice_omni_realtime_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/realtime"
    voice_omni_photo_wait_ms: int = 300
    voice_conversation_mode: str = "realtime_semantic_vad"
    voice_realtime_turn_detection_type: str = "semantic_vad"
    voice_realtime_semantic_vad_threshold: float = 0.65
    voice_realtime_silence_duration_ms: int = 800
    voice_realtime_prefix_padding_ms: int = 300
    voice_input_mode: str = "auto"
    tts_model_name: str = "cosyvoice-v3-flash"
    tts_voice: str = "longanhuan"
    tts_websocket_api_url: str = "wss://dashscope.aliyuncs.com/api-ws/v1/inference"
    tts_sample_rate_hz: int = 22050
    tool_progress_audio_mode: str = "cached"
    voice_model_timeout_ms: int = 45000
    voice_runs_root: str = "runs/session"
    voice_asr_model_name: str = "qwen3-asr-flash"
    voice_asr_mode: str = "realtime"
    voice_asr_realtime_model_name: str = "fun-asr-realtime"
    voice_asr_realtime_timeout_ms: int = 5000
    voice_asr_realtime_max_sentence_silence_ms: int = 300
    voice_session_mode: str = "full_duplex_realtime"
    voice_system_prompt: str = "你的名字是'乐鑫'。你是盲人眼镜上的中文语音助手，能帮助盲人用户识别图片、障碍物、引导过马路等，请用简短口语回答用户问题。请使用简短、口语化、直接的中文回答。"
    max_segment_audio_bytes: int = 524288
    agent_memory_enabled: bool = True
    agent_memory_store_path: str = "runs/memory/agent_memories.json"
    agent_memory_max_prompt_items: int = 6

    @staticmethod
    def build_default_log_file() -> str:
        """生成默认日志文件路径。

        主要逻辑：
        1. 在项目当前工作目录下创建 `logs` 目录约定。
        2. 使用时间戳生成新的日志文件名，避免覆盖上一轮运行结果。

        返回值：
        1. 默认日志文件相对路径。
        """

        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S-%f")
        return os.path.join("logs", f"server-{timestamp}.log")

    @classmethod
    def from_env(cls) -> "ServerSettings":
        """从环境变量读取配置。

        主要逻辑：
        1. 读取环境变量，不存在时回落到默认值。
        2. 将 `SERVER_PORT` 转换为整数并进行合法性校验。
        3. 旧配置如果只切到 `agent_tts`，自动回退 `segment_turn`，避免默认连续对话模式拦截启动。

        返回值：
        1. `ServerSettings` 实例。

        异常情况：
        1. 端口无法转为整数时抛出 `AppError(INVALID_CONFIG)`。
        2. 校验失败时抛出 `AppError(INVALID_CONFIG)`。
        """

        defaults = cls()
        voice_reply_mode = os.getenv("VOICE_REPLY_MODE", defaults.voice_reply_mode)
        if "VOICE_CONVERSATION_MODE" in os.environ:
            voice_conversation_mode = os.environ["VOICE_CONVERSATION_MODE"]
        elif voice_reply_mode != "omni_realtime":
            voice_conversation_mode = "segment_turn"
        else:
            voice_conversation_mode = defaults.voice_conversation_mode

        port_raw = os.getenv("SERVER_PORT", str(defaults.port))
        try:
            port = int(port_raw)
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_PORT 必须是整数",
                details={"value": port_raw},
            ) from exc

        settings = cls(
            host=os.getenv("SERVER_HOST", defaults.host),
            port=port,
            environment=os.getenv("APP_ENV", defaults.environment),
            log_level=os.getenv("LOG_LEVEL", defaults.log_level).upper(),
            log_file=os.getenv("LOG_FILE", cls.build_default_log_file()),
            device_token_map=os.getenv("DEVICE_TOKEN_MAP", defaults.device_token_map),
            heartbeat_interval_ms=cls._parse_int_env(
                "HEARTBEAT_INTERVAL_MS",
                defaults.heartbeat_interval_ms,
            ),
            heartbeat_timeout_ms=cls._parse_int_env(
                "HEARTBEAT_TIMEOUT_MS",
                defaults.heartbeat_timeout_ms,
            ),
            server_device_id=os.getenv("SERVER_DEVICE_ID", defaults.server_device_id),
            dashscope_api_key=os.getenv("DASHSCOPE_API_KEY", defaults.dashscope_api_key),
            voice_model_base_url=os.getenv("VOICE_MODEL_BASE_URL", defaults.voice_model_base_url),
            agent_model_name=os.getenv("AGENT_MODEL_NAME", defaults.agent_model_name),
            voice_model_name=os.getenv("VOICE_MODEL_NAME", defaults.voice_model_name),
            voice_model_voice=os.getenv("VOICE_MODEL_VOICE", defaults.voice_model_voice),
            voice_reply_mode=voice_reply_mode,
            voice_omni_realtime_model_name=os.getenv(
                "VOICE_OMNI_REALTIME_MODEL_NAME",
                defaults.voice_omni_realtime_model_name,
            ),
            voice_omni_realtime_url=os.getenv(
                "VOICE_OMNI_REALTIME_URL",
                defaults.voice_omni_realtime_url,
            ),
            voice_omni_photo_wait_ms=cls._parse_int_env(
                "VOICE_OMNI_PHOTO_WAIT_MS",
                defaults.voice_omni_photo_wait_ms,
            ),
            voice_conversation_mode=voice_conversation_mode,
            voice_realtime_turn_detection_type=os.getenv(
                "VOICE_REALTIME_TURN_DETECTION",
                defaults.voice_realtime_turn_detection_type,
            ),
            voice_realtime_semantic_vad_threshold=cls._parse_float_env(
                "VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD",
                defaults.voice_realtime_semantic_vad_threshold,
            ),
            voice_realtime_silence_duration_ms=cls._parse_int_env(
                "VOICE_REALTIME_SILENCE_DURATION_MS",
                defaults.voice_realtime_silence_duration_ms,
            ),
            voice_realtime_prefix_padding_ms=cls._parse_int_env(
                "VOICE_REALTIME_PREFIX_PADDING_MS",
                defaults.voice_realtime_prefix_padding_ms,
            ),
            voice_input_mode=os.getenv("VOICE_INPUT_MODE", defaults.voice_input_mode),
            tts_model_name=os.getenv("TTS_MODEL_NAME", defaults.tts_model_name),
            tts_voice=os.getenv("TTS_VOICE", defaults.tts_voice),
            tts_websocket_api_url=os.getenv("TTS_WEBSOCKET_API_URL", defaults.tts_websocket_api_url),
            tts_sample_rate_hz=cls._parse_int_env("TTS_SAMPLE_RATE_HZ", defaults.tts_sample_rate_hz),
            tool_progress_audio_mode=os.getenv(
                "TOOL_PROGRESS_AUDIO_MODE",
                defaults.tool_progress_audio_mode,
            ).strip().lower(),
            voice_model_timeout_ms=cls._parse_int_env(
                "VOICE_MODEL_TIMEOUT_MS",
                defaults.voice_model_timeout_ms,
            ),
            voice_runs_root=os.getenv("VOICE_RUNS_ROOT", defaults.voice_runs_root),
            voice_asr_model_name=os.getenv("VOICE_ASR_MODEL_NAME", defaults.voice_asr_model_name),
            voice_asr_mode=os.getenv("VOICE_ASR_MODE", defaults.voice_asr_mode),
            voice_asr_realtime_model_name=os.getenv(
                "VOICE_ASR_REALTIME_MODEL_NAME",
                defaults.voice_asr_realtime_model_name,
            ),
            voice_asr_realtime_timeout_ms=cls._parse_int_env(
                "VOICE_ASR_REALTIME_TIMEOUT_MS",
                defaults.voice_asr_realtime_timeout_ms,
            ),
            voice_asr_realtime_max_sentence_silence_ms=cls._parse_int_env(
                "VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS",
                defaults.voice_asr_realtime_max_sentence_silence_ms,
            ),
            voice_session_mode=os.getenv("VOICE_SESSION_MODE", defaults.voice_session_mode),
            voice_system_prompt=os.getenv("VOICE_SYSTEM_PROMPT", defaults.voice_system_prompt),
            max_segment_audio_bytes=cls._parse_int_env(
                "MAX_SEGMENT_AUDIO_BYTES",
                defaults.max_segment_audio_bytes,
            ),
            agent_memory_enabled=cls._parse_bool_env(
                "AGENT_MEMORY_ENABLED",
                defaults.agent_memory_enabled,
            ),
            agent_memory_store_path=os.getenv("AGENT_MEMORY_STORE_PATH", defaults.agent_memory_store_path),
            agent_memory_max_prompt_items=cls._parse_int_env(
                "AGENT_MEMORY_MAX_PROMPT_ITEMS",
                defaults.agent_memory_max_prompt_items,
            ),
        )
        settings.validate()
        return settings

    @staticmethod
    def _parse_int_env(name: str, default: int) -> int:
        """读取整数环境变量。

        参数：
        1. `name`：环境变量名。
        2. `default`：默认值。

        返回值：
        1. 解析后的整数。

        异常情况：
        1. 无法转为整数时抛出 `AppError(INVALID_CONFIG)`。
        """

        raw = os.getenv(name, str(default))
        try:
            return int(raw)
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                f"{name} 必须是整数",
                details={"value": raw},
            ) from exc

    @staticmethod
    def _parse_float_env(name: str, default: float) -> float:
        """读取浮点数环境变量。

        参数：
        1. `name`：环境变量名。
        2. `default`：默认值。

        返回值：
        1. 解析后的浮点数。

        异常情况：
        1. 无法转为浮点数时抛出 `AppError(INVALID_CONFIG)`。
        """

        raw = os.getenv(name)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError as exc:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                f"{name} 必须是浮点数",
                details={"value": raw},
            ) from exc

    @staticmethod
    def _parse_bool_env(name: str, default: bool) -> bool:
        """读取布尔环境变量。

        参数：
        1. `name`：环境变量名。
        2. `default`：默认值。

        返回值：
        1. 解析后的布尔值。

        异常情况：
        1. 值不在常见真假集合内时抛出 `AppError(INVALID_CONFIG)`。
        """

        raw = os.getenv(name)
        if raw is None:
            return default
        normalized = raw.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            return True
        if normalized in {"0", "false", "no", "off"}:
            return False
        raise build_error(
            ErrorCode.INVALID_CONFIG,
            f"{name} 必须是布尔值",
            details={"value": raw},
        )

    def effective_voice_input_mode(self) -> str:
        """返回当前语音链路实际使用的输入模式。

        主要逻辑：
        1. `VOICE_INPUT_MODE=auto` 时按当前回复分支自动选择。
        2. Omni 分支返回 `raw_audio`，旧 Agent+TTS 分支返回 `asr_text`。
        3. 保留旧字段是为了兼容历史配置读取和日志摘要。

        返回值：
        1. `asr_text` 或 `raw_audio`。
        """

        if self.voice_input_mode == "auto":
            return "raw_audio" if self.voice_reply_mode == "omni_realtime" else "asr_text"
        return self.voice_input_mode

    def omni_turn_detection_enabled(self) -> bool:
        """判断 Omni Realtime 客户端是否应启用服务端 turn detection。

        返回值：
        1. `True` 表示 Omni Realtime 客户端启用语义 VAD。
        2. 当前仅在 `omni_realtime + realtime_semantic_vad` 时启用。
        """

        return self.voice_reply_mode == "omni_realtime" and self.voice_conversation_mode == "realtime_semantic_vad"

    def validate(self) -> None:
        """校验配置合法性。

        主要逻辑：
        1. 校验监听地址非空。
        2. 校验端口在 1~65535。
        3. 校验日志级别在白名单内。

        异常情况：
        1. 任一校验失败时抛出 `AppError(INVALID_CONFIG)`。
        """

        if not self.host.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_HOST 不能为空",
            )
        if not (1 <= self.port <= 65535):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_PORT 必须在 1 到 65535 之间",
                details={"port": self.port},
            )
        valid_levels = {"CRITICAL", "ERROR", "WARNING", "INFO", "DEBUG"}
        if self.log_level not in valid_levels:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "LOG_LEVEL 非法",
                details={"log_level": self.log_level, "valid_levels": sorted(valid_levels)},
            )
        if self.log_file and not self.log_file.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "LOG_FILE 不能为空白字符串",
            )
        if self.heartbeat_interval_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_INTERVAL_MS 必须大于 0",
                details={"heartbeat_interval_ms": self.heartbeat_interval_ms},
            )
        if self.heartbeat_timeout_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_TIMEOUT_MS 必须大于 0",
                details={"heartbeat_timeout_ms": self.heartbeat_timeout_ms},
            )
        if self.heartbeat_timeout_ms <= self.heartbeat_interval_ms:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "HEARTBEAT_TIMEOUT_MS 必须大于 HEARTBEAT_INTERVAL_MS",
                details={
                    "heartbeat_interval_ms": self.heartbeat_interval_ms,
                    "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
                },
            )
        if not self.server_device_id.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "SERVER_DEVICE_ID 不能为空",
            )
        if self.voice_model_timeout_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_MODEL_TIMEOUT_MS 必须大于 0",
                details={"voice_model_timeout_ms": self.voice_model_timeout_ms},
            )
        if not self.voice_model_base_url.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_MODEL_BASE_URL 不能为空",
            )
        if not self.agent_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "AGENT_MODEL_NAME 不能为空",
            )
        if not self.voice_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_MODEL_NAME 不能为空",
            )
        if not self.voice_model_voice.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_MODEL_VOICE 不能为空",
            )
        valid_voice_reply_modes = {"agent_tts", "omni_realtime"}
        if self.voice_reply_mode not in valid_voice_reply_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_REPLY_MODE 非法",
                details={"voice_reply_mode": self.voice_reply_mode, "valid_modes": sorted(valid_voice_reply_modes)},
            )
        if not self.voice_omni_realtime_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_OMNI_REALTIME_MODEL_NAME 不能为空",
            )
        if not self.voice_omni_realtime_url.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_OMNI_REALTIME_URL 不能为空",
            )
        if self.voice_omni_photo_wait_ms < 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_OMNI_PHOTO_WAIT_MS 不能小于 0",
                details={"voice_omni_photo_wait_ms": self.voice_omni_photo_wait_ms},
            )
        valid_voice_conversation_modes = {"segment_turn", "realtime_semantic_vad"}
        if self.voice_conversation_mode not in valid_voice_conversation_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_CONVERSATION_MODE 非法",
                details={
                    "voice_conversation_mode": self.voice_conversation_mode,
                    "valid_modes": sorted(valid_voice_conversation_modes),
                },
            )
        valid_turn_detection_types = {"server_vad", "semantic_vad"}
        if self.voice_realtime_turn_detection_type not in valid_turn_detection_types:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_REALTIME_TURN_DETECTION 非法",
                details={
                    "voice_realtime_turn_detection_type": self.voice_realtime_turn_detection_type,
                    "valid_types": sorted(valid_turn_detection_types),
                },
            )
        if not (-1.0 <= self.voice_realtime_semantic_vad_threshold <= 1.0):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_REALTIME_SEMANTIC_VAD_THRESHOLD 必须在 -1 到 1 之间",
                details={"voice_realtime_semantic_vad_threshold": self.voice_realtime_semantic_vad_threshold},
            )
        if not (200 <= self.voice_realtime_silence_duration_ms <= 6000):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_REALTIME_SILENCE_DURATION_MS 必须在 200 到 6000 之间",
                details={"voice_realtime_silence_duration_ms": self.voice_realtime_silence_duration_ms},
            )
        if not (0 <= self.voice_realtime_prefix_padding_ms <= 2000):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_REALTIME_PREFIX_PADDING_MS 必须在 0 到 2000 之间",
                details={"voice_realtime_prefix_padding_ms": self.voice_realtime_prefix_padding_ms},
            )
        if self.voice_conversation_mode == "realtime_semantic_vad" and self.voice_reply_mode != "omni_realtime":
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_CONVERSATION_MODE=realtime_semantic_vad 需要 VOICE_REPLY_MODE=omni_realtime",
                details={
                    "voice_conversation_mode": self.voice_conversation_mode,
                    "voice_reply_mode": self.voice_reply_mode,
                },
            )
        valid_voice_input_modes = {"auto", "asr_text", "raw_audio"}
        if self.voice_input_mode not in valid_voice_input_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_INPUT_MODE 非法",
                details={"voice_input_mode": self.voice_input_mode, "valid_modes": sorted(valid_voice_input_modes)},
            )
        effective_voice_input_mode = self.effective_voice_input_mode()
        if self.voice_input_mode != "auto":
            expected_input_mode = "raw_audio" if self.voice_reply_mode == "omni_realtime" else "asr_text"
            if effective_voice_input_mode != expected_input_mode:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "VOICE_INPUT_MODE 与 VOICE_REPLY_MODE 不匹配",
                    details={
                        "voice_reply_mode": self.voice_reply_mode,
                        "voice_input_mode": self.voice_input_mode,
                        "expected_voice_input_mode": expected_input_mode,
                    },
                )
        if not self.tts_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "TTS_MODEL_NAME 不能为空",
            )
        if not self.tts_voice.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "TTS_VOICE 不能为空",
            )
        if not self.tts_websocket_api_url.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "TTS_WEBSOCKET_API_URL 不能为空",
            )
        if self.tts_sample_rate_hz <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "TTS_SAMPLE_RATE_HZ 必须大于 0",
                details={"tts_sample_rate_hz": self.tts_sample_rate_hz},
            )
        valid_tool_progress_audio_modes = {"cached", "realtime"}
        if self.tool_progress_audio_mode not in valid_tool_progress_audio_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "TOOL_PROGRESS_AUDIO_MODE 非法",
                details={
                    "tool_progress_audio_mode": self.tool_progress_audio_mode,
                    "valid_modes": sorted(valid_tool_progress_audio_modes),
                },
            )
        if not self.voice_runs_root.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_RUNS_ROOT 不能为空",
            )
        if not self.voice_asr_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_ASR_MODEL_NAME 不能为空",
            )
        valid_voice_asr_modes = {"batch", "realtime"}
        if self.voice_asr_mode not in valid_voice_asr_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_ASR_MODE 非法",
                details={"voice_asr_mode": self.voice_asr_mode, "valid_modes": sorted(valid_voice_asr_modes)},
            )
        if not self.voice_asr_realtime_model_name.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_ASR_REALTIME_MODEL_NAME 不能为空",
            )
        if self.voice_asr_realtime_timeout_ms <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_ASR_REALTIME_TIMEOUT_MS 必须大于 0",
                details={"voice_asr_realtime_timeout_ms": self.voice_asr_realtime_timeout_ms},
            )
        if not (200 <= self.voice_asr_realtime_max_sentence_silence_ms <= 6000):
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_ASR_REALTIME_MAX_SENTENCE_SILENCE_MS 必须在 200 到 6000 之间",
                details={
                    "voice_asr_realtime_max_sentence_silence_ms": (
                        self.voice_asr_realtime_max_sentence_silence_ms
                    )
                },
            )
        valid_voice_session_modes = {"half_duplex", "full_duplex_realtime"}
        if self.voice_session_mode not in valid_voice_session_modes:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "VOICE_SESSION_MODE 非法",
                details={
                    "voice_session_mode": self.voice_session_mode,
                    "valid_modes": sorted(valid_voice_session_modes),
                },
            )
        if self.max_segment_audio_bytes <= 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "MAX_SEGMENT_AUDIO_BYTES 必须大于 0",
                details={"max_segment_audio_bytes": self.max_segment_audio_bytes},
            )
        if self.agent_memory_enabled and not self.agent_memory_store_path.strip():
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "AGENT_MEMORY_STORE_PATH 不能为空",
            )
        if self.agent_memory_max_prompt_items < 0:
            raise build_error(
                ErrorCode.INVALID_CONFIG,
                "AGENT_MEMORY_MAX_PROMPT_ITEMS 不能小于 0",
                details={"agent_memory_max_prompt_items": self.agent_memory_max_prompt_items},
            )

    def summary(self) -> dict[str, str | int | float]:
        """生成配置摘要。

        返回值：
        1. 可直接用于日志打印或接口返回的摘要字典。
        """

        return {
            "host": self.host,
            "port": self.port,
            "environment": self.environment,
            "log_level": self.log_level,
            "log_file": self.log_file or "<stdout-only>",
            "device_token_count": len(self.parse_device_token_map()),
            "heartbeat_interval_ms": self.heartbeat_interval_ms,
            "heartbeat_timeout_ms": self.heartbeat_timeout_ms,
            "server_device_id": self.server_device_id,
            "voice_model_base_url": self.voice_model_base_url,
            "agent_model_name": self.agent_model_name,
            "voice_model_name": self.voice_model_name,
            "voice_model_voice": self.voice_model_voice,
            "voice_reply_mode": self.voice_reply_mode,
            "voice_omni_realtime_model_name": self.voice_omni_realtime_model_name,
            "voice_omni_realtime_url": self.voice_omni_realtime_url,
            "voice_omni_photo_wait_ms": self.voice_omni_photo_wait_ms,
            "voice_conversation_mode": self.voice_conversation_mode,
            "voice_realtime_turn_detection_type": self.voice_realtime_turn_detection_type,
            "voice_realtime_semantic_vad_threshold": self.voice_realtime_semantic_vad_threshold,
            "voice_realtime_silence_duration_ms": self.voice_realtime_silence_duration_ms,
            "voice_realtime_prefix_padding_ms": self.voice_realtime_prefix_padding_ms,
            "voice_input_mode": self.voice_input_mode,
            "effective_voice_input_mode": self.effective_voice_input_mode(),
            "tts_model_name": self.tts_model_name,
            "tts_voice": self.tts_voice,
            "tts_websocket_api_url": self.tts_websocket_api_url,
            "tts_sample_rate_hz": self.tts_sample_rate_hz,
            "tool_progress_audio_mode": self.tool_progress_audio_mode,
            "voice_model_timeout_ms": self.voice_model_timeout_ms,
            "voice_runs_root": self.voice_runs_root,
            "voice_asr_model_name": self.voice_asr_model_name,
            "voice_asr_mode": self.voice_asr_mode,
            "voice_asr_realtime_model_name": self.voice_asr_realtime_model_name,
            "voice_asr_realtime_timeout_ms": self.voice_asr_realtime_timeout_ms,
            "voice_asr_realtime_max_sentence_silence_ms": self.voice_asr_realtime_max_sentence_silence_ms,
            "voice_session_mode": self.voice_session_mode,
            "max_segment_audio_bytes": self.max_segment_audio_bytes,
            "agent_memory_enabled": int(self.agent_memory_enabled),
            "agent_memory_store_path": self.agent_memory_store_path,
            "agent_memory_max_prompt_items": self.agent_memory_max_prompt_items,
        }

    def parse_device_token_map(self) -> dict[str, str]:
        """解析设备配对令牌映射。

        主要逻辑：
        1. 输入字符串按逗号切分多个键值对。
        2. 每个键值对按 `=` 拆分为 `device_id` 与 `token`。
        3. 自动跳过空片段。

        返回值：
        1. `device_id -> token` 字典。

        异常情况：
        1. 格式错误时抛出 `AppError(INVALID_CONFIG)`。
        """

        result: dict[str, str] = {}
        raw = self.device_token_map.strip()
        if not raw:
            return result

        for pair in raw.split(","):
            text = pair.strip()
            if not text:
                continue
            if "=" not in text:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "DEVICE_TOKEN_MAP 格式错误，必须是 device_id=token",
                    details={"item": text},
                )
            device_id, token = text.split("=", 1)
            device_id = device_id.strip()
            token = token.strip()
            if not device_id or not token:
                raise build_error(
                    ErrorCode.INVALID_CONFIG,
                    "DEVICE_TOKEN_MAP 不能包含空的设备编号或令牌",
                    details={"item": text},
                )
            result[device_id] = token
        return result
