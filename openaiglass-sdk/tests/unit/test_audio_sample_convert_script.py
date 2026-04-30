"""音频样例转换脚本测试。"""

from __future__ import annotations

import importlib.util
import struct
import wave
from pathlib import Path

SDK_ROOT = Path(__file__).resolve().parents[2]
CONVERT_SCRIPT = SDK_ROOT / "tests/data/audio-sample/convert_audio_samples.py"


def _load_convert_module():
    """加载音频转换脚本模块。

    返回值：
    1. 已加载的脚本模块。

    异常情况：
    1. importlib 无法创建 loader 时抛出断言异常。
    """

    spec = importlib.util.spec_from_file_location("convert_audio_samples", CONVERT_SCRIPT)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_normalize_existing_wav_rewrites_extensible_header(tmp_path: Path) -> None:
    """测试目标：验证历史扩展 WAV 样例能被规范化为标准 PCM WAV。

    测试方法：
    1. 复制一个 `WAVE_FORMAT_EXTENSIBLE`/48kHz 的历史样例到临时目录。
    2. 调用转换脚本的 `normalize_existing_wav(...)`。
    3. 用 `wave` 和二进制 fmt chunk 同时检查输出格式。

    预期结果：
    1. 输出变成 16kHz、单声道、16bit。
    2. fmt format code 为 1，即标准 `WAVE_FORMAT_PCM`。
    """

    module = _load_convert_module()
    source = SDK_ROOT / "tests/data/audio-sample/wav/我叫文刀文字的文刀锋的刀.wav"
    target = tmp_path / source.name
    target.write_bytes(source.read_bytes())

    module.normalize_existing_wav(target)

    with wave.open(str(target), "rb") as wav_file:
        params = wav_file.getparams()
    assert params.nchannels == 1
    assert params.sampwidth == 2
    assert params.framerate == 16000

    raw = target.read_bytes()
    fmt_index = raw.find(b"fmt ")
    assert fmt_index >= 0
    assert struct.unpack_from("<H", raw, fmt_index + 8)[0] == 1
