from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from agent_core.context.conversation_context import ConversationContextStore
from agent_core.model_adapter.base import ModelAdapter
from agent_core.runtime.response_planner import ResponsePlanner
from agent_core.tool_registry.registry import ToolRegistry
from infra.logging import log_event
from protocol.messages.envelope import Envelope


@dataclass(slots=True)
class AgentInput:
    conversation_id: str
    device_id: str
    trace_id: str
    text: str


@dataclass(slots=True)
class AgentOutput:
    text: str
    commands: list[Envelope] = field(default_factory=list)
    tool_results: list[dict[str, Any]] = field(default_factory=list)


@dataclass(slots=True)
class AgentRuntime:
    context_store: ConversationContextStore
    model_adapter: ModelAdapter
    tool_registry: ToolRegistry
    response_planner: ResponsePlanner
    logger: logging.Logger | None = None

    def handle(self, agent_input: AgentInput) -> AgentOutput:
        if self.logger:
            log_event(
                self.logger,
                logging.INFO,
                "agent.input.received",
                trace_id=agent_input.trace_id,
                device_id=agent_input.device_id,
            )
        self.context_store.append(agent_input.conversation_id, role="user", content=agent_input.text)

        context = [turn.__dict__ for turn in self.context_store.snapshot(agent_input.conversation_id)]
        model_output = self.model_adapter.generate_with_tools(
            prompt=agent_input.text,
            context=context,
            tools=self.tool_registry.list_tool_specs(),
        )

        tool_results: list[dict[str, Any]] = []
        for tool_call in model_output.tool_calls:
            if self.logger:
                log_event(
                    self.logger,
                    logging.INFO,
                    "agent.tool.call",
                    trace_id=agent_input.trace_id,
                    device_id=agent_input.device_id,
                    tool_name=tool_call.name,
                )
            tool_results.append(
                {
                    "name": tool_call.name,
                    "result": self.tool_registry.execute(tool_call.name, tool_call.arguments),
                }
            )

        final_text = model_output.text or "已完成。"
        self.context_store.append(agent_input.conversation_id, role="assistant", content=final_text)

        command = self.response_planner.build_audio_reply(
            trace_id=agent_input.trace_id,
            target_device_id=agent_input.device_id,
            text=final_text,
        )

        if self.logger:
            log_event(
                self.logger,
                logging.INFO,
                "agent.output.ready",
                trace_id=agent_input.trace_id,
                device_id=agent_input.device_id,
            )

        return AgentOutput(text=final_text, commands=[command], tool_results=tool_results)
