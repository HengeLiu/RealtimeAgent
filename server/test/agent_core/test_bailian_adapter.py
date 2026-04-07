from agent_core.model_adapter.bailian_adapter import BailianQwenOmniAdapter



def test_bailian_adapter_parses_structured_tool_calls() -> None:
    def fake_client(_: dict[str, object]) -> dict[str, object]:
        return {
            "text": "done",
            "tool_calls": [
                {"name": "camera_capture_skill", "arguments": {"capture_mode": "single"}},
            ],
        }

    adapter = BailianQwenOmniAdapter(client=fake_client)
    result = adapter.generate_with_tools(prompt="看一下前面", context=[], tools=[])

    assert result.text == "done"
    assert len(result.tool_calls) == 1
    assert result.tool_calls[0].name == "camera_capture_skill"
    assert result.tool_calls[0].arguments["capture_mode"] == "single"
