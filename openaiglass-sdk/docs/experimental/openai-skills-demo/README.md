# OpenAI Skills Runtime Demo

这个目录演示如何基于 OpenAI Python SDK 自实现一个通用 Skills Runtime。

## 快速运行

```bash
cd openai-skills-demo
uv sync --project .
export OPENAI_API_KEY="你的 OpenAI API Key"
uv run --project . python skills_agent.py "请使用数学报告技能，计算 144 + 377，并保存一份简短报告"
```

可选指定模型：

```bash
OPENAI_MODEL=gpt-5.4-mini uv run --project . python skills_agent.py "请计算 12 + 30 并保存报告"
```

## 文件说明

- `DESIGN.md`：工程化设计文档。
- `skills_agent.py`：可运行的最小 Python 原型。
- `skills/math-report/SKILL.md`：示例 Skill。
- `out/`：脚本运行后保存报告的位置，已在 `.gitignore` 中忽略。
