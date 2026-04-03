# START_UP

## 目的

这份文档记录的是当前仓库在本机实际验证过的启动过程，不是理想化流程。

结论先说：

- 推荐使用 `uv` 创建本地 `.venv`
- 推荐使用 Python `3.11`
- 当前代码可以启动 FastAPI 服务
- 当前导航相关模型默认缺失，所以服务能起来，但很多视觉能力不可用
- 当前 macOS 环境下需要用 `SDL_AUDIODRIVER=dummy` 绕过 `pygame` 音频初始化失败

---

## 1. 环境前提

建议环境：

- macOS / Linux
- Python `3.11`
- 已安装 `uv`

本仓库当前不建议直接使用系统 Python `3.13`，因为项目 README 和依赖都明显面向 Python `3.9 - 3.11`。

检查版本：

```bash
python3 --version
uv --version
```

---

## 2. 创建虚拟环境

项目作者习惯用 `uv`，并且当前目录下使用 `.venv`。

在仓库根目录执行：

```bash
UV_CACHE_DIR=/tmp/uv-cache uv venv .venv --python 3.11
```

如果你的机器已经有可用的 `3.11`，上面的命令会直接创建环境。

激活环境：

```bash
source .venv/bin/activate
```

如果 `.venv` 里没有 `pip`，补一次：

```bash
.venv/bin/python -m ensurepip --upgrade
```

---

## 3. 安装依赖

### 3.1 正常依赖安装

当前环境里，`pip` 的全局镜像源可能被配置成清华源。如果你遇到 SSL 或“找不到包”的问题，直接显式使用官方 PyPI：

```bash
.venv/bin/python -m pip install -i https://pypi.org/simple -r requirements.txt
```

### 3.2 已知问题：`pyaudio`

在 macOS 上，`pyaudio==0.2.14` 可能因为缺少 `portaudio.h` 编译失败，报错类似：

```text
fatal error: 'portaudio.h' file not found
```

这是系统依赖问题，不是项目代码问题。

如果你只想先把服务跑起来，可以先跳过 `pyaudio`：

```bash
rg -v '^pyaudio==' requirements.txt | .venv/bin/python -m pip install -i https://pypi.org/simple -r /dev/stdin
```

当前代码里没有直接 `import pyaudio`，所以跳过它不影响 `app_main.py` 启动。

如果你想完整安装它，可以先安装 `portaudio`，然后再安装 `pyaudio`：

```bash
brew install portaudio
.venv/bin/python -m pip install -i https://pypi.org/simple pyaudio
```

---

## 4. 已知依赖兼容问题

### `openai==1.3.5` 与 `httpx`

项目依赖里固定了：

- `openai==1.3.5`

但如果环境里安装到过新的 `httpx`，启动时可能报错：

```text
TypeError: Client.__init__() got an unexpected keyword argument 'proxies'
```

这是 `openai 1.3.5` 和较新 `httpx` 的兼容问题。

修复方式：

```bash
.venv/bin/python -m pip install -i https://pypi.org/simple 'httpx<0.28'
```

---

## 5. 启动命令

当前 macOS 环境下，直接启动会在导入 `yolomedia.py` 时触发：

```text
pygame.error: CoreAudio error ...
```

原因是 `yolomedia.py` 顶层会直接执行 `pygame.mixer.init()`，而当前系统音频设备不可用。

因此建议使用下面这个命令启动：

```bash
SDL_AUDIODRIVER=dummy .venv/bin/python app_main.py
```

如果你是在 Codex / 沙箱类环境中启动，应用还可能因为绑定 UDP `12345` 失败而退出。这种情况下需要在沙箱外启动。

---

## 6. 启动成功后的检查

服务启动后，默认监听：

- HTTP: `8081`
- UDP IMU: `12345`

健康检查：

```bash
curl http://127.0.0.1:8081/api/health
```

正常返回：

```text
OK
```

浏览器访问：

```text
http://127.0.0.1:8081
```

---

## 7. 当前启动日志里常见但不致命的问题

下面这些日志目前可以接受，不会阻止服务启动：

- `AIGLASS_DEVICE=cuda:0 但未检测到 CUDA，将回退到 CPU`
- `Matplotlib created a temporary cache directory ...`
- `on_event is deprecated`
- `Hello from the pygame community`

这些属于：

- 本机没有 CUDA
- 缓存目录不可写
- FastAPI 新版本对旧事件钩子的弃用提醒
- `pygame` 正常打印

---

## 8. 当前功能限制

当前仓库里 `model/` 目录只有：

- `hand_landmarker.task`

缺少至少以下模型时，相关功能不可用：

- `yolo-seg.pt`
- `yoloe-11l-seg.pt`
- `trafficlight.pt`
- `shoppingbest5.pt`

如果这些模型缺失，启动日志通常会看到类似输出：

```text
[NAVIGATION] 错误：找不到模型文件 ...
[TRAFFIC] 模型加载失败 ...
```

这时服务仍可能启动，但下列功能基本不可用：

- 盲道导航
- 障碍物检测
- 红绿灯检测
- 物品查找

也就是说，当前更准确的状态是：

- Web 服务可启动
- 页面可打开
- 音视频链路和部分交互链路可初始化
- 视觉导航能力取决于模型文件是否补齐

---

## 9. 推荐的最小可运行流程

如果你只是想先把服务跑起来，建议按下面顺序：

1. 创建环境

```bash
UV_CACHE_DIR=/tmp/uv-cache uv venv .venv --python 3.11
```

2. 补 `pip`

```bash
.venv/bin/python -m ensurepip --upgrade
```

3. 安装依赖，先跳过 `pyaudio`

```bash
rg -v '^pyaudio==' requirements.txt | .venv/bin/python -m pip install -i https://pypi.org/simple -r /dev/stdin
```

4. 修正 `httpx`

```bash
.venv/bin/python -m pip install -i https://pypi.org/simple 'httpx<0.28'
```

5. 启动服务

```bash
SDL_AUDIODRIVER=dummy .venv/bin/python app_main.py
```

6. 检查健康状态

```bash
curl http://127.0.0.1:8081/api/health
```

---

## 10. 后续建议

如果要把这个项目变成“新机器可直接启动”的状态，建议优先做这几件事：

1. 提供 `.env.example`
2. 去掉代码里的 Windows 默认模型路径
3. 把 `pygame.mixer.init()` 从 `yolomedia.py` 顶层移到真正需要时再初始化
4. 在依赖里显式固定兼容版 `httpx`
5. 为 `pyaudio` 提供可选安装说明，而不是把它当成硬依赖
6. 补齐 `model/` 目录所需模型或提供可配置下载方式
