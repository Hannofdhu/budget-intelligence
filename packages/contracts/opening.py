import uuid
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from packages.contracts.versioning import RevisionCommandResponse


class OpeningSourceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    track: str = Field(min_length=1, max_length=100)
    product: str = Field(min_length=1, max_length=100)
    grade: Literal["七年级", "八年级", "九年级"]
    subject: str = Field(min_length=1, max_length=100)
    campus: str = Field(min_length=1, max_length=200)
    enrollment: Annotated[int, Field(strict=True, ge=0)]
    rate_parameter_id: uuid.UUID


class OpeningCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    source_component_id: uuid.UUID
    source_fiscal_season: int
    rows: list[OpeningSourceInput] = Field(max_length=10000)
    zero_combination_ids: list[uuid.UUID] = Field(max_length=10000)
    reason: str = Field(min_length=1, max_length=500)


class OpeningVersionResponse(BaseModel):
    opening_student_version_id: uuid.UUID
    content_hash: str
    content: dict[str, Any]
    revision: RevisionCommandResponse
