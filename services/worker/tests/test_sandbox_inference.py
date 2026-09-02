from __future__ import annotations

import io
import json
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from paper_research.config import Settings
from paper_research.models import PilotInferenceContract, ProviderUsage
from paper_research.sandbox_inference import (
    SANDBOX_INFERENCE_CLIENT_SOURCE,
    SandboxInferenceEnvelope,
    SandboxInferenceWorker,
)


def contract() -> PilotInferenceContract:
    return PilotInferenceContract.model_validate(
        {
            "key": "judge_output",
            "purpose_zh": "对冻结实验样本作结构化判断。",
            "purpose_en": "Make a structured decision for a frozen experimental sample.",
            "instruction": "Classify the supplied sample using the frozen labels only.",
            "request_json_schema": {
                "type": "object",
                "properties": {"text": {"type": "string", "maxLength": 2000}},
                "required": ["text"],
                "additionalProperties": False,
            },
            "response_json_schema": {
                "type": "object",
                "properties": {
                    "label": {"type": "string", "enum": ["yes", "no"]},
                    "confidence": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "required": ["label", "confidence"],
                "additionalProperties": False,
            },
            "max_calls": 2,
        }
    )


def request_row(*, provider_started: bool = False) -> dict:
    value = contract()
    return {
        "id": "11111111-1111-4111-8111-111111111111",
        "experiment_id": "22222222-2222-4222-8222-222222222222",
        "run_id": "33333333-3333-4333-8333-333333333333",
        "action_id": None,
        "specification_hash": "a" * 64,
        "contract_key": value.key,
        "contract": value.model_dump(mode="json"),
        "request": {"text": "sample"},
        "request_sha256": "b" * 64,
        "status": "running",
        "invocation_id": "44444444-4444-4444-8444-444444444444",
        "reserved_cny": 0.5,
        "provider_started_at": "2026-09-02T00:00:00Z" if provider_started else None,
    }


class Repository:
    def __init__(self) -> None:
        self.finished: list[dict] = []
        self.started = 0

    async def mark_sandbox_inference_provider_started(self, *_args, **_kwargs):
        self.started += 1
        return True

    async def finish_sandbox_inference_request(
        self, request_id, worker_id, **values
    ):
        self.finished.append(
            {"request_id": request_id, "worker_id": worker_id, **values}
        )
        return values

    async def close(self):
        return None


class FlashLlm:
    def __init__(self, result: dict | None = None) -> None:
        self.result = result or {"label": "yes", "confidence": 0.8}
        self.calls: list[dict] = []

    async def structured(self, prompt, response_model, **kwargs):
        self.calls.append({"prompt": prompt, "response_model": response_model, **kwargs})
        parsed = SandboxInferenceEnvelope(result=self.result)
        usage = ProviderUsage(
            provider="deepseek",
            model="deepseek-v4-flash",
            input_tokens=100,
            output_tokens=20,
            metadata={
                "transport": "claude_code",
                "stage": "experiment_sandbox_inference",
                "claude_cli_model": "claude-sonnet-4-5",
            },
        )
        callback = kwargs["before_usage_callback"]
        await callback(usage, parsed)
        return parsed


def worker(tmp_path: Path, repository: Repository, llm: FlashLlm) -> SandboxInferenceWorker:
    settings = Settings(
        _env_file=None,
        ARTIFACT_ROOT=tmp_path,
        EXPERIMENT_WORKER_ID="test-worker",
    )
    return SandboxInferenceWorker(settings, repository=repository, llm=llm)


@pytest.mark.asyncio
async def test_inference_uses_flash_claude_transport_and_settles_schema_result(
    tmp_path: Path,
) -> None:
    repository = Repository()
    llm = FlashLlm()
    service = worker(tmp_path, repository, llm)

    await service.process(request_row())

    assert repository.started == 1
    assert repository.finished[0]["status"] == "completed"
    assert repository.finished[0]["response_payload"] == {
        "label": "yes",
        "confidence": 0.8,
    }
    assert llm.calls[0]["model"] == "deepseek-v4-flash"
    assert llm.calls[0]["stage"] == "experiment_sandbox_inference"
    assert llm.calls[0]["max_budget_usd"] == pytest.approx(0.5 / 7.5)
    assert "UNTRUSTED REQUEST JSON" in llm.calls[0]["prompt"]
    journal = json.loads(
        (
            tmp_path
            / "experiment-inference-journals"
            / "11111111-1111-4111-8111-111111111111.json"
        ).read_text()
    )
    assert journal["settled"] is True
    assert journal["usage"]["metadata"]["transport"] == "claude_code"


@pytest.mark.asyncio
async def test_started_request_is_not_replayed_after_worker_recovery(
    tmp_path: Path,
) -> None:
    repository = Repository()
    llm = FlashLlm()
    service = worker(tmp_path, repository, llm)

    await service.process(request_row(provider_started=True))

    assert llm.calls == []
    assert repository.finished[0]["status"] == "blocked"
    assert repository.finished[0]["cost_cny"] == 0.5
    assert repository.finished[0]["settlement_kind"] == "ambiguous_provider_invocation"


@pytest.mark.asyncio
async def test_response_schema_violation_never_reaches_sandbox(
    tmp_path: Path,
) -> None:
    repository = Repository()
    llm = FlashLlm({"label": "maybe", "confidence": 3})
    service = worker(tmp_path, repository, llm)

    await service.process(request_row())

    assert repository.finished[0]["status"] == "blocked"
    assert repository.finished[0]["public_error_code"] == "response_schema_rejected"
    assert "response_payload" not in repository.finished[0]


@pytest.mark.asyncio
async def test_persisted_request_schema_violation_is_blocked_without_model_or_cost(
    tmp_path: Path,
) -> None:
    repository = Repository()
    llm = FlashLlm()
    service = worker(tmp_path, repository, llm)
    row = request_row()
    row["request"] = {"unexpected": True}

    await service.process(row)

    assert llm.calls == []
    assert repository.started == 0
    assert repository.finished[0]["status"] == "blocked"
    assert repository.finished[0]["cost_cny"] == 0
    assert repository.finished[0]["settlement_kind"] == "preflight_rejected"


def test_contract_schema_is_bounded_and_reference_free() -> None:
    payload = contract().model_dump(mode="json")
    payload["request_json_schema"] = {
        "type": "object",
        "properties": {"input": {"$ref": "https://attacker.invalid/schema"}},
        "required": ["input"],
        "additionalProperties": False,
    }
    with pytest.raises(ValueError, match="supported explicit type|unsupported keywords"):
        PilotInferenceContract.model_validate(payload)

    payload = contract().model_dump(mode="json")
    payload["request_json_schema"]["properties"]["text"]["maxLength"] = "huge"
    with pytest.raises(ValueError, match="valid JSON schema"):
        PilotInferenceContract.model_validate(payload)


def test_sandbox_client_recovers_lost_submit_response_and_transient_poll(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "inference.json"
    config_path.write_text(
        json.dumps(
            {
                "endpoint": "https://project-ref.supabase.co/functions/v1/experiment-sandbox-inference",
                "contracts": {
                    "judge_output": {
                        "tokens": [{"token": "a" * 43, "used": False}]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_ATLAS_INFERENCE_CONFIG", str(config_path))
    namespace: dict = {}
    exec(compile(SANDBOX_INFERENCE_CLIENT_SOURCE, "<sandbox-client>", "exec"), namespace)

    class Clock:
        value = 0.0

        @classmethod
        def monotonic(cls):
            cls.value += 0.01
            return cls.value

        @classmethod
        def sleep(cls, delay):
            cls.value += delay

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    post_count = 0
    poll_count = 0

    def urlopen(request, **_kwargs):
        nonlocal post_count, poll_count
        if request.get_method() == "POST":
            post_count += 1
            # Simulate a committed Edge transaction whose HTTP response was lost.
            raise urllib.error.URLError("response lost")
        poll_count += 1
        if poll_count == 1:
            return Response(b'{"state":"queued","pollAfterMs":1}')
        if poll_count == 2:
            raise urllib.error.URLError("transient poll outage")
        return Response(b'{"state":"completed","result":{"label":"yes"}}')

    namespace["time"] = Clock
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert namespace["infer"]("judge_output", {"text": "sample"}) == {
        "label": "yes"
    }
    assert post_count == 1
    assert poll_count == 3
    assert json.loads(config_path.read_text())["contracts"]["judge_output"][
        "tokens"
    ][0]["used"] is True


def test_sandbox_client_resubmits_only_when_first_submit_never_committed(
    tmp_path: Path, monkeypatch
) -> None:
    config_path = tmp_path / "inference.json"
    config_path.write_text(
        json.dumps(
            {
                "endpoint": "https://project-ref.supabase.co/functions/v1/experiment-sandbox-inference",
                "contracts": {
                    "judge_output": {
                        "tokens": [{"token": "b" * 43, "used": False}]
                    }
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RESEARCH_ATLAS_INFERENCE_CONFIG", str(config_path))
    namespace: dict = {}
    exec(compile(SANDBOX_INFERENCE_CLIENT_SOURCE, "<sandbox-client>", "exec"), namespace)

    class Clock:
        value = 0.0

        @classmethod
        def monotonic(cls):
            cls.value += 0.01
            return cls.value

        @classmethod
        def sleep(cls, delay):
            cls.value += delay

    class Response(io.BytesIO):
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            self.close()

    post_count = 0

    def urlopen(request, **_kwargs):
        nonlocal post_count
        if request.get_method() == "POST":
            post_count += 1
            if post_count == 1:
                raise urllib.error.URLError("request never arrived")
            request_id = request.get_header("X-research-atlas-request-id")
            return Response(
                json.dumps({"requestId": request_id, "pollAfterMs": 1}).encode()
            )
        if post_count == 1:
            raise urllib.error.HTTPError(
                request.full_url, 401, "not found", {}, None
            )
        return Response(b'{"state":"completed","result":{"label":"no"}}')

    namespace["time"] = Clock
    monkeypatch.setattr(urllib.request, "urlopen", urlopen)

    assert namespace["infer"]("judge_output", {"text": "sample"}) == {
        "label": "no"
    }
    assert post_count == 2


def test_proxy_sql_and_edge_contract_are_private_and_one_shot() -> None:
    root = Path(__file__).resolve().parents[3]
    migration = (root / "supabase/migrations/20260902000000_e2b_experiments.sql").read_text()
    config = (root / "supabase/config.toml").read_text()
    edge = (
        root / "supabase/functions/experiment-sandbox-inference/index.ts"
    ).read_text()
    prompts = (root / "services/worker/paper_research/prompts.py").read_text()

    assert "tokens.consumed_at is null" in migration
    assert "for update" in migration[migration.index("consume_sandbox_inference_token") :]
    assert "tokens.expires_at > now()" in migration
    assert "runs.hard_deadline_at > now()" in migration
    assert "experiment_inference_requests" in migration
    assert "experiment_llm_invocations" in migration
    assert "p_run_max_cny" in migration
    assert "grant execute on function public.consume_sandbox_inference_token" in migration
    assert (
        "consume_sandbox_inference_token(text, uuid, jsonb, text, text)"
        in migration
    )
    assert "from public, anon, authenticated" in migration
    assert "verify_jwt = false" in config[config.index("[functions.experiment-sandbox-inference]") :]
    assert "readBoundedBody" in edge
    assert "x-research-atlas-inference-token" in edge
    assert "x-research-atlas-request-id" in edge
    assert "x-research-atlas-poll-token" in edge
    assert "pollToken," not in edge
    assert "DEEPSEEK" not in edge
    assert "no live managed-LLM inference channel" not in prompts
    assert "requires_live_inference=true" in prompts
