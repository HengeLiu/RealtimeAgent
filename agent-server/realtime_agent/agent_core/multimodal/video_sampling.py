from __future__ import annotations


def video_sampling_not_available_reason() -> str:
    """返回当前版本视频抽帧未启用的说明。

    主要功能：为运行产物提供稳定诊断文本。首版先支持 provider 原生 video block，
    抽帧 fallback 在后续接入真实视频资产和 OpenCV 处理链路时实现。
    """

    return "video frame sampling is not enabled in this phase"
