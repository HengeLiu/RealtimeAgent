"""寻找物体集成占位场景。"""


def build_find_object_scenario() -> dict:
    """构造寻找物体场景占位数据。

    主要功能：
    - 为后续集成测试保留一个固定的场景入口。

    返回值：
    - 最小寻找物体场景定义。
    """

    return {
        "task_name": "find_object",
        "target_name": "手机",
        "status": "scenario_placeholder",
    }
