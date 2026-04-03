# scripts

## 目录说明

本目录存放便于开发、联调和验证使用的启动脚本。

## 文件说明

- `README.md`：目录说明文档
- `run_find_object_process_simulation.py`：按标准 `case_id` 运行寻找物体三进程模拟

## 使用原则

- 脚本只负责组织运行入口，不在这里承载核心业务逻辑
- 核心联调逻辑应继续放在 `nextgen/integration/` 下
- 脚本输入应尽量复用 `testdata/` 中定义的标准场景编号
