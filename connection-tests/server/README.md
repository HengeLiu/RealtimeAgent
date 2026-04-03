# Python 服务端

当前服务端已经升级为三端联调中枢，提供：

- `GET /`：WebUI 控制台
- `GET /health`：健康检查
- `WS /ws/glasses`：ESP32 云端连接
- `WS /ws/app`：Flutter App 云端连接
- `WS /ws/ui`：浏览器控制台连接
- `POST /upload/image`：手机上传图片
- `GET /latest-frame`：查看最近一帧
- `WS /ws/audio`：音频占位接口

## 在 IDE 里直接运行

直接运行 `server/app.py` 即可，因为文件底部已经带了：

```python
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8000, reload=True)
```

也可以继续用命令行启动：

```powershell
cd server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
python app.py
```

## 浏览器打开

- `http://你的公网IP:8000/`
- `http://你的公网IP:8000/health`

WebUI 可以：

- 查看 ESP32 / App 是否在线
- 查看最近一帧
- 发文字到 ESP32
- 发文字到 App
- 同时发给两端
- 请求抓拍和状态

## App 发给服务端的关键 WebSocket 指令

```json
{"type":"register_direct_endpoint","host":"192.168.2.10","port":9100,"path":"/ws/direct","mode":"direct_preferred"}
{"type":"send_text_glasses","text":"hello glasses"}
{"type":"send_text_server","text":"hello server"}
{"type":"request_snapshot"}
{"type":"get_status"}
```

## ESP32 新增的云端控制命令

- `DIRECT:APP_ENDPOINT=192.168.2.10,9100,/ws/direct`
- `DIRECT:DISABLE`
- `TEXT:你好`
- `GET_STATUS`
- `SNAP:HQ`

说明：

- App 和 ESP32 的大图像链路现在是“直连优先，云端回退”
- 服务器继续负责 WebUI、信令转发和云端兜底
