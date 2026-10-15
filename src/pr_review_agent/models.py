from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, Field, field_validator


class Severity(StrEnum):
    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"

    @property
    def rank(self) -> int:
        return _SEVERITY_RANK[self]


_SEVERITY_RANK = {
    Severity.LOW: 0,
    Severity.MEDIUM: 1,
    Severity.HIGH: 2,
    Severity.CRITICAL: 3,
}


class Finding(BaseModel):
    file: str = Field(description="Path of the file, exactly as shown in the diff header")
    line: int = Field(ge=1, description="1-based line number in the NEW version of the file")
    severity: Severity
    category: str = Field(description="Short kebab-case bug class, e.g. sql-injection, off-by-one")
    issue: str = Field(min_length=1, description="What is wrong and why it matters")
    suggested_fix: str = Field(description="Concrete fix; replacement code when possible")
    confidence: float = Field(ge=0.0, le=1.0)

    @field_validator("file", "category", "issue", mode="before")
    @classmethod
    def _strip(cls, value: object) -> object:
        return value.strip() if isinstance(value, str) else value

    @field_validator("suggested_fix", mode="before")
    @classmethod
    def _strip_keep_indent(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        lines = value.rstrip().split("\n")
        while lines and not lines[0].strip():
            lines.pop(0)
        return "\n".join(line.rstrip() for line in lines)


class ReviewResult(BaseModel):
    summary: str
    findings: list[Finding] = Field(default_factory=list)


class Usage(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0
    latency_s: float = 0.0
    served_models: list[str] = Field(default_factory=list)

    def __add__(self, other: Usage) -> Usage:
        return Usage(
            input_tokens=self.input_tokens + other.input_tokens,
            output_tokens=self.output_tokens + other.output_tokens,
            cost_usd=self.cost_usd + other.cost_usd,
            latency_s=self.latency_s + other.latency_s,
            served_models=sorted(set(self.served_models) | set(other.served_models)),
        )


class ReviewOutput(BaseModel):
    inline: list[Finding] = Field(default_factory=list)
    off_diff: list[Finding] = Field(default_factory=list)
    summary: str = ""
    usage: Usage = Field(default_factory=Usage)
