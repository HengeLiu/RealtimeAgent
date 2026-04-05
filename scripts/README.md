# scripts

## 目录说明

本目录存放便于开发、联调和验证使用的启动脚本。

当前目录已按“真实可运行入口优先”做过一次清理，早期的固定 case 三进程模拟脚本和独立 message bus 启动脚本已移除。
本轮又继续清理掉了历史容器模拟启动脚本，当前目录只保留仍在使用的本机联调和真实参考入口。

## 文件说明

- `README.md`：目录说明文档
- `run_server_control_runtime.py`：启动服务器控制面参考实现
- `run_glass_control_runtime.py`：启动眼镜控制面参考实现
- `run_local_glass_hardware_probe.py`：使用本机相机、麦克风和喇叭探测伪眼镜硬件能力
- `run_local_test_support_service.py`：启动本机多进程联调用测试支持服务
- 手机端正式实现已迁移到 `clients/phone_flutter/`

## 使用原则

- 脚本只负责组织运行入口，不在这里承载核心业务逻辑
- 核心联调逻辑应继续放在 `nextgen/integration/` 下
- 当前优先使用 `run_local_test_support_service.py` 作为 server + glass 联调统一入口
- 手机端请直接运行 `clients/phone_flutter/` 到 iPhone 真机
