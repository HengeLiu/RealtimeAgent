"""序列化工具。"""

from typing import Any, Dict


def to_serializable(value: Any) -> Any:
    """将对象转换为可序列化结构。

    主要逻辑：
    - 若对象有 `to_dict` 方法，则优先调用
    - 若对象是基础类型，直接返回
    - 若对象是列表或字典，递归处理

    参数：
    - value：待转换对象

    返回值：
    - 可序列化对象
    """

    if hasattr(value, "to_dict") and callable(value.to_dict):
        return value.to_dict()
    if isinstance(value, list):
        return [to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {key: to_serializable(item) for key, item in value.items()}
    return value
