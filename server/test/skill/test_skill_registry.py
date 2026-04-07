from protocol.enums import SkillMode
from skill.base import SkillBase, SkillRequest, SkillResult
from skill.registry import SkillRegistry


class DemoSkill(SkillBase):
    name = "demo_skill"
    description = "Demo"
    input_schema = {"type": "object"}
    output_schema = {"type": "object"}
    mode = SkillMode.SYNC

    def execute(self, request: SkillRequest) -> SkillResult:
        return SkillResult(status="completed", data={"echo": request.input})



def test_skill_registry_register_and_execute() -> None:
    registry = SkillRegistry()
    registry.register(DemoSkill())

    result = registry.execute(
        "demo_skill",
        SkillRequest(trace_id="trace_1", caller="test", input={"x": 1}),
    )

    assert result.status == "completed"
    assert result.data == {"echo": {"x": 1}}
