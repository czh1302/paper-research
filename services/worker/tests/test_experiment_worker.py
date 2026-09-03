from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest
from paper_research.clients.e2b import (
    E2B_BASE_IMAGE_DIGEST,
    E2BSandboxHandle,
    E2BSandboxProvider,
    SandboxFileTooLargeError,
    SandboxNotFoundError,
    SandboxRuntimeTaintedError,
)
from paper_research.clients.llm import ClaudeCodeAccountingError, ClaudeCodeError
from paper_research.config import Settings
from paper_research.experiment_models import (
    AssistantWorkspaceChange,
    CommandExecution,
    ExperimentRecord,
    GeneratedRepositoryFile,
    safe_repository_path,
    specification_hash,
)
from paper_research.experiment_worker import (
    _REPOSITORY_AUDIT_SCRIPT,
    ExperimentBudgetBlocked,
    ExperimentRunDeadlineExceeded,
    ExperimentWorker,
    LeaseLost,
    RichRepositoryManifest,
    WorkspaceResourceLimitExceeded,
    _deterministic_exploratory_repository,
    _exploratory_fallback_specification,
    _validate_generated_repository_quality,
    evaluate_metrics,
    validate_pilot_specification,
)
from paper_research.models import PilotSpecification, ProviderUsage
from paper_research.validation_bundle import ValidationInput, build_validation_bundle
from pydantic import ValidationError


def test_e2b_global_concurrency_accepts_eight_and_rejects_more() -> None:
    assert Settings(_env_file=None, E2B_GLOBAL_CONCURRENCY=8).E2B_GLOBAL_CONCURRENCY == 8
    with pytest.raises(ValidationError):
        Settings(_env_file=None, E2B_GLOBAL_CONCURRENCY=9)


def pilot_payload(*, comparison: str = "absolute") -> dict:
    metric = {
        "key": "effect",
        "name_zh": "主要效果",
        "name_en": "Primary effect",
        "definition_zh": "冻结评价器计算的确定性主要效果。",
        "definition_en": "The deterministic primary effect computed by the frozen evaluator.",
        "json_pointer": "/effect",
        "direction": "higher",
        "comparison": comparison,
        "success_threshold": 0.2,
    }
    properties = {"effect": {"type": "number"}}
    required = ["effect"]
    if comparison != "absolute":
        metric.update(
            {
                "baseline_json_pointer": "/baseline",
                "intervention_json_pointer": "/intervention",
            }
        )
        properties.update(
            {"baseline": {"type": "number"}, "intervention": {"type": "number"}}
        )
        required.extend(["baseline", "intervention"])
    return {
        "version": 1,
        "hypothesis_zh": "在固定数据和资源预算下，核心机制会改善主要确定性评价指标。",
        "hypothesis_en": "Under fixed data and resource budgets, the mechanism improves the deterministic primary metric.",
        "execution_mode": "native_cpu",
        "invariants_zh": ["数据划分不变"],
        "invariants_en": ["The data split remains fixed"],
        "resources": [
            {
                "key": "repo",
                "kind": "code",
                "name": "Public repository",
                "url": "https://github.com/example/research-code",
                "version": "commit-abc123",
                "license": "MIT",
                "purpose_zh": "提供公开基线实现。",
                "purpose_en": "Provides the public baseline implementation.",
            }
        ],
        "allowed_hosts": ["github.com", "pypi.org", "files.pythonhosted.org"],
        "environment_commands": ["python -m pip install -e ."],
        "test_commands": ["python -m pytest -q"],
        "baseline_commands": ["python scripts/baseline.py"],
        "intervention_commands": ["python scripts/intervention.py"],
        "evaluation_commands": ["python .research-atlas/evaluator/score.py"],
        "metrics_output_path": "artifacts/metrics.json",
        "metrics_json_schema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        },
        "metrics": [metric],
        "primary_metric_key": "effect",
        "evaluator_files": [
            {
                "path": "score.py",
                "content": "from pathlib import Path\nPath('artifacts').mkdir(exist_ok=True)\n",
            },
            {
                "path": "test_score.py",
                "content": "def test_frozen_evaluator_contract():\n    assert True\n",
            },
        ],
        "evaluator_test_commands": [
            "pytest .research-atlas/evaluator/test_score.py"
        ],
        "evaluator_cases": [
            {"name": "passes", "metrics": {"effect": 0.3}, "expected_pass": True},
            {"name": "fails", "metrics": {"effect": 0.1}, "expected_pass": False},
        ],
        "artifacts": [
            {
                "path": "artifacts/raw.json",
                "kind": "table",
                "public_safe": False,
                "description_zh": "冻结评价器使用的原始观测。",
                "description_en": "Raw observations consumed by the frozen evaluator.",
            },
            {
                "path": "artifacts/metrics.json",
                "kind": "metrics",
                "public_safe": True,
                "description_zh": "冻结指标结果。",
                "description_en": "Frozen metric result.",
            }
        ],
        "estimated_minutes": 10,
    }


def pilot_spec(*, comparison: str = "absolute") -> PilotSpecification:
    return PilotSpecification.model_validate(pilot_payload(comparison=comparison))


def live_pilot_spec() -> PilotSpecification:
    payload = pilot_payload()
    payload.update(
        {
            "requires_live_inference": True,
            "inference_contracts": [
                {
                    "key": "classify_sample",
                    "purpose_zh": "对实验样本执行冻结标签分类。",
                    "purpose_en": "Classify an experiment sample with frozen labels.",
                    "instruction": "Return exactly one of the frozen labels for the sample.",
                    "request_json_schema": {
                        "type": "object",
                        "properties": {
                            "text": {"type": "string", "maxLength": 2000}
                        },
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                    "response_json_schema": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "enum": ["yes", "no"]}
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                    "max_calls": 2,
                }
            ],
        }
    )
    return PilotSpecification.model_validate(payload)


def experiment(specification: PilotSpecification) -> ExperimentRecord:
    return ExperimentRecord(
        id="experiment-1",
        report_id="report-1",
        job_id="job-1",
        user_id="user-1",
        idea_key="idea-1",
        idea_snapshot={"key": "idea-1", "rank": 1},
        pilot_specification=specification.model_dump(mode="json"),
        pilot_specification_hash=None,
        pilot_compilation_required=False,
        current_revision_id="revision-1",
    )


def test_deterministic_gate_supports_absolute_delta_and_ratio() -> None:
    absolute = evaluate_metrics(pilot_spec(), {"effect": 0.25})
    delta = evaluate_metrics(
        pilot_spec(comparison="delta"),
        {"effect": 999, "baseline": 0.4, "intervention": 0.65},
    )
    ratio = evaluate_metrics(
        pilot_spec(comparison="ratio"),
        {"effect": -999, "baseline": 2.0, "intervention": 3.0},
    )

    assert absolute.passed and absolute.primary_value == 0.25
    assert delta.passed and delta.primary_value == pytest.approx(0.25)
    assert ratio.passed and ratio.primary_value == pytest.approx(1.5)


def test_pilot_contract_rejects_non_frozen_evaluator_and_extra_network_host() -> None:
    outside = pilot_payload()
    outside["evaluation_commands"] = ["python scripts/score.py"]
    with pytest.raises(ValidationError, match="frozen evaluator"):
        PilotSpecification.model_validate(outside)

    extra_host = pilot_payload()
    extra_host["allowed_hosts"].append("telemetry.example.com")
    with pytest.raises(ValidationError, match="declared resource hosts"):
        PilotSpecification.model_validate(extra_host)


def test_worker_level_pilot_validation_checks_metric_schema() -> None:
    specification = pilot_spec()
    validate_pilot_specification(specification)
    invalid = specification.model_copy(
        update={
            "metrics_json_schema": {
                "type": "object",
                "properties": {"effect": {"type": "string"}},
                "required": ["effect"],
            }
        }
    )
    with pytest.raises(Exception, match="numeric JSON schema"):
        validate_pilot_specification(invalid)


def test_exploratory_fallback_is_immediately_executable_and_does_not_overclaim() -> None:
    specification = _exploratory_fallback_specification(
        {
            "hypothesis_zh": "在相同资源预算下，结构化机制能够改善目标任务上的主要评价指标。",
            "hypothesis_en": "Under the same resource budget, the structured mechanism improves the primary task metric.",
        }
    )

    validate_pilot_specification(specification)
    assert specification.execution_mode == "exploratory_cpu_proxy"
    assert specification.estimated_minutes == 10
    assert specification.evaluation_commands == [
        "python .research-atlas/evaluator/score.py"
    ]


def test_exploratory_fallback_repository_runs_without_model_or_network(tmp_path) -> None:
    specification = _exploratory_fallback_specification(
        {
            "title_zh": "可执行探索性代理",
            "hypothesis_zh": "在相同资源预算下，结构化机制能够改善目标任务上的主要评价指标。",
            "hypothesis_en": "Under the same resource budget, the structured mechanism improves the primary task metric.",
        }
    )
    manifest, files = _deterministic_exploratory_repository(
        {"title_zh": "可执行探索性代理"}, specification
    )
    assert len(manifest.files) >= 6
    assert {item.path for item in manifest.files} == {item.path for item in files}
    for item in files:
        destination = tmp_path / item.path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(item.content, encoding="utf-8")

    baseline = subprocess.run(
        [sys.executable, "scripts/run_baseline.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    intervention = subprocess.run(
        [sys.executable, "scripts/run_intervention.py"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    raw = json.loads((tmp_path / "artifacts/raw_results.json").read_text())
    assert json.loads(baseline.stdout)["baseline"] == pytest.approx(raw["baseline"])
    assert json.loads(intervention.stdout) == raw
    assert raw["intervention"] - raw["baseline"] > 0.01

    with pytest.raises(ValueError, match="at least 24"):
        _validate_generated_repository_quality(
            manifest,
            files,
            min_files=24,
            min_total_bytes=60_000,
            min_code_lines=800,
        )


def test_substantive_repository_quality_gate_accepts_layered_code() -> None:
    paths = [
        "README.md",
        "docs/architecture.md",
        "pyproject.toml",
        "config/default.yaml",
        "src/pilot/__init__.py",
        "src/pilot/config.py",
        "src/pilot/data.py",
        "src/pilot/models.py",
        "src/pilot/baseline.py",
        "src/pilot/intervention.py",
        "src/pilot/evaluation.py",
        "src/pilot/artifacts.py",
        "src/pilot/orchestration.py",
        "scripts/run_baseline.py",
        "scripts/run_intervention.py",
        "scripts/run_evaluation.py",
        "scripts/run_full_pilot.py",
        "tests/test_data.py",
        "tests/test_baseline.py",
        "tests/test_intervention.py",
        "tests/test_evaluation.py",
        "tests/test_orchestration.py",
        "experiments/compare_variants.py",
        "requirements-dev.txt",
        "LICENSE",
    ]
    manifest = RichRepositoryManifest.model_validate(
        {
            "architecture_zh": "分层实现数据、基线、干预、评价、命令入口和可复现实验配置。",
            "architecture_en": "A layered implementation of data, baseline, intervention, evaluation, commands, and reproducibility configuration.",
            "files": [
                {
                    "path": path,
                    "purpose": "Provide a substantive reproducible experiment component.",
                    "language": "Python" if path.endswith(".py") else "text",
                    "batch": index // 6 + 1,
                }
                for index, path in enumerate(paths)
            ],
        }
    )
    code = "\n".join(
        f"def operation_{index}(value: int) -> int:\n    return value + {index}"
        for index in range(55)
    )
    test_code = "\n".join(
        f"def test_case_{index}():\n    assert {index} + 1 == {index + 1}"
        for index in range(36)
    )
    files = [
        GeneratedRepositoryFile(
            path=path,
            content=(
                test_code
                if "test" in path
                else code
                if path.endswith(".py")
                else ("Reproducible research repository.\n" * 90)
            ),
        )
        for path in paths
    ]

    _validate_generated_repository_quality(
        manifest,
        files,
        min_files=24,
        min_total_bytes=60_000,
        min_code_lines=800,
    )


def test_new_reports_require_an_executable_cpu_or_exploratory_proxy() -> None:
    code_only = pilot_spec().model_copy(update={"execution_mode": "code_only"})
    with pytest.raises(Exception, match="executable CPU"):
        validate_pilot_specification(code_only)

    exploratory = pilot_spec().model_copy(
        update={
            "execution_mode": "exploratory_cpu_proxy",
            "cpu_proxy_rationale_zh": "资源不足时验证一个更窄、可计算的机制命题。",
            "cpu_proxy_rationale_en": "Tests a narrower computable mechanism claim under constrained resources.",
        }
    )
    validate_pilot_specification(exploratory)


def test_live_inference_requires_a_complete_frozen_contract() -> None:
    missing = pilot_payload()
    missing["requires_live_inference"] = True
    with pytest.raises(ValidationError, match="frozen inference contract"):
        PilotSpecification.model_validate(missing)

    declared = pilot_payload()
    declared.update(
        {
            "requires_live_inference": True,
            "inference_contracts": [
                {
                    "key": "classify_sample",
                    "purpose_zh": "对实验样本执行冻结标签分类。",
                    "purpose_en": "Classify an experiment sample with frozen labels.",
                    "instruction": "Return exactly one of the frozen labels for the sample.",
                    "request_json_schema": {
                        "type": "object",
                        "properties": {"text": {"type": "string", "maxLength": 2000}},
                        "required": ["text"],
                        "additionalProperties": False,
                    },
                    "response_json_schema": {
                        "type": "object",
                        "properties": {
                            "label": {"type": "string", "enum": ["yes", "no"]}
                        },
                        "required": ["label"],
                        "additionalProperties": False,
                    },
                    "max_calls": 2,
                }
            ],
        }
    )
    specification = PilotSpecification.model_validate(declared)
    validate_pilot_specification(specification)
    assert specification.inference_contracts[0].max_calls == 2

    direct_provider = specification.model_copy(
        update={"allowed_hosts": [*specification.allowed_hosts, "*.openai.com"]}
    )
    with pytest.raises(Exception, match="Hosted model providers"):
        validate_pilot_specification(direct_provider)


def test_assistant_change_supports_safe_create_and_delete() -> None:
    change = AssistantWorkspaceChange(
        explanation_zh="新增一个分层模块并删除旧实现。",
        explanation_en="Add a layered module and delete the obsolete implementation.",
        files=[{"path": "src/new_module.py", "content": "VALUE = 1\n"}],
        delete_paths=["src/obsolete.py", "src/obsolete.py"],
    )
    assert change.files[0].path == "src/new_module.py"
    assert change.delete_paths == ["src/obsolete.py"]

    with pytest.raises(ValidationError):
        AssistantWorkspaceChange(
            explanation_zh="尝试修改冻结目录。",
            explanation_en="Attempt to modify the frozen directory.",
            delete_paths=[".research-atlas/pilot-spec.json"],
        )


@pytest.mark.parametrize(
    "path",
    [
        ".env.example",
        "nested/.env.local",
        "nested/.research-atlas/spec.json",
        "src/unsafe\nname.py",
        "界" * 81,
    ],
)
def test_repository_path_rejects_edge_worker_mismatch_cases(path: str) -> None:
    with pytest.raises(ValueError, match="Unsafe repository path"):
        safe_repository_path(path)


async def test_e2b_provider_labels_reproducible_validation_runtime(
    monkeypatch,
) -> None:
    class Commands:
        async def run(self, *_args, **_kwargs):
            return SimpleNamespace(stdout="10240\n")

    class Sandbox:
        sandbox_id = "sandbox-1"
        commands = Commands()

        async def get_info(self):
            return SimpleNamespace(cpu_count=4, memory_mb=8192)

    class Sdk:
        kwargs: dict = {}

        @classmethod
        async def create(cls, **kwargs):
            cls.kwargs = kwargs
            return Sandbox()

    monkeypatch.setattr(E2BSandboxProvider, "_sdk", staticmethod(lambda: Sdk))
    provider = E2BSandboxProvider(
        "test-key",
        template_id="research-atlas-cpu-v1",
        cpu_count=4,
        memory_mib=8192,
        disk_mib=10240,
        run_timeout_seconds=3600,
    )

    await provider.create(
        experiment_id="experiment-1",
        allowed_hosts=["pypi.org"],
        purpose="formal_validation",
    )

    assert Sdk.kwargs["metadata"] == {
        "product": "research-atlas",
        "experiment_id": "experiment-1",
        "runtime_purpose": "formal_validation",
        "template_id": "research-atlas-cpu-v1",
        "base_image_digest": E2B_BASE_IMAGE_DIGEST,
    }


async def test_e2b_provider_publishes_id_before_resource_verification(
    monkeypatch,
) -> None:
    events: list[str] = []

    class Sandbox:
        sandbox_id = "sandbox-before-verify"

        async def get_info(self):
            events.append("verify")
            raise RuntimeError("verification unavailable")

    class Sdk:
        @classmethod
        async def create(cls, **_kwargs):
            return Sandbox()

    monkeypatch.setattr(E2BSandboxProvider, "_sdk", staticmethod(lambda: Sdk))
    provider = E2BSandboxProvider(
        "test-key",
        template_id="research-atlas-cpu-v1",
        cpu_count=4,
        memory_mib=8192,
        disk_mib=10240,
        run_timeout_seconds=3600,
    )

    async def persist(handle) -> None:
        events.append(f"persist:{handle.sandbox_id}")

    with pytest.raises(RuntimeError, match="verification unavailable"):
        await provider.create(
            experiment_id="experiment-1",
            allowed_hosts=[],
            purpose="formal_validation",
            tracking_id="action-1",
            on_created=persist,
        )

    assert events == ["persist:sandbox-before-verify", "verify"]


class FakeRepository:
    def __init__(self) -> None:
        self.runtime = None
        self.saved_runtimes: list[dict] = []
        self.scheduled_runtime_cleanups: list[dict] = []
        self.tainted_runtimes: list[dict] = []
        self.deleted_experiments: list[str] = []
        self.checkpoint_result = True

    async def load_experiment_runtime(self, _experiment_id: str):
        return self.runtime

    async def save_experiment_runtime(self, experiment_id: str, **values):
        self.saved_runtimes.append({"experiment_id": experiment_id, **values})

    async def save_claimed_experiment_runtime(
        self, experiment_id: str, **values
    ):
        self.saved_runtimes.append({"experiment_id": experiment_id, **values})

    async def save_experiment_checkpoint(self, *_args, **_kwargs):
        return self.checkpoint_result

    async def authorize_experiment_llm_call(self, *_args, **_kwargs):
        return experiment(pilot_spec())

    async def assert_experiment_run_within_deadline(self, *_args, **_kwargs):
        return 3600

    async def schedule_claimed_runtime_cleanup(
        self, experiment_id: str, **values
    ):
        self.scheduled_runtime_cleanups.append(
            {"experiment_id": experiment_id, **values}
        )

    async def mark_experiment_runtime_tainted(
        self, experiment_id: str, **values
    ):
        self.tainted_runtimes.append({"experiment_id": experiment_id, **values})
        return {"marked": True, "runtime_kind": "interactive"}

    async def delete_experiment(self, experiment_id: str):
        self.deleted_experiments.append(experiment_id)

    async def close(self):
        return None


class FakeProvider:
    def __init__(
        self,
        *,
        connect_error: Exception | None = None,
        kill_error: Exception | None = None,
        sandbox=None,
    ) -> None:
        self.connect_error = connect_error
        self.kill_error = kill_error
        self.sandbox = sandbox or object()
        self.create_calls = 0
        self.create_kwargs: list[dict] = []
        self.kill_calls: list[str] = []

    async def connect(self, _sandbox_id: str):
        if self.connect_error:
            raise self.connect_error
        return self.sandbox

    async def create(self, **_kwargs):
        self.create_calls += 1
        self.create_kwargs.append(dict(_kwargs))
        return self.sandbox

    async def kill(self, sandbox_id: str):
        self.kill_calls.append(sandbox_id)
        if self.kill_error:
            raise self.kill_error


class FakeLlm:
    async def structured(self, *_args, **_kwargs):  # pragma: no cover - safety sentinel
        raise AssertionError("No model call expected")


class RecordingLlm:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    async def structured(self, _prompt, response_model, **kwargs):
        self.calls.append(kwargs)
        return response_model.model_validate({"value": "ok"})


class FakeSandbox:
    sandbox_id = "new-sandbox"


def make_worker(tmp_path, repository, provider) -> ExperimentWorker:
    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        EXPERIMENT_LEASE_SECONDS=60,
    )
    return ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=provider,
        llm=FakeLlm(),
    )


async def test_subject_inference_uses_exact_edge_host_one_shot_config_and_wrapped_command(
    tmp_path,
) -> None:
    specification = live_pilot_spec()
    record = experiment(specification)

    class TokenRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.token_issue: dict | None = None

        async def load_experiment(self, _experiment_id: str) -> ExperimentRecord:
            return record

        async def replace_sandbox_inference_tokens(
            self, experiment_id: str, run_id: str, **values
        ) -> list[str]:
            self.token_issue = {
                "experiment_id": experiment_id,
                "run_id": run_id,
                **values,
            }
            return [item["token_hash"] for item in values["tokens"]]

    class SubjectSandbox:
        sandbox_id = "subject-sandbox"

        def __init__(self) -> None:
            self.files: dict[str, str] = {}
            self.commands: list[str] = []

        async def write_text(self, path: str, content: str) -> None:
            self.files[path] = content

        async def run(self, command: str, **_kwargs) -> CommandExecution:
            self.commands.append(command)
            return CommandExecution(
                command=command,
                exit_code=0,
                stdout="",
                stderr="",
                elapsed_seconds=0.01,
            )

    repository = TokenRepository()
    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        SUPABASE_URL="https://project-ref.supabase.co",
        EXPERIMENT_WORKER_ID="test-worker",
        EXPERIMENT_LEASE_SECONDS=60,
    )
    worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=FakeLlm(),
    )
    worker._active_experiment = record
    worker._active_run_id = "run-1"
    sandbox = SubjectSandbox()

    assert worker._subject_allowed_hosts(specification) == [
        "github.com",
        "pypi.org",
        "files.pythonhosted.org",
        "project-ref.supabase.co",
    ]
    await worker._prepare_sandbox_inference(
        record, "run-1", specification, sandbox
    )

    assert repository.token_issue is not None
    assert repository.token_issue["specification_hash"] == specification_hash(
        specification
    )
    issued = repository.token_issue["tokens"]
    assert len(issued) == 2
    assert all(set(item) == {"contract_key", "slot", "token_hash"} for item in issued)
    assert all(len(item["token_hash"]) == 64 for item in issued)
    config = json.loads(sandbox.files["/tmp/research-atlas-inference.json"])
    assert config["endpoint"] == (
        "https://project-ref.supabase.co/functions/v1/experiment-sandbox-inference"
    )
    plaintext_tokens = [
        item["token"]
        for item in config["contracts"]["classify_sample"]["tokens"]
    ]
    assert len(plaintext_tokens) == 2
    assert not set(plaintext_tokens).intersection(
        {item["token_hash"] for item in issued}
    )
    assert "DEEPSEEK" not in json.dumps(config)
    assert "SUPABASE_SERVICE_ROLE" not in json.dumps(config)

    await worker._command_sequence(
        record,
        {},
        sandbox,
        key="tests",
        commands=["python scripts/test_subject.py"],
        stage=record_stage(),
        progress_start=1,
        progress_end=2,
        inference_enabled=True,
    )
    assert sandbox.commands[-1] == (
        "PYTHONPATH=/tmp "
        "RESEARCH_ATLAS_INFERENCE_CONFIG=/tmp/research-atlas-inference.json "
        "python scripts/test_subject.py"
    )


async def test_checkpoint_cas_failure_does_not_write_local_recovery(tmp_path) -> None:
    repository = FakeRepository()
    repository.checkpoint_result = False
    worker = make_worker(tmp_path, repository, FakeProvider())
    record = experiment(pilot_spec())
    worker._active_experiment = record

    with pytest.raises(LeaseLost):
        await worker._save_checkpoint(record, {}, record_stage(), 10)

    assert not worker._local_checkpoint_path(record.id).exists()


async def test_guarded_command_durably_fences_tainted_runtime(tmp_path) -> None:
    class TaintedSandbox:
        sandbox_id = "tainted-sandbox"

        async def run(self, *_args, **_kwargs):
            raise SandboxRuntimeTaintedError(
                self.sandbox_id,
                destruction_requested=False,
                cause=RuntimeError("ambiguous provider stream"),
            )

    repository = FakeRepository()
    worker = make_worker(tmp_path, repository, FakeProvider())
    record = experiment(pilot_spec())
    worker._active_experiment = record

    with pytest.raises(SandboxRuntimeTaintedError):
        await worker._run_guarded(TaintedSandbox(), "python experiment.py")

    assert repository.tainted_runtimes == [
        {
            "experiment_id": record.id,
            "sandbox_id": "tainted-sandbox",
            "action_id": None,
            "safe_error": "ambiguous provider stream",
        }
    ]
    assert not worker._runtime_taint_path("tainted-sandbox").exists()


async def test_guarded_command_uses_persisted_run_deadline(tmp_path) -> None:
    class DeadlineRepository(FakeRepository):
        async def assert_experiment_run_within_deadline(self, *_args, **_kwargs):
            return 7

    class RecordingSandbox:
        sandbox_id = "deadline-sandbox"

        def __init__(self) -> None:
            self.timeout = 0

        async def run(self, command, *, cwd, timeout, check):
            self.timeout = timeout
            return CommandExecution(
                command=command, exit_code=0, elapsed_seconds=0.01
            )

    worker = make_worker(tmp_path, DeadlineRepository(), FakeProvider())
    worker._active_run_id = "run-1"
    sandbox = RecordingSandbox()

    await worker._run_guarded(sandbox, "python experiment.py", timeout=600)

    assert sandbox.timeout == 7


async def test_guarded_command_stops_when_persisted_deadline_is_exhausted(
    tmp_path,
) -> None:
    class DeadlineRepository(FakeRepository):
        async def assert_experiment_run_within_deadline(self, *_args, **_kwargs):
            raise RuntimeError("experiment run deadline exceeded")

    worker = make_worker(tmp_path, DeadlineRepository(), FakeProvider())
    worker._active_run_id = "run-1"

    with pytest.raises(ExperimentRunDeadlineExceeded):
        await worker._run_guarded(FakeSandbox(), "python experiment.py")


def record_stage():
    from paper_research.experiment_models import ExperimentStage

    return ExperimentStage.REPO_GENERATION


async def test_transient_connect_error_does_not_create_duplicate_sandbox(tmp_path) -> None:
    repository = FakeRepository()
    repository.runtime = {"sandbox_id": "existing"}
    provider = FakeProvider(connect_error=RuntimeError("temporary network outage"))
    worker = make_worker(tmp_path, repository, provider)
    record = experiment(pilot_spec())
    worker._active_experiment = record

    with pytest.raises(RuntimeError, match="temporary network outage"):
        await worker._sandbox(record, {}, pilot_spec())

    assert provider.create_calls == 0


async def test_confirmed_missing_sandbox_is_rebuilt_from_checkpoint(tmp_path) -> None:
    repository = FakeRepository()
    repository.runtime = {"sandbox_id": "gone"}
    sandbox = FakeSandbox()
    provider = FakeProvider(
        connect_error=SandboxNotFoundError("gone"), sandbox=sandbox
    )
    worker = make_worker(tmp_path, repository, provider)
    record = experiment(pilot_spec())
    worker._active_experiment = record

    result = await worker._sandbox(record, {}, pilot_spec())

    assert result is sandbox
    assert provider.create_calls == 1
    assert provider.create_kwargs[0]["purpose"] == "interactive"
    assert repository.saved_runtimes[-1]["sandbox_id"] == "new-sandbox"
    assert repository.saved_runtimes[-1]["metadata"]["base_image_digest"].startswith(
        "sha256:"
    )


async def test_delete_keeps_runtime_tracked_when_provider_kill_is_ambiguous(
    tmp_path,
) -> None:
    repository = FakeRepository()
    repository.runtime = {"sandbox_id": "still-billable", "state": "running"}
    provider = FakeProvider(kill_error=RuntimeError("temporary E2B outage"))
    worker = make_worker(tmp_path, repository, provider)
    record = experiment(pilot_spec()).model_copy(
        update={"cancellation_requested": True, "deletion_requested_at": "now"}
    )
    worker._active_experiment = record

    await worker._cancel_claimed(record)

    assert provider.kill_calls == ["still-billable"]
    assert repository.deleted_experiments == []
    assert not any(item.get("state") == "destroyed" for item in repository.saved_runtimes)
    assert repository.scheduled_runtime_cleanups == [
        {
            "experiment_id": record.id,
            "worker_id": worker.settings.EXPERIMENT_WORKER_ID,
            "action_id": None,
            "sandbox_id": "still-billable",
            "retry_seconds": 30,
            "safe_error": "temporary E2B outage",
        }
    ]


async def test_successful_delete_removes_local_recovery_checkpoint(tmp_path) -> None:
    repository = FakeRepository()
    repository.runtime = {"sandbox_id": "finished-sandbox", "state": "running"}
    provider = FakeProvider()
    worker = make_worker(tmp_path, repository, provider)
    record = experiment(pilot_spec()).model_copy(
        update={"cancellation_requested": True, "deletion_requested_at": "now"}
    )
    worker._active_experiment = record
    checkpoint_path = worker._local_checkpoint_path(record.id)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    checkpoint_path.write_text("{}", encoding="utf-8")

    await worker._cancel_claimed(record)

    assert provider.kill_calls == ["finished-sandbox"]
    assert repository.deleted_experiments == [record.id]
    assert not checkpoint_path.exists()


async def test_experiment_model_routes_flash_and_pro_explicitly(tmp_path) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    repository = FakeRepository()
    llm = RecordingLlm()
    settings = Settings(_env_file=None, ARTIFACT_ROOT=tmp_path)
    worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=llm,
    )
    worker._active_experiment = experiment(pilot_spec())

    await worker._structured("repository", Response, stage="repository")
    await worker._structured("specification", Response, stage="specification", pro=True)
    image_path = tmp_path / "attachment.png"
    image_path.write_bytes(b"\x89PNG\r\n\x1a\n")
    await worker._structured(
        "screenshot",
        Response,
        stage="experiment_workspace_assistant_vision",
        image_paths=[image_path],
        image_audit=[{"sha256": "digest", "byte_size": 8}],
    )

    assert llm.calls[0]["model"] == settings.CLAUDE_MODEL
    assert llm.calls[0]["stage"] == "repository"
    assert llm.calls[0]["max_turns"] == 4
    assert llm.calls[0]["max_budget_usd"] == pytest.approx(0.2)
    assert llm.calls[1]["model"] == settings.CLAUDE_PRO_MODEL
    assert llm.calls[1]["stage"] == "specification"
    assert llm.calls[1]["max_turns"] == 2
    assert llm.calls[1]["max_budget_usd"] == pytest.approx(0.2)
    assert llm.calls[2]["model"] == settings.CLAUDE_VISION_MODEL
    assert llm.calls[2]["image_paths"] == [image_path]
    assert llm.calls[2]["usage_metadata"] == {"attachment_count": 1, "attachment_bytes": 8}


async def test_repository_generation_can_use_server_side_direct_transport(tmp_path) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    repository = FakeRepository()
    claude = RecordingLlm()
    direct = RecordingLlm()
    settings = Settings(_env_file=None, ARTIFACT_ROOT=tmp_path)
    worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=claude,
        repository_llm=direct,
    )
    worker._active_experiment = experiment(pilot_spec())

    result = await worker._structured(
        "repository",
        Response,
        stage="experiment_repository_manifest",
        transport="deepseek_api",
    )

    assert result.value == "ok"
    assert claude.calls == []
    assert len(direct.calls) == 1
    assert direct.calls[0]["model"] == settings.CLAUDE_MODEL
    assert direct.calls[0]["stage"] == "experiment_repository_manifest"


async def test_experiment_reuses_journaled_result_after_usage_callback_failure(
    tmp_path,
) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    class UsageRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.increment_calls: list[ProviderUsage] = []

        async def increment_experiment_costs(
            self, _experiment_id: str, *, usage: ProviderUsage, **_kwargs
        ):
            self.increment_calls.append(usage.model_copy(deep=True))
            return record.model_copy(update={"llm_cost_cny": usage.estimated_cny})

    class JournalThenFailLlm:
        def __init__(self) -> None:
            self.calls = 0

        async def structured(self, _prompt, response_model, **kwargs):
            self.calls += 1
            result = response_model.model_validate({"value": "paid result"})
            usage = ProviderUsage(
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=100,
                output_tokens=20,
                metadata={"experiment_usage_id": kwargs["usage_id"]},
            )
            await kwargs["before_usage_callback"](usage, result)
            raise ClaudeCodeAccountingError("database temporarily unavailable")

    record = experiment(pilot_spec())
    repository = UsageRepository()
    llm = JournalThenFailLlm()
    settings = Settings(_env_file=None, ARTIFACT_ROOT=tmp_path)
    worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=llm,
    )
    worker._active_experiment = record

    first = await worker._structured("same prompt", Response, stage="repository")

    second_worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=FakeLlm(),
    )
    second_worker._active_experiment = record
    second = await second_worker._structured(
        "same prompt", Response, stage="repository"
    )

    assert first.value == second.value == "paid result"
    assert llm.calls == 1
    assert len(repository.increment_calls) == 1
    assert repository.increment_calls[0].metadata["experiment_usage_id"]


async def test_failed_structured_call_settles_journaled_usage_before_retry(
    tmp_path,
) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    class UsageRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.increment_calls: list[ProviderUsage] = []

        async def increment_experiment_costs(
            self, _experiment_id: str, *, usage: ProviderUsage, **_kwargs
        ):
            self.increment_calls.append(usage.model_copy(deep=True))
            return record.model_copy(update={"llm_cost_cny": usage.estimated_cny})

    class InvalidPaidLlm:
        async def structured(self, _prompt, _response_model, **kwargs):
            usage = ProviderUsage(
                provider="deepseek",
                model="deepseek-v4-flash",
                input_tokens=80,
                output_tokens=10,
                metadata={"experiment_usage_id": kwargs["usage_id"]},
            )
            await kwargs["before_usage_callback"](usage, None)
            raise ClaudeCodeAccountingError("database temporarily unavailable")

    record = experiment(pilot_spec())
    repository = UsageRepository()
    worker = ExperimentWorker(
        Settings(_env_file=None, ARTIFACT_ROOT=tmp_path),
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=InvalidPaidLlm(),
    )
    worker._active_experiment = record

    with pytest.raises(ClaudeCodeAccountingError, match="database temporarily"):
        await worker._structured("invalid paid result", Response, stage="repository")

    assert len(repository.increment_calls) == 1
    journals = list((tmp_path / "experiment-llm-journals" / record.id).glob("*.json"))
    assert len(journals) == 1
    assert json.loads(journals[0].read_text(encoding="utf-8"))["settled"] is True


async def test_failed_paid_retry_uses_a_new_provider_usage_id(tmp_path) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    class UsageRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.usage_ids: list[str] = []
            self.authorization_calls = 0

        async def authorize_experiment_llm_call(self, *_args, **_kwargs):
            self.authorization_calls += 1
            return record

        async def increment_experiment_costs(
            self, _experiment_id: str, *, usage: ProviderUsage, **_kwargs
        ):
            self.usage_ids.append(str(usage.metadata["experiment_usage_id"]))
            return record.model_copy(update={"llm_cost_cny": usage.estimated_cny})

    class RetryLlm:
        def __init__(self) -> None:
            self.calls = 0
            self.usage_ids: list[str] = []

        async def structured(self, _prompt, response_model, **kwargs):
            self.calls += 1
            self.usage_ids.append(str(kwargs["usage_id"]))
            if self.calls == 1:
                usage = ProviderUsage(
                    provider="deepseek",
                    model="deepseek-v4-flash",
                    input_tokens=80,
                    output_tokens=10,
                    metadata={"experiment_usage_id": kwargs["usage_id"]},
                )
                await kwargs["before_usage_callback"](usage, None)
                raise ClaudeCodeAccountingError("database temporarily unavailable")
            return response_model.model_validate({"value": "second attempt"})

    record = experiment(pilot_spec())
    repository = UsageRepository()
    llm = RetryLlm()
    worker = ExperimentWorker(
        Settings(_env_file=None, ARTIFACT_ROOT=tmp_path),
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=llm,
    )
    worker._active_experiment = record

    with pytest.raises(ClaudeCodeAccountingError):
        await worker._structured("retry prompt", Response, stage="repository")
    result = await worker._structured("retry prompt", Response, stage="repository")

    assert result.value == "second attempt"
    assert llm.calls == repository.authorization_calls == 2
    assert len(set(llm.usage_ids)) == 2
    assert repository.usage_ids == [llm.usage_ids[0]]


async def test_experiment_never_repeats_an_ambiguous_started_invocation(
    tmp_path,
) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    class SettlementRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.settlement_calls = 0

        async def settle_experiment_llm_reservation(self, *_args, **_kwargs):
            self.settlement_calls += 1
            return record.model_copy(update={"llm_cost_cny": 5.0})

    class AmbiguousLlm:
        def __init__(self) -> None:
            self.calls = 0

        async def structured(self, *_args, **_kwargs):
            self.calls += 1
            raise RuntimeError("worker disappeared after provider launch")

    record = experiment(pilot_spec())
    repository = SettlementRepository()
    llm = AmbiguousLlm()
    settings = Settings(_env_file=None, ARTIFACT_ROOT=tmp_path)
    worker = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=llm,
    )
    worker._active_experiment = record
    with pytest.raises(RuntimeError, match="provider launch"):
        await worker._structured("same prompt", Response, stage="repository")

    resumed = ExperimentWorker(
        settings,
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=llm,
    )
    resumed._active_experiment = record
    with pytest.raises(ExperimentBudgetBlocked, match="ambiguous prior"):
        await resumed._structured("same prompt", Response, stage="repository")

    assert llm.calls == 1
    assert repository.settlement_calls == 1


async def test_experiment_settles_failed_call_without_usage_immediately(
    tmp_path,
) -> None:
    from pydantic import BaseModel

    class Response(BaseModel):
        value: str

    class SettlementRepository(FakeRepository):
        def __init__(self) -> None:
            super().__init__()
            self.settlement_calls = 0

        async def settle_experiment_llm_reservation(self, *_args, **_kwargs):
            self.settlement_calls += 1
            return record.model_copy(update={"llm_cost_cny": 1.5})

    class FailedLlm:
        async def structured(self, *_args, **_kwargs):
            raise ClaudeCodeError("provider timeout")

    record = experiment(pilot_spec())
    repository = SettlementRepository()
    worker = ExperimentWorker(
        Settings(_env_file=None, ARTIFACT_ROOT=tmp_path),
        repository=repository,
        sandbox_provider=FakeProvider(),
        llm=FailedLlm(),
    )
    worker._active_experiment = record

    with pytest.raises(ClaudeCodeError, match="provider timeout"):
        await worker._structured("prompt", Response, stage="repository")

    assert repository.settlement_calls == 1
    journal_files = list(
        (tmp_path / "experiment-llm-journals" / record.id).glob("*.json")
    )
    assert len(journal_files) == 1
    assert json.loads(journal_files[0].read_text(encoding="utf-8"))["settled"] is True


def test_experiment_llm_budget_is_scoped_to_current_run(tmp_path) -> None:
    worker = make_worker(tmp_path, FakeRepository(), FakeProvider())
    record = experiment(pilot_spec()).model_copy(update={"llm_cost_cny": 7.0})
    worker._active_experiment = record
    worker._llm_cost_at_start = 2.0

    with pytest.raises(ExperimentBudgetBlocked):
        worker._check_llm_budget()

    worker._llm_cost_at_start = 7.0
    worker._check_llm_budget()


class ValidationSandbox:
    def __init__(self, sandbox_id: str, commit: str = "commit-1") -> None:
        self.sandbox_id = sandbox_id
        self.commit = commit
        self.commands: list[str] = []
        self.files: dict[str, bytes] = {}
        self.pause_calls = 0

    async def pause(self) -> None:
        self.pause_calls += 1

    async def run(self, command: str, **_kwargs) -> CommandExecution:
        self.commands.append(command)
        if command in {"git rev-parse HEAD", "/usr/bin/git rev-parse HEAD"}:
            stdout = self.commit + "\n"
        elif " -c " in command and "ls-files" in command:
            stdout = json.dumps(
                {"ok": True, "file_count": 0, "total_bytes": 0, "entries": []}
            )
        elif " -c " in command:
            stdout = json.dumps({"ok": True, "size": 32})
        else:
            stdout = ""
        return CommandExecution(
            command=command,
            exit_code=0,
            stdout=stdout,
            stderr="",
            elapsed_seconds=0.01,
        )

    async def write_text(self, path: str, content: str) -> None:
        self.files[path] = content.encode()

    async def write_bytes(self, path: str, content: bytes) -> None:
        self.files[path] = content

    async def read_text(self, path: str) -> str:
        if path.endswith("/artifacts/metrics.json"):
            return '{"effect": 0.3}'
        return self.files[path].decode()

    async def read_bytes(self, path: str) -> bytes:
        if path.endswith("/artifacts/raw.json"):
            return b'{"baseline": 0.1, "intervention": 0.4}'
        if path.endswith("/artifacts/metrics.json"):
            return b'{"effect": 0.3}'
        return self.files[path]


class ValidationRepository(FakeRepository):
    def __init__(self, record: ExperimentRecord) -> None:
        super().__init__()
        self.record = record
        self.action_progress: dict = {"validationRevisionId": "revision-1"}
        self.create_run_calls = 0
        self.finalized_runs: list[dict] = []
        self.updated_experiments: list[dict] = []
        self.validation_runtime: dict | None = None
        self.validation_events: list[str] = []
        self.storage: dict[str, bytes] = {}

    async def load_experiment(self, _experiment_id: str) -> ExperimentRecord:
        return self.record

    async def get_experiment_revision(self, _experiment_id: str, revision_id: str):
        assert revision_id == "revision-1"
        return {
            "id": revision_id,
            "git_commit": "commit-1",
            "bundle_storage_path": "user/experiment/repository.bundle",
        }

    async def download_experiment_storage(self, _storage_path: str) -> bytes:
        if _storage_path in self.storage:
            return self.storage[_storage_path]
        return b"git-bundle"

    async def upload_experiment_artifact(self, **values):
        path = f"private/{values['file_name']}"
        self.storage[path] = values["content"]
        return {"storage_path": path}

    async def update_experiment_action_progress(
        self, _action_id: str, _worker_id: str, response: dict
    ) -> bool:
        self.action_progress = dict(response)
        return True

    async def create_experiment_run(self, *_args, **_kwargs):
        self.create_run_calls += 1
        return {"id": "run-1"}

    async def finalize_experiment_run(self, run_id: str, **values):
        self.finalized_runs.append({"id": run_id, **values})
        return self.finalized_runs[-1]

    async def update_claimed_experiment(self, _experiment_id: str, **values):
        self.updated_experiments.append(values)
        return self.record

    async def reserve_claimed_validation_runtime(
        self, experiment_id: str, **values
    ):
        self.validation_events.append("reserve")
        if not self.validation_runtime or self.validation_runtime.get("state") == "destroyed":
            self.validation_runtime = {
                "experiment_id": experiment_id,
                "action_id": values["action_id"],
                "run_id": values["run_id"],
                "sandbox_id": None,
                "state": "creating",
            }
        return dict(self.validation_runtime)

    async def attach_claimed_validation_runtime(
        self, _experiment_id: str, **values
    ):
        self.validation_events.append("attach")
        assert self.validation_runtime is not None
        self.validation_runtime.update(
            {"sandbox_id": values["sandbox_id"], "state": "running"}
        )
        return dict(self.validation_runtime)

    async def load_validation_runtime(self, _action_id: str):
        return dict(self.validation_runtime) if self.validation_runtime else None

    async def finish_claimed_validation_runtime(
        self, _experiment_id: str, **values
    ):
        self.validation_events.append(
            "destroyed" if values["destroyed"] else "destroying"
        )
        assert self.validation_runtime is not None
        self.validation_runtime["state"] = (
            "destroyed" if values["destroyed"] else "destroying"
        )
        return dict(self.validation_runtime)


class ValidationProvider(FakeProvider):
    def __init__(
        self,
        subject: ValidationSandbox,
        evaluator: ValidationSandbox,
        *,
        transient_evaluator_kill_failures: int = 0,
    ):
        super().__init__(sandbox=subject)
        self.sandboxes = {subject.sandbox_id: subject, evaluator.sandbox_id: evaluator}
        self.create_sequence = [subject, evaluator]
        self.transient_kill_failures = transient_evaluator_kill_failures

    async def connect(self, sandbox_id: str):
        return self.sandboxes[sandbox_id]

    async def create(self, **kwargs):
        sandbox = self.create_sequence[self.create_calls]
        self.create_calls += 1
        self.create_kwargs.append(dict(kwargs))
        return sandbox

    async def kill(self, sandbox_id: str):
        self.kill_calls.append(sandbox_id)
        if sandbox_id == "clean-evaluator" and self.transient_kill_failures:
            self.transient_kill_failures -= 1
            raise RuntimeError("temporary cleanup outage")


async def test_manual_validation_uses_clean_sandbox_and_resumes_cleanup_without_rerun(
    tmp_path,
) -> None:
    specification = pilot_spec()
    record = experiment(specification).model_copy(
        update={"pilot_specification_hash": specification_hash(specification)}
    )
    repository = ValidationRepository(record)
    subject = ValidationSandbox("clean-subject")
    evaluator = ValidationSandbox("clean-evaluator")
    interactive = ValidationSandbox("interactive")
    provider = ValidationProvider(
        subject, evaluator, transient_evaluator_kill_failures=1
    )
    provider.sandboxes[interactive.sandbox_id] = interactive
    worker = make_worker(tmp_path, repository, provider)
    worker._active_experiment = record
    worker._active_action_id = "action-1"

    with pytest.raises(RuntimeError, match="temporary cleanup outage"):
        await worker._manual_validation(
            record,
            {},
            interactive,
            specification,
            repository.action_progress,
        )

    subject_commands_after_evaluation = list(subject.commands)
    evaluator_commands_after_evaluation = list(evaluator.commands)
    assert interactive.commands == []
    assert provider.create_calls == 2
    assert [item["purpose"] for item in provider.create_kwargs] == [
        "formal_subject",
        "formal_evaluator",
    ]
    assert "python scripts/baseline.py" in subject.commands
    assert "python scripts/intervention.py" in subject.commands
    assert "python .research-atlas/evaluator/score.py" not in subject.commands
    assert "python scripts/baseline.py" not in evaluator.commands
    assert "python scripts/intervention.py" not in evaluator.commands
    assert (
        evaluator.files["/home/user/repository/artifacts/raw.json"]
        == b'{"baseline":0.1,"intervention":0.4}'
    )
    assert "/home/user/repository/scripts/baseline.py" not in evaluator.files
    assert interactive.pause_calls == 1
    assert repository.validation_events[:2] == ["reserve", "attach"]
    assert repository.create_run_calls == 1
    assert repository.finalized_runs == []
    assert repository.action_progress["validationPhase"] == "cleanup_pending"
    assert repository.action_progress["validationSandboxId"] == "clean-evaluator"
    assert repository.action_progress["validationRuntime"]["template_id"] == (
        worker.settings.E2B_TEMPLATE_ID
    )
    assert "validationResult" in repository.action_progress
    assert repository.validation_runtime is not None
    assert repository.validation_runtime["state"] == "destroying"

    response = await worker._manual_validation(
        record,
        {},
        interactive,
        specification,
        repository.action_progress,
    )

    assert response["outcome"] == "initial_support"
    assert provider.create_calls == 2
    assert repository.create_run_calls == 1
    assert subject.commands == subject_commands_after_evaluation
    assert evaluator.commands == evaluator_commands_after_evaluation
    assert provider.kill_calls == [
        "clean-subject",
        "clean-evaluator",
        "clean-evaluator",
    ]
    assert len(repository.finalized_runs) == 1
    assert repository.validation_runtime["state"] == "destroyed"
    assert "validationSandboxId" not in repository.action_progress


async def test_automatic_evaluation_resumes_in_fresh_evaluator_only_sandbox(
    tmp_path,
) -> None:
    specification = pilot_spec()
    record = experiment(specification).model_copy(
        update={"pilot_specification_hash": specification_hash(specification)}
    )
    repository = ValidationRepository(record)
    bundle = build_validation_bundle(
        specification,
        [
            ValidationInput(
                "artifacts/raw.json",
                b'{"baseline":0.1,"intervention":0.4}',
            )
        ],
        max_file_bytes=2 * 1024 * 1024,
        max_total_bytes=16 * 1024 * 1024 - 65_536,
    )
    repository.storage["private/automatic-inputs.zip"] = bundle
    evaluator = ValidationSandbox("automatic-evaluator")
    provider = FakeProvider(sandbox=evaluator)
    worker = make_worker(tmp_path, repository, provider)
    worker._active_experiment = record
    checkpoint = {
        "automaticRawInputs": {
            "storagePath": "private/automatic-inputs.zip",
            "sha256": hashlib.sha256(bundle).hexdigest(),
        },
        "automaticSubjectDestroyed": True,
        "automaticSubjectRevisionArchived": True,
        "commands": {
            "baseline": [
                CommandExecution(
                    command="python scripts/baseline.py",
                    exit_code=0,
                    elapsed_seconds=0.1,
                ).model_dump(mode="json")
            ]
        },
    }

    _, evaluation, _ = await worker._run_frozen_experiment(
        record,
        checkpoint,
        None,
        None,  # type: ignore[arg-type] - no repair occurs in the resumed phase
        [],
        specification,
        "run-1",
    )

    assert evaluation.passed
    assert provider.create_calls == 1
    assert provider.create_kwargs[0]["purpose"] == "formal_evaluator"
    assert "python scripts/baseline.py" not in evaluator.commands
    assert (
        evaluator.files["/home/user/repository/artifacts/raw.json"]
        == b'{"baseline":0.1,"intervention":0.4}'
    )
    assert checkpoint["automaticEvaluatorDestroyed"] is True
    assert provider.kill_calls == ["automatic-evaluator"]


async def test_automatic_validation_destroys_subject_before_evaluator(tmp_path) -> None:
    specification = pilot_spec()
    record = experiment(specification).model_copy(
        update={"pilot_specification_hash": specification_hash(specification)}
    )
    repository = ValidationRepository(record)
    subject = ValidationSandbox("automatic-subject")
    evaluator = ValidationSandbox("automatic-evaluator")
    provider = FakeProvider(sandbox=evaluator)
    worker = make_worker(tmp_path, repository, provider)
    worker._active_experiment = record
    checkpoint: dict = {"commands": {}, "current_revision_id": "revision-1"}

    _, evaluation, _ = await worker._run_frozen_experiment(
        record,
        checkpoint,
        subject,
        None,  # type: ignore[arg-type] - successful commands do not invoke repair
        [],
        specification,
        "run-1",
    )

    assert evaluation.passed
    assert provider.kill_calls == ["automatic-subject", "automatic-evaluator"]
    assert provider.create_calls == 1
    assert provider.create_kwargs[0]["purpose"] == "formal_evaluator"
    assert "python scripts/baseline.py" in subject.commands
    assert "python scripts/baseline.py" not in evaluator.commands
    assert checkpoint["automaticSubjectDestroyed"] is True
    assert checkpoint["automaticEvaluatorDestroyed"] is True
    assert checkpoint["automaticRawInputs"]["storagePath"]


class ResourceLimitSandbox:
    sandbox_id = "limit-sandbox"

    def __init__(self, payload: dict, *, content: bytes = b"") -> None:
        self.payload = payload
        self.content = content
        self.read_calls = 0

    async def run(self, command: str, **_kwargs) -> CommandExecution:
        return CommandExecution(
            command=command,
            exit_code=0,
            stdout=json.dumps(self.payload),
            stderr="",
            elapsed_seconds=0.01,
        )

    async def read_bytes(self, _path: str) -> bytes:
        self.read_calls += 1
        return self.content


async def test_repository_resource_audit_rejects_symlink_before_read(tmp_path) -> None:
    sandbox = ResourceLimitSandbox(
        {"ok": False, "error": "symlink", "path": "data/latest.csv"}
    )
    worker = make_worker(tmp_path, FakeRepository(), FakeProvider())

    with pytest.raises(WorkspaceResourceLimitExceeded, match="symlink"):
        await worker._audit_repository(sandbox, include_untracked=True)

    assert sandbox.read_calls == 0


def test_repository_resource_audit_script_rejects_real_symlink(tmp_path) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(repository)], check=True, capture_output=True
    )
    (repository / "source.py").write_text("VALUE = 1\n", encoding="utf-8")
    (repository / "linked.py").symlink_to("source.py")

    result = subprocess.run(
        [
            "python3",
            "-c",
            _REPOSITORY_AUDIT_SCRIPT,
            str(repository),
            "1",
            "48",
            "1024",
            "4096",
        ],
        check=True,
        capture_output=True,
        text=True,
    )

    assert json.loads(result.stdout) == {
        "ok": False,
        "error": "symlink",
        "path": "linked.py",
    }


async def test_oversized_archive_is_rejected_before_worker_read(tmp_path) -> None:
    sandbox = ResourceLimitSandbox(
        {
            "ok": False,
            "error": "file_size",
            "value": 100,
            "limit": 10,
        },
        content=b"x" * 100,
    )
    worker = make_worker(tmp_path, FakeRepository(), FakeProvider())

    with pytest.raises(WorkspaceResourceLimitExceeded, match="file_size"):
        await worker._read_sandbox_bytes_limited(sandbox, "/tmp/repository.zip", 10)

    assert sandbox.read_calls == 0


async def test_public_result_artifact_has_a_hard_upload_limit(tmp_path) -> None:
    worker = make_worker(tmp_path, FakeRepository(), FakeProvider())
    worker._active_experiment = experiment(pilot_spec())

    with pytest.raises(WorkspaceResourceLimitExceeded, match="metrics"):
        await worker._upload_experiment_artifact(
            experiment=worker._active_experiment,
            kind="metrics",
            file_name="metrics.json",
            content=b"x" * (1024 * 1024 + 1),
            public_safe=True,
        )


async def test_e2b_file_stream_stops_at_caller_memory_limit() -> None:
    class Stream:
        def __init__(self) -> None:
            self.closed = False
            self.chunks = iter((b"abc", b"def"))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.chunks)
            except StopIteration as error:
                raise StopAsyncIteration from error

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            self.closed = True

    stream = Stream()

    class Files:
        async def read(self, *_args, **_kwargs):
            return stream

    handle = E2BSandboxHandle(SimpleNamespace(files=Files()), "sandbox")

    with pytest.raises(SandboxFileTooLargeError, match="5-byte"):
        await handle.read_bytes_limited("/tmp/large", 5)

    assert stream.closed


async def test_e2b_command_output_is_bounded_inside_the_sandbox() -> None:
    class Stream:
        def __init__(self, content: bytes) -> None:
            self.chunks = iter((content,))

        def __aiter__(self):
            return self

        async def __anext__(self):
            try:
                return next(self.chunks)
            except StopIteration as error:
                raise StopAsyncIteration from error

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    class Files:
        def __init__(self) -> None:
            self.values: dict[str, bytes] = {}

        async def write(self, path: str, content, **_kwargs):
            self.values[path] = (
                content if isinstance(content, bytes) else str(content).encode()
            )

        async def read(self, path: str, *, format: str, **_kwargs):
            assert format == "stream"
            return Stream(self.values[path])

    class Commands:
        def __init__(self, files: Files) -> None:
            self.files = files
            self.calls: list[str] = []

        async def run(self, command: str, **_kwargs):
            self.calls.append(command)
            if "/supervisor.py" in command and command.startswith(
                "/usr/bin/python3 -I -S"
            ):
                temp_root = next(
                    path.rsplit("/", 1)[0]
                    for path in self.files.values
                    if path.endswith("/command.sh")
                )
                self.files.values[f"{temp_root}/stdout.tail"] = b"bounded stdout"
                self.files.values[f"{temp_root}/stderr.tail"] = b"bounded stderr"
                return SimpleNamespace(
                    exit_code=0, stdout="__RESEARCH_ATLAS_EXIT__=7\n", stderr=""
                )
            return SimpleNamespace(exit_code=0, stdout="", stderr="")

    files = Files()
    commands = Commands(files)
    handle = E2BSandboxHandle(
        SimpleNamespace(files=files, commands=commands), "sandbox"
    )

    execution = await handle.run(
        "python noisy.py --write-forever", check=False, timeout=3600
    )

    wrapper = next(
        item
        for item in commands.calls
        if item.startswith("/usr/bin/python3 -I -S") and "/supervisor.py" in item
    )
    command_file = next(
        value.decode()
        for path, value in files.values.items()
        if path.endswith("/command.sh")
    )
    assert "python noisy.py --write-forever" not in wrapper
    assert "python noisy.py --write-forever" in command_file
    supervisor_file = next(
        value.decode()
        for path, value in files.values.items()
        if path.endswith("/supervisor.py")
    )
    assert '"/usr/sbin/runuser"' in supervisor_file
    assert "start_new_session=True" in supervisor_file
    assert "os.killpg" in supervisor_file
    assert "baseline_user_processes" in supervisor_file
    assert "kill_new_user_processes" in supervisor_file
    assert execution.exit_code == 7
    assert execution.stdout == "bounded stdout"
    assert execution.stderr == "bounded stderr"


def test_experiment_checkpoint_retry_is_always_thirty_seconds() -> None:
    assert [ExperimentWorker._retry_delay(attempt) for attempt in range(8)] == [30] * 8
