from __future__ import annotations

import io
import zipfile

import pytest
from paper_research.clients.e2b import E2BSandboxProvider
from paper_research.models import PilotSpecification
from paper_research.validation_bundle import (
    ValidationBundleError,
    ValidationInput,
    build_validation_bundle,
    parse_validation_bundle,
    validation_input_paths,
)


def specification() -> PilotSpecification:
    return PilotSpecification.model_validate(
        {
            "version": 1,
            "hypothesis_zh": "在固定公开数据和计算预算下，干预机制会改善预先冻结的主要指标。",
            "hypothesis_en": "Under frozen public data and compute budgets, the intervention improves the primary metric.",
            "execution_mode": "native_cpu",
            "invariants_zh": ["数据、预算和评估器保持不变"],
            "invariants_en": ["Data, budget, and evaluator remain frozen."],
            "resources": [
                {
                    "key": "fixture",
                    "kind": "dataset",
                    "name": "fixture",
                    "url": "https://github.com/example/data.json",
                    "version": "v1",
                    "license": "MIT",
                    "purpose_zh": "提供冻结的测试数据。",
                    "purpose_en": "Provides the frozen test data.",
                }
            ],
            "allowed_hosts": ["github.com"],
            "environment_commands": ["python -m pip --version"],
            "test_commands": ["python -m pytest"],
            "baseline_commands": ["python baseline.py"],
            "intervention_commands": ["python intervention.py"],
            "evaluation_commands": [
                "python .research-atlas/evaluator/score.py"
            ],
            "metrics_output_path": "artifacts/metrics.json",
            "metrics_json_schema": {
                "type": "object",
                "properties": {"effect": {"type": "number"}},
                "required": ["effect"],
                "additionalProperties": False,
            },
            "metrics": [
                {
                    "key": "effect",
                    "name_zh": "主要效果",
                    "name_en": "Primary effect",
                    "definition_zh": "冻结评估器从原始输出计算出的效果。",
                    "definition_en": "Effect computed from raw outputs by the frozen evaluator.",
                    "json_pointer": "/effect",
                    "direction": "higher",
                    "success_threshold": 0.2,
                    "comparison": "absolute",
                }
            ],
            "primary_metric_key": "effect",
            "evaluator_files": [
                {
                    "path": "score.py",
                    "content": "from pathlib import Path\nprint(Path('artifacts/raw.json').read_text())\n",
                }
            ],
            "evaluator_test_commands": [
                "python .research-atlas/evaluator/score.py"
            ],
            "evaluator_cases": [
                {"name": "pass", "metrics": {"effect": 0.3}, "expected_pass": True},
                {"name": "fail", "metrics": {"effect": 0.1}, "expected_pass": False},
            ],
            "artifacts": [
                {
                    "path": "artifacts/raw.json",
                    "kind": "table",
                    "description_zh": "冻结评估器的原始输入",
                    "description_en": "Raw frozen evaluator input.",
                },
                {
                    "path": "artifacts/metrics.json",
                    "kind": "metrics",
                    "public_safe": True,
                    "description_zh": "冻结指标输出",
                    "description_en": "Frozen metric output.",
                },
            ],
            "estimated_minutes": 5,
        }
    )


def test_validation_bundle_transfers_only_declared_raw_inputs() -> None:
    spec = specification()
    assert validation_input_paths(spec) == ["artifacts/raw.json"]
    bundle = build_validation_bundle(
        spec,
        [ValidationInput(path="artifacts/raw.json", content=b'{"raw": 7}')],
        max_file_bytes=1024,
        max_total_bytes=4096,
    )

    restored = parse_validation_bundle(
        spec, bundle, max_file_bytes=1024, max_total_bytes=4096
    )

    assert restored == [ValidationInput("artifacts/raw.json", b'{"raw":7}')]


def test_validation_bundle_rejects_final_metrics_as_its_only_input() -> None:
    spec = specification().model_copy(
        update={"artifacts": [specification().artifacts[1]]}
    )
    with pytest.raises(ValidationBundleError, match="declared raw evaluator input"):
        validation_input_paths(spec)


def test_validation_bundle_rejects_tampering_and_extra_members() -> None:
    spec = specification()
    bundle = build_validation_bundle(
        spec,
        [ValidationInput(path="artifacts/raw.json", content=b'{"value":"original"}')],
        max_file_bytes=1024,
        max_total_bytes=4096,
    )
    source = zipfile.ZipFile(io.BytesIO(bundle))
    changed = io.BytesIO()
    with source, zipfile.ZipFile(changed, "w") as target:
        for name in source.namelist():
            content = source.read(name)
            target.writestr(
                name,
                b'{"value":"tampered"}' if name == "payload/000" else content,
            )

    with pytest.raises(ValidationBundleError, match="content hash"):
        parse_validation_bundle(
            spec, changed.getvalue(), max_file_bytes=1024, max_total_bytes=4096
        )


def test_validation_bundle_rejects_missing_or_oversized_inputs() -> None:
    spec = specification()
    with pytest.raises(ValidationBundleError, match="frozen artifact manifest"):
        build_validation_bundle(
            spec, [], max_file_bytes=1024, max_total_bytes=4096
        )
    with pytest.raises(ValidationBundleError, match="file limit"):
        build_validation_bundle(
            spec,
            [ValidationInput("artifacts/raw.json", b"x" * 1025)],
            max_file_bytes=1024,
            max_total_bytes=4096,
        )


def test_validation_bundle_canonicalizes_json_and_rejects_executable_formats() -> None:
    spec = specification()
    with pytest.raises(ValidationBundleError, match="duplicate key"):
        build_validation_bundle(
            spec,
            [ValidationInput("artifacts/raw.json", b'{"x":1,"x":2}')],
            max_file_bytes=1024,
            max_total_bytes=4096,
        )
    unsafe_format = spec.model_copy(
        update={
            "artifacts": [
                spec.artifacts[0].model_copy(update={"path": "artifacts/raw.pkl"}),
                spec.artifacts[1],
            ]
        }
    )
    with pytest.raises(ValidationBundleError, match="canonical JSON"):
        validation_input_paths(unsafe_format)


def test_validation_bundle_rejects_unsafe_evaluator_source() -> None:
    spec = specification()
    unsafe = spec.model_copy(
        update={
            "evaluator_files": [
                spec.evaluator_files[0].model_copy(
                    update={"content": "import pickle\npickle.loads(b'payload')\n"}
                )
            ]
        }
    )
    with pytest.raises(ValidationBundleError, match="unsafe module"):
        validation_input_paths(unsafe)
    attribute_escape = spec.model_copy(
        update={
            "evaluator_files": [
                spec.evaluator_files[0].model_copy(
                    update={
                        "content": "import pathlib\npathlib.os.system('echo unsafe')\n"
                    }
                )
            ]
        }
    )
    with pytest.raises(ValidationBundleError, match="operating-system processes"):
        validation_input_paths(attribute_escape)


async def test_formal_evaluator_sandbox_has_no_network(monkeypatch) -> None:
    class Sandbox:
        sandbox_id = "evaluator"

    class Sdk:
        kwargs: dict = {}

        @classmethod
        async def create(cls, **kwargs):
            cls.kwargs = kwargs
            return Sandbox()

    async def skip_resource_check(*_args) -> None:
        return None

    monkeypatch.setattr(E2BSandboxProvider, "_sdk", staticmethod(lambda: Sdk))
    monkeypatch.setattr(E2BSandboxProvider, "_verify_resources", skip_resource_check)
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
        allowed_hosts=["github.com"],
        purpose="formal_evaluator",
    )

    assert Sdk.kwargs["allow_internet_access"] is False
    assert Sdk.kwargs["network"]["allow_out"] == []
    assert Sdk.kwargs["network"]["deny_out"] == ["0.0.0.0/0"]
