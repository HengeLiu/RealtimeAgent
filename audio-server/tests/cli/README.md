# cli 测试

本目录覆盖命令行、打包发布、配置同步和文档命令，不承担协议或 SDK runtime 主路径回归。

| 文件 | 测试目标和范围 |
| --- | --- |
| `test_cli_developer_workflow.py` | 验证开发者常用 CLI 入口存在并可解析。 |
| `test_cli_server_process.py` | 验证 server 进程管理 CLI 行为。 |
| `test_docs_commands.py` | 验证文档中的命令仍可定位到真实 CLI。 |
| `test_live_check.py` | 验证 live check 命令和健康检查输出。 |
| `test_package_boundary.py` | 验证 SDK package 边界和不该打包的内容。 |
| `test_package_check_release_inputs.py` | 验证 release 前 package check 输入。 |
| `test_public_api_parity.py` | 验证公开 API re-export 与预期一致。 |
| `test_release_package.py` | 验证 release package 构建与内容。 |
| `test_signed_token_auth.py` | 验证签名 token 认证相关 CLI / 配置边界。 |
