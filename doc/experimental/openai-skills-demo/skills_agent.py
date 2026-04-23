from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

from openai import OpenAI


@dataclass(frozen=True)
class Skill:
    """表示一个可被模型选择的 Skill。

    主要功能：保存 Skill 的元数据和 `SKILL.md` 正文。
    主要属性：`id` 是稳定标识，`name` 是展示名称，`description` 用于模型初筛，
    `tools` 是激活后允许调用的工具列表，`path` 是 `SKILL.md` 文件路径，
    `markdown` 是完整文件内容。
    主要方法：该类只承载数据，不提供业务方法。
    """

    id: str
    name: str
    description: str
    tools: list[str]
    path: Path
    markdown: str
    version: str | None = None


@dataclass
class SkillSession:
    """保存单次对话运行中的 Skill 状态。

    主要功能：记录 OpenAI Responses API 的上一轮响应 ID 和当前激活的 Skill。
    主要属性：`previous_response_id` 用于延续模型上下文，`active_skill_id`
    用于限制后续可调用工具。
    主要方法：该类只承载运行状态，不提供业务方法。
    """

    previous_response_id: str | None = None
    active_skill_id: str | None = None


class SkillRegistry:
    """负责加载和查询本地 Skills。

    主要功能：扫描 `skills/*/SKILL.md`，解析 frontmatter，并按 Skill 名称建立索引。
    主要方法：`load` 读取目录，`get` 查询单个 Skill，`values` 返回全部 Skill。
    主要属性：`root` 是 Skills 根目录，`skills` 是已加载 Skill 的字典。
    """

    def __init__(self, root: Path) -> None:
        """初始化 Registry。

        主要逻辑：只保存根目录，不立即扫描，调用方通过 `load` 明确触发加载。
        参数：`root` 是本地 Skills 根目录。
        返回值：无。
        异常情况：该函数不主动抛出异常。
        """

        self.root = root
        self.skills: dict[str, Skill] = {}

    def load(self) -> None:
        """扫描本地 Skills 目录并建立索引。

        主要逻辑：查找一级子目录下的 `SKILL.md`，读取 frontmatter 中的
        `name`、`description`、`tools` 和 `version` 字段。
        参数：无。
        返回值：无。
        异常情况：如果文件不可读，会由 `Path.read_text` 抛出系统异常。
        """

        self.skills.clear()
        for skill_md in sorted(self.root.glob("*/SKILL.md")):
            markdown = skill_md.read_text(encoding="utf-8")
            meta = parse_frontmatter(markdown)
            name = meta.get("name") or skill_md.parent.name
            description = meta.get("description") or name
            tools = split_csv(meta.get("tools", ""))
            skill = Skill(
                id=name,
                name=name,
                description=description,
                tools=tools,
                path=skill_md,
                markdown=markdown,
                version=meta.get("version"),
            )
            self.skills[skill.id] = skill

    def get(self, skill_id: str) -> Skill | None:
        """按 ID 查询 Skill。

        主要逻辑：从内存索引中读取 Skill。
        参数：`skill_id` 是模型请求读取的 Skill ID。
        返回值：找到时返回 `Skill`，否则返回 `None`。
        异常情况：该函数不主动抛出异常。
        """

        return self.skills.get(skill_id)

    def values(self) -> Iterable[Skill]:
        """返回全部已加载 Skills。

        主要逻辑：暴露字典值迭代器，供 prompt 构建使用。
        参数：无。
        返回值：Skill 迭代器。
        异常情况：该函数不主动抛出异常。
        """

        return self.skills.values()


def parse_frontmatter(markdown: str) -> dict[str, str]:
    """解析 `SKILL.md` 顶部的简单 frontmatter。

    主要逻辑：只支持 `key: value` 形式，足够支撑最小原型。
    参数：`markdown` 是完整 `SKILL.md` 文本。
    返回值：frontmatter 字段字典。
    异常情况：frontmatter 缺失或格式不完整时返回空字典。
    """

    match = re.match(r"^---\n(.*?)\n---\n", markdown, flags=re.DOTALL)
    if not match:
        return {}
    result: dict[str, str] = {}
    for raw_line in match.group(1).splitlines():
        if ":" not in raw_line:
            continue
        key, value = raw_line.split(":", 1)
        result[key.strip()] = value.strip()
    return result


def split_csv(value: str) -> list[str]:
    """把逗号分隔字符串解析为列表。

    主要逻辑：去掉空白项，保留原始顺序。
    参数：`value` 是逗号分隔文本。
    返回值：字符串列表。
    异常情况：该函数不主动抛出异常。
    """

    return [item.strip() for item in value.split(",") if item.strip()]


def render_skill_catalog(registry: SkillRegistry) -> str:
    """把候选 Skills 渲染成给模型看的摘要。

    主要逻辑：只暴露 `id`、`name` 和 `description`，不暴露完整 Skill 正文。
    参数：`registry` 是已加载的 Skill Registry。
    返回值：XML 风格的 Skill 摘要文本。
    异常情况：该函数不主动抛出异常。
    """

    lines = ["<available_skills>"]
    for skill in registry.values():
        lines.append(
            f'- id="{skill.id}" name="{skill.name}" description="{skill.description}"'
        )
    lines.append("</available_skills>")
    return "\n".join(lines)


def build_instructions(registry: SkillRegistry, active_skill: Skill | None) -> str:
    """构建传给 Responses API 的 instructions。

    主要逻辑：要求模型先扫描 Skill 摘要，只有明确适用时才调用 `read_skill`，
    并且初始阶段最多读取一个 Skill。
    参数：`registry` 提供候选 Skills，`active_skill` 表示当前已激活 Skill。
    返回值：instructions 字符串。
    异常情况：该函数不主动抛出异常。
    """

    instructions = f"""
You are an assistant with optional Skills.

Before answering:
- Inspect <available_skills>.
- If exactly one skill clearly applies, call read_skill with that skill_id.
- If several skills may apply, choose the most specific one and call read_skill.
- If no skill applies, answer without reading a skill.
- Do not read more than one skill before proceeding.

{render_skill_catalog(registry)}
""".strip()
    if active_skill is None:
        return instructions
    return (
        instructions
        + f"""

Active skill: {active_skill.name}
Follow the active skill instructions. Only use tools allowed by the active skill unless the user asks for something unrelated.
"""
    )


def build_tools() -> list[dict[str, Any]]:
    """声明传给 OpenAI Responses API 的函数工具。

    主要逻辑：提供一个内置 `read_skill` 工具和两个示例业务工具。
    参数：无。
    返回值：符合 Responses API 的工具定义列表。
    异常情况：该函数不主动抛出异常。
    """

    return [
        {
            "type": "function",
            "name": "read_skill",
            "description": "Read the full SKILL.md instructions for a candidate skill.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "skill_id": {
                        "type": "string",
                        "description": "The skill id to read.",
                    }
                },
                "required": ["skill_id"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "add_numbers",
            "description": "Add two numbers and return the sum.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "a": {"type": "number"},
                    "b": {"type": "number"},
                },
                "required": ["a", "b"],
                "additionalProperties": False,
            },
        },
        {
            "type": "function",
            "name": "save_text",
            "description": "Save text to a file under ./out.",
            "strict": True,
            "parameters": {
                "type": "object",
                "properties": {
                    "filename": {"type": "string"},
                    "text": {"type": "string"},
                },
                "required": ["filename", "text"],
                "additionalProperties": False,
            },
        },
    ]


def build_allowed_tool_choice(active_skill: Skill | None) -> dict[str, Any]:
    """构建当前轮允许模型调用的工具集合。

    主要逻辑：未激活 Skill 时只允许 `read_skill`；激活后允许 Skill 声明的工具。
    参数：`active_skill` 是当前会话已激活的 Skill。
    返回值：Responses API 的 `tool_choice` 配置。
    异常情况：该函数不主动抛出异常。
    """

    allowed_tools = [{"type": "function", "name": "read_skill"}]
    if active_skill is not None:
        allowed_tools.extend({"type": "function", "name": name} for name in active_skill.tools)
    return {"type": "allowed_tools", "mode": "auto", "tools": allowed_tools}


def save_text(filename: str, text: str, output_dir: Path) -> dict[str, str]:
    """把文本安全保存到输出目录。

    主要逻辑：只使用文件名部分，避免用户通过 `../` 写出输出目录。
    参数：`filename` 是目标文件名，`text` 是文件内容，`output_dir` 是保存目录。
    返回值：包含保存路径和状态的字典。
    异常情况：目录创建或文件写入失败时抛出系统异常。
    """

    output_dir.mkdir(parents=True, exist_ok=True)
    safe_name = Path(filename).name or "report.txt"
    target = output_dir / safe_name
    target.write_text(text, encoding="utf-8")
    return {"status": "saved", "path": str(target)}


def execute_tool(
    name: str,
    arguments: dict[str, Any],
    registry: SkillRegistry,
    session: SkillSession,
    output_dir: Path,
) -> str:
    """执行模型请求的函数工具。

    主要逻辑：`read_skill` 会激活 Skill，业务工具会执行本地示例逻辑。
    参数：`name` 是工具名，`arguments` 是模型提供的参数，`registry` 用于查找 Skill，
    `session` 用于记录 active skill，`output_dir` 是输出目录。
    返回值：给模型的工具输出字符串。
    异常情况：未知工具不会抛出异常，而是返回错误 JSON。
    """

    if name == "read_skill":
        skill_id = str(arguments.get("skill_id", ""))
        skill = registry.get(skill_id)
        if skill is None:
            return json.dumps({"error": f"Unknown skill: {skill_id}"}, ensure_ascii=False)
        session.active_skill_id = skill.id
        return skill.markdown

    if name == "add_numbers":
        total = float(arguments["a"]) + float(arguments["b"])
        return json.dumps({"sum": total}, ensure_ascii=False)

    if name == "save_text":
        result = save_text(
            filename=str(arguments["filename"]),
            text=str(arguments["text"]),
            output_dir=output_dir,
        )
        return json.dumps(result, ensure_ascii=False)

    return json.dumps({"error": f"Unknown tool: {name}"}, ensure_ascii=False)


def get_response_output_items(response: Any) -> list[Any]:
    """读取 Responses API 响应中的 output 项。

    主要逻辑：兼容 OpenAI SDK 的对象属性形式和字典形式。
    参数：`response` 是 SDK 返回的响应对象。
    返回值：output 项列表。
    异常情况：缺少 output 时返回空列表。
    """

    if isinstance(response, dict):
        return list(response.get("output") or [])
    return list(getattr(response, "output", []) or [])


def get_item_field(item: Any, field: str) -> Any:
    """从响应项中读取字段。

    主要逻辑：兼容对象属性和字典两种结构。
    参数：`item` 是响应项，`field` 是字段名。
    返回值：字段值，缺失时返回 `None`。
    异常情况：该函数不主动抛出异常。
    """

    if isinstance(item, dict):
        return item.get(field)
    return getattr(item, field, None)


def extract_function_calls(response: Any) -> list[Any]:
    """提取模型返回的函数调用。

    主要逻辑：筛选 `type == "function_call"` 的 output 项。
    参数：`response` 是 SDK 返回的响应对象。
    返回值：函数调用项列表。
    异常情况：该函数不主动抛出异常。
    """

    return [
        item
        for item in get_response_output_items(response)
        if get_item_field(item, "type") == "function_call"
    ]


def extract_output_text(response: Any) -> str:
    """提取模型最终文本。

    主要逻辑：优先使用 SDK 的 `output_text` 便捷字段；缺失时从 message 内容中兜底提取。
    参数：`response` 是 SDK 返回的响应对象。
    返回值：最终文本。
    异常情况：该函数不主动抛出异常。
    """

    text = get_item_field(response, "output_text")
    if isinstance(text, str) and text:
        return text
    chunks: list[str] = []
    for item in get_response_output_items(response):
        if get_item_field(item, "type") != "message":
            continue
        content = get_item_field(item, "content") or []
        for part in content:
            if get_item_field(part, "type") in {"output_text", "text"}:
                value = get_item_field(part, "text")
                if isinstance(value, str):
                    chunks.append(value)
    return "\n".join(chunks)


def parse_call_arguments(call: Any) -> dict[str, Any]:
    """解析函数调用参数。

    主要逻辑：把 Responses API 返回的 JSON 字符串参数转成字典。
    参数：`call` 是函数调用项。
    返回值：参数字典。
    异常情况：JSON 无效时返回空字典。
    """

    raw = get_item_field(call, "arguments") or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def run_agent(
    prompt: str,
    model: str,
    skills_dir: Path,
    output_dir: Path,
    max_rounds: int,
) -> str:
    """运行一次 Skills Agent 对话。

    主要逻辑：调用 Responses API，让模型先选择 Skill，再按函数调用循环执行工具。
    参数：`prompt` 是用户输入，`model` 是模型名，`skills_dir` 是 Skills 目录，
    `output_dir` 是输出目录，`max_rounds` 是最大工具循环轮数。
    返回值：模型最终回答。
    异常情况：OpenAI API 调用失败时由 SDK 抛出异常。
    """

    registry = SkillRegistry(skills_dir)
    registry.load()
    if not list(registry.values()):
        raise RuntimeError(f"No skills found under {skills_dir}")

    client = OpenAI()
    session = SkillSession()
    response = client.responses.create(
        model=model,
        input=[{"role": "user", "content": prompt}],
        instructions=build_instructions(registry, active_skill=None),
        tools=build_tools(),
        tool_choice=build_allowed_tool_choice(active_skill=None),
        parallel_tool_calls=False,
        store=True,
    )

    for _ in range(max_rounds):
        function_calls = extract_function_calls(response)
        if not function_calls:
            session.previous_response_id = get_item_field(response, "id")
            return extract_output_text(response)

        tool_outputs: list[dict[str, Any]] = []
        for call in function_calls:
            name = str(get_item_field(call, "name") or "")
            call_id = str(get_item_field(call, "call_id") or "")
            output = execute_tool(
                name=name,
                arguments=parse_call_arguments(call),
                registry=registry,
                session=session,
                output_dir=output_dir,
            )
            tool_outputs.append(
                {
                    "type": "function_call_output",
                    "call_id": call_id,
                    "output": output,
                }
            )

        active_skill = registry.get(session.active_skill_id or "")
        response = client.responses.create(
            model=model,
            previous_response_id=get_item_field(response, "id"),
            input=tool_outputs,
            instructions=build_instructions(registry, active_skill=active_skill),
            tools=build_tools(),
            tool_choice=build_allowed_tool_choice(active_skill=active_skill),
            parallel_tool_calls=False,
            store=True,
        )

    return "Stopped: too many tool-call rounds."


def parse_args(argv: list[str]) -> argparse.Namespace:
    """解析命令行参数。

    主要逻辑：支持用户输入、模型名、Skills 目录、输出目录和最大工具轮数。
    参数：`argv` 是命令行参数列表。
    返回值：`argparse.Namespace`。
    异常情况：参数非法时由 `argparse` 输出错误并退出。
    """

    parser = argparse.ArgumentParser(description="Run a minimal OpenAI Skills Runtime demo.")
    parser.add_argument("prompt", nargs="*", help="User prompt.")
    parser.add_argument(
        "--model",
        default=os.getenv("OPENAI_MODEL", "gpt-5.4-mini"),
        help="OpenAI model name. Defaults to OPENAI_MODEL or gpt-5.4-mini.",
    )
    parser.add_argument(
        "--skills-dir",
        default="skills",
        help="Directory containing skill folders.",
    )
    parser.add_argument(
        "--output-dir",
        default="out",
        help="Directory used by save_text.",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=8,
        help="Maximum function-calling rounds.",
    )
    return parser.parse_args(argv)


def main(argv: list[str]) -> int:
    """命令行入口。

    主要逻辑：读取参数，检查 API Key，运行 Agent，并打印最终回答。
    参数：`argv` 是命令行参数列表。
    返回值：进程退出码，`0` 表示成功。
    异常情况：运行失败时打印错误并返回非零退出码。
    """

    args = parse_args(argv)
    if not os.getenv("OPENAI_API_KEY"):
        print("Missing OPENAI_API_KEY.", file=sys.stderr)
        return 2
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        prompt = "请使用数学报告技能，计算 144 + 377，并保存一份简短报告。"

    try:
        result = run_agent(
            prompt=prompt,
            model=args.model,
            skills_dir=Path(args.skills_dir),
            output_dir=Path(args.output_dir),
            max_rounds=args.max_rounds,
        )
    except Exception as exc:
        print(f"Run failed: {exc}", file=sys.stderr)
        return 1

    print(result)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
