# iteration-v8：账号级设备组织

对应 SDK 版本：sdk-v9

## 背景

功能开发计划中后续会出现多副眼镜、多台手机和多用户并存场景。旧 SDK 只维护设备组和一对一绑定，能够支撑单账号联调，但缺少账号级索引和跨账号隔离。

## 本轮改动

1. 新增 `DeviceAccount` 公共模型。
2. `DeviceGroupRuntime.register_device()` 支持 `account_id/user_id`。
3. `DeviceGroupRuntime.bind_devices()` 增加跨账号绑定拒绝。
4. `DeviceGroupRuntime.build_snapshot()` 增加 `accounts` 快照。
5. `ControlRuntime` 从 `device.register` 读取账号字段，写入连接快照、设备 metadata 和注册响应。
6. 自动绑定兜底策略增加账号一致性判断。
7. 更新功能开发指南、SDK 支持情况说明和 `sdk-version`。

## 验收

已通过：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/unit/test_sdk_phase_two.py -q
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind uv run --with pytest python -m pytest openaiglass-sdk/tests/integration/test_control_register_flow.py -q
```

覆盖点：

1. 同账号眼镜和手机绑定后出现在同一账号快照。
2. 跨账号绑定被拒绝。
3. 控制面注册响应和运行态快照包含账号字段。
4. 旧的无账号单眼镜单手机自动绑定仍保持兼容。

## 后续边界

sdk-v9 不是完整权限系统。授权、审计、组织管理后台、远程配置中心和多实例设备目录仍需后续专项处理。
