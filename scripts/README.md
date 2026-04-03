# scripts

## 目录说明

本目录存放便于开发、联调和验证使用的启动脚本。

## 文件说明

- `README.md`：目录说明文档
- `run_find_object_process_simulation.py`：按标准 `case_id` 运行寻找物体三进程模拟
- `run_server_control_runtime.py`：启动服务器控制面参考实现
- `run_glass_control_runtime.py`：启动眼镜控制面参考实现
- `run_phone_control_runtime.py`：启动手机控制面参考实现
- `run_real_control_plane_demo.py`：启动真实场景控制面三进程 demo
- `run_real_find_object_demo.py`：启动真实场景找物通讯三进程 demo
- `run_real_peer_link_recovery_demo.py`：启动真实场景任务级连接恢复 demo
- `run_simulated_runtime.py`：启动单个模拟运行时 HTTP 服务并写出探针文件
- `run_container_find_object_case.py`：在容器环境下执行标准找物场景并写出结果

## 使用原则

- 脚本只负责组织运行入口，不在这里承载核心业务逻辑
- 核心联调逻辑应继续放在 `nextgen/integration/` 下
- 脚本输入应尽量复用 `testdata/` 中定义的标准场景编号
