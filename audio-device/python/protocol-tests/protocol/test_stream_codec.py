import json
from pathlib import Path

import pytest

from realtime_agent_device import StreamChunk, StreamChunkCodec


ROOT = Path(__file__).resolve().parents[4]


pytestmark = [pytest.mark.protocol, pytest.mark.device_sdk]


def test_python_device_stream_codec_reads_golden_fixture() -> None:
    """测试目标：确认 Python Device SDK 读取跨语言 stream 黄金样例。

    测试方法：解码 `protocol/data/fixtures/streams/rgb-chunk.bin` 并比对 header。
    预期结果：SDK 编解码结果与协议样例一致。
    """

    header = json.loads((ROOT / "protocol/data/fixtures/streams/rgb-header.json").read_text(encoding="utf-8"))
    raw = (ROOT / "protocol/data/fixtures/streams/rgb-chunk.bin").read_bytes()

    data = StreamChunkCodec.decode_header(raw)

    for key, value in header.items():
        assert data[key] == value
    assert data["payload"] == b"abc"


def test_python_device_stream_codec_round_trips_chunk_object() -> None:
    """测试目标：确认 Python Device SDK 的 StreamChunk 对象可无损往返。

    测试方法：编码再解码一帧 `sensor.mic` chunk。
    预期结果：解码后的对象等于原对象。
    """

    chunk = StreamChunk(
        user_id="user-001",
        session_id="dev-001",
        stream_id="stream-001",
        stream_type="sensor.mic",
        seq=1,
        payload=b"abc",
        final=True,
    )

    assert StreamChunkCodec.decode(StreamChunkCodec.encode(chunk)) == chunk


def test_python_device_stream_codec_rejects_mismatch() -> None:
    """测试目标：确认 Python Device SDK 会拒绝损坏的二进制帧。

    测试方法：编码合法 chunk 后截断 payload。
    预期结果：解码抛出 payload_size mismatch。
    """

    raw = StreamChunkCodec.encode_header({"stream_id": "s1"}, b"abc")
    with pytest.raises(ValueError, match="payload_size mismatch"):
        StreamChunkCodec.decode_header(raw[:-1])
