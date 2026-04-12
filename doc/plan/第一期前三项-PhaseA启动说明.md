# 第一期开发 Phase A 启动说明

## 1. 文档目标

本说明用于 Phase A 脚手架阶段，提供三端最小入口启动方式、服务端环境变量说明和测试执行命令。

## 2. 服务端启动

### 2.1 启动命令

在仓库根目录执行：

```bash
PYTHONPATH=server/src python3 -m app.main
```

可选覆盖参数：

```bash
PYTHONPATH=server/src python3 -m app.main --host 127.0.0.1 --port 8081
```

### 2.2 环境变量

- `SERVER_HOST`：监听地址，默认 `0.0.0.0`
- `SERVER_PORT`：监听端口，默认 `8081`
- `APP_ENV`：环境名，默认 `dev`
- `LOG_LEVEL`：日志级别，默认 `INFO`
- `DEVICE_TOKEN_MAP`：设备配对映射，格式 `glass-001=token-a,glass-002=token-b`

### 2.3 基础路由

- `GET /api/health`：健康检查
- `GET /api/config-summary`：配置摘要

## 3. 手机端模拟入口

```bash
python3 phone/src/main.py --once
```

默认持续运行：

```bash
python3 phone/src/main.py
```

## 4. 眼镜端模拟入口

```bash
python3 glass/src/main.py --once
```

默认持续运行：

```bash
python3 glass/src/main.py
```

## 5. 一键测试命令

```bash
bash script/run_phase_a_tests.sh
```

该命令会执行：

- 协议模型与编解码单元测试
- 配置与错误模型单元测试
- 服务端基础路由冒烟测试
