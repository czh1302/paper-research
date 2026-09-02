from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol, TypeVar

from pydantic import BaseModel

from .idea_replay_models import (
    IdeaReplayResult,
    ReplayFinalGate,
    ReplayFinalSynthesis,
    ReplayIdeaPair,
    ReplayIdeaProposal,
    ReplayPaperClassification,
    ReplayPaperClassificationBatch,
    ReplayRoleReview,
    ReplayRoleReviewBatch,
    ResearchGapBatch,
    ResearchGapDossier,
)
from .models import PaperEvidenceProfile, ProblemBrief

SchemaModel = TypeVar("SchemaModel", bound=BaseModel)
REPLAY_VERSION = 2
REVIEW_DIMENSIONS = ("novelty", "mechanism", "feasibility", "experiment")


class StructuredClient(Protocol):
    async def structured(
        self,
        prompt: str,
        response_model: type[SchemaModel],
        *,
        allow_web_search: bool = False,
        model: str | None = None,
        stage: str = "unspecified",
    ) -> SchemaModel: ...


@dataclass(frozen=True)
class IdeaReplaySource:
    sha256: str
    briefs: list[ProblemBrief]
    profiles: list[PaperEvidenceProfile]
    landscape_overview_zh: str
    legacy_example: dict[str, Any] | None


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    temporary.replace(path)


def load_idea_replay_source(path: Path) -> IdeaReplaySource:
    raw = path.read_bytes()
    payload = json.loads(raw)
    checkpoint = payload.get("checkpoint", payload)
    v4 = dict(checkpoint.get("v4") or {})
    presentation = dict(v4.get("presentation") or {})
    landscape = dict(presentation.get("literature_landscape") or {})
    brief_payloads = checkpoint.get("problem_briefs") or presentation.get("problem_briefs")
    if not brief_payloads:
        raise ValueError("Checkpoint does not contain a reusable Problem Brief")
    profiles = [
        PaperEvidenceProfile.model_validate(item)
        for item in landscape.get("profiles") or []
    ]
    external_profiles = [item for item in profiles if item.role == "external"]
    if len(external_profiles) < 6:
        raise ValueError("Checkpoint does not contain six reusable external full-text profiles")

    legacy_example: dict[str, Any] | None = None
    ideas = list(presentation.get("ideas") or [])
    reviews = list(presentation.get("reviews") or [])
    if ideas:
        idea = dict(ideas[0])
        matching_review = next(
            (
                item
                for item in reviews
                if item.get("idea_key") == idea.get("key")
            ),
            None,
        )
        legacy_example = {
            "title_zh": idea.get("title_zh"),
            "thesis_zh": idea.get("one_sentence_zh"),
            "missing_evidence_zh": idea.get("missing_evidence_zh") or [],
            "review_rationale_zh": (
                matching_review.get("rationale_zh") if matching_review else None
            ),
        }
    return IdeaReplaySource(
        sha256=hashlib.sha256(raw).hexdigest(),
        briefs=[ProblemBrief.model_validate(item) for item in brief_payloads],
        profiles=profiles,
        landscape_overview_zh=str(landscape.get("overview_zh") or ""),
        legacy_example=legacy_example,
    )


def _compact_briefs(briefs: list[ProblemBrief]) -> list[dict[str, Any]]:
    return [
        {
            "paper_id": item.paper_id,
            "title": item.title,
            "research_question_zh": item.research_question_zh,
            "inputs_zh": [value.explanation_zh for value in item.inputs],
            "outputs_zh": [value.explanation_zh for value in item.outputs],
            "algorithm_zh": [
                f"{value.order}. {value.title_zh}: {value.explanation_zh}"
                for value in item.algorithm_steps
            ],
            "constraints_zh": [value.explanation_zh for value in item.constraints],
        }
        for item in briefs
    ]


def _compact_profiles(
    profiles: list[PaperEvidenceProfile], paper_ids: set[str] | None = None
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for item in profiles:
        if item.role != "external" or (paper_ids is not None and item.paper_id not in paper_ids):
            continue
        rows.append(
            {
                "paper_id": item.paper_id,
                "title": item.title,
                "year": item.year,
                "venue": item.venue,
                "source_url": item.source_url,
                "task_zh": item.task.claim_zh,
                "method_zh": item.method.claim_zh,
                "evaluation_zh": item.output_or_evaluation.claim_zh,
                "constraints_zh": item.constraints.claim_zh,
                "limitations_zh": item.limitations.claim_zh,
            }
        )
    return rows


def _classification_prompt(source: IdeaReplaySource) -> str:
    return f"""You are curating evidence for a top-tier computer-science research proposal.
Classify EVERY supplied external paper by its actual relationship to the input problem. A paper
may have multiple roles. direct_competitor requires a materially similar task and research claim;
generic LLM-agent, debugging, survey, or long-context papers are not direct competitors merely
because they share keywords. Preserve exact paper_id values and do not invent papers. Respond in
concise Chinese.

INPUT PROBLEM:
{_json(_compact_briefs(source.briefs))}

GROUNDED FULL-TEXT PROFILES:
{_json(_compact_profiles(source.profiles))}
"""


def _gap_prompt(
    source: IdeaReplaySource, classifications: list[ReplayPaperClassification]
) -> str:
    return f"""Act as a senior area chair. Derive exactly three evidence-backed research gaps
BEFORE proposing any mechanism. A gap is eligible only if at least three supplied papers are true
direct competitors and at least two supplied papers support implementation feasibility. Explain
why the nearest work cannot already solve it, identify real code/data assets, and classify every
remaining unknown as literature, prerequisite, or empirical. An empirical unknown may be answered
by the proposed future experiment; literature and prerequisite unknowns block qualification.
Preserve exact paper IDs. Infer plausible top-tier venue families. Do not use generic motivation,
invent prevalence claims, or claim absolute novelty.

INPUT PROBLEM:
{_json(_compact_briefs(source.briefs))}

CURRENT LANDSCAPE SUMMARY:
{source.landscape_overview_zh}

EVIDENCE ROLES:
{_json([item.model_dump(mode="json") for item in classifications])}

GROUNDED PROFILES:
{_json(_compact_profiles(source.profiles))}

LEGACY WEAK IDEA (negative example; do not repeat unless every listed defect is resolved):
{_json(source.legacy_example)}
"""


def _mechanism_prompt(
    source: IdeaReplaySource,
    gap: ResearchGapDossier,
    classifications: list[ReplayPaperClassification],
) -> str:
    ids = set(
        gap.closest_work_ids
        + gap.supporting_work_ids
        + gap.counterevidence_work_ids
    )
    return f"""Propose exactly two technically different paper-core mechanisms for this grounded
research gap. Each must be concrete enough to implement: state its inputs, maintained state,
decision process, outputs, and at least two substantive components. Compare against at least three
closest papers one by one. Provide real data/code assets, baselines, metrics, a falsifiable success
criterion, and explain the criterion's basis. Do not invent improvement percentages. Reject prompt
engineering, model swaps, generic agent orchestration, component shopping lists, and evaluation-only
ideas. Preserve exact paper IDs. Use concise but technically precise Chinese.

INPUT PROBLEM:
{_json(_compact_briefs(source.briefs))}

RESEARCH GAP:
{gap.model_dump_json()}

EVIDENCE ROLES:
{_json([item.model_dump(mode="json") for item in classifications if item.paper_id in ids])}

RELEVANT GROUNDED PROFILES:
{_json(_compact_profiles(source.profiles, ids))}
"""


REVIEW_INSTRUCTIONS = {
    "novelty": "Locate direct collisions, test whether the claimed delta is material, and reject generic recombinations.",
    "mechanism": "Test identifiability, causal logic, and whether inputs/state/decision/output define an implementable method.",
    "feasibility": "Verify that named code, data, baselines, dependencies, and resources are actually supported by the profiles.",
    "experiment": "Test falsifiability, baseline strength, metric validity, success-criterion provenance, and confound control.",
}


def _review_prompt(
    dimension: str,
    source: IdeaReplaySource,
    candidates: list[ReplayIdeaProposal],
) -> str:
    ids = {
        paper_id
        for idea in candidates
        for paper_id in (
            idea.closest_work_ids
            + idea.supporting_work_ids
            + idea.counterevidence_work_ids
        )
    }
    return f"""Act as an independent hostile reviewer for the {dimension} dimension.
{REVIEW_INSTRUCTIONS[dimension]}
Review every candidate independently and set dimension exactly to {dimension!r}. Use fatal when a
core claim collapses, major when substantial new literature or design work is required, minor only
for local repairs, and pass only when the supplied evidence resolves the dimension. Classify each
blocking unknown as literature, prerequisite, or empirical. Preserve exact idea keys and paper IDs.
Do not average scores and do not force a winner.

CANDIDATES:
{_json([item.model_dump(mode="json") for item in candidates])}

RELEVANT GROUNDED PROFILES:
{_json(_compact_profiles(source.profiles, ids))}
"""


def _synthesis_prompt(
    source: IdeaReplaySource,
    candidates: list[ReplayIdeaProposal],
    reviews: list[ReplayRoleReview],
) -> str:
    return f"""Act as the final senior researcher, not a copy editor. Select the strongest candidate
and materially repair every reviewer objection that can be repaired using the supplied evidence.
Return one final proposal and set parent_candidate_key to the selected original key. You may narrow
the claim or change the mechanism, but may not invent papers, assets, prevalence, results, or
success percentages. A fatal or major objection must be resolved in the proposal, not merely
declared resolved. Leave genuinely unresolved objections explicit. The result will be independently
re-reviewed, so polished prose cannot override a technical defect.

INPUT PROBLEM:
{_json(_compact_briefs(source.briefs))}

CANDIDATES:
{_json([item.model_dump(mode="json") for item in candidates])}

INDEPENDENT REVIEWS:
{_json([item.model_dump(mode="json") for item in reviews])}
"""


def _final_gate_prompt(
    source: IdeaReplaySource,
    synthesis: ReplayFinalSynthesis,
) -> str:
    idea = synthesis.final_idea
    ids = set(
        idea.closest_work_ids
        + idea.supporting_work_ids
        + idea.counterevidence_work_ids
    )
    return f"""Perform a fresh hostile gate review of the revised proposal. Return exactly one result
for each of novelty, mechanism, feasibility, and experiment. A conditional_pass requires pass or
minor on all four dimensions. Any unresolved direct collision, unsupported research-gap claim,
unavailable data/code prerequisite, unimplementable mechanism, or non-falsifiable experiment is
major or fatal. Only unknowns that the proposed experiment itself is designed to measure may be
classified empirical. Treat every numerical success threshold as major unless it is tied to a
cited baseline, a registered error budget, a power analysis, or a clearly identified pilot that
will set the threshold before evaluation. Do not reward presentation quality and do not force a
pass.

FINAL PROPOSAL:
{synthesis.model_dump_json()}

RELEVANT GROUNDED PROFILES:
{_json(_compact_profiles(source.profiles, ids))}
"""


def _ground_classifications(
    batch: ReplayPaperClassificationBatch,
    source: IdeaReplaySource,
) -> list[ReplayPaperClassification]:
    allowed = {item.paper_id for item in source.profiles if item.role == "external"}
    grounded: dict[str, ReplayPaperClassification] = {}
    for item in batch.papers:
        if item.paper_id in allowed:
            grounded[item.paper_id] = item.model_copy(
                update={"roles": list(dict.fromkeys(item.roles))}
            )
    if len(grounded) < 6:
        raise ValueError("Evidence classification retained fewer than six grounded papers")
    if len(
        [item for item in grounded.values() if "direct_competitor" in item.roles]
    ) < 3:
        raise ValueError("Evidence classification retained fewer than three direct competitors")
    return list(grounded.values())


def _ground_gaps(
    batch: ResearchGapBatch,
    classifications: list[ReplayPaperClassification],
) -> list[ResearchGapDossier]:
    roles = {item.paper_id: set(item.roles) for item in classifications}
    allowed = set(roles)
    grounded: list[ResearchGapDossier] = []
    for item in sorted(batch.gaps, key=lambda value: value.priority):
        closest = [
            value
            for value in dict.fromkeys(item.closest_work_ids)
            if value in allowed and "direct_competitor" in roles[value]
        ]
        supporting = [
            value
            for value in dict.fromkeys(item.supporting_work_ids)
            if value in allowed
            and roles[value].intersection(
                {"mechanism_foundation", "feasibility_support"}
            )
        ]
        counter = [
            value
            for value in dict.fromkeys(item.counterevidence_work_ids)
            if value in allowed and "counterevidence" in roles[value]
        ]
        if len(closest) < 3 or len(supporting) < 2:
            continue
        grounded.append(
            item.model_copy(
                update={
                    "closest_work_ids": closest,
                    "supporting_work_ids": supporting,
                    "counterevidence_work_ids": counter,
                }
            )
        )
    if len(grounded) < 2:
        raise ValueError("Fewer than two research gaps survived evidence grounding")
    return grounded


def _ground_candidates(
    pair: ReplayIdeaPair,
    gap: ResearchGapDossier,
    classifications: list[ReplayPaperClassification],
) -> list[ReplayIdeaProposal]:
    roles = {item.paper_id: set(item.roles) for item in classifications}
    allowed = set(roles)
    output: list[ReplayIdeaProposal] = []
    for item in pair.ideas:
        if item.gap_key != gap.key:
            continue
        closest = [
            value
            for value in dict.fromkeys(item.closest_work_ids)
            if value in allowed and "direct_competitor" in roles[value]
        ]
        supporting = [
            value for value in dict.fromkeys(item.supporting_work_ids) if value in allowed
        ]
        counter = [
            value
            for value in dict.fromkeys(item.counterevidence_work_ids)
            if value in allowed
        ]
        differences = [
            value
            for value in item.closest_work_differences
            if value.paper_id in closest
        ]
        if (
            len(closest) < 3
            or len(supporting) < 2
            or len({value.paper_id for value in differences}) < 3
        ):
            continue
        output.append(
            item.model_copy(
                update={
                    "closest_work_ids": closest,
                    "supporting_work_ids": supporting,
                    "counterevidence_work_ids": counter,
                    "closest_work_differences": differences,
                }
            )
        )
    if len(output) != 2:
        raise ValueError(f"Gap {gap.key!r} did not retain two grounded mechanisms")
    return output


def _ground_reviews(
    batch: ReplayRoleReviewBatch,
    dimension: str,
    candidates: list[ReplayIdeaProposal],
) -> list[ReplayRoleReview]:
    candidate_keys = {item.key for item in candidates}
    by_key = {
        item.idea_key: item
        for item in batch.reviews
        if item.idea_key in candidate_keys and item.dimension == dimension
    }
    output: list[ReplayRoleReview] = []
    for item in candidates:
        review = by_key.get(item.key)
        if review:
            output.append(review)
        else:
            output.append(
                ReplayRoleReview(
                    idea_key=item.key,
                    dimension=dimension,
                    severity="fatal",
                    rationale_zh="独立审查未返回该候选，证据链不完整，不能通过。",
                    fatal_flaws_zh=["缺少完整的独立审查结果"],
                    blocking_unknowns=[
                        {
                            "kind": "prerequisite",
                            "description_zh": "必须补齐该维度的独立审查",
                        }
                    ],
                )
            )
    return output


def _normalize_final_synthesis(
    synthesis: ReplayFinalSynthesis,
    classifications: list[ReplayPaperClassification],
    valid_profile_ids: set[str],
) -> ReplayFinalSynthesis:
    """Keep adjacent evidence without counting it as direct competition."""

    roles = {item.paper_id: set(item.roles) for item in classifications}
    idea = synthesis.final_idea
    closest = [
        paper_id
        for paper_id in dict.fromkeys(idea.closest_work_ids)
        if paper_id in valid_profile_ids
        and "direct_competitor" in roles.get(paper_id, set())
    ]
    adjacent_support = [
        paper_id
        for paper_id in dict.fromkeys(idea.closest_work_ids)
        if paper_id in valid_profile_ids
        and roles.get(paper_id, set()).intersection(
            {"mechanism_foundation", "feasibility_support"}
        )
    ]
    supporting = [
        paper_id
        for paper_id in dict.fromkeys(
            [*idea.supporting_work_ids, *adjacent_support]
        )
        if paper_id in valid_profile_ids
    ]
    differences = [
        item for item in idea.closest_work_differences if item.paper_id in closest
    ]
    counterevidence = [
        paper_id
        for paper_id in dict.fromkeys(idea.counterevidence_work_ids)
        if paper_id in valid_profile_ids
    ]

    # If grounding would violate the result schema, preserve the inspectable model
    # output and let the deterministic gate reject it instead of crashing after a
    # paid call.
    if (
        len(closest) < 3
        or len(supporting) < 2
        or len({item.paper_id for item in differences}) < 3
    ):
        return synthesis

    normalized = idea.model_copy(
        update={
            "closest_work_ids": closest,
            "supporting_work_ids": supporting,
            "counterevidence_work_ids": counterevidence,
            "closest_work_differences": differences,
        }
    )
    return synthesis.model_copy(update={"final_idea": normalized})


def determine_replay_decision(
    final_idea: ReplayIdeaProposal,
    gate: ReplayFinalGate,
    classifications: list[ReplayPaperClassification],
    valid_profile_ids: set[str],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if gate.idea_key != final_idea.key:
        reasons.append("最终复审的 Idea key 与修订方案不一致")
    severities = {item.dimension: item.severity for item in gate.dimensions}
    if set(severities) != set(REVIEW_DIMENSIONS):
        reasons.append("最终复审未覆盖四个必要维度")
    for dimension, severity in severities.items():
        if severity in {"major", "fatal"}:
            reasons.append(f"{dimension} 审查仍为 {severity}")
    blocking = [
        unknown
        for item in gate.dimensions
        for unknown in item.blocking_unknowns
        if unknown.kind in {"literature", "prerequisite"}
    ]
    if blocking:
        reasons.append("仍存在文献或实现前置条件未解决")

    roles = {item.paper_id: set(item.roles) for item in classifications}
    closest = list(dict.fromkeys(final_idea.closest_work_ids))
    direct_closest = [
        paper_id
        for paper_id in closest
        if paper_id in valid_profile_ids
        and "direct_competitor" in roles.get(paper_id, set())
    ]
    supporting = list(dict.fromkeys(final_idea.supporting_work_ids))
    if len(direct_closest) < 3:
        reasons.append("未绑定至少三篇有效的直接竞争全文论文")
    if len(supporting) < 2 or any(
        paper_id not in valid_profile_ids for paper_id in supporting
    ):
        reasons.append("未绑定至少两篇有效的可行性全文论文")
    difference_ids = {
        item.paper_id for item in final_idea.closest_work_differences
    }
    if len(difference_ids.intersection(direct_closest)) < 3:
        reasons.append("未逐篇说明与三篇最近工作的实质差异")

    generic_text = " ".join(
        [
            final_idea.title_zh,
            final_idea.thesis_zh,
            final_idea.core_contribution_zh,
            final_idea.mechanism.decision_process_zh,
            *final_idea.mechanism.components_zh,
        ]
    )
    forbidden = (
        r"仅(?:替换|更换).*(?:模型|大模型)",
        r"只(?:增加|加入).*大模型",
        r"纯(?:评测|评价|基准)",
    )
    if any(re.search(pattern, generic_text) for pattern in forbidden):
        reasons.append("技术机制属于禁止的模型替换或纯评测方向")
    if gate.model_decision != "conditional_pass":
        reasons.append("最终独立复审没有给出条件通过")

    if reasons:
        return (
            "rejected" if gate.model_decision == "rejected" else "needs_evidence",
            list(dict.fromkeys(reasons)),
        )
    return "conditional_pass", []


def render_idea_replay_markdown(
    result: IdeaReplayResult,
    profiles: list[PaperEvidenceProfile],
) -> str:
    profile_map = {item.paper_id: item for item in profiles}
    status = {
        "conditional_pass": "条件通过",
        "needs_evidence": "仍需补证",
        "rejected": "未通过",
    }[result.decision]
    lines = [
        "# Idea-only 质量验证",
        "",
        f"- 最终状态：**{status}**",
        f"- 复用全文档案：{result.source_profile_count} 篇",
        f"- 论文分类模型：`{result.classification_model}`",
        f"- Gap、Idea 与审查模型：`{result.idea_model}`",
        "- 本次未运行检索、MinerU、PDF 处理或沙箱实验。",
        "",
    ]
    if result.deterministic_reasons_zh:
        lines.extend(["## 未通过的确定性原因", ""])
        lines.extend(f"- {item}" for item in result.deterministic_reasons_zh)
        lines.append("")

    idea = result.final_synthesis.final_idea
    lines.extend(
        [
            "## 最终主 Idea",
            "",
            f"### {idea.title_zh}",
            "",
            idea.thesis_zh,
            "",
            "### 研究问题与贡献",
            "",
            f"- **形式化问题：** {idea.formal_problem_zh}",
            f"- **可证伪假设：** {idea.hypothesis_zh}",
            f"- **核心贡献：** {idea.core_contribution_zh}",
            "",
            "### 技术机制",
            "",
            f"- **输入：** {idea.mechanism.inputs_zh}",
            f"- **状态：** {idea.mechanism.state_zh}",
            f"- **决策过程：** {idea.mechanism.decision_process_zh}",
            f"- **输出：** {idea.mechanism.outputs_zh}",
            "",
        ]
    )
    lines.extend(
        f"{index}. {item}"
        for index, item in enumerate(idea.mechanism.components_zh, start=1)
    )
    lines.extend(["", "### 与最近工作的实质差异", ""])
    for item in idea.closest_work_differences:
        paper = profile_map.get(item.paper_id)
        title = paper.title if paper else item.paper_id
        lines.extend(
            [
                f"- **{title}**",
                f"  - 现有方法：{item.prior_approach_zh}",
                f"  - 本方案差异：{item.precise_difference_zh}",
            ]
        )
    experiment = idea.experiment
    lines.extend(
        [
            "",
            "### 第一个可证伪实验",
            "",
            f"- **输入与资产：** {experiment.inputs_and_assets_zh}",
            f"- **Baselines：** {experiment.baselines_zh}",
            f"- **核心改动：** {experiment.intervention_zh}",
            f"- **指标：** {'；'.join(experiment.metrics_zh)}",
            f"- **成功条件：** {experiment.success_criterion_zh}",
            f"- **阈值依据：** {experiment.success_criterion_basis_zh}",
            f"- **资源：** {experiment.resources_zh}",
            "",
            "## 最终独立复审",
            "",
            "| 维度 | 结论 | 理由 |",
            "| --- | --- | --- |",
        ]
    )
    labels = {
        "novelty": "新颖性与撞车",
        "mechanism": "技术机制",
        "feasibility": "工程可行性",
        "experiment": "实验设计",
    }
    for item in result.final_gate.dimensions:
        lines.append(
            f"| {labels[item.dimension]} | {item.severity} | "
            f"{item.rationale_zh.replace('|', '｜')} |"
        )
    if result.final_gate.next_research_queries:
        lines.extend(["", "## 下一步定向补证", ""])
        lines.extend(f"- {item}" for item in result.final_gate.next_research_queries)

    lines.extend(["", "## 三个研究 Gap", ""])
    for gap in result.gaps:
        lines.extend(
            [
                f"### {gap.priority}. {gap.title_zh}",
                "",
                gap.problem_zh,
                "",
                f"- **现有工作为何未解决：** {gap.why_unsolved_zh}",
                f"- **可用资产：** {gap.available_assets_zh}",
                f"- **目标方向：** {', '.join(gap.target_venues)}",
                "",
            ]
        )

    cited_ids = list(
        dict.fromkeys(
            idea.closest_work_ids
            + idea.supporting_work_ids
            + idea.counterevidence_work_ids
        )
    )
    lines.extend(["## 采用的全文论文", ""])
    for paper_id in cited_ids:
        paper = profile_map.get(paper_id)
        if not paper:
            continue
        suffix = f"（{paper.year}）" if paper.year else ""
        if paper.source_url:
            lines.append(f"- [{paper.title}]({paper.source_url}){suffix}")
        else:
            lines.append(f"- {paper.title}{suffix}")
    lines.append("")
    return "\n".join(lines)


class IdeaReplayRunner:
    def __init__(
        self,
        client: StructuredClient,
        *,
        classification_model: str,
        idea_model: str,
        output: Path,
    ) -> None:
        self.client = client
        self.classification_model = classification_model
        self.idea_model = idea_model
        self.output = output
        self.checkpoint_path = output / ".checkpoint.json"
        self.state: dict[str, Any] = {}

    def _load_state(self, source: IdeaReplaySource) -> None:
        if self.checkpoint_path.exists():
            self.state = json.loads(self.checkpoint_path.read_text(encoding="utf-8"))
            if self.state.get("source_checkpoint_sha256") != source.sha256:
                raise ValueError("Idea replay checkpoint belongs to a different source checkpoint")
            if int(self.state.get("version", 0)) != REPLAY_VERSION:
                raise ValueError("Idea replay checkpoint version is not supported")
        else:
            self.state = {
                "version": REPLAY_VERSION,
                "source_checkpoint_sha256": source.sha256,
                "classification_model": self.classification_model,
                "idea_model": self.idea_model,
                "stages": {},
                "calls": [],
            }
            self._save_state()

    def _save_state(self) -> None:
        _atomic_json(self.checkpoint_path, self.state)

    async def _stage(
        self,
        name: str,
        prompt: str,
        response_model: type[SchemaModel],
        provider_model: str,
    ) -> SchemaModel:
        cached = dict(self.state.get("stages") or {}).get(name)
        if cached is not None:
            return response_model.model_validate(cached)
        result = await self.client.structured(
            prompt,
            response_model,
            allow_web_search=False,
            model=provider_model,
            stage=f"idea_replay:{name}",
        )
        self.state.setdefault("stages", {})[name] = result.model_dump(mode="json")
        self.state.setdefault("calls", []).append(
            {
                "stage": name,
                "model": provider_model,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        self._save_state()
        return result

    async def run(self, checkpoint: Path) -> IdeaReplayResult:
        source = load_idea_replay_source(checkpoint)
        self.output.mkdir(parents=True, exist_ok=True)
        self._load_state(source)
        completed = self.state.get("result")
        if completed:
            result = IdeaReplayResult.model_validate(completed)
            valid_profile_ids = {
                item.paper_id for item in source.profiles if item.role == "external"
            }
            normalized_synthesis = _normalize_final_synthesis(
                result.final_synthesis,
                result.classifications,
                valid_profile_ids,
            )
            decision, reasons = determine_replay_decision(
                normalized_synthesis.final_idea,
                result.final_gate,
                result.classifications,
                valid_profile_ids,
            )
            result = result.model_copy(
                update={
                    "final_synthesis": normalized_synthesis,
                    "decision": decision,
                    "deterministic_reasons_zh": reasons,
                }
            )
            self.state["result"] = result.model_dump(mode="json")
            self._save_state()
            self._write_outputs(result, source)
            return result

        classification_batch = await self._stage(
            "classification",
            _classification_prompt(source),
            ReplayPaperClassificationBatch,
            self.classification_model,
        )
        classifications = _ground_classifications(classification_batch, source)

        gap_batch = await self._stage(
            "gaps",
            _gap_prompt(source, classifications),
            ResearchGapBatch,
            self.idea_model,
        )
        gaps = _ground_gaps(gap_batch, classifications)

        candidates: list[ReplayIdeaProposal] = []
        for gap in gaps[:2]:
            pair = await self._stage(
                f"mechanisms:{gap.key}",
                _mechanism_prompt(source, gap, classifications),
                ReplayIdeaPair,
                self.idea_model,
            )
            candidates.extend(_ground_candidates(pair, gap, classifications))

        reviews: list[ReplayRoleReview] = []
        for dimension in REVIEW_DIMENSIONS:
            batch = await self._stage(
                f"review:{dimension}",
                _review_prompt(dimension, source, candidates),
                ReplayRoleReviewBatch,
                self.idea_model,
            )
            reviews.extend(_ground_reviews(batch, dimension, candidates))

        synthesis = await self._stage(
            "final_synthesis",
            _synthesis_prompt(source, candidates, reviews),
            ReplayFinalSynthesis,
            self.idea_model,
        )
        valid_profile_ids = {
            item.paper_id for item in source.profiles if item.role == "external"
        }
        synthesis = _normalize_final_synthesis(
            synthesis,
            classifications,
            valid_profile_ids,
        )
        gate = await self._stage(
            "final_gate",
            _final_gate_prompt(source, synthesis),
            ReplayFinalGate,
            self.idea_model,
        )
        decision, reasons = determine_replay_decision(
            synthesis.final_idea,
            gate,
            classifications,
            valid_profile_ids,
        )
        result = IdeaReplayResult(
            source_checkpoint_sha256=source.sha256,
            classification_model=self.classification_model,
            idea_model=self.idea_model,
            source_profile_count=len(valid_profile_ids),
            classifications=classifications,
            gaps=gaps,
            candidates=candidates,
            reviews=reviews,
            final_synthesis=synthesis,
            final_gate=gate,
            decision=decision,
            deterministic_reasons_zh=reasons,
        )
        self.state["result"] = result.model_dump(mode="json")
        self._save_state()
        self._write_outputs(result, source)
        return result

    def _write_outputs(
        self, result: IdeaReplayResult, source: IdeaReplaySource
    ) -> None:
        (self.output / "idea-review.json").write_text(
            result.model_dump_json(indent=2), encoding="utf-8"
        )
        (self.output / "idea-review.md").write_text(
            render_idea_replay_markdown(result, source.profiles), encoding="utf-8"
        )
