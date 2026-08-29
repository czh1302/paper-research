from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .clients.llm import ClaudeCodeClient
from .clients.mineru import MinerUClient
from .config import Settings
from .document import blocks_as_prompt, chunk_blocks, normalize_mineru_zip, validate_pdf
from .models import (
    AnalysisMode,
    AnalysisReport,
    CandidatePaper,
    DocumentBlock,
    DocumentIR,
    Evidence,
    Job,
    JobStatus,
    JointProblemStatement,
    ProblemStatement,
    ProviderUsage,
    QueryBundle,
    ReportPresentation,
    RoundAnalysis,
    SearchQuery,
    WebDiscovery,
)
from .prompts import (
    baseline_report_prompt,
    joint_problem_prompt,
    merge_problem_prompt,
    problem_statement_prompt,
    query_prompt,
    report_presentation_prompt,
    round_analysis_prompt,
    web_discovery_prompt,
)
from .reporting import DISCLAIMER_EN, DISCLAIMER_ZH, report_markdown, report_visualization_data
from .sources import LiteratureRetriever, build_sources
from .sources.retriever import merge_candidates, source_coverage
from .sources.web import SerperSource, TavilySource

LOGGER = logging.getLogger(__name__)


class JobCancelled(RuntimeError):
    pass


class BudgetBlocked(RuntimeError):
    pass


def estimate_usage_cny(usage: ProviderUsage) -> float:
    # Conservative current peak pricing plus a small FX margin.
    is_pro = bool(usage.model and "v4-pro" in usage.model)
    input_price = 1.32 if is_pro else 0.44
    output_price = 3.96 if is_pro else 1.32
    usd = (usage.input_tokens * input_price + usage.output_tokens * output_price) / 1_000_000
    return round(usd * 7.5, 6)


def rank_candidates(
    candidates: list[CandidatePaper], query_bundle: QueryBundle
) -> list[CandidatePaper]:
    query_tokens = set(
        token
        for query in query_bundle.queries
        for token in re.findall(r"[a-z0-9]{3,}", query.query.casefold())
    )
    for paper in candidates:
        text_tokens = set(re.findall(r"[a-z0-9]{3,}", f"{paper.title} {paper.abstract}".casefold()))
        lexical = len(query_tokens & text_tokens) / max(1, len(query_tokens))
        source_bonus = min(len(paper.sources) * 0.04, 0.16)
        abstract_bonus = 0.08 if paper.abstract else 0
        citation_bonus = min((paper.citation_count or 0) / 1000, 0.08)
        paper.relevance_score = min(
            1, lexical * 0.72 + source_bonus + abstract_bonus + citation_bonus
        )
    return sorted(
        candidates, key=lambda item: (item.relevance_score, item.citation_count or 0), reverse=True
    )


def reconstruct_search_audit(candidates: list[CandidatePaper]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], set[str]] = {}
    for paper in candidates:
        sources = paper.sources or ["unknown"]
        queries = paper.queries or ["(query unavailable in checkpoint)"]
        for source in sources:
            for query in queries:
                grouped.setdefault((source, query), set()).add(paper.canonical_id)
    return [
        {
            "source": source,
            "query": query,
            "count": len(paper_ids),
            "warning": "Reconstructed from checkpointed candidate provenance",
        }
        for (source, query), paper_ids in sorted(grouped.items())
    ]


def should_stop(
    previous_high_ids: set[str],
    current: RoundAnalysis,
    previous_coverage: float,
    total_axes: set[str],
) -> tuple[bool, dict[str, float | int]]:
    current_high = set(current.high_relevance_ids)
    new_high = len(current_high - previous_high_ids)
    coverage = len(set(current.covered_axes)) / max(1, len(total_axes | set(current.covered_axes)))
    gain = max(0, coverage - previous_coverage)
    return new_high < 3 and gain < 0.05, {
        "new_high_relevance": new_high,
        "coverage": round(coverage, 4),
        "coverage_gain": round(gain, 4),
    }


def ground_analysis(analysis: RoundAnalysis, candidates: list[CandidatePaper]) -> RoundAnalysis:
    allowed_urls = {url for paper in candidates for url in (paper.url, paper.pdf_url) if url}
    allowed_ids = {paper.canonical_id for paper in candidates}
    grounded_cells = []
    for cell in analysis.comparison_cells:
        urls = sorted(set(cell.evidence_urls) & allowed_urls)
        if urls and cell.paper_id in allowed_ids:
            grounded_cells.append(cell.model_copy(update={"evidence_urls": urls}))
    grounded_opportunities = []
    for opportunity in analysis.opportunities:
        evidence = sorted(set(opportunity.novelty_evidence) & allowed_urls)
        if evidence:
            grounded_opportunities.append(
                opportunity.model_copy(update={"novelty_evidence": evidence})
            )
    return analysis.model_copy(
        update={
            "comparison_cells": grounded_cells,
            "opportunities": grounded_opportunities,
            "high_relevance_ids": sorted(set(analysis.high_relevance_ids) & allowed_ids),
        }
    )


def ground_presentation(
    presentation: ReportPresentation,
    problems: list[ProblemStatement],
    candidates: list[CandidatePaper],
    rounds: list[RoundAnalysis],
) -> ReportPresentation | None:
    allowed_evidence_ids = {
        evidence.id for problem in problems for evidence in problem.evidence
    }
    allowed_paper_ids = {paper.canonical_id for paper in candidates}
    allowed_urls = {
        url
        for paper in candidates
        for url in (paper.url, paper.pdf_url)
        if url
    }
    allowed_urls.update(
        url
        for result in rounds
        for cell in result.comparison_cells
        for url in cell.evidence_urls
    )
    allowed_urls.update(
        url
        for result in rounds
        for opportunity in result.opportunities
        for url in opportunity.novelty_evidence
    )

    findings = []
    for finding in presentation.key_findings:
        evidence_ids = list(
            dict.fromkeys(item for item in finding.pdf_evidence_ids if item in allowed_evidence_ids)
        )
        urls = list(dict.fromkeys(item for item in finding.source_urls if item in allowed_urls))
        if evidence_ids or urls:
            findings.append(
                finding.model_copy(
                    update={"pdf_evidence_ids": evidence_ids, "source_urls": urls}
                )
            )

    themes = []
    for theme in presentation.themes:
        paper_ids = list(
            dict.fromkeys(item for item in theme.paper_ids if item in allowed_paper_ids)
        )
        if paper_ids:
            themes.append(theme.model_copy(update={"paper_ids": paper_ids}))

    ideas = []
    for idea in sorted(presentation.ideas, key=lambda item: item.priority):
        urls = list(dict.fromkeys(item for item in idea.evidence_urls if item in allowed_urls))
        if urls:
            ideas.append(idea.model_copy(update={"evidence_urls": urls}))

    if not findings or not ideas:
        return None
    return presentation.model_copy(
        update={"key_findings": findings, "themes": themes, "ideas": ideas}
    )


def ground_problem(problem: ProblemStatement, blocks: list[DocumentBlock]) -> ProblemStatement:
    block_map = {block.id: block for block in blocks}
    supplied = {item.id: item for item in problem.evidence}

    def normalized_text(value: str) -> str:
        return " ".join(value.split())

    evidence_aliases: dict[str, str] = {}
    for evidence_id, evidence in supplied.items():
        if evidence_id in block_map:
            evidence_aliases[evidence_id] = evidence_id
            continue
        excerpt = evidence.text.strip()
        if not excerpt:
            continue
        exact_matches = [block.id for block in blocks if excerpt in block.text]
        if len(exact_matches) == 1:
            evidence_aliases[evidence_id] = exact_matches[0]
            continue
        normalized_excerpt = normalized_text(excerpt)
        if len(normalized_excerpt) >= 40:
            normalized_matches = [
                block.id
                for block in blocks
                if normalized_excerpt in normalized_text(block.text)
            ]
            if len(normalized_matches) == 1:
                evidence_aliases[evidence_id] = normalized_matches[0]

    def resolve_id(value: str) -> str | None:
        if value in block_map:
            return value
        if value in evidence_aliases:
            return evidence_aliases[value]
        suffix_matches = [
            block_id
            for block_id in block_map
            if block_id.endswith(value) or value.endswith(block_id)
        ]
        return suffix_matches[0] if len(suffix_matches) == 1 else None

    def valid_ids(values: list[str]) -> list[str]:
        return list(
            dict.fromkeys(
                resolved for value in values if (resolved := resolve_id(value)) is not None
            )
        )

    updates: dict[str, Any] = {
        "background_evidence_ids": valid_ids(problem.background_evidence_ids),
        "task_evidence_ids": valid_ids(problem.task_evidence_ids),
        "algorithm_evidence_ids": valid_ids(problem.algorithm_evidence_ids),
        "formalization_evidence_ids": valid_ids(problem.formalization_evidence_ids),
    }
    element_fields = (
        "inputs",
        "outputs",
        "objectives",
        "constraints",
        "assumptions",
        "metrics",
    )
    for field in element_fields:
        grounded_elements = []
        for element in getattr(problem, field):
            evidence_ids = valid_ids(element.evidence_ids)
            if evidence_ids:
                grounded_elements.append(element.model_copy(update={"evidence_ids": evidence_ids}))
        updates[field] = grounded_elements

    required_narratives = (
        updates["background_evidence_ids"],
        updates["task_evidence_ids"],
        updates["algorithm_evidence_ids"],
    )
    if not all(required_narratives) or not updates["inputs"] or not updates["outputs"]:
        missing = []
        for name in ("background", "task", "algorithm"):
            if not updates[f"{name}_evidence_ids"]:
                missing.append(name)
        if not updates["inputs"]:
            missing.append("inputs")
        if not updates["outputs"]:
            missing.append("outputs")
        invalid_ids = sorted(
            {
                evidence_id
                for evidence_id in (
                    problem.background_evidence_ids
                    + problem.task_evidence_ids
                    + problem.algorithm_evidence_ids
                    + [
                        item
                        for field in element_fields
                        for element in getattr(problem, field)
                        for item in element.evidence_ids
                    ]
                )
                if resolve_id(evidence_id) is None
            }
        )
        raise ValueError(
            "Problem statement contains ungrounded required fields: "
            f"missing={','.join(missing)} invalid_evidence_ids={invalid_ids[:20]}"
        )
    if problem.formalization and not updates["formalization_evidence_ids"]:
        raise ValueError("Problem formalization is not grounded in PDF evidence")

    referenced_ids = set().union(*required_narratives, updates["formalization_evidence_ids"])
    for field in element_fields:
        for element in updates[field]:
            referenced_ids.update(element.evidence_ids)
    supplied_by_block: dict[str, Evidence] = {}
    for evidence_id, evidence in supplied.items():
        resolved = resolve_id(evidence_id)
        if resolved and resolved not in supplied_by_block:
            supplied_by_block[resolved] = evidence
    grounded_evidence = []
    for evidence_id in sorted(referenced_ids):
        block = block_map[evidence_id]
        proposed = supplied_by_block.get(evidence_id)
        excerpt = proposed.text.strip() if proposed else ""
        if not excerpt or excerpt not in block.text:
            excerpt = block.text[:4000]
        grounded_evidence.append(
            Evidence(
                id=evidence_id,
                paper_id=problem.paper_id,
                page=block.page,
                section=block.section,
                text=excerpt,
                bbox=block.bbox,
            )
        )
    updates["evidence"] = grounded_evidence
    return problem.model_copy(update=updates)


class AnalysisPipeline:
    def __init__(self, settings: Settings, repository: Any | None = None) -> None:
        self.settings = settings
        self.repository = repository
        self._active_job_id: str | None = None
        self.llm = ClaudeCodeClient(
            Settings.reveal(settings.DEEPSEEK_API_KEY) or "mock",
            binary=settings.CLAUDE_BIN,
            model=settings.CLAUDE_MODEL,
            effort=settings.CLAUDE_EFFORT,
            timeout_seconds=settings.CLAUDE_TIMEOUT_SECONDS,
            usage_callback=self._record_usage,
        )
        token = Settings.reveal(settings.MINERU_API_TOKEN)
        self.mineru = (
            MinerUClient(
                token,
                base_url=settings.MINERU_BASE_URL,
                model=settings.MINERU_MODEL,
                poll_seconds=settings.MINERU_POLL_SECONDS,
                timeout_seconds=settings.MINERU_TIMEOUT_SECONDS,
            )
            if token
            else None
        )
        sources = build_sources(settings)
        serper_key = Settings.reveal(settings.SERPER_API_KEY)
        tavily_key = Settings.reveal(settings.TAVILY_API_KEY)
        if serper_key and settings.SEARCH_PROFILE == "academic_web":
            sources.extend(
                [SerperSource(serper_key, scholar=True), SerperSource(serper_key, scholar=False)]
            )
        if tavily_key and settings.SEARCH_PROFILE == "academic_web":
            sources.append(TavilySource(tavily_key))
        self.retriever = LiteratureRetriever(
            sources, max_concurrency=settings.MAX_PROVIDER_CONCURRENCY
        )

    async def close(self) -> None:
        await self.retriever.close()
        if self.mineru:
            await self.mineru.close()

    async def _record_usage(self, usage: ProviderUsage) -> None:
        usage.estimated_cny = estimate_usage_cny(usage)
        if self.repository and self._active_job_id:
            await self.repository.record_usage(self._active_job_id, usage)
        else:
            ledger = self.settings.ARTIFACT_ROOT / "provider-usage.jsonl"
            payload = {
                "created_at": datetime.now(timezone.utc).isoformat(),
                "job_id": self._active_job_id,
                **usage.model_dump(mode="json"),
            }

            def append_usage() -> None:
                ledger.parent.mkdir(parents=True, exist_ok=True)
                with ledger.open("a", encoding="utf-8") as output:
                    output.write(json.dumps(payload, ensure_ascii=False) + "\n")

            await asyncio.to_thread(append_usage)

    async def _local_monthly_spend_cny(self) -> float:
        ledger = self.settings.ARTIFACT_ROOT / "provider-usage.jsonl"
        month = datetime.now(timezone.utc).strftime("%Y-%m")

        def read_spend() -> float:
            if not ledger.exists():
                return 0.0
            total = 0.0
            for line in ledger.read_text(encoding="utf-8").splitlines():
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if str(row.get("created_at", "")).startswith(month):
                    total += float(row.get("estimated_cny", 0))
            return total

        return await asyncio.to_thread(read_spend)

    async def _check_budget(self) -> None:
        if self.repository:
            spend = await self.repository.monthly_spend_cny()
        else:
            spend = await self._local_monthly_spend_cny()
        if spend >= self.settings.BUDGET_GUARD_CNY:
            raise BudgetBlocked(f"Monthly DeepSeek guard reached: CNY {spend:.2f}")

    async def _call_llm(self, prompt: str, model: type[Any], *, web: bool = False) -> Any:
        await self._check_budget()
        return await self.llm.structured(prompt, model, allow_web_search=web)

    async def _event(
        self, job_id: str, kind: str, message: str, data: dict[str, Any] | None = None
    ) -> None:
        LOGGER.info("job=%s %s: %s", job_id, kind, message)
        if self.repository:
            await self.repository.add_event(job_id, kind, message, data)

    async def _update(
        self, job_id: str, status: JobStatus, stage: str, progress: int, **extra: Any
    ) -> None:
        if self.repository:
            await self.repository.update_job(
                job_id,
                status=status.value,
                stage=stage,
                progress=progress,
                **extra,
            )

    async def _cancel_guard(self, job_id: str) -> None:
        if self.repository and await self.repository.is_cancelled(job_id):
            raise JobCancelled("Job was cancelled by the user")

    async def parse_document(
        self, file_path: Path, paper_id: str, title: str, workspace: Path
    ) -> DocumentIR:
        if not self.mineru:
            raise RuntimeError("MINERU_API_TOKEN is required for cloud parsing")
        archive_path = await self.mineru.extract(
            file_path, paper_id, workspace / "mineru-downloads"
        )
        document = normalize_mineru_zip(
            archive_path, workspace / "parsed" / paper_id, paper_id, title
        )
        if archive_path.name.endswith("-flash.zip"):
            document.parser = "mineru-flash"
            document.degraded = True
        return document

    async def extract_problem(self, document: DocumentIR) -> ProblemStatement:
        fragments = []
        for blocks in chunk_blocks(document.blocks):
            fragment = await self._call_llm(
                problem_statement_prompt(
                    document.paper_id, document.title, blocks_as_prompt(blocks)
                ),
                ProblemStatement,
            )
            fragments.append(ground_problem(fragment, blocks))
        if not fragments:
            raise ValueError(f"No readable blocks in {document.title}")
        if len(fragments) == 1:
            return fragments[0]
        merged = await self._call_llm(merge_problem_prompt(fragments), ProblemStatement)
        return ground_problem(merged, document.blocks)

    async def _discover_web(self, bundle: QueryBundle) -> WebDiscovery:
        if self.settings.SEARCH_PROFILE == "academic_only":
            return WebDiscovery(warnings=["Web retrieval disabled by academic_only ablation"])
        try:
            return await self._call_llm(
                web_discovery_prompt([item.query for item in bundle.queries]),
                WebDiscovery,
                web=True,
            )
        except Exception as error:  # WebSearch is fail-soft; structured APIs still run.
            LOGGER.warning("DeepSeek WebSearch unavailable: %s", error)
            return WebDiscovery(warnings=[f"DeepSeek WebSearch unavailable: {error}"])

    async def analyze_baseline(self, job: Job, file_path: Path) -> AnalysisReport:
        """Run the intentionally one-call whole-paper baseline used only by benchmark evaluation."""
        if len(job.files) != 1:
            raise ValueError("The one-call baseline accepts exactly one PDF")
        self._active_job_id = job.id
        artifact_root = self.settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"baseline-{job.id[:8]}-", dir=artifact_root))
        try:
            paper_id = job.files[0].sha256 or hashlib.sha256(file_path.read_bytes()).hexdigest()
            document = await self.parse_document(
                file_path, paper_id, job.files[0].original_name, workspace_path
            )
            report = await self._call_llm(
                baseline_report_prompt(job.id, document),
                AnalysisReport,
                web=True,
            )
            if len(report.problem_statements) != 1:
                raise ValueError("Baseline did not return exactly one problem statement")
            problem = report.problem_statements[0].model_copy(update={"paper_id": paper_id})
            problem = ground_problem(problem, document.blocks)
            candidates = merge_candidates(report.related_papers)
            rounds = [ground_analysis(round_result, candidates) for round_result in report.rounds]
            if len(rounds) != 1:
                raise ValueError("Baseline did not return exactly one analysis round")
            grounded = report.model_copy(
                update={
                    "job_id": job.id,
                    "problem_statements": [problem],
                    "joint_problem_statement": None,
                    "related_papers": candidates,
                    "rounds": rounds,
                    "parser_audit": [
                        {
                            "paper_id": paper_id,
                            "parser": document.parser,
                            "degraded": document.degraded,
                            "page_count": document.page_count,
                        }
                    ],
                    "source_coverage": {
                        "counts": source_coverage(candidates),
                        "queries": len(report.search_audit),
                        "rounds_completed": 1,
                        "visualizations": {},
                    },
                    "limitations_zh": DISCLAIMER_ZH,
                    "limitations_en": DISCLAIMER_EN,
                }
            )
            grounded.source_coverage["visualizations"] = report_visualization_data(grounded)
            return grounded
        finally:
            self._active_job_id = None
            shutil.rmtree(workspace_path, ignore_errors=True)

    async def analyze_files(
        self,
        job: Job,
        local_files: list[Path],
        *,
        persist: bool = True,
    ) -> AnalysisReport:
        if len(local_files) != len(job.files):
            raise ValueError("Local file count does not match job files")
        self._active_job_id = job.id
        artifact_root = self.settings.ARTIFACT_ROOT.resolve()
        artifact_root.mkdir(parents=True, exist_ok=True)
        workspace_path = Path(tempfile.mkdtemp(prefix=f"job-{job.id[:8]}-", dir=artifact_root))
        try:
            stored_state = (
                await self.repository.load_analysis_state(job.id)
                if self.repository and persist
                else {"problems": [], "candidates": [], "rounds": []}
            )
            stored_problem_rows = [
                row for row in stored_state["problems"] if row["paper_id"] != "__joint__"
            ]
            problems: list[ProblemStatement]
            parser_audit: list[dict[str, Any]] = []
            if len(stored_problem_rows) == len(job.files):
                problems = [
                    ProblemStatement.model_validate(row["content"]) for row in stored_problem_rows
                ]
                parser_audit = [
                    {
                        "paper_id": problem.paper_id,
                        "parser": "checkpoint",
                        "degraded": None,
                        "page_count": None,
                    }
                    for problem in problems
                ]
                await self._event(job.id, "resumed", "Reused checkpointed problem statements")
            else:
                await self._update(job.id, JobStatus.PARSING, "parsing", 5)
                await self._event(job.id, "stage", "Parsing PDFs with MinerU Precision Extract")
                problems = []
                for index, (job_file, file_path) in enumerate(
                    zip(job.files, local_files, strict=True)
                ):
                    await self._cancel_guard(job.id)
                    paper_id = job_file.sha256 or hashlib.sha256(file_path.read_bytes()).hexdigest()
                    if self.repository and persist and not job_file.sha256:
                        await self.repository.update_upload_hash(job_file.id, paper_id)
                    document = await self.parse_document(
                        file_path, paper_id, job_file.original_name, workspace_path
                    )
                    await self._event(
                        job.id,
                        "stage",
                        f"Extracting grounded problem statement from {job_file.original_name}",
                        {"page_count": document.page_count, "parser": document.parser},
                    )
                    problem = await self.extract_problem(document)
                    if (
                        not problem.is_computer_science
                        and problem.computer_science_confidence >= 0.8
                    ):
                        raise ValueError(
                            f"{job_file.original_name} is not classified as a computer-science paper "
                            f"(confidence={problem.computer_science_confidence:.2f})"
                        )
                    problems.append(problem)
                    parser_audit.append(
                        {
                            "paper_id": paper_id,
                            "parser": document.parser,
                            "degraded": document.degraded,
                            "page_count": document.page_count,
                        }
                    )
                    if self.repository and persist:
                        await self.repository.save_problem_statement(
                            job.id, paper_id, problem.model_dump(mode="json")
                        )
                    await self._event(
                        job.id,
                        "paper_parsed",
                        f"Parsed {job_file.original_name}",
                        {
                            "paper": index + 1,
                            "total": len(local_files),
                            "page_count": document.page_count,
                            "parser": document.parser,
                            "degraded": document.degraded,
                        },
                    )

            joint: JointProblemStatement | None = None
            if job.mode == AnalysisMode.MULTI:
                stored_joint = next(
                    (row for row in stored_state["problems"] if row["paper_id"] == "__joint__"),
                    None,
                )
                if stored_joint:
                    joint = JointProblemStatement.model_validate(stored_joint["content"])
                else:
                    joint = await self._call_llm(
                        joint_problem_prompt(problems), JointProblemStatement
                    )
                    if self.repository and persist:
                        await self.repository.save_problem_statement(
                            job.id, "__joint__", joint.model_dump(mode="json")
                        )

            await self._update(job.id, JobStatus.PROBLEM_READY, "problem_ready", 30)
            await self._event(job.id, "stage", "Problem statement ready")

            all_candidates = [
                CandidatePaper.model_validate(row["content"]) for row in stored_state["candidates"]
            ]
            rounds = [
                RoundAnalysis.model_validate(row["analysis"]) for row in stored_state["rounds"]
            ]
            search_audit = [
                {"round": row["round_number"], **audit_item}
                for row in stored_state["rounds"]
                for audit_item in (row.get("queries") or {}).get("audit", [])
            ]
            total_axes = {
                "task",
                "input",
                "output",
                "objective",
                "constraints",
                "algorithm",
                "dataset",
                "metric",
                "limitations",
            }
            previous = rounds[-1] if rounds else None
            previous_high_ids = {
                paper_id for item in rounds for paper_id in item.high_relevance_ids
            }
            previous_coverage = (
                len(set(previous.covered_axes)) / max(1, len(total_axes)) if previous else 0.0
            )

            for round_number in range(len(rounds) + 1, job.max_rounds + 1):
                await self._cancel_guard(job.id)
                await self._update(
                    job.id,
                    JobStatus.SEARCHING,
                    "searching",
                    30 + int((round_number - 1) / job.max_rounds * 45),
                    current_round=round_number,
                )
                if all_candidates and not rounds and round_number == 1:
                    checkpoint_queries = list(
                        dict.fromkeys(
                            query
                            for paper in all_candidates
                            for query in paper.queries
                            if query.strip()
                        )
                    )[:8]
                    if not checkpoint_queries:
                        checkpoint_queries = [problems[0].task_en]
                    bundle = QueryBundle(
                        round_number=round_number,
                        queries=[
                            SearchQuery(
                                query=query,
                                rationale="Recovered from the local retrieval checkpoint",
                            )
                            for query in checkpoint_queries
                        ],
                    )
                    previous_ids = {item.canonical_id for item in all_candidates}
                    audit = reconstruct_search_audit(all_candidates)
                    await self._event(
                        job.id,
                        "resumed",
                        f"Reused {len(all_candidates)} checkpointed candidates",
                    )
                else:
                    bundle = await self._call_llm(
                        query_prompt(problems, round_number, previous), QueryBundle
                    )
                    # The schema cannot enforce a prompt-derived number.
                    bundle.round_number = round_number
                    academic_task = self.retriever.retrieve(bundle)
                    web_task = self._discover_web(bundle)
                    (round_candidates, audit), web_discovery = await asyncio.gather(
                        academic_task, web_task
                    )
                    for paper in web_discovery.papers:
                        paper.sources = sorted(set(paper.sources + ["deepseek_websearch"]))
                        paper.queries = sorted(
                            set(paper.queries + web_discovery.searched_queries)
                        )
                    round_candidates = merge_candidates(round_candidates + web_discovery.papers)
                    round_candidates = rank_candidates(round_candidates, bundle)
                    previous_ids = {item.canonical_id for item in all_candidates}
                    all_candidates = rank_candidates(
                        merge_candidates(all_candidates + round_candidates), bundle
                    )
                    if self.repository and persist:
                        await self.repository.save_candidates(
                            job.id, [item.model_dump(mode="json") for item in all_candidates]
                        )
                    audit.extend(
                        {
                            "source": "deepseek_websearch",
                            "query": query,
                            "count": len(web_discovery.papers),
                            "warning": "; ".join(web_discovery.warnings) or None,
                        }
                        for query in web_discovery.searched_queries
                        or [item.query for item in bundle.queries]
                    )
                search_audit.extend({"round": round_number, **item} for item in audit)

                await self._update(job.id, JobStatus.ANALYZING, "analyzing", 55)
                analysis = await self._call_llm(
                    round_analysis_prompt(problems, all_candidates, previous), RoundAnalysis
                )
                analysis = ground_analysis(analysis, all_candidates)
                rounds.append(analysis)
                new_candidates = [
                    item for item in all_candidates if item.canonical_id not in previous_ids
                ]
                stop, stop_metrics = should_stop(
                    previous_high_ids, analysis, previous_coverage, total_axes
                )
                stop_metrics["new_candidates"] = len(new_candidates)
                await self._event(
                    job.id, "round_complete", f"Search round {round_number} complete", stop_metrics
                )
                if self.repository and persist:
                    await self.repository.save_search_round(
                        job.id,
                        round_number,
                        {"bundle": bundle.model_dump(mode="json"), "audit": audit},
                        analysis.model_dump(mode="json"),
                    )
                previous = analysis
                previous_high_ids |= set(analysis.high_relevance_ids)
                previous_coverage = float(stop_metrics["coverage"])
                if stop and round_number < job.max_rounds:
                    await self._event(
                        job.id,
                        "early_stop",
                        "Search converged; stopping before the configured round limit",
                        stop_metrics,
                    )
                    break

            await self._update(job.id, JobStatus.RENDERING, "rendering", 90)
            report = AnalysisReport(
                job_id=job.id,
                problem_statements=problems,
                joint_problem_statement=joint,
                related_papers=all_candidates,
                rounds=rounds,
                search_audit=search_audit,
                parser_audit=parser_audit,
                source_coverage={
                    "counts": source_coverage(all_candidates),
                    "queries": len(search_audit),
                    "rounds_completed": len(rounds),
                    "visualizations": {},
                },
                limitations_zh=DISCLAIMER_ZH,
                limitations_en=DISCLAIMER_EN,
            )
            await self._event(job.id, "stage", "Synthesizing the readable research brief")
            try:
                presentation = await self._call_llm(
                    report_presentation_prompt(problems, joint, all_candidates, rounds),
                    ReportPresentation,
                )
                presentation = ground_presentation(
                    presentation, problems, all_candidates, rounds
                )
                if presentation:
                    report = report.model_copy(update={"presentation": presentation})
                else:
                    await self._event(
                        job.id,
                        "presentation_fallback",
                        "Readable brief contained no grounded findings; using compatibility view",
                    )
            except Exception as error:  # Presentation is optional; core analysis is already complete.
                LOGGER.warning("Readable report synthesis unavailable: %s", error)
                await self._event(
                    job.id,
                    "presentation_fallback",
                    "Readable brief synthesis unavailable; using compatibility view",
                )
            report.source_coverage["visualizations"] = report_visualization_data(report)
            markdown = report_markdown(report)
            if self.repository and persist:
                await self.repository.save_report(job.id, report.model_dump(mode="json"), markdown)
            await self._event(job.id, "completed", "Report generated")
            return report
        finally:
            self._active_job_id = None
            shutil.rmtree(workspace_path, ignore_errors=True)

    async def run_job(self, job: Job) -> AnalysisReport:
        if not self.repository:
            raise RuntimeError("run_job requires a repository")
        download_root = self.settings.ARTIFACT_ROOT / "downloads" / job.id
        download_root.mkdir(parents=True, exist_ok=True)
        paths = []
        try:
            for job_file in job.files:
                destination = download_root / f"{job_file.id}.pdf"
                await self.repository.download_upload(job_file.storage_path, destination)
                validate_pdf(destination)
                paths.append(destination)
            return await self.analyze_files(job, paths)
        finally:
            shutil.rmtree(download_root, ignore_errors=True)
