from __future__ import annotations

import asyncio

from audio_chat.app import AudioChatApp, AudioChatConfig
from audio_chat.config import load_yaml_config
from audio_chat.memory import JsonlMemoryStore, LlmMemoryManagementAgent, MemoryOperationAction, MemoryOperationPlan, MemoryService


class FakeMemoryManager:
    """测试用记忆子 Agent，返回预设动作计划。"""

    def __init__(self, plans: list[MemoryOperationPlan]) -> None:
        self.plans = list(plans)
        self.requests = []
        self.existing_batches = []

    def plan(self, *, request, existing_memories):
        """记录请求并返回下一条计划。"""

        self.requests.append(request)
        self.existing_batches.append(list(existing_memories))
        return self.plans.pop(0)


def test_memory_service_writes_searches_and_deletes_user_memory_json(tmp_path) -> None:
    """测试目标：验证 Memory Service 的用户级 memory.json 存储闭环。

    测试方法：启用 Memory Service，写入两条用户记忆，按关键词搜索后删除其中一条。
    预期结果：搜索只能返回当前用户的有效记录，删除后的记录不再出现，并写入
    `runs/<app_name>/<user_id>/memory.json` 形态的用户级文件。
    """

    service = MemoryService(enabled=True, store=JsonlMemoryStore(tmp_path / "memory"))

    first = service.write(user_id="user-001", content="白色水杯在书桌左侧", metadata={"source": "test"})
    service.write(user_id="user-001", content="手机通常放在玄关", metadata={"source": "test"})
    service.write(user_id="user-002", content="白色水杯在厨房", metadata={"source": "other-user"})

    matches = service.search(user_id="user-001", query="水杯", limit=10)
    assert [item.content for item in matches] == ["白色水杯在书桌左侧"]

    assert service.delete(user_id="user-001", memory_id=first.memory_id) is True
    assert service.search(user_id="user-001", query="水杯", limit=10) == []
    assert (tmp_path / "memory" / "user-001" / "memory.json").exists()


def test_memory_disabled_does_not_expose_memory_tools(tmp_path) -> None:
    """测试目标：验证 memory.enabled=false 不影响现有内置工具。

    测试方法：创建默认 App，读取 ToolRegistry 中的工具名。
    预期结果：基础工具仍存在，memory_search 和 manage_memory 不暴露给 Agent。
    """

    app = AudioChatApp(AudioChatConfig(runs_root=str(tmp_path / "runs"), memory_enabled=False))

    names = app.tool_registry.list_names()
    assert "query_device_state" in names
    assert "memory_search" not in names
    assert "manage_memory" not in names


def test_memory_enabled_exposes_and_executes_builtin_tools(tmp_path) -> None:
    """测试目标：验证启用 memory 后内置 Tool 可被 Agent 调用。

    测试方法：通过 ToolGateway 调用 manage_memory 和 memory_search。
    预期结果：写入结果成功，随后搜索能返回同一条记忆。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            memory_enabled=True,
            memory_path=str(tmp_path / "memory"),
        )
    )

    assert {"manage_memory", "memory_search"}.issubset(set(app.tool_registry.list_names()))
    write_result = asyncio.run(
        app.tool_gateway.call(
            name="manage_memory",
            user_id="user-memory",
            session_id="session-memory",
            input_data={"memory_context": "电梯口在走廊尽头"},
        )
    )
    search_result = asyncio.run(
        app.tool_gateway.call(
            name="memory_search",
            user_id="user-memory",
            session_id="session-memory",
            input_data={"topic": "电梯位置"},
        )
    )

    assert write_result.ok is True
    assert search_result.ok is True
    assert search_result.data["memories"][0]["content"] == "电梯口在走廊尽头"


def test_memory_service_uses_manager_agent_plan(tmp_path) -> None:
    """测试目标：验证 MemoryService 通过记忆子 Agent 计划维护记忆。

    测试方法：注入测试用 manager，先返回新增动作，再返回带 memory_id 的更新动作。
    预期结果：MemoryService 会把已有记忆传给子 Agent，并按动作计划写入同一主题槽位。
    """

    manager = FakeMemoryManager(
        plans=[
            MemoryOperationPlan(
                actions=[
                    MemoryOperationAction(
                        operation="add",
                        memory_type="personalized",
                        topic="导航偏好",
                        content="用户喜欢导航提示简短。",
                    )
                ],
                feedback="已记住导航偏好",
            ),
            MemoryOperationPlan(
                actions=[
                    MemoryOperationAction(
                        operation="update",
                        memory_type="personalized",
                        topic="导航偏好",
                        content="用户喜欢导航提示简短，并且先说方向再说距离。",
                    )
                ],
                feedback="已更新导航偏好",
            ),
        ]
    )
    service = MemoryService(enabled=True, store=JsonlMemoryStore(tmp_path / "memory"), manager_agent=manager)

    first = service.manage(user_id="user-plan", memory_context="记住我的导航提示要简短")
    second = service.manage(user_id="user-plan", memory_context="补充一下，先说方向再说距离")
    records = service.search_by_topics(user_id="user-plan", topics=["导航偏好"])

    assert first["feedback"] == "已记住导航偏好"
    assert second["feedback"] == "已更新导航偏好"
    assert len(manager.existing_batches[0]) == 0
    assert manager.existing_batches[1][0].topic == "导航偏好"
    assert records[0].content == "用户喜欢导航提示简短，并且先说方向再说距离。"


def test_memory_enabled_uses_runs_user_memory_json_by_default(tmp_path) -> None:
    """测试目标：验证默认 memory 落盘位置与会话产物同属 runs 用户目录。

    测试方法：只配置 runs_root 和 memory_enabled，不额外配置 memory_path，
    然后通过 manage_memory 写入一条用户记忆。
    预期结果：记忆文件写入 `runs_root/<user_id>/memory.json`，方便联调时按用户查看。
    """

    runs_root = tmp_path / "runs"
    app = AudioChatApp(AudioChatConfig(runs_root=str(runs_root), memory_enabled=True))

    result = asyncio.run(
        app.tool_gateway.call(
            name="manage_memory",
            user_id="user-default-memory",
            session_id="session-default-memory",
            input_data={"memory_context": "用户默认走左侧楼梯"},
        )
    )

    assert result.ok is True
    assert (runs_root / "user-default-memory" / "memory.json").exists()


def test_memory_records_are_split_into_basic_and_personalized_prompt_layers(tmp_path) -> None:
    """测试目标：验证长期记忆按当前约定拆成 basic 和 personalized 两层。

    测试方法：写入一条姓名基本信息和一条楼梯偏好个性化信息，然后读取 memory.json
    和 prompt fragment。
    预期结果：落盘记录包含 memory_type/topic/content；prompt 中 basic 注入全文，
    personalized 只注入主题，详情需要通过 memory_search 再读取。
    """

    service = MemoryService(enabled=True, store=JsonlMemoryStore(tmp_path / "runs"))
    service.manage(user_id="user-layer", memory_context="我叫文刀，文字的文，刀锋的刀。")
    service.manage(user_id="user-layer", memory_context="用户默认走左侧楼梯")

    memory_path = tmp_path / "runs" / "user-layer" / "memory.json"
    payload = memory_path.read_text(encoding="utf-8")
    assert '"memory_type": "basic"' in payload
    assert '"topic": "姓名"' in payload
    assert '"memory_type": "personalized"' in payload
    assert '"topic": "楼梯偏好"' in payload

    fragment = service.build_prompt_fragment(user_id="user-layer")
    assert "基本信息：" in fragment
    assert "- 姓名: 用户名字叫文刀。" in fragment
    assert "个性化信息主题：" in fragment
    assert "- 楼梯偏好" in fragment
    assert "用户默认走左侧楼梯" not in fragment


def test_memory_enabled_injects_model_instructions_and_delete_tool_action(tmp_path) -> None:
    """测试目标：验证启用 memory 后模型提示词、工具 schema 和删除动作都可用。

    测试方法：直接构造启用 memory 的 AudioChatApp，检查运行时提示词和工具列表，
    再通过 ToolGateway 写入、删除、查询一条记忆。
    预期结果：提示词包含长期记忆规则，manage_memory 已暴露给模型，删除后查询不到原记忆。
    """

    app = AudioChatApp(
        AudioChatConfig(
            runs_root=str(tmp_path / "runs"),
            memory_enabled=True,
            memory_path=str(tmp_path / "memory"),
            realtime_instructions="你是测试助手。",
            text_system_prompt="你是测试助手。",
        )
    )

    assert "长期记忆规则" in app.config.realtime_instructions
    assert "长期记忆规则" in app.config.text_system_prompt
    assert "manage_memory" in {tool["function"]["name"] for tool in app.tool_gateway.provider_schemas()}

    write_result = asyncio.run(
        app.tool_gateway.call(
            name="manage_memory",
            user_id="user-memory-rule",
            session_id="session-memory-rule",
            input_data={"memory_context": "用户喜欢靠右侧行走"},
        )
    )
    delete_result = asyncio.run(
        app.tool_gateway.call(
            name="manage_memory",
            user_id="user-memory-rule",
            session_id="session-memory-rule",
            input_data={"memory_context": "忘记用户喜欢靠右侧行走"},
        )
    )
    search_result = asyncio.run(
        app.tool_gateway.call(
            name="memory_search",
            user_id="user-memory-rule",
            session_id="session-memory-rule",
            input_data={"topic": "用户偏好"},
        )
    )

    assert write_result.ok is True
    assert delete_result.ok is True
    assert delete_result.data["actions"][0]["success"] is True
    assert search_result.data["memories"] == []


def test_memory_manager_is_configured_as_system_capability(tmp_path) -> None:
    """测试目标：验证记忆管理子 Agent 来自 memory.manager 配置，而不是 text 模型配置。

    测试方法：写入一份 text 模型为 mock、memory.manager 为 dashscope-compatible 的
    server.yaml，并通过 AudioChatConfig 构建 App。
    预期结果：MemoryService 内部 manager 是 LlmMemoryManagementAgent，且使用
    memory.manager.model。
    """

    app_dir = tmp_path / "memory-manager-app"
    app_dir.mkdir()
    config_path = app_dir / "server.yaml"
    config_path.write_text(
        """
app_name: memory-manager-app
agent:
  text:
    model_provider: mock
    model: mock-text
memory:
  enabled: true
  manager:
    provider: dashscope-compatible
    model: qwen-memory
    api_key_env: DASHSCOPE_API_KEY
    base_url: https://dashscope.aliyuncs.com/compatible-mode/v1
""".strip(),
        encoding="utf-8",
    )

    loaded = load_yaml_config(config_path)
    app = AudioChatApp(AudioChatConfig.from_loaded_config(loaded))

    assert isinstance(app.memory_service.manager_agent, LlmMemoryManagementAgent)
    assert app.memory_service.manager_agent.model == "qwen-memory"
    assert app.config.text_model_provider == "mock"
