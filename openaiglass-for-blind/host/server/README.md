# host/server

本目录只放盲人 AI 眼镜业务服务端宿主入口。它负责装配 SDK、注册业务能力并启动服务端运行时，不直接实现 SDK 的通信、绑定、日志和任务状态机底座。

当前入口：

```bash
openaiglass server local start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env
```

服务端本地配置源放在业务工程：

```bash
cp openaiglass-for-blind/config/local_server.env.example openaiglass-for-blind/config/local_server.env
openaiglass server local start \
  --app-module host.server.main \
  --app-root openaiglass-for-blind \
  --config openaiglass-for-blind/config/local_server.env
```
