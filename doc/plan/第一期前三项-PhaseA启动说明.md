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
PYTHONPATH=server/src python3 -m app.main --host 127.0.0.1 --port 8765
```

### 2.2 环境变量

- `SERVER_HOST`：监听地址，默认 `0.0.0.0`
- `SERVER_PORT`：监听端口，默认 `8765`
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

## 4. 眼镜端入口（ESP-IDF）

眼镜端采用 ESP-IDF C 工程入口，目录为 `glass/src`，启动方式如下：

```bash
cd glass/src
idf.py set-target esp32s3
idf.py build
idf.py flash monitor
```

说明：

1. 当前 Phase A 使用最小待机主循环 `main/glass_main.c`，用于验证端侧入口与主循环可运行。
2. 后续 Phase C 会在该入口基础上接入 WakeNet、端点检测、播放闭麦状态机。

## 5. 一键测试命令

```bash
bash script/run_phase_a_tests.sh
```

该命令会执行：

- 协议模型与编解码单元测试
- 配置与错误模型单元测试
- 服务端基础路由冒烟测试
