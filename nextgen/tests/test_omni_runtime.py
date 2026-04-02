"""Omni 运行时支持代码测试。"""

from nextgen.apps.server.agent.omni_runtime import (
    OmniStreamPiece,
    build_chat_messages,
    build_chat_request,
)


def test_build_chat_messages_wraps_content_as_user_message() -> None:
    """验证聊天消息构造符合统一结构。"""

    messages = build_chat_messages([{"type": "text", "text": "帮我看看前面是什么"}])
    assert messages == [{"role": "user", "content": [{"type": "text", "text": "帮我看看前面是什么"}]}]


def test_build_chat_request_contains_audio_and_stream_fields() -> None:
    """验证请求体包含音频和流式配置。"""

    request = build_chat_request(
        [{"type": "text", "text": "帮我找一下杯子"}],
        voice="Cherry",
        audio_format="wav",
        model="qwen-omni-turbo",
    )
    assert request["model"] == "qwen-omni-turbo"
    assert request["audio"]["voice"] == "Cherry"
    assert request["audio"]["format"] == "wav"
    assert request["stream"] is True


def test_omni_stream_piece_holds_text_and_audio() -> None:
    """验证分片对象能稳定承接文本和音频。"""

    piece = OmniStreamPiece(text_delta="你好", audio_b64="ZmFrZQ==")
    assert piece.text_delta == "你好"
    assert piece.audio_b64 == "ZmFrZQ=="
