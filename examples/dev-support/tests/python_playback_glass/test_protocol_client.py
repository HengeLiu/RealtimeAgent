from audio_chat_python_playback_glass.protocol_client import decode_stream_chunk, encode_stream_chunk, ws_url


def test_stream_chunk_codec_matches_browser_glass_shape() -> None:
    """测试目标：验证 Python 回放端使用 browser-glass 相同的二进制帧形状。

    测试方法：编码一帧后检查 4 字节 big-endian header 长度，并解码回原 payload。
    预期结果：header 和 payload 可无损往返。
    """

    payload = b"abc"
    raw = encode_stream_chunk({"stream_id": "s1", "stream_type": "sensor.mic", "seq": 1}, payload)
    header_len = int.from_bytes(raw[:4], "big")

    assert header_len > 0
    decoded = decode_stream_chunk(raw)
    assert decoded["stream_id"] == "s1"
    assert decoded["payload"] == payload
    assert decoded["payload_size"] == len(payload)


def test_ws_url_converts_http_to_websocket() -> None:
    """测试目标：验证端侧协议 URL 由 HTTP server URL 派生。

    测试方法：分别传入 http 和 https URL。
    预期结果：生成 ws 和 wss URL，不硬编码 server 地址。
    """

    assert ws_url("http://127.0.0.1:8765", "/ws/control") == "ws://127.0.0.1:8765/ws/control"
    assert ws_url("https://example.test", "/ws/control") == "wss://example.test/ws/control"
