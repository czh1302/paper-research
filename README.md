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
       └─ outbound Python worker (no public port)
            ├─ MinerU Precision Extract → Flash fallback
            ├─ Claude Code → DeepSeek V4 Flash (high effort)
            ├─ arXiv, OpenReview, OpenAlex, Crossref, DBLP
            └─ Serper Scholar/Web, Tavily, DeepSeek WebSearch
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

3. Add Edge Function secrets. Provider secrets do **not** belong in Supabase because only the
   worker needs them.

   ```bash
   npx supabase secrets set \
     TURNSTILE_SECRET_KEY=ROTATED_VALUE \
     PUBLIC_SITE_URL=https://YOUR_USER.github.io/SJTU_Task_final
   ```

4. Deploy the seven functions:

   ```bash
   for function_name in create-upload create-job cancel-job delete-job create-share revoke-share get-share; do
     npx supabase functions deploy "${function_name}"
   done
   ```

5. During local Edge Function development only, set `ALLOW_INSECURE_LOCAL_DEV=true`; never set it
   in the hosted project.

The database migration creates a private `papers` bucket, owner-only RLS, five monthly analysis
units, atomic job reservation, one active job per user, worker leases, early-stop refunds, provider
usage accounting, 24-hour upload expiry and 30-day revocable share expiry. Private reports remain
until their owner deletes the corresponding job.

### Administrator dashboard

The `/admin` route is available only to users explicitly listed in `public.admin_users`. It
provides a read-only, paginated view of every registered user and analysis job; it does not grant
cross-user mutation or PDF Storage access.

After deploying the latest database migration and web build, install the local QR helper and create
or refresh the administrator login QR code:

```bash
.venv/bin/pip install -e '.[admin]'
.venv/bin/python scripts/create-admin-qr.py
```

The QR code is written to `.artifacts/admin-login-qr.png` with mode `600`. It contains a single-use
Supabase Magic Link, not a permanent password. Treat the image as a temporary credential and
regenerate it by rerunning the command after it is used or expires. Neither the image nor the raw
link may be committed or sent to a third-party QR service.

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

## Worker and pilot

The worker polls Supabase every ten seconds and renews its lease during long MinerU/LLM calls:

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

- Round one searches exact task phrases, problem variables, methods, datasets, metrics and citation
  neighbors across every configured source.
- Subsequent rounds target uncovered axes and contradictions. The pipeline stops when it adds fewer
  than three high-relevance papers and coverage grows by less than five percentage points.
- DOI, arXiv, OpenReview, OpenAlex and normalized-title identifiers are used for deduplication.
- Every problem field stores PDF evidence IDs; every comparison cell stores external evidence URLs.
- “Nobody studied this” is never emitted. The report says only that no evidence was found within the
  recorded sources, queries and retrieval date.
- Reports support browser Print-to-PDF plus Markdown, JSON and CSV export. Read-only share links are
  hashed in the database, expire after 30 days, and can be revoked.

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
  Research Atlas deletes its local copy immediately, removes the Supabase source after a successful
  report, and retains a 24-hour expiry fallback for failed/interrupted work; MinerU's separate
  temporary cache follows MinerU policy.
- PDF parsing attribution is visible in the site footer as required by the MinerU license.
- DeepSeek spend is conservatively estimated from returned token usage. New calls stop at CNY 95,
  reserving CNY 5 under the CNY 100 monthly cap. Disable automatic account recharge as a second guard.
- The worker is single-concurrency. If the server reboots, rerun `scripts/run-worker-nohup.sh`; expired
  leases and idempotent database upserts let the job resume from stored problem/search checkpoints.
