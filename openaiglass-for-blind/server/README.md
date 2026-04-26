# server

本目录只放盲人 AI 眼镜业务服务端入口。它负责装配 SDK、注册业务能力并启动服务端运行时，不直接实现 SDK 的通信、绑定、日志和任务状态机底座。

当前入口：

```bash
PYTHONPATH=../openaiglass-sdk/python:. python server/main.py
```
