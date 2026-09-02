# Research Atlas / 论文自动调研平台

Research Atlas turns one to five computer-science PDFs into evidence-linked problem statements,
multi-source related-work retrieval, comparison matrices, and calibrated research opportunities.
The public frontend is static; all private control data lives in Supabase; a single outbound worker
on the research server performs MinerU parsing and DeepSeek analysis.

> Security status: every credential previously pasted into chat must be revoked. This repository
> contains placeholders only and will refuse production work without newly rotated credentials.

## Architecture

```text
React/Vite on GitHub Pages
  └─ Supabase Auth + private Storage + Postgres/RLS + Realtime + Edge Functions
       ├─ outbound Python analysis worker (no public port)
            ├─ MinerU Precision Extract → Flash fallback
            ├─ Claude Code → DeepSeek V4 Pro/Flash (high effort)
            ├─ arXiv, OpenReview, OpenAlex, Crossref, DBLP
            └─ Serper Scholar/Web, Tavily, DeepSeek WebSearch
       └─ isolated experiment worker → private E2B CPU sandboxes
```

The model never receives Bash or file-write tools. Analysis calls expose no tools; the isolated
discovery call exposes only `WebSearch`. Provider HTTP calls and PDF handling remain deterministic
application code.

## Repository layout

- `apps/web`: bilingual React application, report visualizations, exports and GitHub Pages build.
- `services/worker`: typed async worker, MinerU/search/Claude clients, pipeline and tests.
- `supabase`: database migration with RLS/RPC plus signed-upload, job and share Edge Functions.
- `benchmark`: fixed ten-paper manifest and automatic proxy evaluator.
- `scripts`: secret initialization, worker launch and verification.

## Local setup

Prerequisites are Python 3.10+, Node 20+, a Supabase CLI, and Claude Code 2.1.248 or newer.

```bash
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/python -m pip install -e '.[dev]'
npm install
chmod 700 scripts/setup-secrets.sh scripts/start-worker.sh scripts/run-worker-nohup.sh scripts/verify.sh
```

Upgrade Claude Code in user space, then check the version:

```bash
claude install latest
claude --version
```

Do not copy real keys into `.env`. Run the non-echoing prompt after rotating all exposed keys:

```bash
scripts/setup-secrets.sh
set -a
source /home/czh/.config/paper-research/secrets.env
set +a
.venv/bin/paper-research doctor
```

The full provider set needs newly issued DeepSeek, MinerU, OpenAlex, Serper and Tavily keys.
OpenReview uses public read-only endpoints and never stores a personal password. arXiv, Crossref
and DBLP do not need API keys; Crossref only requires a contact email for its polite pool.

## Supabase

1. Create a free project in the closest available region and enable email verification.
   In Authentication → CAPTCHA, select Cloudflare Turnstile and enter the same rotated secret;
   registration passes the widget token directly to Supabase Auth.
2. Link the CLI and apply the migration:

   ```bash
   npx supabase login
   npx supabase link --project-ref YOUR_PROJECT_REF
   npx supabase db push
   ```

3. Add Edge Function secrets. DeepSeek credentials remain Worker-only. The terminal relay needs
   `E2B_API_KEY` to attach to an existing PTY; the sandbox-inference endpoint needs only the normal
   Supabase function environment (`SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`) plus the runtime
   flag. Neither function sends a long-lived credential into a sandbox.

   ```bash
   npx supabase secrets set \
     TURNSTILE_SECRET_KEY=ROTATED_VALUE \
     PUBLIC_SITE_URL=https://YOUR_USER.github.io/paper-research/ \
     ADMIN_REDIRECT_URL=https://YOUR_USER.github.io/paper-research/?admin=1 \
     E2B_API_KEY=ROTATED_VALUE \
     E2B_PILOT_ENABLED=false \
     E2B_MANUAL_EXPERIMENT_ENABLED=false \
     E2B_AUTO_EXPERIMENT_ENABLED=false \
     E2B_MAX_SPEND_USD=90 \
     E2B_ESTIMATED_COST_PER_SECOND_USD=0.000092 \
     E2B_RUN_TIMEOUT_SECONDS=3600 \
     EXPERIMENT_LLM_MAX_CNY_PER_RUN=5 \
     EXPERIMENT_ASSISTANT_MAX_CNY=20 \
     EXPERIMENT_LLM_MAX_CNY=40 \
     EXPERIMENT_LLM_GLOBAL_MAX_CNY=200
   ```

   Keep the same three runtime flags in the Worker secrets file and in Edge
   Function secrets. Changing only one side is intentionally insufficient: the
   API gate and the background executor must both authorize creation. The
   optional `EXPERIMENT_TERMINAL_WS_URL` is derived from `SUPABASE_URL` when it
   is not configured.

4. Deploy the Edge Functions. The experiment functions may be deployed while
   `E2B_PILOT_ENABLED=false`, `E2B_MANUAL_EXPERIMENT_ENABLED=false`, and
   `E2B_AUTO_EXPERIMENT_ENABLED=false`; enable the runtime first, then manual
   creation, and enable automatic primary-Idea experiments only after the
   manual smoke passes:

   ```bash
   for function_name in create-upload create-job cancel-job delete-job create-share revoke-share get-share admin-qr-login get-source-pdf get-report-section set-job-favorite admin-delete-job admin-delete-user list-report-experiments start-idea-experiment get-experiment-workspace read-experiment-file save-experiment-file move-experiment-file delete-experiment-file submit-experiment-action experiment-inference experiment-sandbox-inference create-terminal-ticket experiment-terminal-relay cancel-experiment delete-experiment download-experiment-repository get-experiment-artifact get-shared-experiment-artifact; do
     npx supabase functions deploy "${function_name}"
   done
   ```

5. During local Edge Function development only, set `ALLOW_INSECURE_LOCAL_DEV=true`; never set it
   in the hosted project.

The database migration creates a private `papers` bucket, owner-only RLS, atomic job creation, one
active job per user, worker leases, provider usage accounting, 24-hour upload expiry and 30-day
revocable share expiry. During the private beta there is no per-user or monthly analysis-unit quota.
Private reports remain until their owner deletes the corresponding job.

### Administrator dashboard

The `/admin` route is available only to users explicitly listed in `public.admin_users`. It
provides a paginated view of every registered user and analysis job. Administrators can submit
audited permanent deletion requests for non-admin users and jobs, while reports remain non-editable.
Cross-user experiment workspaces are review-only: administrators can inspect and cancel/delete but
cannot edit code, write to the terminal, chat with Flash, or start validation runs.

Password login and administrator QR login both open the new-analysis workspace. Supabase permits
multiple active sessions for the same account by default, and the frontend uses local-scope sign-out
so logging out on one browser or device does not terminate sessions on other devices.

After deploying the latest database migration and web build, install the local QR helper and create
or refresh the administrator login QR code:

```bash
.venv/bin/pip install -e '.[admin]'
.venv/bin/python scripts/create-admin-qr.py
```

The QR code is written to `.artifacts/admin-login-qr.png` with mode `600`. By default it contains a
permanent, reusable administrator bearer ticket. Each scan is audited and exchanged server-side for
a fresh Supabase session; the QR remains usable until a newer QR revokes it or an operator revokes it
in the database. Pass `--valid-days 1..30` only when a finite lifetime is preferred. Anyone holding a
copy can repeatedly obtain an administrator session, so the image must be protected exactly like a
permanent administrator password. Neither the image nor the raw ticket may be committed or sent to
a third-party QR service.

## Frontend

Copy the public-only template and run Vite:

```bash
cp apps/web/.env.example apps/web/.env.local
npm run dev
```

Fill only `VITE_SUPABASE_URL`, `VITE_SUPABASE_ANON_KEY`, and the Turnstile site key. GitHub Pages
uses the same names as repository **Variables**, not provider secrets. Enable Pages with “GitHub
Actions” as its source, then push `main` to run `.github/workflows/pages.yml`.

For production Turnstile, create a Managed widget for the GitHub Pages hostname, then run the
interactive helper. It hides the secret while typing and synchronizes the local files, Supabase
Auth/Edge Functions, and the GitHub Pages variable without printing secret values:

```bash
.venv/bin/python scripts/configure-production-turnstile.py
```

## Workers and E2B pilot

Production uses a systemd user service so the worker restarts after failures and server reboots.
The secrets initializer pins `CLAUDE_BIN` to `/home/czh/.local/bin/claude`; keep that installation at
Claude Code 2.1.248 or newer so systemd does not fall back to an older global binary.
Install it once, then enable lingering so it remains active without an interactive login session:

```bash
chmod 700 scripts/install-worker-service.sh
scripts/install-worker-service.sh
sudo loginctl enable-linger "${USER}"
systemctl --user status paper-research-worker.service
journalctl --user -u paper-research-experiment-worker.service -f
journalctl --user -u paper-research-worker.service -f
```

Build and verify the isolated CPU template before enabling experiments. The smoke creates one
short-lived sandbox, checks file/command/PTY access, outbound network filtering, pause/resume and
destruction, and never invokes a model or analyzes a paper:

```bash
set -a
source /home/czh/.config/paper-research/secrets.env
set +a
scripts/build-e2b-template.sh
.venv/bin/python scripts/e2b-smoke.py --template research-atlas-cpu-v1
```

Install the services and enable features in stages. First write the verified
template while every creation gate remains closed:

```bash
scripts/set-experiment-runtime-config.sh false research-atlas-cpu-v1 false false
scripts/install-worker-service.sh
```

After the migration and Edge Functions are deployed, enable the runtime on the
Worker and Edge sides, then the manual entry point, and only then automatic
primary-Idea experiments. Restart both Worker services after each local flag
change. Disabling all three flags on both sides is the immediate kill switch;
the additive database migration does not need to be rolled back.

New V4 reports freeze a Pro-authored `PilotSpecification` before code generation. The separate
experiment worker then uses Claude Code + V4 Flash to generate a layered repository, runs the
baseline/intervention against the immutable evaluator, archives every Git revision, and exposes a
private Monaco/xterm workspace. Experiment status and scientific outcome are separate: a valid
`not_support` result is not a system failure. The report remains complete while an experiment is
queued or automatically recovering. Historical V4 Ideas compile the same frozen specification
only after their owner explicitly starts an experiment.

Python uses the latest published E2B SDK (`2.46.0`); the Edge terminal relay pins the JavaScript
SDK to `2.46.1`. Sandboxes receive no E2B, DeepSeek, or Supabase service-role credential and no
input PDFs. A frozen pilot that explicitly declares managed inference receives only expiring,
one-shot tokens bound to that experiment run, specification hash, and schema. Subject sandboxes
expose no public port and can reach only package/resource hosts plus the project's exact Supabase
Edge hostname. The fresh evaluator sandbox has no token and runs with networking disabled.

`experiment-sandbox-inference` deliberately uses `verify_jwt=false`: E2B subject code has no user
JWT, so the function authenticates an atomically consumed one-shot SHA-256-backed credential on
submit and a separate opaque polling credential on GET. The client chooses the request ID and poll
credential before submit, so it can recover a committed request even when the POST response is lost
without replaying the one-shot credential. Deploy it only together with the migration that creates
the token/request ledger and service-role-only RPCs. Claude Code + V4 Flash runs on the local
experiment Worker; the Edge Function never receives the DeepSeek key.

The worker polls Supabase every ten seconds and renews its lease during long MinerU/LLM calls. For
temporary development sessions where systemd is unavailable, the legacy nohup launcher remains:

```bash
scripts/run-worker-nohup.sh
tail -f .artifacts/logs/worker.log
```

It can also run the supplied paper without Supabase after rotated DeepSeek and MinerU credentials
are configured:

```bash
set -a
source /home/czh/.config/paper-research/secrets.env
set +a
.venv/bin/paper-research analyze-local 2509.21074v4.pdf --rounds 1
```

Outputs are written under `.artifacts/local-report` and are ignored by version control. Never run
that command with the credentials exposed in chat.

## Search and report behavior

- Every DeepSeek reasoning call is launched through the pinned Claude Code CLI. Problem Statement,
  Problem Brief, Idea generation/revision and Idea review use V4 Pro; retrieval planning, WebSearch,
  paper profiling, landscape synthesis and report rendering use V4 Flash. Usage records include the
  pipeline stage, provider model, Claude Code transport and mapped Claude CLI model.
- With `IDEA_PIPELINE_V3=true`, each round extracts an evidence-reviewed Problem Brief, generates
  falsifiable Ideas, runs Idea-specific academic and web queries, parses selected open full text,
  and validates collision risk, feasibility and a first experiment.
- With `IDEA_PIPELINE_V4=true`, the worker first completes multi-platform retrieval, screens open
  papers, and builds full-text evidence profiles. Only then does it propose and review paper-core
  Ideas. V3 and V4 are mutually exclusive.
- V4 proposes eight diverse paper-level candidates only after the literature landscape is built,
  then evolves the strongest lineages for up to eight hostile review attempts. Numerical thresholds
  may be relaxed only after all strict attempts, but collision,
  full-text evidence, experiment completeness and computer-science relevance are never relaxed.
  A successful V4 task always contains at least one strict or explicitly low-confidence pass;
  otherwise the task fails or becomes budget-blocked instead of publishing a zero-Idea report.
- DOI, arXiv, OpenReview, OpenAlex and normalized-title identifiers are used for deduplication.
- Every problem field stores PDF evidence IDs. Idea claims and horizontal matrix rows retain only
  grounded candidate IDs and URLs; snippets and metadata cannot independently support a validated
  or promising Idea.
- V4 checkpoints retrieval, completed full-text profiles, the research landscape, every Idea draft
  and hostile review, the final presentation, report sections and cited-page previews. A restarted
  worker reuses completed stages and only renders missing preview pages, avoiding repeated MinerU,
  search and model charges outside an interrupted in-flight provider request.
- “Nobody studied this” is never emitted. The report says only that no evidence was found within the
  recorded sources, queries and retrieval date.
- The report UI uses Overview, Problem, Related Work and Research Ideas sections. V2 and V3 reports
  share the same routes. Internal evidence IDs and raw audit JSON stay out of the human view;
  citations open as source/page previews and V3 exposes an Idea-specific horizontal matrix.
- The whole site defaults to Chinese and can switch immediately to English. PDF and Markdown exports
  use the active language; JSON and CSV preserve the complete bilingual and audit data. Read-only
  share links are hashed in the database, expire after 30 days, and can be revoked.

To rerun only Gap, Idea and review stages from an existing V4 checkpoint without modifying the
source checkpoint or production database:

```bash
paper-research idea-replay \
  --checkpoint .artifacts/pipeline-checkpoints/JOB_ID.json \
  --output .artifacts/idea-quality-replay-new
```

The replay classifies papers with V4 Flash and runs every Gap, Idea, review and final-gate stage
with V4 Pro. Use a new output directory when changing replay versions or model policies.

## Verification and benchmark

```bash
scripts/verify.sh
python scripts/fetch-benchmark.py
```

The benchmark manifest fixes two papers in each of networking, systems/databases, AI/ML, security,
and software engineering. Downloaded PDFs and outputs stay under ignored artifacts. Automatic
metrics are proxies; V4 Pro may be used only by an offline, blinded evaluator and never by online
generation.

## Privacy, limits and operations

- Each PDF is at most 50 MB and 100 pages. MinerU Precision currently supports these limits, while
  Flash fallback applies only to files within its smaller service limit.
- The upload screen requires explicit consent because PDFs are sent to both Supabase and MinerU.
  Research Atlas deletes the worker's temporary copy immediately. PDFs bound to a job remain in
  private Supabase storage so owners and administrators can open cited pages and highlights; deleting
  the job permanently removes its input PDFs, cached open-access evidence PDFs, report and citations.
  Unbound uploads retain a 24-hour expiry; MinerU's separate temporary cache follows MinerU policy.
- PDF parsing attribution is visible in the site footer as required by the MinerU license.
- DeepSeek spend is conservatively estimated from returned token usage. New calls stop at CNY 95,
  reserving CNY 5 under the CNY 100 monthly cap. Disable automatic account recharge as a second guard.
- The worker is single-concurrency. If the server reboots, rerun `scripts/run-worker-nohup.sh`; expired
  leases and idempotent database upserts let the job resume from stored problem/search checkpoints.
