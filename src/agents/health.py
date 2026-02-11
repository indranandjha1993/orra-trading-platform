from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass(slots=True)
class AgentHealth:
    name: str
    healthy: bool = False
    ready: bool = False
    last_error: str | None = None
    last_success_at: datetime | None = None
    last_run_at: datetime | None = None
    metrics: dict[str, int] = field(default_factory=dict)

    def mark_run(self) -> None:
        self.last_run_at = datetime.now(timezone.utc)

    def mark_success(self) -> None:
        self.healthy = True
        self.ready = True
        self.last_error = None
        self.last_success_at = datetime.now(timezone.utc)

    def mark_error(self, error: Exception) -> None:
        self.healthy = False
        self.last_error = str(error)

    def payload(self) -> dict[str, object]:
        return {
            "name": self.name,
            "healthy": self.healthy,
            "ready": self.ready,
            "last_error": self.last_error,
            "last_success_at": self.last_success_at.isoformat() if self.last_success_at else None,
            "last_run_at": self.last_run_at.isoformat() if self.last_run_at else None,
            "metrics": self.metrics,
        }
