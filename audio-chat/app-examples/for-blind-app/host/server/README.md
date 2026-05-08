# host/server 样板

业务 server 入口应保持很薄，只负责加载 YAML、开启 Tool / Task 自动发现和注册业务 provider。可复制：

- `examples/basic-app/host/server/main.py`
- `examples/basic-app/config/server.yaml`

业务代码不要 import Control Service、Stream Service、Asset Service、Output Service 等内部对象。
