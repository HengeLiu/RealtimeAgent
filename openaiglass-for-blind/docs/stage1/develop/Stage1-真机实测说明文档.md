# Stage1 真机实测说明文档

更新时间：2026-04-29

## 1. 当前结论

当前业务侧已按当前 SDK 边界完成收口，可以开始基础真机实测。依赖眼镜到手机视频链路的能力应通过 SDK 标准装配的 `DeviceGroupContext.start_phone_video_link(...)` 验证，不再由业务宿主手动注入视频链路 adapter。

真机实测目标不是证明所有能力都已经产品级可用，而是把离线回放中已经通过的能力放到真实三端链路里验证：

1. 服务端能启动并暴露健康检查、运行态查询和调试接口。
2. iOS 手机端能注册、心跳、绑定，并承载通用手机视频任务。
3. ESP32 眼镜端能注册、心跳、绑定，并执行视频流、通知和语音相关命令。
4. `timer`、`navigation` 可优先验证创建、查询、取消和通知。
5. `find_object`、`traffic_light` 需要重点验证真实 iPhone 视频流、手机任务事件回流和完成后释放视频链路。

## 2. 实测前置条件

### 2.1 硬件和网络

1. Mac、iPhone 和眼镜设备在同一个局域网内。
2. Mac 防火墙允许服务端监听端口，默认 `8765`。
3. iPhone 能访问 `http://<Mac局域网IP>:8765/api/health`。
4. 眼镜能访问 `ws://<Mac局域网IP>:8765/ws/control`。
5. iPhone 摄像头权限已允许。
6. 眼镜摄像头、麦克风、扬声器和 Wi-Fi 配置可用。

### 2.2 软件和配置

1. Mac 已安装 `uv`。
2. Python 使用 3.11 或更高版本。
3. 如需语音模型、ASR 或 TTS，环境中应配置 `DASHSCOPE_API_KEY`。
4. 服务端配置文件应存在：

```text
config/local_server.env
```

5. 手机配置文件应存在：

```text
host/phone/config/AppConfig.plist
```

6. 眼镜本地构建配置应存在：

```text
host/glass/config/local_build.env
```

如缺少眼镜配置，可从模板生成：

```bash
cp config/local_server.env.example config/local_server.env
cp host/phone/config/AppConfig.plist.example host/phone/config/AppConfig.plist
cp host/glass/config/local_build.env.example host/glass/config/local_build.env
```

## 3. 推荐实测顺序

不要一开始直接测语音对话。建议按以下顺序逐层推进：

1. 离线回放确认。
2. 三端配置一致性检查。
3. 服务端单独启动。
4. 手机端注册和心跳。
5. 眼镜端注册和心跳。
6. `timer` 实测。
7. `navigation` 路线准备实测。
8. SDK 补齐视频链路公开装配后，再做手机视频链路、`find_object` 和 `traffic_light` 实测。
9. 语音触发完整业务能力。

## 4. 离线回放确认

在真机前先执行：

```bash
python3 -m compileall -q openaiglass-for-blind/capabilities openaiglass-for-blind/host/server/main.py
```

组件级场景回放入口已删除；当前统一使用 `glass-playback` 设备级数据回放。

业务能力单元测试：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-for-blind:. \
python3 -m unittest discover -s openaiglass-for-blind/tests -p 'test_*.py' -v
```

真实 iPhone + `glass-playback` 前置检查：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind:. \
python3 openaiglass-for-blind/tests/glass_playback_acceptance.py \
  --config openaiglass-for-blind/host/glass-playback/config/real_iphone_find_object.acceptance.example.json \
  --phone-device-id phone-001 \
  --check-only
```

真实 iPhone + `glass-playback` 基础回放：

```bash
PYTHONPATH=openaiglass-sdk/server-python:openaiglass-sdk/glass-playback:openaiglass-for-blind:. \
python3 openaiglass-for-blind/tests/glass_playback_acceptance.py \
  --config openaiglass-for-blind/host/glass-playback/config/real_iphone_find_object.acceptance.example.json \
  --phone-device-id phone-001
```

当前预期结果：

1. 编译通过。
2. SDK 预检通过时，说明业务宿主、配置和边界检查可进入下一步。
3. 业务单元测试通过，说明业务 Tool/Task 对 SDK 公开能力的调用没有基础回归。
4. 设备级真机回放应通过 `glass-playback` 和真实 iPhone 承载，暂不使用 `phone-mock`。
5. 找物体视频链路的完整回放需要 SDK 补齐真实服务端中的 `DeviceGroupContext.start_phone_video_link(...)` 标准装配。

如完整预检可在非沙箱环境执行，运行：

```bash
uv run openaiglass.sdk.preflight --report logs/sdk-preflight-current.json
```

如果只在当前受限沙箱执行，可先跳过需要本地端口或父级 SDK 写入的项：

```bash
uv run openaiglass.sdk.preflight \
```

## 5. 三端配置同步和检查

### 5.1 获取 Mac 局域网 IP

```bash
ifconfig | grep "inet " | grep -v 127.0.0.1
```

服务端本地配置文件为：

```text
config/local_server.env
```

关键配置示例：

```bash
HOST=0.0.0.0
PORT=8765
SERVER_PUBLIC_HOST=192.168.1.23
LOG_LEVEL=DEBUG
DEVICE_TOKEN_MAP=glass-001=pair-demo-token,phone-001=pair-demo-token
```

`SERVER_PUBLIC_HOST` 不需要随着网络变化手动维护；执行同步脚本时会自动探测 Mac 当前局域网 IPv4，并回写到该配置文件。自动探测失败时再手动指定：

```bash
uv run openaiglass.config.sync --app-root openaiglass-for-blind --public-host 192.168.1.23
```

### 5.2 同步配置到手机和眼镜

```bash
uv run openaiglass.config.sync --app-root openaiglass-for-blind
```

该脚本会先自动探测当前本机服务端局域网 IP，并回写 `config/local_server.env` 的 `SERVER_PUBLIC_HOST`；然后根据业务目录下的服务端配置同步业务手机配置和眼镜本地构建配置。手机 App 会从 `host/phone/ios/GlassesVideoReceiver.xcodeproj` 启动，并把 `host/phone/config/AppConfig.plist` 作为资源打包；不再写入 SDK 目录下的 iOS 配置文件。

### 5.3 执行真机前检查

服务端未启动前：

```bash
uv run openaiglass.sdk.live-check \
```

服务端启动后：

```bash
uv run openaiglass.sdk.live-check \
```

检查重点：

1. `config_alignment.ok == true`。
2. `server_health.ok == true`。
3. `DEVICE_TOKEN_MAP` 同时包含手机和眼镜设备编号。
4. 手机 `serverBaseURLString` 等于 `http://<SERVER_PUBLIC_HOST>:8765`。
5. 眼镜 `GLASS_SERVER_WS_URI` 等于 `ws://<SERVER_PUBLIC_HOST>:8765/ws/control`。

## 6. 启动服务端

本地启动：

```bash
uv run openaiglass.server.run --app-module host.server.main --app-root openaiglass-for-blind
```

跟随日志：

```bash
服务端日志由 `openaiglass.server.run` 前台输出，或通过运行时日志配置查看。
```

停止服务端：

```bash
使用 `Ctrl-C` 停止前台服务，或按运行环境停止对应服务进程。
```

健康检查：

```bash
curl http://127.0.0.1:8765/api/health
curl http://<SERVER_PUBLIC_HOST>:8765/api/health
```

运行态检查：

```bash
curl http://127.0.0.1:8765/api/runtime/devices
```

服务端日志重点搜索：

```bash
rg "register|heartbeat|bind|phone|glass|task|notification|error|exception" ../logs/server.log logs/server.log
```

## 7. 启动手机端

业务功能开发者不要直接进入 `openaiglass-sdk` 目录。手机端统一通过当前业务工程入口启动：

```bash
uv run openaiglass.phone.open --app-root openaiglass-for-blind
```

该入口会先执行业务配置同步，再打开 iOS 手机运行时工程。后续操作：

1. 选择真机 iPhone。
2. 构建并运行。
3. 允许摄像头和局域网访问权限。
4. 在 App 页面确认服务端地址、手机设备编号和目标眼镜编号。

当前业务工程的 iOS App 已在启动入口中注册两个业务插件：

1. `find_object_phone_task`：接收视频帧后上报 `phone.vision.find_object.result`。
2. `traffic_light_phone_task`：接收视频帧后上报 `phone.vision.traffic_light.result`。

这说明手机端不再只是视频回显；它已经会把帧交给业务插件处理并通过 SDK 事件接口回传服务端。`find_object_phone_task` 已支持优先加载业务 App 资源中的 CoreML YOLO 模型；如果手机端上报 `source=heuristic`，说明当前构建没有打包模型，只能验证链路，不能代表正式识别效果。红绿灯当前仍是启发式占位实现。真机实测时应先看任务、帧处理、事件上报和通知闭环是否成立，再单独评估模型效果。

首次执行时，如果本地配置文件不存在，脚本会自动从模板创建：

1. `config/local_server.env`
2. `host/phone/config/AppConfig.plist`
3. `host/glass/config/local_build.env`

创建后脚本会停止，并打印每个配置文件里要修改的字段。按提示修改：

1. `config/local_server.env`
   - `SERVER_PUBLIC_HOST`：Mac 当前局域网 IPv4，由 `openaiglass.config.sync` 自动探测并回写。
   - `DEVICE_TOKEN_MAP`：手机和眼镜设备编号对应的配对令牌。
   - `PHONE_DEVICE_ID` / `GLASS_DEVICE_ID`：可选；不填时从 `DEVICE_TOKEN_MAP` 中推断。
2. `host/phone/config/AppConfig.plist`
   - 基础连接配置无需手动改，脚本会根据 `local_server.env` 自动写入 `serverBaseURLString`、`phoneDeviceID`、`pairToken`、`desiredGlassDeviceID`。
3. `host/glass/config/local_build.env`
   - `GLASS_WIFI_PRIMARY_SSID` / `GLASS_WIFI_PRIMARY_PASSWORD`：眼镜连接的 Wi-Fi。
   - 服务器地址和设备令牌无需手动改，脚本会根据 `local_server.env` 自动写入 `GLASS_SERVER_WS_URI`、`GLASS_DEVICE_ID`、`GLASS_PAIR_TOKEN`。

修改完成后重新执行 `uv run openaiglass.phone.open --app-root openaiglass-for-blind`。

如只想先验证手机端工程是否可构建，可执行：

```bash
uv run openaiglass.phone.build-sim --app-root openaiglass-for-blind
```

服务端运行态应看到：

1. `phone-001` 在线。
2. 手机有心跳。
3. 手机上报 camera sink 或视频接收地址。

如手机无法注册，优先检查：

1. iPhone 是否能访问 `http://<SERVER_PUBLIC_HOST>:8765/api/health`。
2. iOS AppConfig 中 `pairToken` 是否和 `DEVICE_TOKEN_MAP` 一致。
3. iOS 是否允许本地网络权限。
4. 服务端日志中是否出现 `pair_token` 或 register 失败。

## 8. 启动眼镜端

构建并启动眼镜：

```bash
uv run openaiglass.glass.start --repo-root .
```

服务端运行态应看到：

1. `glass-001` 在线。
2. 眼镜有心跳。
3. 眼镜和手机自动绑定或按配置绑定。

如眼镜无法注册，优先检查：

1. 眼镜 Wi-Fi 是否连接到同一局域网。
2. `host/glass/config/local_build.env` 中 `GLASS_SERVER_WS_URI` 是否正确。
3. `GLASS_PAIR_TOKEN` 是否和 `DEVICE_TOKEN_MAP` 一致。
4. 服务端日志中是否有控制 WebSocket 连接和 register 失败详情。

## 9. 手机视频链路实测

当前状态：业务侧不再通过宿主代码手动注入视频链路 debug adapter。若 SDK 还没有在真实服务端中标准绑定 `DeviceGroupContext.start_phone_video_link(...)`，本节只作为 SDK 修复后的验收步骤。

先只测视频链路，不测业务识别：

```bash
PYTHONPATH=../openaiglass-sdk/server-python:. ../.venv/bin/python scripts/start_phone_video_link.py \
  --host 127.0.0.1 \
  --port 8765 \
  --glass-device-id glass-001 \
  --frame-interval-ms 500 \
  --reason live_video_smoke
```

预期现象：

1. 服务端返回视频任务编号。
2. 眼镜收到 `sensor.camera.stream.start`。
3. iPhone 页面或日志能看到接收帧。
4. 服务端运行态能看到视频任务或相关命令记录。

失败定位：

1. 如果返回 `路径不存在: /api/debug/find-object/start`，说明服务端仍是旧进程，先停止旧服务进程，再执行 `uv run openaiglass.server.run --app-module host.server.main --app-root openaiglass-for-blind`。
2. 如果服务端返回目标设备离线，检查眼镜控制连接是否已注册且 `voice.session.opened` 已完成。
3. 如果服务端返回缺少手机，检查手机是否在线、是否上报 `camera_sink_ws_uri`、是否和眼镜完成绑定。
4. 如果眼镜没有推帧，检查眼镜摄像头和控制命令日志。
5. 如果 iPhone 无帧，检查 iPhone camera sink 地址是否上报。

## 10. find_object 实测

当前状态：`find_object` 的 Tool 和 Task 仍按 SDK 公开接口实现，但完整实测依赖第 9 节视频链路能力。SDK 未补齐前，不应在业务侧恢复手写 adapter 或直接调用 debug 方法。

启动找物体任务：

```bash
PYTHONPATH=../openaiglass-sdk/server-python:. ../.venv/bin/python scripts/start_find_object.py \
  --host 127.0.0.1 \
  --port 8765 \
  --glass-device-id glass-001 \
  --target-object 水杯 \
  --frame-interval-ms 500 \
  --reason live_find_object
```

预期现象：

1. 服务端创建 `find_object` 任务。
2. 眼镜视频链路启动。
3. 手机端开始处理视频帧。
4. 手机端真实 YOLO 模型命中时，上报 `source=coreml_yolo`、`label`、`bbox` 和 `confidence`；无模型构建只会上报 `source=heuristic`。
5. 找到目标后服务端提交高优先级通知。
6. 眼镜端播报或展示“找到水杯”类提示。
7. 服务端停止手机任务和视频链路。

观察点：

1. 服务端日志：`find_object`、`phone.vision.find_object.result`、`task.completed`。
2. 手机日志：视频帧接收、处理器输出、检测来源 `source=coreml_yolo|heuristic`。
3. 眼镜日志：视频流开始、视频流停止、通知播放。

## 11. traffic_light 实测

当前状态：`traffic_light` 的 Tool、Task 和 iOS 插件样例仍保留；完整实测同样依赖第 9 节视频链路能力。SDK 未补齐前，可先用任务事件接口或 `phone-mock` 验证服务端任务事件处理和通知策略。

当前没有单独的红绿灯启动脚本，建议先通过语音或调试入口触发 `start_traffic_light`。如需要临时调试，可参考 `scripts/start_find_object.py` 增加同类业务调试脚本，但不要改 SDK 框架。

预期现象：

1. 服务端创建 `traffic_light` 任务。
2. 眼镜视频链路启动。
3. 手机端红绿灯插件或处理器输出 `phone.vision.traffic_light.result`。
4. 红灯提交 `critical` 通知。
5. 绿灯提交 `high` 通知。
6. 单次识别模式下任务完成并释放视频链路；连续识别模式下可取消。

观察点：

1. 服务端日志：`traffic_light`、`phone.vision.traffic_light.result`、`sensor.camera.stream.stop`。
2. 手机日志：红绿灯识别结果。
3. 眼镜日志：红灯停止提示、绿灯谨慎通过提示。

如真实 iOS 宿主无法同时承载找物体和红绿灯业务插件，应停止继续业务规避，并把“业务插件装配能力不足”写入阻塞点文档交给 SDK 团队。

## 12. timer 实测

计时器业务不依赖手机视频链路。建议通过语音触发：

```text
帮我设置一个 1 分钟计时器
```

预期现象：

1. 服务端调用 `start_timer`。
2. SDK 创建 `timer_task`。
3. 眼镜端收到“计时器已开始”通知。
4. 自然到点时收到“时间到了”或用户自定义完成提示。
5. 取消时收到“计时器已取消”通知。

当前边界：

1. 当前自然到点由业务侧轻量 `threading.Timer` 验证。
2. 离线回放仍可用 `timer.tick` 和 `timer.finished` 推进。
3. SDK 尚未提供生产级通用定时调度和“到点先回流 Agent 决策”的公开接口，详见阻塞点文档。

如果真机测试确认缺少自然到点事件调度，应把它作为 SDK 改进建议记录到：

```text
docs/stage1/develop/架构阻塞点说明与改进建议.md
```

## 13. navigation 实测

### 13.1 路线准备

建议通过语音触发：

```text
帮我导航去桂林路地铁站
```

预期现象：

1. 服务端调用 `prepare_navigation`。
2. MCP trace 中出现 `amap.poi_search`、`amap.geocode`、`amap.route_plan`。
3. SDK 创建 `navigation_task`。
4. 眼镜端收到“导航路线已准备”通知。

当前边界：

1. AMap 当前是业务侧 adapter；在 `config/.env` 配置 `AMAP_API_KEY`，并在 `local_server.yaml` 配置 `business.navigation.amap.default_origin` 后调用真实高德 Web 服务。
2. 没有真实 AMap key/config 时，自动回退 mock，不验证真实地图服务。
3. POI 候选确认已在离线回放验证，真机多轮 Agent 澄清话术仍需后续实测。

### 13.3 search 搜索工具实测

建议通过语音触发：

```text
帮我查一下大模型是什么
```

预期现象：

1. 服务端调用 `search_web`。
2. MCP trace 中出现 `web.search`。
3. Agent 基于搜索结果标题、摘要和链接组织回答。

当前边界：

1. 正式搜索 provider 为博查 AI Search，需在 `local_server.yaml` 配置 `business.search.web.provider=bocha`，并在 `config/.env` 配置 `BOCHA_SEARCH_API_KEY`。
2. 未配置 API Key 且 provider 为 `auto` 时，会回退 DuckDuckGo HTML，仅用于本地开发验证。
3. 当前不抓取全文网页正文。

### 13.2 视觉事件最小策略

在导航任务存在时，触发红绿灯识别或通过调试入口发送 `phone.vision.traffic_light.result`。

预期现象：

1. 红灯：眼镜收到“前方红灯，请停下等待”。
2. 黄灯：眼镜收到“前方黄灯，请暂缓通过并等待下一次提示”。
3. 绿灯：眼镜收到“前方绿灯，可继续按导航前进”。
4. 同一信号重复时不重复播报。

当前不测：

1. 复杂最后 10 米策略。
2. 真实地图偏航重算。
3. 多传感器融合导航。

## 14. 语音完整链路实测

在前面各项都通过后，再测语音入口：

1. 眼镜端开始语音会话。
2. 说出找物、红绿灯、计时器、导航类请求。
3. 服务端观察 ASR、agent-core、Tool 调用、Task 创建、TTS 和通知。
4. 眼镜端观察播报是否完整。

建议记录每轮实测：

1. 用户原始语音。
2. ASR 文本。
3. agent tool 调用名称和参数。
4. task_id。
5. 最终通知文本。
6. 是否释放视频链路或手机任务。

## 15. 结果记录模板

建议每次真机实测保存一份记录：

```text
实测日期：
设备：
服务端 commit：
SERVER_PUBLIC_HOST：
手机设备编号：
眼镜设备编号：

前置检查：
- 离线场景：
- live_check：
- 服务端 health：

能力结果：
- phone register：
- glass register：
- auto bind：
- video link：
- find_object：
- traffic_light：
- timer：
- navigation：
- voice trigger：

失败现象：
日志位置：
初步归因：
是否 SDK 阻塞：
下一步：
```

## 16. 停止条件

遇到以下情况应停止继续业务侧绕行，并更新阻塞点文档：

1. 业务能力必须修改 `openaiglass-sdk` 才能继续。
2. 真实 iOS 宿主无法装配或分发多个业务插件。
3. SDK 无法把手机任务事件稳定回传到服务端任务。
4. SDK 无法把通知可靠下发到眼镜端。
5. 设备绑定、心跳、断线重连等系统性问题阻塞业务实测。

以下情况不算 SDK 硬阻塞，只记录为外部配置或产品能力缺口：

1. 没有真实 AMap key/config。
2. 模型 API key 缺失。
3. 真实识别模型准确率不足。
4. 测试场地没有红绿灯或目标物体。
5. 局域网、防火墙或 iOS 权限配置错误。
