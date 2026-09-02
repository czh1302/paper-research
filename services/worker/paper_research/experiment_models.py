from __future__ import annotations

import hashlib
import json
from enum import Enum
from pathlib import PurePosixPath
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import PilotSpecification


class ExperimentStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    RECOVERING = "recovering"
    WAITING_RESOURCES = "waiting_resources"
    READY = "ready"
    CANCELLED = "cancelled"


class ExperimentStage(str, Enum):
    SPEC_FREEZE = "spec_freeze"
    REPO_GENERATION = "repo_generation"
    ENVIRONMENT_SETUP = "environment_setup"
    BASELINE = "baseline"
    INTERVENTION = "intervention"
    EVALUATION = "evaluation"
    REPAIR = "repair"
    ARCHIVE = "archive"
    INTERACTIVE = "interactive"


class ExperimentOutcome(str, Enum):
    PENDING = "pending"
    INITIAL_SUPPORT = "initial_support"
    NOT_SUPPORT = "not_support"
    INCONCLUSIVE = "inconclusive"
    ENVIRONMENT_BLOCKED = "environment_blocked"
    RESOURCE_LIMITED = "resource_limited"
    BUDGET_BLOCKED = "budget_blocked"
    CANCELLED = "cancelled"


class ExperimentRecord(BaseModel):
    model_config = ConfigDict(extra="allow")

    id: str
    report_id: str
    job_id: str
    user_id: str
    idea_key: str
    idea_rank: int = 1
    idea_snapshot: dict[str, Any]
    pilot_specification: dict[str, Any] = Field(default_factory=dict)
    pilot_specification_hash: str | None = None
    pilot_compilation_required: bool = True
    automatic_initial_run: bool = False
    status: ExperimentStatus = ExperimentStatus.QUEUED
    stage: str = ExperimentStage.SPEC_FREEZE.value
    progress: int = 0
    outcome: ExperimentOutcome = ExperimentOutcome.PENDING
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    baseline_revision_id: str | None = None
    current_revision_id: str | None = None
    latest_run_id: str | None = None
    user_validation_count: int = 0
    max_user_validations: int = 3
    repair_count: int = 0
    e2b_seconds: int = 0
    e2b_cost_usd: float = 0
    llm_cost_cny: float = 0
    retry_count: int = 0
    cancellation_requested: bool = False
    deletion_requested_at: str | None = None

    def validated_specification(self) -> PilotSpecification:
        return PilotSpecification.model_validate(self.pilot_specification)


class PilotCompilation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    accepted: bool
    rationale_zh: str = Field(min_length=6, max_length=600)
    rationale_en: str = Field(min_length=10, max_length=1200)
    specification: PilotSpecification | None = None

    @model_validator(mode="after")
    def validate_decision(self) -> PilotCompilation:
        if self.accepted and self.specification is None:
            raise ValueError("An accepted pilot compilation requires a specification")
        return self


def safe_repository_path(value: str) -> str:
    normalized = value.replace("\\", "/").strip()
    if normalized.startswith("./"):
        normalized = normalized[2:]
    path = PurePosixPath(normalized)
    if (
        not normalized
        or len(normalized.encode("utf-8")) > 240
        or any(ord(character) < 32 or ord(character) == 127 for character in normalized)
        or path.is_absolute()
        or ".." in path.parts
        or any(
            part in {"", ".", ".git", ".env", ".research-atlas"}
            or part.startswith(".env.")
            for part in path.parts
        )
        or normalized.startswith(".research-atlas/")
    ):
        raise ValueError(f"Unsafe repository path: {value!r}")
    return str(path)


class RepositoryFilePlan(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    purpose: str = Field(min_length=3, max_length=300)
    language: str = Field(min_length=1, max_length=40)
    batch: int = Field(ge=1, le=8)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return safe_repository_path(value)


class RepositoryManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    architecture_zh: str = Field(min_length=20, max_length=1000)
    architecture_en: str = Field(min_length=30, max_length=1800)
    files: list[RepositoryFilePlan] = Field(min_length=6, max_length=48)

    @model_validator(mode="after")
    def validate_unique_paths(self) -> RepositoryManifest:
        paths = [item.path for item in self.files]
        if len(paths) != len(set(paths)):
            raise ValueError("Repository manifest contains duplicate paths")
        if not any(path.casefold().startswith("readme") for path in paths):
            raise ValueError("Repository manifest must include a README")
        if not any("test" in path.casefold() for path in paths):
            raise ValueError("Repository manifest must include tests")
        batch_counts: dict[int, int] = {}
        for item in self.files:
            batch_counts[item.batch] = batch_counts.get(item.batch, 0) + 1
        if any(count > 8 for count in batch_counts.values()):
            raise ValueError("Repository batches may contain at most eight files")
        return self


class GeneratedRepositoryFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=200_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return safe_repository_path(value)


class RepositoryFileBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    files: list[GeneratedRepositoryFile] = Field(min_length=1, max_length=8)


class RepairFile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    content: str = Field(max_length=200_000)
    reason: str = Field(min_length=3, max_length=400)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        return safe_repository_path(value)


class ExperimentRepair(BaseModel):
    model_config = ConfigDict(extra="forbid")

    diagnosis: str = Field(min_length=8, max_length=1000)
    files: list[RepairFile] = Field(min_length=1, max_length=8)
    verification_commands: list[str] = Field(min_length=1, max_length=6)

    @field_validator("verification_commands")
    @classmethod
    def validate_verification_commands(cls, values: list[str]) -> list[str]:
        return [_safe_workspace_command(value) for value in values]


class ExperimentInterpretation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary_zh: str = Field(min_length=12, max_length=600)
    summary_en: str = Field(min_length=20, max_length=1200)
    limitations_zh: list[str] = Field(default_factory=list, max_length=8)
    limitations_en: list[str] = Field(default_factory=list, max_length=8)
    next_steps_zh: list[str] = Field(default_factory=list, max_length=6)
    next_steps_en: list[str] = Field(default_factory=list, max_length=6)


class AssistantWorkspaceChange(BaseModel):
    model_config = ConfigDict(extra="forbid")

    explanation_zh: str = Field(min_length=2, max_length=800)
    explanation_en: str = Field(min_length=3, max_length=1400)
    files: list[GeneratedRepositoryFile] = Field(default_factory=list, max_length=12)
    delete_paths: list[str] = Field(default_factory=list, max_length=12)
    commands: list[str] = Field(default_factory=list, max_length=6)

    @field_validator("delete_paths")
    @classmethod
    def validate_delete_paths(cls, values: list[str]) -> list[str]:
        return list(dict.fromkeys(safe_repository_path(value) for value in values))

    @field_validator("commands")
    @classmethod
    def validate_commands(cls, values: list[str]) -> list[str]:
        return [_safe_workspace_command(value) for value in values]

    @model_validator(mode="after")
    def validate_file_operations(self) -> AssistantWorkspaceChange:
        replacement_paths = [item.path for item in self.files]
        if len(replacement_paths) != len(set(replacement_paths)):
            raise ValueError("Assistant file replacements must use unique paths")
        if set(replacement_paths).intersection(self.delete_paths):
            raise ValueError("Assistant cannot replace and delete the same path")
        return self


def _safe_workspace_command(value: str) -> str:
    command = value.strip()
    lowered = command.casefold()
    forbidden = (
        ".research-atlas",
        "sudo ",
        "systemctl ",
        "service ",
        "docker ",
        "rm -rf",
        "git clean",
        "git reset",
        "curl ",
        "wget ",
        "/proc/",
        "printenv",
    )
    if not command or len(command) > 1000 or any(item in lowered for item in forbidden):
        raise ValueError("Workspace verification command is unsafe")
    return command


class CommandExecution(BaseModel):
    command: str
    exit_code: int
    stdout: str = ""
    stderr: str = ""
    elapsed_seconds: float = 0


class DeterministicEvaluation(BaseModel):
    passed: bool
    primary_metric_key: str
    primary_value: float
    threshold: float
    direction: Literal["higher", "lower"]
    metrics: dict[str, float]
    evaluator_cases_passed: bool
    specification_hash: str


def specification_hash(specification: PilotSpecification | dict[str, Any]) -> str:
    value = (
        specification.model_dump(mode="json")
        if isinstance(specification, PilotSpecification)
        else specification
    )
    canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()
