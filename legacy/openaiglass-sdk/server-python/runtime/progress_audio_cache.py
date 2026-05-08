"""工具前置播报音频缓存管理器。

本模块负责 Tool `progress_message` 的静态音频缓存，包括启动预热、缓存指纹、
WAV 读写、过期文件清理和运行时读取。它不负责播放仲裁和设备控制消息，
缓存命中的 PCM 仍由语音运行时写入统一播放流。
"""

from __future__ import annotations

import hashlib
import json
import os
import threading
import wave
from typing import Any

from agent_core import AgentFacade
from infra.config import ServerSettings
from infra.errors import ErrorCode, build_error
from infra.logging import LogContext, log_debug, log_info
from runtime.audio_utils import PCM16StreamResampler, build_wav_bytes
from runtime.voice_constants import SERVER_CHANNELS, SERVER_SAMPLE_RATE_HZ, SERVER_SAMPLE_WIDTH_BYTES
from runtime.voice_models import ModelChunk
from runtime.voice_state import ProgressAudioCacheEntry


class ProgressAudioCacheManager:
    """管理工具前置播报静态音频缓存。

    主要功能：
    1. 启动时读取 ToolRegistry 中的 `progress_message` 并预生成缓存。
    2. 按 TTS 模型、音色、主回复音频来源和播放格式生成缓存指纹。
    3. 删除过期、损坏或与当前配置不一致的旧缓存。
    4. 在工具调用前提供已缓存 PCM，未命中时让上层回退实时合成。

    主要属性：
    1. `cache`：内存中的文本到缓存条目映射。
    2. `cache_lock`：保护 `cache` 的锁。
    3. `ready`：启动预加载是否完成的事件。
    """

    def __init__(
        self,
        *,
        settings: ServerSettings,
        model_client,
        agent_facade: AgentFacade,
        logger,
    ) -> None:
        """初始化工具前置播报缓存管理器。

        主要逻辑：
        1. 保存配置、模型客户端、AgentFacade 和日志对象。
        2. 创建内存缓存、缓存锁和预加载完成事件。

        参数：
        1. `settings`：服务端配置。
        2. `model_client`：可创建流式 TTS 会话的模型客户端。
        3. `agent_facade`：用于读取 ToolRegistry 中的播报文案。
        4. `logger`：SDK 日志对象。

        返回值：
        1. 无返回值。

        异常情况：
        1. 初始化本身不访问网络和文件，不抛出业务异常。
        """

        self._settings = settings
        self._model_client = model_client
        self._agent_facade = agent_facade
        self._logger = logger
        self.cache: dict[str, ProgressAudioCacheEntry] = {}
        self.cache_lock = threading.Lock()
        self.ready = threading.Event()

    def start_preload(self) -> None:
        """启动工具前置播报音频缓存预生成。

        主要逻辑：
        1. 先检查全局工具前置播报开关。
        2. 从当前 agent-core 工具注册表读取所有 `progress_message`。
        3. 离线缓存模式下，服务启动后在后台生成或加载本地 WAV 缓存。
        4. 没有播报文案、文案被删除或文案变化时，同步清理旧缓存。

        返回值：
        1. 无返回值。

        异常情况：
        1. 预生成失败只写 DEBUG 日志，不阻塞服务启动。
        """

        if not self._settings.tool_progress_audio_enabled:
            self.clear_on_startup(reason="disabled")
            return
        progress_provider = self.provider()
        if self._settings.tool_progress_audio_mode != "cached" or progress_provider != "tts":
            self.ready.set()
            log_info(
                self._logger,
                (
                    "工具前置播报音频缓存已跳过 "
                    f"mode={self._settings.tool_progress_audio_mode} provider={progress_provider}"
                ),
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )
            return
        if not self._settings.dashscope_api_key.strip():
            self.ready.set()
            return
        try:
            tool_registry = self._agent_facade.get_tool_registry()
            list_messages = getattr(tool_registry, "list_progress_messages", None)
            if not callable(list_messages):
                self.ready.set()
                return
            progress_messages = list_messages()
        except Exception as exc:
            self.ready.set()
            log_debug(
                self._logger,
                f"工具前置播报缓存读取工具列表失败，已跳过: reason={exc!r}",
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )
            return
        if not progress_messages:
            self.clear_on_startup(reason="no_progress_messages")
            return
        threading.Thread(
            target=self.preload,
            args=(progress_messages,),
            name="progress-audio-cache-preload",
            daemon=True,
        ).start()

    def clear_on_startup(self, *, reason: str) -> None:
        """启动时把工具前置播报缓存收敛为空。

        主要逻辑：
        1. 当全局关闭或当前工具没有播报文案时，旧缓存不再可靠。
        2. 清理缓存目录中的旧 WAV 和元数据。
        3. 无论清理是否成功，都标记预加载结束，避免工具调用链路等待。

        参数：
        1. `reason`：清理原因，用于日志排查。

        返回值：
        1. 无返回值。

        异常情况：
        1. 缓存目录不存在或无法读取时静默跳过。
        """

        cache_dir = self.cache_dir()
        self.prune_stale(cache_dir=cache_dir, expected_profiles={})
        with self.cache_lock:
            self.cache.clear()
        self.ready.set()
        log_info(
            self._logger,
            f"工具前置播报音频缓存已收敛为空 reason={reason}",
            LogContext(session_id="progress_audio_cache", device_id="server"),
        )

    def preload(self, progress_messages: list[tuple[str, str]]) -> None:
        """批量加载或生成工具前置播报音频缓存。

        主要逻辑：
        1. 对相同播报文本去重，避免重复生成相同音频。
        2. 先按当前工具集合和配置清理旧缓存。
        3. 逐条读取或生成缓存，失败时记录 DEBUG 并继续处理其他文本。

        参数：
        1. `progress_messages`：Tool 名称和播报文本列表。

        返回值：
        1. 无返回值。

        异常情况：
        1. 单条缓存失败不向外抛出，整体预加载结束后设置 ready。
        """

        unique_messages: dict[str, str] = {}
        for tool_name, message in progress_messages:
            text = message.strip()
            if text and text not in unique_messages:
                unique_messages[text] = tool_name
        cache_dir = self.cache_dir()
        os.makedirs(cache_dir, exist_ok=True)
        expected_profiles = {
            self.cache_key(text): self.cache_profile(text)
            for text in unique_messages
        }
        self.prune_stale(cache_dir=cache_dir, expected_profiles=expected_profiles)
        succeeded = 0
        for text, tool_name in unique_messages.items():
            try:
                entry = self.load_or_create_entry(
                    tool_name=tool_name,
                    text=text,
                    cache_dir=cache_dir,
                )
            except Exception as exc:  # noqa: BLE001 - 启动预生成失败不应影响主服务
                log_debug(
                    self._logger,
                    f"工具前置播报音频缓存生成失败: tool={tool_name} text={text!r} reason={exc!r}",
                    LogContext(session_id="progress_audio_cache", device_id="server"),
                )
                continue
            with self.cache_lock:
                self.cache[text] = entry
            succeeded += 1
        self.ready.set()
        log_info(
            self._logger,
            (
                "工具前置播报音频缓存预加载完成 "
                f"message_count={len(unique_messages)} cached_count={succeeded} cache_dir={cache_dir}"
            ),
            LogContext(session_id="progress_audio_cache", device_id="server"),
        )

    def load_or_create_entry(
        self,
        *,
        tool_name: str,
        text: str,
        cache_dir: str,
    ) -> ProgressAudioCacheEntry:
        """加载或创建单条工具前置播报音频缓存。

        主要逻辑：
        1. 根据当前配置生成缓存 key 和元数据指纹。
        2. 优先读取已存在且元数据匹配的 WAV。
        3. 未命中时调用 TTS 生成 PCM，并写入 WAV 和元数据。

        参数：
        1. `tool_name`：播报所属 Tool 名称。
        2. `text`：播报文本。
        3. `cache_dir`：缓存目录。

        返回值：
        1. 缓存条目。

        异常情况：
        1. TTS 返回空音频时抛出 `ErrorCode.INTERNAL_ERROR`。
        2. 文件写入失败会向外抛出底层异常，由预加载逻辑记录并跳过。
        """

        profile = self.cache_profile(text)
        cache_key = self.cache_key(text)
        wav_path = os.path.join(cache_dir, f"{cache_key}.wav")
        metadata_path = os.path.join(cache_dir, f"{cache_key}.json")
        pcm_bytes = self.read_cached_wav(
            wav_path,
            metadata_path=metadata_path,
            expected_profile=profile,
        )
        if pcm_bytes is None:
            pcm_bytes = self.synthesize_text_to_pcm(text)
            with open(wav_path, "wb") as file:
                file.write(build_wav_bytes(pcm_bytes, SERVER_SAMPLE_RATE_HZ, SERVER_CHANNELS))
            self.write_metadata(metadata_path, profile)
        return ProgressAudioCacheEntry(
            tool_name=tool_name,
            text=text,
            wav_path=wav_path,
            metadata_path=metadata_path,
            profile=profile,
            pcm_bytes=pcm_bytes,
        )

    def synthesize_text_to_pcm(self, text: str) -> bytes:
        """把一段前置播报文本合成为 16k 单声道 PCM。

        主要逻辑：
        1. 创建当前 TTS 模型的流式会话。
        2. 把 TTS 返回的音频分片重采样为服务端播放格式。
        3. 汇总所有 PCM 分片并返回。

        参数：
        1. `text`：要合成的播报文本。

        返回值：
        1. 16k 单声道 PCM 字节。

        异常情况：
        1. TTS 没有返回音频时抛出 `ErrorCode.INTERNAL_ERROR`。
        """

        pcm_parts: list[bytes] = []
        resampler_box: list[PCM16StreamResampler | None] = [None]

        def _on_chunk(chunk: ModelChunk) -> None:
            if not chunk.audio_pcm_bytes:
                return
            resampler = resampler_box[0]
            if resampler is None or chunk.sample_rate_hz != resampler._input_rate_hz:
                resampler = PCM16StreamResampler(chunk.sample_rate_hz, SERVER_SAMPLE_RATE_HZ)
                resampler_box[0] = resampler
            pcm = resampler.push(chunk.audio_pcm_bytes, final=False)
            if pcm:
                pcm_parts.append(pcm)

        tts_session = self._model_client.create_streaming_tts_session(
            settings=self._settings,
            on_chunk=_on_chunk,
        )
        tts_session.push_text(text)
        tts_session.finish()
        if resampler_box[0] is not None:
            tail = resampler_box[0].push(b"", final=True)
            if tail:
                pcm_parts.append(tail)
        pcm_bytes = b"".join(pcm_parts)
        if not pcm_bytes:
            raise build_error(
                ErrorCode.INTERNAL_ERROR,
                "工具前置播报 TTS 返回空音频",
                details={"text": text},
            )
        return pcm_bytes

    def read_cached_wav(
        self,
        wav_path: str,
        *,
        metadata_path: str,
        expected_profile: dict[str, Any],
    ) -> bytes | None:
        """读取本地缓存 WAV，格式不符合当前播放要求时返回 None。

        主要逻辑：
        1. 检查 WAV 文件和元数据是否存在且匹配当前配置。
        2. 校验采样率、声道数和采样宽度。
        3. 不匹配或读取失败时删除对应缓存文件。

        参数：
        1. `wav_path`：WAV 文件路径。
        2. `metadata_path`：元数据文件路径。
        3. `expected_profile`：当前期望的缓存指纹。

        返回值：
        1. 命中时返回 PCM 字节，否则返回 None。

        异常情况：
        1. 文件损坏或格式不匹配时不向外抛出，会删除缓存并返回 None。
        """

        if not os.path.exists(wav_path):
            return None
        if not self.metadata_matches(metadata_path, expected_profile):
            self.remove_files(wav_path, metadata_path)
            return None
        try:
            with wave.open(wav_path, "rb") as reader:
                if (
                    reader.getframerate() != SERVER_SAMPLE_RATE_HZ
                    or reader.getnchannels() != SERVER_CHANNELS
                    or reader.getsampwidth() != SERVER_SAMPLE_WIDTH_BYTES
                ):
                    self.remove_files(wav_path, metadata_path)
                    return None
                return reader.readframes(reader.getnframes())
        except Exception:
            self.remove_files(wav_path, metadata_path)
            return None

    def metadata_matches(self, metadata_path: str, expected_profile: dict[str, Any]) -> bool:
        """检查缓存元数据是否与当前模型和音色配置一致。

        主要逻辑：
        1. 元数据必须存在，旧版本没有元数据的 WAV 视为过期。
        2. 元数据中的生成方式、TTS 模型、当前最终播报模型和音色都必须一致。
        3. 任意字段不一致都会触发删除并重新生成。

        参数：
        1. `metadata_path`：元数据文件路径。
        2. `expected_profile`：当前期望的缓存指纹。

        返回值：
        1. 匹配返回 True，否则返回 False。

        异常情况：
        1. 元数据读取或 JSON 解析失败时返回 False。
        """

        try:
            with open(metadata_path, "r", encoding="utf-8") as file:
                metadata = json.load(file)
        except Exception:
            return False
        return metadata == expected_profile

    def write_metadata(self, metadata_path: str, profile: dict[str, Any]) -> None:
        """写入工具前置播报缓存元数据。

        主要逻辑：
        1. 使用 UTF-8 写入 JSON。
        2. 对 key 排序，便于人工排查缓存差异。

        参数：
        1. `metadata_path`：元数据文件路径。
        2. `profile`：要写入的缓存指纹。

        返回值：
        1. 无返回值。

        异常情况：
        1. 文件写入失败会向外抛出底层异常。
        """

        with open(metadata_path, "w", encoding="utf-8") as file:
            json.dump(profile, file, ensure_ascii=False, sort_keys=True, indent=2)

    def remove_files(self, wav_path: str, metadata_path: str) -> None:
        """删除一组过期或损坏的工具前置播报缓存文件。

        主要逻辑：
        1. 分别删除 WAV 和元数据。
        2. 删除失败只写 DEBUG 日志，不影响主服务启动。

        参数：
        1. `wav_path`：WAV 文件路径。
        2. `metadata_path`：元数据文件路径。

        返回值：
        1. 无返回值。

        异常情况：
        1. 删除失败会被捕获并记录。
        """

        for path in (wav_path, metadata_path):
            try:
                if os.path.exists(path):
                    os.remove(path)
            except OSError as exc:
                log_debug(
                    self._logger,
                    f"工具前置播报缓存删除失败，已忽略: path={path} reason={exc!r}",
                    LogContext(session_id="progress_audio_cache", device_id="server"),
                )

    def prune_stale(
        self,
        *,
        cache_dir: str,
        expected_profiles: dict[str, dict[str, Any]],
    ) -> None:
        """启动时清理与当前播报模型、生成方式或音色不一致的旧缓存。

        主要逻辑：
        1. 扫描缓存目录中的 WAV 和 JSON 文件。
        2. 不在当前工具集合中的缓存会被删除。
        3. 当前工具仍存在但元数据不匹配的缓存也会被删除。

        参数：
        1. `cache_dir`：缓存目录。
        2. `expected_profiles`：当前期望保留的缓存 key 到指纹映射。

        返回值：
        1. 无返回值。

        异常情况：
        1. 缓存目录不存在或无法读取时静默返回。
        """

        removed = 0
        try:
            names = os.listdir(cache_dir)
        except OSError:
            return
        basenames = {name.rsplit(".", 1)[0] for name in names if name.endswith((".wav", ".json"))}
        for basename in basenames:
            wav_path = os.path.join(cache_dir, f"{basename}.wav")
            metadata_path = os.path.join(cache_dir, f"{basename}.json")
            expected_profile = expected_profiles.get(basename)
            should_remove = expected_profile is None
            if expected_profile is not None and not self.metadata_matches(
                metadata_path,
                expected_profile,
            ):
                should_remove = True
            if should_remove:
                self.remove_files(wav_path, metadata_path)
                removed += 1
        if removed:
            log_info(
                self._logger,
                f"工具前置播报旧缓存已清理 removed_count={removed} cache_dir={cache_dir}",
                LogContext(session_id="progress_audio_cache", device_id="server"),
            )

    def cache_dir(self) -> str:
        """返回工具前置播报音频缓存目录。

        返回值：
        1. 当前配置下的缓存目录路径。

        异常情况：
        1. 本函数不访问文件系统，不抛出业务异常。
        """

        return os.path.join(self._settings.voice_runs_root, "progress-audio-cache")

    def cache_key(self, text: str) -> str:
        """按当前前置播报与最终播报配置生成稳定缓存键。

        主要逻辑：
        1. 基于缓存指纹生成 JSON。
        2. 使用 SHA-256 前 24 位作为文件名，避免播报文本直接出现在路径中。

        参数：
        1. `text`：播报文本。

        返回值：
        1. 缓存文件基名。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        payload = self.cache_profile(text)
        raw = json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
        return hashlib.sha256(raw).hexdigest()[:24]

    def cache_profile(self, text: str) -> dict[str, Any]:
        """生成工具前置播报缓存指纹。

        主要逻辑：
        1. 记录缓存实际生成方式，目前为专用 TTS。
        2. 同时记录当前最终回复的音频模型和音色。
        3. 采样率和播放格式也纳入指纹，防止格式不一致的 WAV 被误用。

        参数：
        1. `text`：播报文本。

        返回值：
        1. 可序列化的缓存指纹。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        if self._settings.effective_voice_server_mode() == "omni_server":
            reply_audio_provider = "omni_realtime"
            reply_model_name = self._settings.voice_omni_realtime_model_name
            reply_voice = self._settings.voice_model_voice
        else:
            reply_audio_provider = "tts"
            reply_model_name = self._settings.tts_model_name
            reply_voice = self._settings.tts_voice
        return {
            "cache_schema": 2,
            "text": text,
            "tool_progress_audio_mode": self._settings.tool_progress_audio_mode,
            "progress_audio_provider": "tts",
            "tts_model_name": self._settings.tts_model_name,
            "tts_voice": self._settings.tts_voice,
            "tts_sample_rate_hz": self._settings.tts_sample_rate_hz,
            "reply_audio_provider": reply_audio_provider,
            "reply_model_name": reply_model_name,
            "reply_voice": reply_voice,
            "playback_sample_rate_hz": SERVER_SAMPLE_RATE_HZ,
            "channels": SERVER_CHANNELS,
        }

    def provider(self) -> str:
        """返回工具前置播报应该使用的音频生成方。

        主要逻辑：
        1. 主回复是 Omni Realtime 音频直出时，前置播报也使用 Omni Realtime。
        2. 主回复是 Agent 文本加独立 TTS 时，前置播报使用同一个 TTS 服务。

        返回值：
        1. `omni_realtime` 或 `tts`。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        return "omni_realtime" if self._settings.effective_voice_server_mode() == "omni_server" else "tts"

    def get_cached_pcm(self, text: str) -> bytes | None:
        """读取已预生成的前置播报 PCM。

        主要逻辑：
        1. 空文本直接返回 None。
        2. 预加载未完成时最多等待 50ms，避免工具调用长期阻塞。
        3. 从内存缓存中读取 PCM，未命中返回 None。

        参数：
        1. `text`：播报文本。

        返回值：
        1. 命中时返回 PCM 字节，否则返回 None。

        异常情况：
        1. 本函数不抛出业务异常。
        """

        normalized = text.strip()
        if not normalized:
            return None
        if not self.ready.is_set():
            self.ready.wait(timeout=0.05)
        with self.cache_lock:
            entry = self.cache.get(normalized)
            return entry.pcm_bytes if entry is not None else None
