"""QA result models."""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional


@dataclass
class StageResult:
    """Result from a single QA stage."""
    stage_name: str
    passed: bool
    data: Dict[str, Any] = field(default_factory=dict)
    issues: List[str] = field(default_factory=list)
    error: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary."""
        result = {
            "passed": self.passed,
            **self.data,
        }
        if self.issues:
            result["issues"] = self.issues
        if self.error:
            result["error"] = self.error
        return result


@dataclass
class QAResult:
    """Complete QA pipeline result."""
    job_id: Optional[str] = None
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"))
    language: str = "en"
    stages: Dict[str, StageResult] = field(default_factory=dict)
    execution_time_seconds: float = 0.0

    @property
    def passed(self) -> bool:
        """Check if all stages passed."""
        return all(stage.passed for stage in self.stages.values())

    @property
    def failed_stages(self) -> List[str]:
        """Get list of failed stage names."""
        return [name for name, stage in self.stages.items() if not stage.passed]

    @property
    def all_issues(self) -> List[str]:
        """Get all issues from all stages."""
        issues = []
        for stage in self.stages.values():
            issues.extend(stage.issues)
        return issues

    def add_stage_result(self, result: StageResult) -> None:
        """Add a stage result."""
        self.stages[result.stage_name] = result

    def to_dict(self) -> Dict[str, Any]:
        """Convert to dictionary for JSON serialization."""
        return {
            "job_id": self.job_id,
            "timestamp": self.timestamp,
            "language": self.language,
            "overall_passed": self.passed,
            "execution_time_seconds": self.execution_time_seconds,
            "stages": {
                name: stage.to_dict()
                for name, stage in self.stages.items()
            },
            "summary": {
                "total_stages": len(self.stages),
                "passed_stages": sum(1 for s in self.stages.values() if s.passed),
                "failed_stages": len(self.failed_stages),
                "issues": self.all_issues,
            }
        }
