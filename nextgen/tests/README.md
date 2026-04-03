# tests

## 目录说明

本目录存放 `nextgen/` 的测试代码。

## 文件说明

- `test_models.py`：共享模型测试
- `test_protocol.py`：协议模型测试
- `test_imports.py`：模块导入测试
- `test_find_object_smoke.py`：寻找物体基础冒烟测试
- `test_asr_support.py`：ASR 支持代码测试
- `test_omni_runtime.py`：Omni 运行时支持代码测试
- `test_server_runtime.py`：服务器运行时测试
- `test_state_log_store.py`：状态日志存储测试
- `test_sensor_hub.py`：感知总线测试
- `test_executor_bus.py`：执行总线测试
- `test_gateway_logic.py`：接入层逻辑测试
- `test_local_task_center.py`：本地任务中心测试
- `test_runtime_integration.py`：运行时集成测试
- `test_interface_contracts.py`：接口层测试
- `test_scenarios.py`：场景测试
- `test_find_object_e2e.py`：寻找物体黄金链路端到端测试
- `test_simulation_data.py`：标准测试数据测试
- `test_process_simulation.py`：三进程模拟测试

## 测试分层说明

当前测试已经覆盖以下四层：

- 场景测试：`test_scenarios.py`
- 集成测试：`test_runtime_integration.py`、`test_find_object_smoke.py`、`test_process_simulation.py`
- 接口测试：`test_protocol.py`、`test_interface_contracts.py`
- 单元测试：其余以模块能力和原子逻辑为主的测试文件
