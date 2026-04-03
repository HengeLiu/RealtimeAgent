"""标准测试数据测试。"""

from nextgen.integration.smoke.testdata_loader import StandardTestDataLoader


def test_standard_testdata_loader_loads_voice_cases() -> None:
    """验证标准测试数据加载器可以加载语音 case。"""

    loader = StandardTestDataLoader()
    voice_cases = loader.load_voice_cases()

    assert len(voice_cases) >= 5
    assert any(item["expected_intent"] == "find_object" for item in voice_cases)


def test_standard_testdata_loader_builds_find_object_case() -> None:
    """验证标准测试数据加载器可以构造找物联调输入。"""

    loader = StandardTestDataLoader()
    case = loader.build_find_object_case("find_object_phone_center_001")

    assert case["voice_text"] == "帮我找一下手机"
    assert case["target_name"] == "手机"
    assert case["expected_final_status"] == "completed"
    assert len(case["candidates"]) == 1
    assert case["hand_observation"] is None


def test_standard_testdata_loader_builds_hand_observation_case() -> None:
    """验证包含手部观测的找物 case 可以正确构造。"""

    loader = StandardTestDataLoader()
    case = loader.build_find_object_case("find_object_phone_grasp_001")

    assert case["hand_observation"] is not None
    assert case["hand_observation"].grasp_detected is True
    assert case["expected_hint_contains"] == "保持"
