# benchmark_v1

The manifest fixes ten computer-science papers before model evaluation: two each in networking,
systems/databases, AI/ML, security, and software engineering. PDFs are downloaded to ignored local
artifacts and are never redistributed by this repository.

```bash
python scripts/fetch-benchmark.py
paper-research analyze-local 2509.21074v4.pdf --rounds 1 --output .artifacts/benchmark/repllm
paper-research baseline-local 2509.21074v4.pdf --output .artifacts/benchmark/repllm-baseline
python benchmark/evaluate.py .artifacts/benchmark/repllm/report.json
python benchmark/judge.py \
  .artifacts/benchmark/repllm/report.json \
  .artifacts/benchmark/repllm-baseline/report.json
```

The evaluator measures structural and evidence proxies. It does not replace expert labels and
must not be described as proof of research novelty. Retrieval Recall@K is added only after a
versioned citation-label artifact has been generated from each paper's Related Work references;
the search agent must not see that label artifact.

The V4 Pro judge randomizes report order for each of three repetitions, strips run identifiers,
uses no tools, and writes an explicit automatic-proxy disclaimer. It is offline-only and is never
called by the public worker path.

Preview the budget-prioritized experiment matrix with `python benchmark/run_experiments.py`.
Nothing paid runs unless `--execute` is supplied. The `core` tier contains ten staged one-round
runs and ten one-call baselines; `extended` contains the three-repeat, 1-vs-3-round, and
academic-only-vs-web ablations for one networking and one AI paper. Local model usage is appended
to `.artifacts/provider-usage.jsonl`, and the same CNY 95 guard applies before every model call.
