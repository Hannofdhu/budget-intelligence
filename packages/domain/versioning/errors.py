from dataclasses import dataclass, field
from typing import Any


@dataclass
class DomainError(Exception):
    code: str
    message: str
    status_code: int
    details: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:
        return self.message
