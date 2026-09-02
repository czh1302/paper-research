from __future__ import annotations

import ipaddress
import json
import shlex
from datetime import datetime
from enum import Enum
from typing import Annotated, Any, Literal
from urllib.parse import urlparse

from jsonschema import Draft202012Validator
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
    model_validator,
)

HttpUrlString = Annotated[str, StringConstraints(pattern=r"^https?://")]

PILOT_PACKAGE_HOSTS = frozenset(
    {
        "pypi.org",
        "*.pypi.org",
        "files.pythonhosted.org",
        "registry.npmjs.org",
        "github.com",
        "*.github.com",
        "githubusercontent.com",
        "*.githubusercontent.com",
        "huggingface.co",
        "*.huggingface.co",
    }
)


def _public_http_url(value: str) -> str:
    parsed = urlparse(value)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("Only absolute HTTP(S) evidence URLs are allowed")
    return value


class JobStatus(str, Enum):
    QUEUED = "queued"
    PARSING = "parsing"
    PROBLEM_READY = "problem_ready"
    SEARCHING = "searching"
    ANALYZING = "analyzing"
    RENDERING = "rendering"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    FAILED = "failed"
    BUDGET_BLOCKED = "budget_blocked"
    RECOVERING = "recovering"
    WAITING_RESOURCES = "waiting_resources"
    NEEDS_INPUT = "needs_input"


class AnalysisMode(str, Enum):
    SINGLE = "single"
    MULTI = "multi"


class Evidence(BaseModel):
    id: str
    paper_id: str
    page: int | None = None
    section: str | None = None
    text: str = Field(min_length=1, max_length=4000)
    bbox: list[float] | None = None
    bboxes: list[list[float]] = Field(default_factory=list)
    asset_id: str | None = None
    evidence_type: Literal[
        "input", "output", "algorithm", "constraint", "external"
    ] | None = None
    source_url: HttpUrlString | None = None


class ProblemElement(BaseModel):
    name: str
    description_zh: str
    description_en: str
    symbol: str | None = None
    domain: str | None = None
    evidence_ids: list[str] = Field(min_length=1)


class ProblemStatement(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    is_computer_science: bool
    computer_science_confidence: float = Field(ge=0, le=1)
    background_zh: str
    background_en: str
    background_evidence_ids: list[str] = Field(min_length=1)
    task_zh: str
    task_en: str
    task_evidence_ids: list[str] = Field(min_length=1)
    inputs: list[ProblemElement]
    outputs: list[ProblemElement]
    objectives: list[ProblemElement]
    constraints: list[ProblemElement]
    assumptions: list[ProblemElement]
    algorithm_zh: str
    algorithm_en: str
    algorithm_evidence_ids: list[str] = Field(min_length=1)
    metrics: list[ProblemElement]
    formalization: str | None = None
    formalization_evidence_ids: list[str] = Field(default_factory=list)
    confidence: float = Field(ge=0, le=1)
    evidence: list[Evidence]

    @model_validator(mode="after")
    def formalization_requires_evidence(self) -> ProblemStatement:
        if self.formalization and not self.formalization_evidence_ids:
            raise ValueError("formalization requires at least one evidence ID")
        return self


class JointProblemStatement(BaseModel):
    paper_ids: list[str] = Field(min_length=2, max_length=5)
    common_problem_zh: str
    common_problem_en: str
    aligned_concepts: list[dict[str, Any]]
    differences: list[dict[str, Any]]
    compatible_assumptions: list[str]
    conflicting_assumptions: list[str]
    formalization: str | None = None


class DocumentBlock(BaseModel):
    id: str
    paper_id: str
    kind: str = "text"
    text: str
    page: int | None = None
    section: str | None = None
    bbox: list[float] | None = None


class DocumentIR(BaseModel):
    paper_id: str
    title: str
    markdown: str
    blocks: list[DocumentBlock]
    page_count: int | None = None
    parser: str = "mineru-precision"
    degraded: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchQuery(BaseModel):
    query: str
    rationale: str
    axes: list[str] = Field(default_factory=list)
    source_hint: str | None = None


class QueryBundle(BaseModel):
    round_number: int
    queries: list[SearchQuery] = Field(min_length=1, max_length=24)
    uncovered_axes: list[str] = Field(default_factory=list)


class ProblemBriefItem(BaseModel):
    label_zh: str = Field(max_length=80)
    label_en: str = Field(max_length=140)
    explanation_zh: str = Field(max_length=220)
    explanation_en: str = Field(max_length=420)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class AlgorithmStep(BaseModel):
    order: int = Field(ge=1, le=6)
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=140)
    explanation_zh: str = Field(max_length=240)
    explanation_en: str = Field(max_length=440)
    evidence_ids: list[str] = Field(min_length=1, max_length=4)


class ProblemBrief(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    research_question_zh: str = Field(max_length=180)
    research_question_en: str = Field(max_length=360)
    research_question_evidence_ids: list[str] = Field(min_length=1, max_length=6)
    inputs: list[ProblemBriefItem] = Field(min_length=1, max_length=4)
    outputs: list[ProblemBriefItem] = Field(min_length=1, max_length=4)
    algorithm_steps: list[AlgorithmStep] = Field(min_length=3, max_length=6)
    constraints: list[ProblemBriefItem] = Field(min_length=1, max_length=6)


class IdeaDraft(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    axis: Literal[
        "input",
        "output",
        "method",
        "constraint",
        "evaluation",
        "efficiency",
        "reliability",
        "transfer",
    ]
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    hypothesis_zh: str = Field(max_length=260)
    hypothesis_en: str = Field(max_length=520)
    change_from_target_zh: str = Field(max_length=260)
    change_from_target_en: str = Field(max_length=520)
    rationale_zh: str = Field(max_length=260)
    rationale_en: str = Field(max_length=520)
    feasibility_assumption_zh: str = Field(max_length=220)
    feasibility_assumption_en: str = Field(max_length=440)
    target_evidence_ids: list[str] = Field(min_length=1, max_length=6)


class IdeaDraftBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ideas: list[IdeaDraft] = Field(min_length=1, max_length=8)


class IdeaQueryPlan(BaseModel):
    idea_key: str
    academic_queries: list[str] = Field(min_length=2, max_length=2)
    web_queries: list[str] = Field(min_length=1, max_length=1)


class IdeaQueryPlanBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plans: list[IdeaQueryPlan] = Field(min_length=1, max_length=8)


class ExperimentPlan(BaseModel):
    inputs_zh: str = Field(min_length=5, max_length=220)
    inputs_en: str = Field(min_length=10, max_length=440)
    baseline_zh: str = Field(min_length=3, max_length=220)
    baseline_en: str = Field(min_length=5, max_length=440)
    intervention_zh: str = Field(min_length=5, max_length=260)
    intervention_en: str = Field(min_length=10, max_length=520)
    metrics_zh: str = Field(min_length=3, max_length=220)
    metrics_en: str = Field(min_length=5, max_length=440)
    success_criterion_zh: str = Field(min_length=5, max_length=220)
    success_criterion_en: str = Field(min_length=10, max_length=440)
    resources_zh: str = Field(min_length=3, max_length=220)
    resources_en: str = Field(min_length=5, max_length=440)


class PilotResource(BaseModel):
    """A public, immutable input that an experiment is allowed to download."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z0-9][a-z0-9._-]*$")
    kind: Literal["dataset", "code", "model", "benchmark", "other"]
    name: str = Field(min_length=2, max_length=180)
    url: HttpUrlString
    version: str = Field(min_length=1, max_length=100)
    license: str = Field(min_length=1, max_length=120)
    sha256: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    estimated_bytes: int | None = Field(default=None, ge=0, le=10_000_000_000)
    purpose_zh: str = Field(min_length=4, max_length=240)
    purpose_en: str = Field(min_length=8, max_length=480)

    @field_validator("url")
    @classmethod
    def validate_resource_url(cls, value: str) -> str:
        checked = _public_http_url(value)
        parsed = urlparse(checked)
        if parsed.username or parsed.password:
            raise ValueError("Pilot resource URLs must not contain credentials")
        host = parsed.hostname or ""
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (
            address.is_private
            or address.is_loopback
            or address.is_link_local
            or address.is_reserved
            or address.is_multicast
            or address.is_unspecified
        ):
            raise ValueError("Pilot resources must use a public network address")
        return checked


class PilotMetric(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=60, pattern=r"^[a-z][a-z0-9_.-]*$")
    name_zh: str = Field(min_length=2, max_length=120)
    name_en: str = Field(min_length=2, max_length=180)
    definition_zh: str = Field(min_length=8, max_length=300)
    definition_en: str = Field(min_length=12, max_length=600)
    json_pointer: str = Field(min_length=1, max_length=160, pattern=r"^/")
    direction: Literal["higher", "lower"]
    comparison: Literal["absolute", "delta", "ratio"] = "absolute"
    baseline_json_pointer: str | None = Field(
        default=None, max_length=160, pattern=r"^/"
    )
    intervention_json_pointer: str | None = Field(
        default=None, max_length=160, pattern=r"^/"
    )
    success_threshold: float
    unit: str | None = Field(default=None, max_length=40)

    @model_validator(mode="after")
    def validate_comparison_inputs(self) -> PilotMetric:
        if self.comparison == "absolute":
            if self.baseline_json_pointer or self.intervention_json_pointer:
                raise ValueError("Absolute metrics must use only json_pointer")
        elif not (self.baseline_json_pointer and self.intervention_json_pointer):
            raise ValueError(
                "Delta and ratio metrics require baseline and intervention JSON pointers"
            )
        return self


class PilotEvaluatorCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=2, max_length=100)
    metrics: dict[str, float] = Field(min_length=1, max_length=12)
    expected_pass: bool


class PilotEvaluatorFile(BaseModel):
    """A Pro-authored deterministic evaluator file frozen with the contract."""

    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=180)
    content: str = Field(min_length=20, max_length=60_000)

    @field_validator("path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        cleaned = value.replace("\\", "/").strip()
        if cleaned.startswith("/") or any(
            part in {"", ".", ".."} for part in cleaned.split("/")
        ):
            raise ValueError("Frozen evaluator paths must be safe relative paths")
        return cleaned


class PilotArtifactRule(BaseModel):
    model_config = ConfigDict(extra="forbid")

    path: str = Field(min_length=1, max_length=240)
    kind: Literal["metrics", "plot", "log", "table", "report", "repository"]
    public_safe: bool = False
    description_zh: str = Field(min_length=2, max_length=180)
    description_en: str = Field(min_length=3, max_length=360)

    @field_validator("path")
    @classmethod
    def validate_relative_path(cls, value: str) -> str:
        cleaned = value.replace("\\", "/").strip()
        if cleaned.startswith("/") or any(part in {"", ".", ".."} for part in cleaned.split("/")):
            raise ValueError("Pilot artifact paths must be safe relative paths")
        return cleaned


_INFERENCE_SCHEMA_KEYWORDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "const",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "minProperties",
        "maxProperties",
        "description",
        "title",
    }
)
_INFERENCE_SCHEMA_TYPES = frozenset(
    {"object", "array", "string", "number", "integer", "boolean", "null"}
)


def _validate_inference_schema(schema: dict[str, Any], *, label: str) -> dict[str, Any]:
    """Validate the deliberately small JSON-Schema dialect shared with Edge.

    The inference proxy accepts only this non-recursive, reference-free subset so
    the Edge admission check and the Worker output check have identical semantics.
    A Pro-authored contract therefore cannot introduce remote references, regular
    expressions, executable formats, or an unbounded recursive schema.
    """

    serialized = json.dumps(schema, ensure_ascii=False, separators=(",", ":"))
    if len(serialized.encode("utf-8")) > 12_000:
        raise ValueError(f"{label} is too large")
    try:
        Draft202012Validator.check_schema(schema)
    except Exception as error:
        raise ValueError(f"{label} is not a valid JSON schema") from error

    def visit(node: Any, depth: int) -> None:
        if not isinstance(node, dict) or depth > 8:
            raise ValueError(f"{label} must be a bounded JSON object schema")
        unexpected = set(node).difference(_INFERENCE_SCHEMA_KEYWORDS)
        if unexpected:
            raise ValueError(f"{label} uses unsupported keywords: {sorted(unexpected)!r}")
        schema_type = node.get("type")
        if schema_type not in _INFERENCE_SCHEMA_TYPES:
            raise ValueError(f"{label} requires one supported explicit type per node")
        if "enum" in node and (
            not isinstance(node["enum"], list) or not 1 <= len(node["enum"]) <= 64
        ):
            raise ValueError(f"{label} enum must contain 1-64 values")
        if schema_type == "object":
            properties = node.get("properties", {})
            required = node.get("required", [])
            if not isinstance(properties, dict) or len(properties) > 64:
                raise ValueError(f"{label} has invalid object properties")
            if not isinstance(required, list) or any(
                not isinstance(item, str) or item not in properties for item in required
            ):
                raise ValueError(f"{label} has invalid required properties")
            if node.get("additionalProperties", False) is not False:
                raise ValueError(f"{label} must forbid additional object properties")
            for child in properties.values():
                visit(child, depth + 1)
        elif schema_type == "array":
            if "items" not in node:
                raise ValueError(f"{label} arrays require an item schema")
            visit(node["items"], depth + 1)
        elif "properties" in node or "required" in node or "items" in node:
            raise ValueError(f"{label} contains keywords incompatible with its type")

    visit(schema, 0)
    if schema.get("type") != "object":
        raise ValueError(f"{label} must describe a top-level object")
    return schema


class PilotInferenceContract(BaseModel):
    """A frozen, bounded managed-inference call exposed to subject code only."""

    model_config = ConfigDict(extra="forbid")

    key: str = Field(pattern=r"^[a-z][a-z0-9_]{1,47}$")
    purpose_zh: str = Field(min_length=8, max_length=300)
    purpose_en: str = Field(min_length=12, max_length=600)
    instruction: str = Field(min_length=20, max_length=4_000)
    request_json_schema: dict[str, Any]
    response_json_schema: dict[str, Any]
    max_calls: int = Field(default=1, ge=1, le=8)
    max_request_bytes: int = Field(default=16_384, ge=256, le=32_768)
    max_response_bytes: int = Field(default=32_768, ge=256, le=65_536)

    @field_validator("request_json_schema")
    @classmethod
    def validate_request_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_inference_schema(value, label="request_json_schema")

    @field_validator("response_json_schema")
    @classmethod
    def validate_response_schema(cls, value: dict[str, Any]) -> dict[str, Any]:
        return _validate_inference_schema(value, label="response_json_schema")


class PilotSpecification(BaseModel):
    """Frozen, executable contract compiled by the Pro model before code generation."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1] = 1
    hypothesis_zh: str = Field(min_length=20, max_length=420)
    hypothesis_en: str = Field(min_length=30, max_length=800)
    # ``code_only`` remains readable for historical reports, but the current
    # pipeline only emits one of the three executable modes below.
    execution_mode: Literal[
        "native_cpu",
        "valid_cpu_proxy",
        "exploratory_cpu_proxy",
        "code_only",
    ]
    cpu_proxy_rationale_zh: str | None = Field(default=None, max_length=400)
    cpu_proxy_rationale_en: str | None = Field(default=None, max_length=800)
    invariants_zh: list[str] = Field(min_length=1, max_length=8)
    invariants_en: list[str] = Field(min_length=1, max_length=8)
    resources: list[PilotResource] = Field(min_length=1, max_length=12)
    allowed_hosts: list[str] = Field(min_length=1, max_length=40)
    environment_commands: list[str] = Field(min_length=1, max_length=12)
    test_commands: list[str] = Field(min_length=1, max_length=8)
    baseline_commands: list[str] = Field(min_length=1, max_length=8)
    intervention_commands: list[str] = Field(min_length=1, max_length=8)
    evaluation_commands: list[str] = Field(min_length=1, max_length=8)
    metrics_output_path: str = Field(min_length=1, max_length=240)
    metrics_json_schema: dict[str, Any]
    metrics: list[PilotMetric] = Field(min_length=1, max_length=12)
    primary_metric_key: str = Field(min_length=1, max_length=60)
    evaluator_files: list[PilotEvaluatorFile] = Field(min_length=1, max_length=8)
    evaluator_test_commands: list[str] = Field(min_length=1, max_length=8)
    evaluator_cases: list[PilotEvaluatorCase] = Field(min_length=2, max_length=12)
    artifacts: list[PilotArtifactRule] = Field(min_length=1, max_length=16)
    requires_live_inference: bool = False
    inference_contracts: list[PilotInferenceContract] = Field(
        default_factory=list, max_length=4
    )
    estimated_minutes: int = Field(ge=1, le=60)
    estimated_cpu_count: int = Field(default=4, ge=1, le=4)
    estimated_memory_mib: int = Field(default=8192, ge=256, le=8192)
    estimated_disk_mib: int = Field(default=10240, ge=256, le=10240)

    @field_validator("allowed_hosts")
    @classmethod
    def validate_allowed_hosts(cls, values: list[str]) -> list[str]:
        normalized: list[str] = []
        for value in values:
            host = value.strip().lower().rstrip(".")
            candidate = host[2:] if host.startswith("*.") else host
            if (
                not candidate
                or "/" in candidate
                or ":" in candidate
                or candidate == "localhost"
                or candidate.endswith(".local")
                or "." not in candidate
                or (host.startswith("*.") and candidate.count(".") < 1)
            ):
                raise ValueError("allowed_hosts must contain public DNS hostnames")
            try:
                address = ipaddress.ip_address(candidate)
            except ValueError:
                address = None
            if address is not None:
                raise ValueError("allowed_hosts must contain DNS names, not IP literals")
            normalized.append(host)
        return list(dict.fromkeys(normalized))

    @field_validator(
        "environment_commands",
        "test_commands",
        "baseline_commands",
        "intervention_commands",
        "evaluation_commands",
        "evaluator_test_commands",
    )
    @classmethod
    def validate_commands(cls, values: list[str]) -> list[str]:
        forbidden = ("sudo ", "systemctl ", "service ", "docker ", "${", "`")
        cleaned: list[str] = []
        for value in values:
            command = value.strip()
            if not command or len(command) > 1000:
                raise ValueError("Pilot commands must be non-empty and at most 1000 characters")
            if any(marker in command.casefold() for marker in forbidden):
                raise ValueError("Pilot commands contain a forbidden operation")
            cleaned.append(command)
        return cleaned

    @field_validator("metrics_output_path")
    @classmethod
    def validate_metrics_path(cls, value: str) -> str:
        return PilotArtifactRule.validate_relative_path(value)

    @model_validator(mode="after")
    def validate_contract(self) -> PilotSpecification:
        metric_keys = {item.key for item in self.metrics}
        if self.primary_metric_key not in metric_keys:
            raise ValueError("primary_metric_key must reference a declared metric")
        if self.execution_mode in {"valid_cpu_proxy", "exploratory_cpu_proxy"} and not (
            self.cpu_proxy_rationale_zh and self.cpu_proxy_rationale_en
        ):
            raise ValueError("A CPU proxy requires a bilingual scientific rationale")
        schema_type = self.metrics_json_schema.get("type")
        if schema_type != "object" or not isinstance(
            self.metrics_json_schema.get("properties"), dict
        ):
            raise ValueError("metrics_json_schema must describe a JSON object")
        if len(self.invariants_zh) != len(self.invariants_en):
            raise ValueError("Bilingual invariant lists must have matching lengths")
        if self.requires_live_inference and not self.inference_contracts:
            raise ValueError(
                "Live managed inference requires at least one frozen inference contract"
            )
        if not self.requires_live_inference and self.inference_contracts:
            raise ValueError(
                "Inference contracts require requires_live_inference=true"
            )
        contract_keys = [item.key for item in self.inference_contracts]
        if len(contract_keys) != len(set(contract_keys)):
            raise ValueError("Inference contracts must use unique keys")
        resource_hosts = {
            (urlparse(item.url).hostname or "").casefold() for item in self.resources
        }

        def covered(host: str, rules: set[str]) -> bool:
            return host in rules or any(
                rule.startswith("*.") and host.endswith(rule[1:]) for rule in rules
            )

        allowed = set(self.allowed_hosts)
        if not all(covered(host, allowed) for host in resource_hosts):
            raise ValueError("Every resource hostname must be in allowed_hosts")
        if any(
            rule not in PILOT_PACKAGE_HOSTS
            and not any(
                rule == host
                or (rule.startswith("*.") and host.endswith(rule[1:]))
                for host in resource_hosts
            )
            for rule in allowed
        ):
            raise ValueError(
                "allowed_hosts may contain only package hosts and declared resource hosts"
            )
        evaluator_paths = [item.path for item in self.evaluator_files]
        if len(evaluator_paths) != len(set(evaluator_paths)):
            raise ValueError("Frozen evaluator files must use unique paths")
        frozen_prefix = ".research-atlas/evaluator/"
        for command in self.evaluation_commands + self.evaluator_test_commands:
            if any(marker in command for marker in (";", "&&", "||", "|", ">", "<", "`", "$")):
                raise ValueError("Frozen evaluator commands cannot use shell composition")
            try:
                tokens = shlex.split(command)
            except ValueError as error:
                raise ValueError("Frozen evaluator command has invalid quoting") from error
            executable = tokens[0].rsplit("/", 1)[-1] if tokens else ""
            frozen_tokens = [token for token in tokens[1:] if token.startswith(frozen_prefix)]
            if executable not in {"python", "python3", "pytest"} or not frozen_tokens:
                raise ValueError(
                    "Evaluator commands must execute files from the frozen evaluator directory"
                )
            if len(tokens) != 2 or len(frozen_tokens) != 1:
                raise ValueError(
                    "Evaluator commands must invoke exactly one frozen Python file without flags"
                )
            for token in frozen_tokens:
                referenced = token[len(frozen_prefix) :]
                if referenced not in evaluator_paths:
                    raise ValueError("Evaluator command references an undeclared frozen file")
        return self


class IdeaEvidence(BaseModel):
    paper_id: str
    relationship: Literal["support", "overlap", "counterevidence"]
    claim_zh: str = Field(max_length=240)
    claim_en: str = Field(max_length=480)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=3)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class IdeaAssessment(BaseModel):
    idea_key: str
    axis: str
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    hypothesis_zh: str = Field(max_length=260)
    hypothesis_en: str = Field(max_length=520)
    change_from_target_zh: str = Field(max_length=260)
    change_from_target_en: str = Field(max_length=520)
    recommendation_reason_zh: str = Field(max_length=260)
    recommendation_reason_en: str = Field(max_length=520)
    feasibility_conditions_zh: str = Field(max_length=300)
    feasibility_conditions_en: str = Field(max_length=600)
    unresolved_questions_zh: list[str] = Field(default_factory=list, max_length=4)
    unresolved_questions_en: list[str] = Field(default_factory=list, max_length=4)
    evidence: list[IdeaEvidence] = Field(default_factory=list, max_length=8)
    experiment: ExperimentPlan
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    evidence_confidence: float = Field(ge=0, le=1)
    collision_risk: Literal["low", "medium", "high"]
    verdict: Literal["viable", "conditional", "rejected"]
    rejection_reason_zh: str = Field(default="", max_length=260)
    rejection_reason_en: str = Field(default="", max_length=520)


class IdeaAssessmentBatch(BaseModel):
    model_config = ConfigDict(extra="forbid")

    assessments: list[IdeaAssessment] = Field(min_length=1, max_length=8)


class IdeaResearchRound(BaseModel):
    version: Literal[3] = 3
    round_number: int = Field(ge=1, le=5)
    drafts: list[IdeaDraft] = Field(min_length=1, max_length=8)
    assessments: list[IdeaAssessment] = Field(min_length=1, max_length=8)
    selected_idea_keys: list[str] = Field(default_factory=list, max_length=3)
    rejected_idea_keys: list[str] = Field(default_factory=list, max_length=8)
    full_text_paper_ids: list[str] = Field(default_factory=list, max_length=6)


class RejectedIdea(BaseModel):
    idea_key: str
    title_zh: str
    title_en: str
    reason_zh: str
    reason_en: str


class IdeaComparisonRow(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_role: Literal["input", "external"]
    paper_id: str
    title: str
    relationship: Literal["baseline", "support", "overlap", "counterevidence"]
    task_or_capability_zh: str = Field(max_length=420)
    task_or_capability_en: str = Field(max_length=800)
    method_or_change_zh: str = Field(max_length=420)
    method_or_change_en: str = Field(max_length=800)
    output_or_evaluation_zh: str = Field(max_length=420)
    output_or_evaluation_en: str = Field(max_length=800)
    key_constraint_zh: str = Field(max_length=420)
    key_constraint_en: str = Field(max_length=800)
    difference_to_idea_zh: str = Field(max_length=420)
    difference_to_idea_en: str = Field(max_length=800)
    evidence_grade: Literal[
        "input_pdf", "full_text", "abstract", "snippet", "metadata"
    ]
    source_urls: list[HttpUrlString] = Field(default_factory=list, max_length=3)
    input_evidence_ids: list[str] = Field(default_factory=list, max_length=8)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class IdeaComparisonMatrix(BaseModel):
    model_config = ConfigDict(extra="forbid")

    idea_key: str
    status: Literal["viable", "conditional", "rejected"]
    rows: list[IdeaComparisonRow] = Field(min_length=1, max_length=16)


class CandidatePaper(BaseModel):
    model_config = ConfigDict(extra="ignore")

    canonical_id: str = ""
    title: str
    abstract: str = ""
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    venue: str | None = None
    url: HttpUrlString
    pdf_url: HttpUrlString | None = None
    doi: str | None = None
    arxiv_id: str | None = None
    openreview_id: str | None = None
    openalex_id: str | None = None
    reference_ids: list[str] = Field(default_factory=list)
    citation_count: int | None = None
    open_access: bool | None = None
    sources: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)
    idea_keys: list[str] = Field(default_factory=list)
    relevance_score: float = Field(default=0, ge=0, le=1)
    evidence_grade: Literal["full_text", "abstract", "snippet", "metadata"] = "metadata"
    retrieved_at: datetime = Field(default_factory=datetime.utcnow)

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        return _public_http_url(value)

    @field_validator("pdf_url")
    @classmethod
    def validate_pdf_url(cls, value: str | None) -> str | None:
        return _public_http_url(value) if value else None


class ComparisonCell(BaseModel):
    paper_id: str
    axis: str
    value_zh: str = Field(max_length=300)
    value_en: str = Field(max_length=500)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=3)
    confidence: float = Field(ge=0, le=1)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ResearchOpportunity(BaseModel):
    title_zh: str = Field(max_length=200)
    title_en: str = Field(max_length=200)
    rationale_zh: str = Field(max_length=500)
    rationale_en: str = Field(max_length=700)
    novelty_evidence: list[HttpUrlString] = Field(min_length=1, max_length=5)
    proposed_experiment_zh: str = Field(max_length=500)
    proposed_experiment_en: str = Field(max_length=700)
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)

    @field_validator("novelty_evidence")
    @classmethod
    def validate_novelty_evidence(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class PresentationFinding(BaseModel):
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=160)
    statement_zh: str = Field(max_length=300)
    statement_en: str = Field(max_length=600)
    implication_zh: str = Field(max_length=240)
    implication_en: str = Field(max_length=480)
    pdf_evidence_ids: list[str] = Field(default_factory=list, max_length=6)
    source_urls: list[HttpUrlString] = Field(default_factory=list, max_length=4)

    @field_validator("source_urls")
    @classmethod
    def validate_source_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ResearchTheme(BaseModel):
    title_zh: str = Field(max_length=80)
    title_en: str = Field(max_length=160)
    summary_zh: str = Field(max_length=300)
    summary_en: str = Field(max_length=600)
    paper_ids: list[str] = Field(min_length=1, max_length=4)


class PresentationIdea(BaseModel):
    key: str = Field(min_length=1, max_length=50)
    priority: int = Field(ge=1, le=3)
    title_zh: str = Field(max_length=100)
    title_en: str = Field(max_length=180)
    idea_zh: str = Field(max_length=260)
    idea_en: str = Field(max_length=520)
    gap_zh: str = Field(max_length=300)
    gap_en: str = Field(max_length=600)
    approach_zh: str = Field(max_length=300)
    approach_en: str = Field(max_length=600)
    first_experiment_zh: str = Field(max_length=360)
    first_experiment_en: str = Field(max_length=700)
    expected_outcome_zh: str = Field(max_length=240)
    expected_outcome_en: str = Field(max_length=480)
    main_risk_zh: str = Field(max_length=240)
    main_risk_en: str = Field(max_length=480)
    recommendation_reason_zh: str = Field(max_length=220)
    recommendation_reason_en: str = Field(max_length=440)
    feasibility_reason_zh: str = Field(max_length=180)
    feasibility_reason_en: str = Field(max_length=360)
    impact_reason_zh: str = Field(max_length=180)
    impact_reason_en: str = Field(max_length=360)
    uncertainty_reason_zh: str = Field(max_length=180)
    uncertainty_reason_en: str = Field(max_length=360)
    feasibility: float = Field(ge=0, le=1)
    impact: float = Field(ge=0, le=1)
    uncertainty: float = Field(ge=0, le=1)
    evidence_urls: list[HttpUrlString] = Field(min_length=1, max_length=5)

    @field_validator("evidence_urls")
    @classmethod
    def validate_evidence_urls(cls, values: list[str]) -> list[str]:
        return [_public_http_url(value) for value in values]


class ReportPresentation(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[2] = 2
    headline_zh: str = Field(max_length=100)
    headline_en: str = Field(max_length=200)
    executive_summary_zh: str = Field(max_length=600)
    executive_summary_en: str = Field(max_length=1000)
    key_findings: list[PresentationFinding] = Field(min_length=1, max_length=3)
    themes: list[ResearchTheme] = Field(min_length=3, max_length=5)
    ideas: list[PresentationIdea] = Field(min_length=3, max_length=3)

    @model_validator(mode="after")
    def priorities_are_unique(self) -> ReportPresentation:
        if sorted(item.priority for item in self.ideas) != [1, 2, 3]:
            raise ValueError("presentation idea priorities must be exactly 1, 2, and 3")
        return self


class ReportPresentationV3(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[3] = 3
    headline_zh: str = Field(max_length=180)
    headline_en: str = Field(max_length=360)
    problem_briefs: list[ProblemBrief] = Field(min_length=1, max_length=5)
    ideas: list[IdeaAssessment] = Field(default_factory=list, max_length=3)
    promising_ideas: list[IdeaAssessment] = Field(default_factory=list, max_length=3)
    rejected_ideas: list[RejectedIdea] = Field(default_factory=list, max_length=8)
    idea_comparisons: list[IdeaComparisonMatrix] = Field(default_factory=list, max_length=8)


class EvidenceLocator(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    asset_id: str
    paper_id: str
    page: int = Field(ge=1)
    quote: str = Field(min_length=8, max_length=1800)
    section: str | None = Field(default=None, max_length=200)
    evidence_type: Literal[
        "input", "output", "algorithm", "constraint", "external"
    ]
    bboxes: list[list[float]] = Field(default_factory=list, max_length=8)

    @field_validator("bboxes")
    @classmethod
    def validate_bboxes(cls, values: list[list[float]]) -> list[list[float]]:
        output: list[list[float]] = []
        for value in values:
            if len(value) != 4:
                continue
            box = [max(0.0, min(float(item), 1000.0)) for item in value]
            if box[2] > box[0] and box[3] > box[1]:
                output.append(box)
        return output


class GroundedClaim(BaseModel):
    model_config = ConfigDict(extra="forbid")

    claim_zh: str = Field(min_length=8, max_length=500)
    claim_en: str = Field(min_length=12, max_length=900)
    evidence: list[EvidenceLocator] = Field(min_length=1, max_length=8)


class PaperEvidenceProfile(BaseModel):
    model_config = ConfigDict(extra="forbid")

    paper_id: str
    title: str
    year: int | None = None
    venue: str | None = None
    source_url: HttpUrlString | None = None
    pdf_url: HttpUrlString | None = None
    role: Literal["input", "external"]
    evidence_grade: Literal["input_pdf", "full_text", "abstract"]
    task: GroundedClaim
    input_or_data: GroundedClaim
    method: GroundedClaim
    output_or_evaluation: GroundedClaim
    constraints: GroundedClaim
    limitations: GroundedClaim

    @field_validator("source_url")
    @classmethod
    def validate_profile_url(cls, value: str | None) -> str | None:
        return _public_http_url(value) if value else None

    @field_validator("pdf_url")
    @classmethod
    def validate_profile_pdf_url(cls, value: str | None) -> str | None:
        return _public_http_url(value) if value else None


class LiteratureThemeV4(BaseModel):
    key: str = Field(min_length=1, max_length=60)
    title_zh: str = Field(min_length=2, max_length=100)
    title_en: str = Field(min_length=3, max_length=180)
    summary_zh: str = Field(min_length=12, max_length=420)
    summary_en: str = Field(min_length=20, max_length=800)
    paper_ids: list[str] = Field(min_length=1, max_length=12)


class LiteratureLandscape(BaseModel):
    model_config = ConfigDict(extra="forbid")

    overview_zh: str = Field(min_length=30, max_length=900)
    overview_en: str = Field(min_length=50, max_length=1600)
    candidate_count: int = Field(ge=0)
    screened_count: int = Field(ge=0)
    full_text_count: int = Field(ge=0)
    source_counts: dict[str, int]
    themes: list[LiteratureThemeV4] = Field(min_length=2, max_length=8)
    profiles: list[PaperEvidenceProfile] = Field(min_length=1, max_length=31)


class LiteratureLandscapeDraft(BaseModel):
    overview_zh: str = Field(min_length=30, max_length=900)
    overview_en: str = Field(min_length=50, max_length=1600)
    themes: list[LiteratureThemeV4] = Field(min_length=2, max_length=8)


class PaperRanking(BaseModel):
    paper_id: str
    relevance: float = Field(ge=0, le=1)
    reason: str = Field(max_length=240)


class PaperRankingBatch(BaseModel):
    rankings: list[PaperRanking] = Field(min_length=1, max_length=80)


class SubmissionIdea(BaseModel):
    model_config = ConfigDict(extra="forbid")

    key: str = Field(min_length=1, max_length=60)
    rank: int = Field(default=0, ge=0, le=3)
    title_zh: str = Field(min_length=4, max_length=120)
    title_en: str = Field(min_length=8, max_length=220)
    one_sentence_zh: str = Field(min_length=20, max_length=300)
    one_sentence_en: str = Field(min_length=30, max_length=600)
    pain_point_zh: str = Field(min_length=20, max_length=500)
    pain_point_en: str = Field(min_length=30, max_length=900)
    hypothesis_zh: str = Field(min_length=20, max_length=420)
    hypothesis_en: str = Field(min_length=30, max_length=800)
    core_contribution_zh: str = Field(min_length=20, max_length=500)
    core_contribution_en: str = Field(min_length=30, max_length=900)
    mechanism_zh: str = Field(min_length=20, max_length=600)
    mechanism_en: str = Field(min_length=30, max_length=1000)
    change_from_input_zh: str = Field(min_length=20, max_length=500)
    change_from_input_en: str = Field(min_length=30, max_length=900)
    experiment: ExperimentPlan
    pilot_specification: PilotSpecification | None = None
    closest_work_ids: list[str] = Field(min_length=2, max_length=10)
    supporting_work_ids: list[str] = Field(min_length=2, max_length=10)
    counterevidence_work_ids: list[str] = Field(default_factory=list, max_length=6)
    unresolved_questions_zh: list[str] = Field(default_factory=list, max_length=5)
    unresolved_questions_en: list[str] = Field(default_factory=list, max_length=5)
    feasibility: float = Field(default=0, ge=0, le=1)
    submission_value: float = Field(default=0, ge=0, le=1)
    evidence_confidence: float = Field(default=0, ge=0, le=1)
    collision_risk: Literal["low", "medium", "high"] = "medium"
    verdict: Literal[
        "recommended", "alternative", "needs_evidence", "rejected"
    ] = "needs_evidence"
    qualification_tier: Literal["strict", "relaxed", "exploratory"] = "strict"
    review_attempt: int = Field(default=1, ge=1, le=8)
    missing_evidence_zh: list[str] = Field(default_factory=list, max_length=5)
    missing_evidence_en: list[str] = Field(default_factory=list, max_length=5)
    lineage_id: str | None = Field(default=None, max_length=80)
    parent_key: str | None = Field(default=None, max_length=80)
    revision_number: int = Field(default=0, ge=0, le=8)


class SubmissionIdeaBatch(BaseModel):
    ideas: list[SubmissionIdea] = Field(min_length=4, max_length=8)


class SubmissionIdeaPairBatch(BaseModel):
    ideas: list[SubmissionIdea] = Field(min_length=2, max_length=2)


class SubmissionIdeaSingleBatch(BaseModel):
    ideas: list[SubmissionIdea] = Field(min_length=1, max_length=1)


class IdeaReview(BaseModel):
    idea_key: str
    idea_title_zh: str = Field(default="", max_length=120)
    idea_title_en: str = Field(default="", max_length=220)
    decision: Literal["recommended", "alternative", "needs_evidence", "rejected"]
    rationale_zh: str = Field(min_length=12, max_length=500)
    rationale_en: str = Field(min_length=20, max_length=900)
    closest_work_ids: list[str] = Field(min_length=2, max_length=10)
    supporting_work_ids: list[str] = Field(min_length=2, max_length=10)
    counterevidence_work_ids: list[str] = Field(default_factory=list, max_length=6)
    missing_evidence_zh: list[str] = Field(default_factory=list, max_length=5)
    missing_evidence_en: list[str] = Field(default_factory=list, max_length=5)
    feasibility: float = Field(ge=0, le=1)
    submission_value: float = Field(ge=0, le=1)
    evidence_confidence: float = Field(ge=0, le=1)
    collision_risk: Literal["low", "medium", "high"]


class IdeaReviewBatch(BaseModel):
    reviews: list[IdeaReview] = Field(min_length=1, max_length=8)


class IdeaAttemptSummary(BaseModel):
    attempt: int = Field(ge=1, le=8)
    generated: int = Field(ge=0, le=8)
    grounded: int = Field(ge=0, le=8)
    strict_passed: int = Field(ge=0, le=3)
    added_candidates: int = Field(default=0, ge=0)
    added_full_text: int = Field(default=0, ge=0)
    rejection_reasons_zh: list[str] = Field(default_factory=list, max_length=12)
    rejection_reasons_en: list[str] = Field(default_factory=list, max_length=12)


class IdeaComparisonBoard(BaseModel):
    idea_key: str
    input_paper_id: str
    external_paper_ids: list[str] = Field(min_length=1, max_length=10)
    profiles: list[PaperEvidenceProfile] = Field(min_length=2, max_length=11)


class ReportPresentationV4(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[4] = 4
    generation_id: str | None = Field(default=None, max_length=64)
    headline_zh: str = Field(min_length=12, max_length=220)
    headline_en: str = Field(min_length=20, max_length=420)
    problem_briefs: list[ProblemBrief] = Field(min_length=1, max_length=5)
    literature_landscape: LiteratureLandscape
    ideas: list[SubmissionIdea] = Field(default_factory=list, max_length=3)
    reviews: list[IdeaReview] = Field(default_factory=list, max_length=8)
    comparison_boards: list[IdeaComparisonBoard] = Field(default_factory=list, max_length=3)
    idea_attempt_summaries: list[IdeaAttemptSummary] = Field(default_factory=list, max_length=8)
    idea_evolution_audit: list[dict[str, Any]] = Field(default_factory=list, max_length=8)


class RoundAnalysis(BaseModel):
    summary_zh: str = Field(max_length=1500)
    summary_en: str = Field(max_length=2000)
    comparison_cells: list[ComparisonCell] = Field(max_length=18)
    opportunities: list[ResearchOpportunity] = Field(max_length=3)
    covered_axes: list[str] = Field(max_length=20)
    uncovered_axes: list[str] = Field(max_length=20)
    high_relevance_ids: list[str] = Field(max_length=30)
    source_warnings: list[str] = Field(default_factory=list, max_length=20)


class AnalysisReport(BaseModel):
    job_id: str
    generation_id: str | None = Field(default=None, max_length=64)
    generated_at: datetime = Field(default_factory=datetime.utcnow)
    problem_statements: list[ProblemStatement]
    joint_problem_statement: JointProblemStatement | None = None
    related_papers: list[CandidatePaper]
    rounds: list[RoundAnalysis]
    search_audit: list[dict[str, Any]]
    parser_audit: list[dict[str, Any]] = Field(default_factory=list)
    source_coverage: dict[str, Any]
    limitations_zh: str
    limitations_en: str
    presentation: ReportPresentation | ReportPresentationV3 | ReportPresentationV4 | None = None
    idea_rounds: list[IdeaResearchRound] = Field(default_factory=list)


class JobFile(BaseModel):
    id: str
    storage_path: str
    original_name: str
    size_bytes: int
    sha256: str | None = None


class Job(BaseModel):
    id: str
    user_id: str
    mode: AnalysisMode
    max_rounds: int = Field(ge=1, le=5)
    languages: list[Literal["zh", "en"]] = Field(default_factory=lambda: ["zh", "en"])
    research_brief: str = Field(default="", max_length=2000)
    status: JobStatus
    current_round: int = 0
    stage: str = "queued"
    files: list[JobFile] = Field(min_length=1, max_length=5)
    checkpoint: dict[str, Any] = Field(default_factory=dict)
    retry_count: int = Field(default=0, ge=0)
    next_retry_at: datetime | None = None
    last_recovery_at: datetime | None = None

    @model_validator(mode="after")
    def validate_mode_file_count(self) -> Job:
        count = len(self.files)
        if self.mode == AnalysisMode.SINGLE and count != 1:
            raise ValueError("single mode requires exactly one PDF")
        if self.mode == AnalysisMode.MULTI and not 2 <= count <= 5:
            raise ValueError("multi mode requires two to five PDFs")
        return self


class ProviderUsage(BaseModel):
    provider: str
    model: str | None = None
    input_tokens: int = 0
    output_tokens: int = 0
    requests: int = 1
    estimated_cny: float = 0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SourceResult(BaseModel):
    source: str
    query: str
    papers: list[CandidatePaper]
    warning: str | None = None
    request_count: int = 1


class WebDiscovery(BaseModel):
    papers: list[CandidatePaper] = Field(default_factory=list, max_length=12)
    searched_queries: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


class ShareView(BaseModel):
    token: str
    report: AnalysisReport
    expires_at: datetime
