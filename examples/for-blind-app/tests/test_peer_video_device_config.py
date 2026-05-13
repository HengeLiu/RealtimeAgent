from __future__ import annotations

from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[2]


def test_peer_video_device_configs_share_user_and_roles() -> None:
    """测试目标：验证 peer video 默认联调配置能把 phone 和 glass 放进同一用户组。

    测试方法：读取 browser-glass 与 Python phone preview 配置，检查 user_id 和
    properties。
    预期结果：两端 user_id 一致，且分别声明 phone receiver / glass sender 能力。
    """

    browser = yaml.safe_load((ROOT / "dev-support/devices/browser-glass/device.audio-chat.yaml").read_text(encoding="utf-8"))
    phone = yaml.safe_load((ROOT / "dev-support/devices/python-phone/phone.preview.yaml").read_text(encoding="utf-8"))

    assert browser["user_id"] == phone["user_id"] == "user-browser-glass-001"
    assert browser["properties"]["device_role"] == "glass"
    assert browser["properties"]["endpoint.role.glass"] is True
    assert browser["properties"]["peer.video.sender"] is True
    assert phone["properties"]["device_role"] == "phone"
    assert phone["properties"]["endpoint.role.phone"] is True
    assert phone["properties"]["endpoint.compute.vision"] is True
    assert phone["properties"]["peer.video.receiver"] is True
    assert phone["peer_video"]["timeout_seconds"] == 30
    assert phone["peer_video"]["yolo_mock"]["complete_after_frames"] == 0
