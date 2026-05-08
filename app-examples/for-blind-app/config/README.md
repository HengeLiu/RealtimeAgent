# config 样板

业务 app-root 的配置应以 YAML 为主、环境变量覆盖为辅。最小配置可复制：

- `app-examples/basic-app/server.yaml`

本地同步使用：

```bash
uv run audio-chat.config.sync --app-root app-examples/for-blind-app
```

当前目录是迁移样板说明，不保存本地 token、WiFi 密码或真实设备私有配置。
