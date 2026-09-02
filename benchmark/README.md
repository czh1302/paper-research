# Teacher benchmark v1

This frozen six-paper suite evaluates the three teacher-requested stages—problem
statement extraction, related-work retrieval, and horizontal comparison—while
also allowing the current production pipeline to continue through Idea,
PilotSpecification, repository generation, and the automatic main-Idea experiment.

The suite deliberately does **not** publish a composite score. Measurements without
human gold labels are named `*_auto` or `*_proxy` and are reported per paper plus
median, IQR, macro/micro aggregation and blinded win/tie/loss. The `2509.21074v4`
case is marked development-exposed and is excluded from the held-out count.

Methodology follows these task-specific precedents:

- [RPC-Bench](https://aclanthology.org/2026.acl-long.1277/): correctness,
  completeness, and conciseness for paper comprehension.
- [ALCE](https://arxiv.org/abs/2305.14627): citation precision/recall and
  claim-level support.
- [TREC pooling](https://trec.nist.gov/data/reljudge_eng.html): a frozen pooled
  relevance set rather than an unverifiable open-Web
  recall denominator.
- [arXiv2Table](https://aclanthology.org/2026.acl-long.346/): schema coverage,
  unary cell fidelity, and pairwise relational consistency for literature-review
  tables.
- [DeepResearch Bench](https://arxiv.org/abs/2506.11763): effective citations,
  citation accuracy, and blinded report quality dimensions.

Silver relevance labels are produced three times from a source-only frozen rubric
and a source-hidden Top-20 result pool. Report comparison uses three independently
judged, counterbalanced A/B pairs (each evaluated in both orientations). Citation
deletion, citation swapping, and numeric contradiction perturbations must lower the
associated fidelity score; otherwise that proxy is explicitly marked unreliable.

Run or resume the unattended supervisor:

```bash
paper-research benchmark-run \
  --manifest benchmark/teacher_benchmark_v1.json \
  --owner-from-job 08f0ca6d-abcf-42a4-9b58-6ed07996d135 \
  --cold --include-baseline \
  --analysis-concurrency 2 --baseline-concurrency 2 --judge-concurrency 2 \
  --resume --output .artifacts/benchmark/teacher-v1
```

Read-only progress is available with:

```bash
paper-research benchmark-status --output .artifacts/benchmark/teacher-v1
```

The supervisor writes atomic `run-state.json`, `jobs.json`, `status.md`, and
`status.csv`. Once all six metric records exist it writes `summary.json`,
`summary.csv`, and `summary.md`, followed by `SUCCESS`, `DEGRADED`, or `INCOMPLETE`.

## Symmetric joint benchmark

`teacher_joint_v1.json` is an independent two-paper case.  A `cases` manifest
preserves the declared input order and sets `mode: "multi"` plus
`semantics: "symmetric"`; legacy manifests that use `papers` continue to run as
one-paper cases without conversion.

The joint supervisor reserves one production job in `waiting_resources`, but it
does not activate that job or launch its one-call baseline while the six
teacher-v1 production reports are incomplete.  Only after all six jobs are
`completed` and each has a report does it verify that the analysis queue has no active slots, reload both
analysis workers successfully, and only then activates the joint production job
and baseline.  A failed worker reload leaves the joint case waiting and is
retried by systemd; mixed worker versions are never activated deliberately.

Run or resume it with:

```bash
paper-research benchmark-run \
  --manifest benchmark/teacher_joint_v1.json \
  --owner-from-job 08f0ca6d-abcf-42a4-9b58-6ed07996d135 \
  --cold --include-baseline \
  --analysis-concurrency 2 --baseline-concurrency 1 --judge-concurrency 1 \
  --wait-for-benchmark-output .artifacts/benchmark/teacher-v1 \
  --reload-worker-service paper-research-worker.service \
  --reload-worker-service paper-research-worker-2.service \
  --resume --output .artifacts/benchmark/teacher-joint-v1
```

The baseline parses both PDFs separately and makes one Pro synthesis call.  Its
structured horizontal comparison must contain both inputs and external-paper
cells; a narrative-only comparison is invalid.  Joint metrics report each
input's problem quality separately, dual-input and agreement/difference/conflict
grounding, bridge-paper retrieval, and an explicit zero/one structured external
comparison presence score.  Every source claim carries its input paper ID so
that identical page numbers in the two PDFs remain attributable.
