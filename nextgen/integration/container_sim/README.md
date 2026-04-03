# container_sim

## 目录说明

本目录存放容器级模拟联调的支持代码。

## 文件说明

- `README.md`：目录说明文档
- `ws_client.py`：三端直连 WebSocket RPC 客户端
- `runtime_ws_server.py`：三端直连 WebSocket RPC 服务端
- `file_bus.py`：共享文件总线实现
- `runtime_probe.py`：容器运行时探针与状态文件读写支持
- `case_runner.py`：容器级找物场景运行支持
- `services.py`：容器级三端直连服务
- `http_client.py`：旧版 HTTP 联调用客户端，当前仅保留作参考
- `runtime_http_server.py`：旧版 HTTP 联调用服务端，当前仅保留作参考

## 当前阶段说明

当前为容器级模拟第一版，重点验证：

- 三端镜像可构建
- 三端容器可独立启动
- 配置、卷挂载和场景输入可统一管理
- 场景运行结果可写出到共享目录

当前主链路已切换为三端直接 WebSocket 长连接通信。
