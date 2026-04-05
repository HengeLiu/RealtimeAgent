# tests

## 目录说明

本目录存放 `nextgen/` 的测试代码。

## 文件说明

- `test_models.py`：共享模型测试
- `test_protocol.py`：协议模型测试
- `test_imports.py`：模块导入测试
- `test_asr_support.py`：ASR 支持代码测试
- `test_omni_runtime.py`：Omni 运行时支持代码测试
- `test_server_runtime.py`：服务器运行时测试
- `test_state_log_store.py`：状态日志存储测试
- `test_sensor_hub.py`：感知总线测试
- `test_executor_bus.py`：执行总线测试
- `test_interface_contracts.py`：接口层测试
- `test_simulation_data.py`：标准测试数据测试
- `test_local_glass_hardware.py`：本机伪眼镜硬件适配测试
- `test_glass_ui_routes.py`：眼镜独立 UI 路由测试
- `test_voice_session_manager.py`：语音会话管理测试

## 测试分层说明

当前测试主要覆盖以下三层：

- 运行时与服务层测试：`test_server_runtime.py`、`test_voice_session_manager.py`
- 接口测试：`test_protocol.py`、`test_interface_contracts.py`
- 单元测试：其余以模块能力和原子逻辑为主的测试文件
