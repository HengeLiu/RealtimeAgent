import json
from pathlib import Path

import pytest

from audio_chat.protocol import StreamChunk, StreamChunkCodec


ROOT = Path(__file__).resolve().parents[2]
STREAM_ROOT = ROOT / "testdata/protocol/streams"


def test_stream_chunk_codec_matches_golden_binary_fixture() -> None:
    """测试目标：确认 server stream 编解码器与跨语言黄金二进制样例一致。

    测试方法：读取 `rgb-header.json` 和 `rgb-chunk.bin`，解码二进制帧并比对字段。
    预期结果：header 字段和 payload 都能无损还原。
    """

    expected_header = json.loads((STREAM_ROOT / "rgb-header.json").read_text(encoding="utf-8"))
    raw = (STREAM_ROOT / "rgb-chunk.bin").read_bytes()

    decoded = StreamChunkCodec.decode(raw)

    for key, value in expected_header.items():
        if key == "payload_size":
            assert len(decoded.payload) == value
            continue
        assert getattr(decoded, key) == value
    assert decoded.payload == b"abc"


def test_stream_chunk_codec_rejects_payload_size_mismatch() -> None:
    """测试目标：确认 stream chunk 的 payload_size 不一致时会暴露协议错误。

    测试方法：编码合法 chunk 后截断最后一个 payload byte，再尝试解码。
    预期结果：解码器抛出 `payload_size mismatch`。
    """

    raw = StreamChunkCodec.encode(
        StreamChunk(
            user_id="user-001",
            session_id="dev-001",
            stream_id="stream-001",
            stream_type="sensor.rgb",
            seq=0,
            payload=b"abc",
            codec="jpeg",
            sample_rate=1,
            channels=1,
            duration_ms=0,
            final=True,
        )
    )
    with pytest.raises(ValueError, match="payload_size mismatch"):
        StreamChunkCodec.decode(raw[:-1])
