# for-blind-app 单元测试

本目录放 for-blind-app 的纯应用单元测试。它不承担 SDK 协议行为验证，也不承担真实样例回放。

当前应用测试主要位于：

- `examples/for-blind-app/app-tests/`
- `examples/for-blind-app/replay-tests/`

新增用例只有在不启动应用链路、不依赖真实样例、不验证端侧协议互认时才放到这里。
