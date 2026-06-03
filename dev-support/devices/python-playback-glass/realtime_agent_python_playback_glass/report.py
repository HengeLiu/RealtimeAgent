from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .assertions import AssertionResult
from .case_schema import PlaybackCase
from .protocol_client import PlaybackStats


@dataclass(frozen=True)
class CaseReport:
    """单个 Case 报告。"""

    id: str
    name: str
    ok: bool
    runs_dir: str
    failed_assertions: list[dict[str, Any]]
    summary: dict[str, Any]

    @classmethod
    def from_assertion(cls, *, case: PlaybackCase, assertion_result: AssertionResult, stats: PlaybackStats, report_dir: Path | None) -> "CaseReport":
        """从断言结果构造报告。"""

        report = cls(case.id, case.name, assertion_result.ok, assertion_result.runs_dir, [failure.__dict__ for failure in assertion_result.failed_assertions], {**assertion_result.summary, "registered": stats.registered})
        if report_dir is not None:
            report_dir.mkdir(parents=True, exist_ok=True)
            (report_dir / f"{case.id}.result.json").write_text(json.dumps(report.to_dict(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return report

    def to_dict(self) -> dict[str, Any]:
        """返回可序列化报告。"""

        return {"id": self.id, "name": self.name, "ok": self.ok, "runs_dir": self.runs_dir, "failed_assertions": self.failed_assertions, "summary": self.summary}


def write_summary_report(*, reports: list[CaseReport], report_path: str | Path, suite_id: str | None = None) -> dict[str, Any]:
    """写出统一 report.json。"""

    path = Path(report_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {"ok": all(report.ok for report in reports), "suite": suite_id, "case_count": len(reports), "passed": sum(1 for report in reports if report.ok), "failed": sum(1 for report in reports if not report.ok), "cases": [report.to_dict() for report in reports]}
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data
