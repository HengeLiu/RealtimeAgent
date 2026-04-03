# OpenAIglasses_for_Navigation

## 目录说明

当前仓库已经按“新架构实现”和“旧项目原始代码”分层组织。

## 主要目录

- [nextgen](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/nextgen)：新的三端运行时实现、共享模型、协议、集成联调代码
- [docs](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/docs)：架构、阶段计划、时序图、联调和测试文档
- [testdata](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/testdata)：模拟联调和真机联调共用的标准测试数据
- [scripts](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/scripts)：开发和联调脚本入口
- [origin-src](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/origin-src)：旧项目原始代码、旧静态资源、旧部署文件

## 当前约束

- 所有新功能和新架构代码优先写入 `nextgen/`
- `origin-src/` 仅作为旧实现参考、迁移来源和回归对照对象
- 若需要做模拟联调，优先使用 `testdata/` 和 `scripts/`

## Python 环境

当前仓库已经按 `uv` 项目方式固定 Python 依赖：

- 根目录使用 [pyproject.toml](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/pyproject.toml) 管理依赖
- 根目录使用 [uv.lock](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/uv.lock) 固定解析后的锁定版本
- 根目录使用 [\.python-version](/Users/elio/dev/llm-project/OpenAIglasses_for_Navigation/.python-version) 固定 Python 版本为 `3.11`
- 后续建议统一使用 `uv sync` 或 `uv run` 维护与执行

推荐命令：

```bash
uv sync --frozen
uv run pytest nextgen/tests -q
```
