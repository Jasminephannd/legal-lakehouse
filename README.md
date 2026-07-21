# legal-lakehouse

A serverless medallion-architecture data pipeline over the [Open Australian Legal Corpus](https://huggingface.co/datasets/isaacus/open-australian-legal-corpus). It samples ~2,000 legal documents from Hugging Face into an S3 bronze layer, validates and partitions them into silver Parquet via a containerised Lambda, and models them into a dbt star schema in gold — queryable in Athena. Every piece of infrastructure is defined in Terraform; nothing was clicked in the console.

Built to demonstrate the stack Australian data-engineering roles actually ask for: SQL, Python, AWS (S3/Glue/Lambda/Athena), dimensional modelling, dbt, medallion architecture, IaC, and CI/CD.

> **Current state:** the pipeline runs end to end — 2,000 documents through bronze → silver → gold, reconciling exactly, with **43 unit tests and 44 dbt tests** passing. CI runs on every PR; CD deploys on merge to `main` via GitHub OIDC with no long-lived AWS credentials.
>
> One known defect is open and deliberately visible rather than hidden: two of 764 decisions fail court derivation and are flagged at `warn` severity. See *Data quality*.

![Architecture](docs/architecture.svg)

---

## Quickstart

Requires Python 3.11+, Terraform ≥1.10, Docker, and AWS credentials for `ap-southeast-2`.

**Start here if you're forking.** S3 bucket names are globally unique, so the defaults below will not be available to you. Change all three, and note that `infra/main/versions.tf`'s `backend` block **cannot use a variable** — that bucket name is a literal and must be edited by hand to match:

| Where | What to change |
|---|---|
| `infra/bootstrap/variables.tf` | `state_bucket_name` |
| `infra/main/versions.tf` | the `backend "s3"` `bucket` literal — must match the above exactly |
| `infra/main/variables.tf` | `state_bucket_name`, `data_bucket_name`, `github_repo*` |

```bash
# 0. Environment. Nothing below works without these.
python -m venv .venv
.venv\Scripts\Activate.ps1          # PowerShell. NOT `activate` — that resolves to
                                     # activate.bat, which sets vars in a cmd subprocess
                                     # and exits, silently doing nothing.
                                     # macOS/Linux: source .venv/bin/activate
pip install -r requirements.txt

$env:DATA_BUCKET           = "<your-data-bucket>"
$env:ATHENA_S3_STAGING_DIR = "s3://<your-data-bucket>/athena-results/"
$env:HF_TOKEN              = "hf_..."   # optional; avoids Hugging Face rate limiting

# 1. Bootstrap remote state (once, ever). Local state by design — this
#    creates the bucket that everything else stores its state in.
cd infra/bootstrap && terraform init && terraform apply

# 2. Create the ECR repository BEFORE pushing to it.
#    parser_image_tag has no default (deliberately — it stops you applying
#    against an image that doesn't exist), so pass a placeholder here.
cd ../main && terraform init
terraform apply -target=aws_ecr_repository.parser -var="parser_image_tag=placeholder"

# 3. Build and push the parser image.
#    --provenance=false --sbom=false is MANDATORY: Buildx's default
#    attestations produce an OCI image index, which Lambda cannot run.
ACCOUNT=$(aws sts get-caller-identity --query Account --output text)
REGISTRY=$ACCOUNT.dkr.ecr.ap-southeast-2.amazonaws.com
aws ecr get-login-password --region ap-southeast-2 \
  | docker login --username AWS --password-stdin $REGISTRY

SHA=$(git rev-parse --short HEAD)
cd ../.. && docker buildx build --platform linux/arm64 -f src/parser/Dockerfile \
  -t $REGISTRY/legal-lakehouse-parser:$SHA \
  --provenance=false --sbom=false --push .

# 4. Deploy everything else.
cd infra/main && terraform apply -var="parser_image_tag=$SHA"

# 5. Ingest → bronze. The Lambda fires automatically on upload.
#    Takes ~10 minutes and streams ~50k records from Hugging Face to
#    sample 2,000. Writes to bronze/ingest_date=<today>/.
cd ../.. && python -m src.ingest.run --bucket $env:DATA_BUCKET

# 6. Register partitions, then build gold.
#    Run the MSCK in the Athena console (workgroup: legal-lakehouse).
#    MSCK REPAIR TABLE legal_lakehouse.silver_judgments;
cd dbt
$env:DBT_PROFILES_DIR = "$PWD"      # profiles.yml lives in dbt/, not ~/.dbt/
dbt deps && dbt build
```

**Two operational rules that aren't obvious:**

- **Re-run `MSCK REPAIR TABLE` after any new ingest.** Partitions are discovered, not automatic. Skipping it produces a *green* dbt run over an empty or partial table — see *Data quality*.
- **After changing an incremental model's SQL, run `dbt build --full-refresh` once.** The incremental filter is about new data; it will not recompute rows written by the old logic. CD exposes this as a `dbt_full_refresh` workflow input.

Tests run with no AWS access at all:

```bash
pytest -v      # 43 tests
ruff check src tests
```

---

## Layer contract

| | **Bronze** | **Silver** | **Gold** |
|---|---|---|---|
| **Format** | Gzipped JSONL, 250 records/file | Parquet, Snappy | Parquet via Athena CTAS |
| **Partitioning** | `ingest_date=YYYY-MM-DD` | `jurisdiction=X/year=Y` (Hive) | `jurisdiction_code/year_partition` on the fact |
| **Mutability** | Immutable, append-only | Overwritten by deterministic key | Incremental merge on `doc_id` |
| **Schema enforcement** | None — raw as pulled | Pydantic `ParsedDoc`; failures → `rejected/` | dbt tests: `not_null`, `unique`, `accepted_values`, `relationships` |
| **Retention** | 30-day lifecycle expiry | Indefinite | Indefinite |
| **Written by** | `src/ingest/run.py` | Parser Lambda | dbt |
| **Consumed by** | The parser Lambda only | dbt, Athena | Analysts, BI |

Bronze expires after 30 days on purpose: it's a re-fetchable cache of a public dataset, not a system of record. Silver can always be rebuilt from a fresh pull.

---

## Data model

Star schema, grain stated explicitly on every model.

- **`fct_judgment`** — one row per legal document. FKs to all three dimensions, degenerate dimensions (`doc_id`, `citation`, `source_url`), measures (`text_length`, `word_count`). Materialised incrementally on `unique_key='doc_id'`.
- **`dim_jurisdiction`** — one row per jurisdiction. Federal/state/territory split.
- **`dim_court`** — one row per court, with `court_level` (appellate / first_instance / tribunal) inferred from name patterns.
- **`dim_date`** — daily spine 1900–2030 via `dbt_utils.date_spine`, with decade/quarter/weekend attributes.
- **`agg_judgments_by_court_year`** — mart: counts and length statistics per court and year.

All dimensions use **surrogate keys** (`dbt_utils.generate_surrogate_key`), not natural keys. If the source recodes a jurisdiction, every fact row referencing it would break under natural keys; a hash insulates the warehouse from source-system churn and keeps every FK column a uniform type.

Two dimensions carry **explicit unknown members**: `dim_court` has a "Not applicable" row (legislation has no court) and `dim_date` has an "unknown" row (~a fifth of the corpus has no parseable date). Facts point at those rather than carrying null FKs — which is what lets the `relationships` tests be strict rather than tolerating nulls.

---

## Example queries

Real output from the deployed warehouse (2,000 documents), not illustrative.

### Busiest courts by decade

Exercises the full join path — fact → `dim_court` → `dim_date`.

```sql
SELECT c.court_name, c.court_level, (d.year / 10) * 10 AS decade,
       count(*) AS judgments, round(avg(f.text_length)) AS avg_chars
FROM fct_judgment f
JOIN dim_court c ON f.court_key = c.court_key
JOIN dim_date  d ON f.date_key  = d.date_key
WHERE c.court_level <> 'not_applicable'
GROUP BY 1, 2, 3 ORDER BY judgments DESC LIMIT 10;
```

| court_name | court_level | decade | judgments | avg_chars |
|---|---|---|---|---|
| Federal Court of Australia | first_instance | 2010 | 102 | 40,759 |
| Federal Court of Australia | first_instance | 2000 | 92 | 29,675 |
| High Court of Australia | appellate | *(none)* | 65 | 44,318 |
| Supreme Court of New South Wales | first_instance | 2010 | 59 | 42,813 |
| Federal Court of Australia | first_instance | 2020 | 49 | 68,491 |
| Supreme Court of New South Wales | first_instance | 2000 | 42 | 35,051 |
| Federal Court of Australia | first_instance | 1990 | 36 | 34,322 |
| Supreme Court of New South Wales | first_instance | 2020 | 25 | 36,274 |
| Land and Environment Court of New South Wales | first_instance | 2010 | 22 | 42,364 |
| Industrial Relations Commission of New South Wales | first_instance | 2000 | 18 | 59,719 |

Two things worth reading off this rather than skipping past:

**Average decision length roughly doubles from the 1990s to the 2020s** for the Federal Court (34k → 68k characters). Consistent with the general trend toward longer written reasons, and a plausible hook for the semantic-search phase — longer documents are exactly the ones where retrieval beats keyword search.

**Every High Court row has a null decade.** All 65 High Court decisions resolved to `dim_date`'s unknown member, meaning none carried a parseable date. That is a source-data property (the High Court scrape populates `date` inconsistently), not a parser bug — but it is precisely the kind of thing an `INNER JOIN` to `dim_date` would have hidden by dropping the rows entirely. The LEFT JOIN in `fct_judgment` is why they appear here at all, visibly incomplete rather than silently absent.

### Document mix by jurisdiction

```sql
SELECT j.jurisdiction_name, j.jurisdiction_level,
       count(*) FILTER (WHERE f.doc_type = 'decision') AS decisions,
       count(*) FILTER (WHERE f.doc_type = 'primary_legislation') AS primary_leg,
       count(*) FILTER (WHERE f.doc_type = 'secondary_legislation') AS secondary_leg,
       count(*) FILTER (WHERE f.doc_type = 'bill') AS bills,
       count(*) AS total
FROM fct_judgment f
JOIN dim_jurisdiction j ON f.jurisdiction_key = j.jurisdiction_key
GROUP BY 1, 2 ORDER BY total DESC;
```

| jurisdiction_name | level | decisions | primary_leg | secondary_leg | bills | total |
|---|---|---|---|---|---|---|
| New South Wales | state | 331 | 63 | 33 | 0 | 427 |
| Commonwealth of Australia | federal | 398 | 0 | 0 | 0 | 398 |
| Queensland | state | 0 | 133 | 108 | 44 | 285 |
| Western Australia | state | 0 | 146 | 139 | 0 | 285 |
| South Australia | state | 0 | 121 | 122 | 42 | 285 |
| Tasmania | state | 0 | 92 | 193 | 0 | 285 |
| Norfolk Island | territory | 35 | 0 | 0 | 0 | 35 |

**The stratification worked, and its limits are visible.** Four jurisdictions land on exactly 285 — the per-jurisdiction quota — confirming the cap is binding rather than decorative. NSW and the Commonwealth exceed it via the bounded top-up pass. Norfolk Island falls short at 35 because the corpus simply doesn't contain more.

**Queensland, WA, SA and Tasmania show zero decisions.** Not a pipeline defect: the corpus's only caselaw sources are `nsw_caselaw`, `federal_court_of_australia` and the High Court, so state caselaw outside NSW isn't in the source at all. Worth stating plainly, because a reader could otherwise take this table as evidence those states produce no case law.

### Unknown-member integrity

The bug that nearly shipped, expressed as a query.

```sql
SELECT c.court_name, f.doc_type, count(*) AS n
FROM fct_judgment f
JOIN dim_court c ON f.court_key = c.court_key
WHERE c.court_level = 'not_applicable'
GROUP BY 1, 2 ORDER BY n DESC;
```

| court_name | doc_type | n |
|---|---|---|
| Not applicable | secondary_legislation | 595 |
| Not applicable | primary_legislation | 555 |
| Not applicable | bill | 86 |
| **Not applicable** | **decision** | **2** |

The first three rows are correct by construction — legislation and bills have no court, so they resolve to the explicit unknown member rather than a null FK. They total 1,236, and with the two below, exactly the 1,238 rows that failed the `relationships` test before the surrogate-key fix.

**The last row is a genuine defect.** A `decision` should always have a court. Two records reached silver with a citation the parser couldn't resolve to a court abbreviation, so `court` came back `None` and they fell into the not-applicable bucket alongside legislation. This is *undercounting by silence*: no test fails, no error is logged, and the documents are still queryable — they're just wrong, and invisible unless you go looking. Fixing it means either tightening the citation regex or adding a test asserting `doc_type = 'decision' AND court_level = 'not_applicable'` returns zero rows. The second is the better first move, because it turns a silent defect into a loud one before deciding how to parse it. Left open deliberately and listed under *What I'd do next*.

---

## Design decisions and tradeoffs

**Lambda over Glue/PySpark.**
*Alternative:* a Glue Spark job.
*Why:* at 2,000 documents (~20 MB compressed), Spark is pure overhead — cluster startup alone exceeds the entire Lambda runtime. I'd switch when any of these hit: a single batch exceeding Lambda's 10 GB memory ceiling, per-batch runtime approaching the 15-minute timeout, or any transformation requiring a shuffle (joins/aggregations across partitions), which Lambda can't do without pulling everything into one invocation. Roughly: low hundreds of thousands of documents, or the first genuine cross-partition join.

**Athena over Redshift.**
*Alternative:* a Redshift cluster or Serverless workgroup.
*Why:* the query pattern is infrequent and analytical over data already in S3. Athena has no idle cost; Redshift Serverless bills a minimum RPU-hour and provisioned Redshift bills continuously. At this volume Athena costs cents. Redshift becomes right when queries are frequent enough that per-query scanning costs exceed a cluster's fixed cost, or when concurrency/joins need a real query planner over sorted, distributed storage.

**One bucket with prefixes over three buckets.**
*Alternative:* separate bronze/silver/gold buckets.
*Why:* one bucket keeps IAM policies short and readable — a single ARN with prefix conditions instead of three sets of grants. Prefix-scoped lifecycle rules give the independent retention that separate buckets would. Separate buckets earn their complexity when layers need genuinely different security boundaries (different accounts, different encryption keys, different compliance regimes) — not here.

**Incremental over full refresh on the fact table.**
*Alternative:* `materialized='table'`, rebuilt every run.
*Why:* full refresh would work fine at 2,000 rows and is simpler. But the incremental filter is the piece that has to be *correct* before scale makes it matter, and `unique_key='doc_id'` over a deterministic SHA-256 means reprocessing merges rather than duplicating — the warehouse inherits the same idempotency the storage layer already has. Building it now costs nothing; retrofitting it after the fact table matters is a migration.

**Deterministic `doc_id` = SHA-256 of source URL.**
*Alternative:* a UUID or row-number surrogate assigned at parse time.
*Why:* this is the single decision that makes the whole pipeline idempotent. Same source document → same ID → same S3 key → S3 overwrites instead of appending. Re-running the pipeline on the same bronze data is a no-op rather than a duplication event, and backfilling a partition is safe by construction. A random ID would make every re-run a data-quality incident.

**Container image Lambda over a zip.**
*Alternative:* a zip deployment package with a pyarrow layer.
*Why:* pyarrow alone exceeds Lambda's 250 MB unzipped limit. The container path also picks up Docker as a demonstrable skill and makes the runtime reproducible locally. Cost: cold starts are slower, and the ECR push is a genuine extra failure surface (see "Known issues").

**No Glue crawler.**
*Alternative:* scheduled crawler to infer the silver schema.
*Why:* crawlers cost money per run and drift — they'll happily infer a changed type and silently break downstream models. The Glue table is declared explicitly in Terraform, so schema changes go through code review like everything else.

**GitHub OIDC over stored AWS keys.**
*Alternative:* `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` as repo secrets.
*Why:* no long-lived credentials exist to leak or rotate. The trust policy is scoped to a single branch — a `:*` wildcard there would let a PR from any fork assume the deploy role, which is the most common way this gets misconfigured.
*Status:* working. Getting there took hours, because GitHub's real `sub` claim carries immutable numeric IDs that no documentation shows — see "Known issues". The trust policy now pins both claim forms, and the ID-bearing form is the stronger of the two: numeric IDs are immutable, so a deleted-and-recreated repo of the same name won't match. A wildcard was written as a diagnostic and reverted **without ever being applied**.

**No AWS credentials in PR CI.**
*Alternative:* `terraform plan` on PRs, posted as a comment.
*Why:* that's a genuinely nice touch, and I'd add it on a private repo. On a public one it means fork PRs can reach AWS, and the blast radius of a mistake in that trust policy is the whole account. `terraform validate -backend=false` and `dbt parse` catch the same class of error with zero credentials.

---

## Data quality

**Reconciliation.** `bronze == silver + rejected`, enforced two ways:
- `src/ops/reconcile.py` — the storage-side check (bronze manifest count vs. Athena silver count vs. rejected JSONL line count). `rejected/` has no Glue table over it, so this half can't be expressed in SQL.
- `dbt/tests/assert_fact_matches_silver_source.sql` — the warehouse-side check, running in CI on every `dbt build`.

**Result on the real ingest: 2,000 bronze → 2,000 silver → 0 rejected.**

Zero rejects is an honest result, not a passing grade: the Open Australian Legal Corpus is already curated and normalised by its publisher, so every record satisfies the `ParsedDoc` contract. That left the `rejected/` path completely untested against real infrastructure — an error path that has never run is not a proven error path.

So `src/ops/seed_reject_fixture.py` writes a deliberately malformed batch under its own `ingest_date` (so it never contaminates real counts), with one record per validator:

| Fixture record | Expected | Validator |
|---|---|---|
| valid control | parsed | — |
| empty text | **rejected** | `text_must_not_be_empty` |
| missing jurisdiction | **rejected** | `jurisdiction_must_be_present` |
| missing url | **rejected** | `source_url_must_be_present` |
| unknown doc_type | **rejected** | `doc_type_must_be_known` |
| malformed date | parsed, `year='unknown'` | by design — never a crash |

Result: 6 → 2 silver + 4 rejected. The valid control landing in silver alongside four rejects is the "one bad record must not kill the batch" guarantee, demonstrated rather than asserted.

**Known data quality characteristics of the source:**
- Roughly a fifth of documents have no parseable date and land in the `year=unknown` partition. This is a property of the corpus (much legislation carries no single date), not a parsing failure.
- `court` does not exist in the source. It's derived from the neutral citation (`[2013] NSWSC 1668` → "Supreme Court of New South Wales") via a lookup table covering the common Australian courts and tribunals. The table is **not exhaustive** — unmapped abbreviations fall through as the raw abbreviation rather than being dropped, so `dim_court` will contain some bare codes.
- The corpus is heavily skewed toward NSW caselaw (~half of all documents). The ingest deliberately stratifies by jurisdiction with a per-year cap to avoid a single-jurisdiction sample that would make the partitioning look pointless.
- **All 65 High Court decisions carry no parseable date** and resolve to `dim_date`'s unknown member. A property of the source scrape, not the parser — but visible only because the fact table LEFT JOINs `dim_date`; an inner join would have dropped the rows entirely.
- **Two of 764 decisions have no court** — a genuine defect, not a source characteristic. See *Example queries* above. Flagged at `warn` severity by `assert_decisions_have_a_court`.
- Four jurisdictions have **no caselaw at all** in the sample, because the corpus's only caselaw sources are `nsw_caselaw`, `federal_court_of_australia` and the High Court. Absence of Queensland/WA/SA/Tasmanian decisions is a source limitation, not a pipeline defect.

**Other checks:** dbt source freshness (`warn_after: 24h`, `error_after: 72h`), `not_null`/`unique` on `doc_id` at the staging layer so failures surface before the fact table, `relationships` tests on every FK, `accepted_values` on jurisdiction/doc_type/court_level, and a domain-specific singular test asserting no document is dated in the future.

**The check that catches a green build over an empty warehouse.** One run reported `PASS=38` with `fct_judgment` at 0 rows and `dim_court` at 1. Every `not_null` and `unique` test passed, because a test over zero rows finds zero violations — and `assert_fact_matches_silver_source` passed because `0 == 0`. The suite was made entirely of *"no row may violate X"* assertions, and **none of them requires that rows exist at all.**

`assert_gold_tables_are_not_empty` asserts the opposite direction: a row-count floor on every gold relation. Floors are deliberately low — a smoke test for "the pipeline ran", not an assertion about corpus size, which would break every time the sample is resized. `dim_court`'s floor is 2 rather than 1, because it always carries its "Not applicable" member and a floor of 1 would be satisfied by an empty corpus.

---

## Observability

Structured JSON logging throughout the parser — CloudWatch Logs Insights queries JSON natively, plain strings it cannot:

```
fields @timestamp, source_key, parsed_count, rejected_count, duration_ms
| filter event = "batch_completed"
| sort @timestamp desc
```

Custom metrics via **EMF** (Embedded Metric Format) — writing a specially-shaped JSON object to stdout, so CloudWatch extracts `RecordsParsed`, `RecordsRejected` and `ParseDurationMs` with no `PutMetricData` call, no added latency, and no extra IAM permission. `source_key` is deliberately a *property*, not a dimension: one metric stream per S3 object would explode cardinality and cost.

---

## Operations

**Backfill a single partition:**
```bash
python -m src.ops.backfill --jurisdiction new_south_wales --year 2019 --dry-run
python -m src.ops.backfill --jurisdiction new_south_wales --year 2019
```

Safe to run repeatedly — deterministic `doc_id`s mean identical output keys, so S3 overwrites rather than appending. Run it twice and the Athena row count is unchanged.

**Prove idempotency end-to-end:**
```bash
python -m src.ops.reinvoke_parser --bucket <data-bucket> --ingest-date 2026-07-20
```
Run twice; `parsed` and `distinct silver files written` are identical both times.

**Tear down:**
```bash
cd infra/main && terraform destroy
# infra/bootstrap has prevent_destroy on the state bucket — remove it deliberately if you mean it
```

---

## Cost

### Measured

Cost Explorer, `ap-southeast-2`, charge type **Usage** (i.e. excluding free-tier credits, so this is what it would have cost on a paid account):

| Service | 19–21 Jul |
|---|---|
| S3 | $0.01 |
| ECR | <$0.01 |
| Athena | <$0.01 |
| Glue | <$0.01 |
| CloudWatch | <$0.01 |
| Lambda | <$0.01 |
| **Total** | **$0.01** |

**The entire build — 2,000 documents, ~40 Lambda invocations, dozens of Athena queries, several container builds — cost one cent.** Every line except S3 rounds to zero at two decimal places, which is a real result but a useless one for planning. The modelled breakdown below is where the actual structure is.

*Method note: filtering to charge type `Usage` matters. Cost Explorer nets credits out by default, which makes everything read $0.00 and tells you nothing.*

### Modelled unit economics

Volumes are measured; rates are list price at time of writing and should be re-checked.

| Component | Volume | Monthly at rest |
|---|---|---|
| S3 — bronze | 19.4 MB (8 gzipped JSONL batches) | ~$0.0005 |
| S3 — silver | ~30 MB (39 Snappy Parquet files) | ~$0.0008 |
| S3 — gold | <5 MB (Iceberg + Hive tables) | negligible |
| **ECR — parser image** | **~0.5 GB × up to 5 retained tags** | **~$0.25** |
| Glue Data Catalog | ~10 tables | $0 (first 1M objects free) |
| Lambda | ~40 invocations, 2048 MB, arm64 | $0 (within Always Free) |
| CloudWatch Logs | small, 14-day retention | ~$0.01 |
| Athena | per query, **10 MB minimum billed** | ~$0.00005/query |

**The most interesting finding: the container image costs roughly 100× more to store than the data it processes.** ECR at ~$0.10/GB-month against a ~500 MB image (pyarrow is most of it) dominates every other line, while the entire data lake is fractions of a cent. The `keep-last-5` ECR lifecycle policy is therefore not housekeeping — it is the single most effective cost control in the project. Without it, every commit to `main` would add half a gigabyte of billable storage forever.

**Athena's 10 MB per-query minimum means this dataset is below the granularity at which Athena billing resolves.** Silver is ~30 MB total, so a full scan and a single-partition scan cost nearly the same. Partitioning here is a correctness and latency decision, not yet a cost one — it becomes a cost decision somewhere around the 10 GB mark. Saying so is more honest than claiming partition pruning saves money at this scale, which it doesn't.

### Projected

Holding architecture constant and scaling only document count:

| | Documents | S3 | Athena (per full scan) | ECR | **Monthly total** |
|---|---|---|---|---|---|
| Today | 2,000 | ~$0.002 | $0.00005 | $0.25 | **~$0.26** |
| 10× | 20,000 | ~$0.02 | $0.0015 | $0.25 | **~$0.28** |
| 100× | 200,000 (near full corpus) | ~$0.15 | $0.015 | $0.25 | **~$0.42** |

**Cost is essentially flat under 100× growth**, because the dominant line — the container image — is independent of data volume. That is the serverless argument stated concretely: there is no idle compute to pay for, so scaling data 100× moves the bill by 16 cents.

The number that would actually change the picture is **query frequency**, not data volume. A dashboard refreshing every five minutes against a 6 GB silver layer would cost more than storing the entire corpus. If this grew, the first optimisation would be a scheduled aggregate table, not better partitioning.

### Design choices made for cost

- **Lambda at 2048 MB, not 1024 MB** — *cheaper*, not more expensive. Billing is per GB-second and CPU scales with memory, so finishing 3× faster at 2× memory wins.
- **arm64 over x86_64** — a further ~20% per-invocation saving, and the reason the Dockerfile targets `linux/arm64`.
- **`keep-last-5` on ECR** — see above; the highest-impact control in the project.
- **Lifecycle rules** — bronze expires at 30 days (it's a re-fetchable cache of a public dataset, not a system of record); Athena results at 7 days.
- **No Glue crawler** — crawlers bill per run and drift. The silver table is declared explicitly in Terraform.
- **A $10/month Budget with alerts at 50% and 80%, created before any infrastructure** — deliberately the first thing built, because it protects against mistakes made while writing the Terraform itself.

**Teardown:** `cd infra/main && terraform destroy` removes everything except the state bucket (`prevent_destroy`), taking the running cost to zero. Quickstart recreates it.

---

## Known issues and gotchas hit while building

Documented because each cost real time and none is obvious from the docs.

**GitHub's OIDC `sub` claim contains immutable numeric IDs that no documentation shows.** This was the single most expensive bug in the project — hours, not minutes — so it's documented in full.

*Symptom:* `cd.yml` fails at the credentials step with `Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity`, while every value on the AWS side verifies as correct.

*Cause:* the `sub` claim GitHub actually issues is

```
repo:Jasminephannd@57733436/legal-lakehouse@1306134894:ref:refs/heads/main
```

not the classic, universally-documented shape

```
repo:Jasminephannd/legal-lakehouse:ref:refs/heads/main
```

GitHub appends **immutable numeric IDs** to both the owner and the repository name. The trust policy, written from AWS's and GitHub's own documentation, never matched.

*Why it's so hard to spot:* the IDs are invisible everywhere you'd naturally look. `github.repository` in the workflow context renders as the plain `owner/repo`. The IAM trust policy, OIDC provider URL, audience, `ClientIDList`, and role ARN all verify as correct — because they are. CloudTrail logs nothing, because the request is rejected at token validation. The only way to see it is to **decode the issued JWT**, which is why `cd.yml` now has a permanent step that does exactly that.

*Fix:* list both `sub` forms in the trust policy (see `infra/main/oidc.tf`). The ID form is the stronger of the two — numeric IDs are immutable, so deleting and recreating a repo under the same name yields a different ID and won't match, closing a name-reuse hole the classic form leaves open.

*A false lead worth recording:* the deploy role was originally named `legal-lakehouse-github-deploy`, and there is a real open bug where a role name containing `github` breaks this action ([#1093](https://github.com/aws-actions/configure-aws-credentials/issues/1093), [#953](https://github.com/aws-actions/configure-aws-credentials/issues/953)). That looked like a perfect match for the symptoms and it was **not** the cause here. The role is still named `legal-lakehouse-ci-deploy` to avoid the known issue, but renaming it fixed nothing.

*The transferable lesson:* when every input verifies as correct, stop re-verifying inputs and go read what's actually on the wire.

| Checked | Result |
|---|---|
| Secret present in the workflow | `AWS_DEPLOY_ROLE_ARN` length 60 — correct ARN, no whitespace. Confirmed by a preflight step that prints length, never the value |
| Repository vs. Environment secret | Repository secret; the job declares no `environment:`, so an Environment secret would never resolve |
| `id-token: write` permission | Present at workflow level. Its absence produces a *different* error, and the log shows a token being issued |
| OIDC provider URL | `token.actions.githubusercontent.com` — exact |
| Provider `ClientIDList` | `["sts.amazonaws.com"]` — matches the audience the action requests |
| Trust policy `aud` condition | `StringEquals sts.amazonaws.com` — matches |
| Trust policy `sub` condition | `repo:<owner>/legal-lakehouse:ref:refs/heads/main` |
| Rendered workflow context | `github.repository` = `<owner>/legal-lakehouse` — appeared to match exactly |
| CA thumbprint | Replaced a placeholder with GitHub's two real intermediate thumbprints. No change (consistent with AWS's documented behaviour of ignoring it since July 2023) |
| CloudTrail `AssumeRoleWithWebIdentity` | No events in `us-east-1` or `ap-southeast-2` — the request is rejected at token validation, before account-level policy evaluation |
| Role name containing `github` | Renamed to `legal-lakehouse-ci-deploy`. A real known issue, but **not** the cause here |
| **Decoded JWT `sub` claim** | **`repo:<owner>@57733436/legal-lakehouse@1306134894:ref:refs/heads/main` — immutable IDs appended. This was the bug.** |

A wildcard trust policy (`repo:owner/repo:*`) was written as a diagnostic and reverted **without ever being applied** — loosening it would have masked the real cause and shipped the exact misconfiguration this project is meant to demonstrate avoiding.

**`dbt_utils.generate_surrogate_key` never returns NULL, so `coalesce` around it never fires.** The function substitutes an internal placeholder (`_dbt_utils_surrogate_key_null_`) for NULL inputs and hashes *that*, so this — which reads correctly and is a common pattern — is broken:

```sql
-- WRONG: the first argument is always a hash, just a hash of "null"
coalesce(
    {{ dbt_utils.generate_surrogate_key(['s.court_name']) }},
    {{ dbt_utils.generate_surrogate_key(["'__not_applicable__'"]) }}
)

-- RIGHT: coalesce the value, then hash it
{{ dbt_utils.generate_surrogate_key(["coalesce(s.court_name, '__not_applicable__')"]) }}
```

Every non-decision document (legislation and bills, which have no court) got `md5('_dbt_utils_surrogate_key_null_')` — a well-formed hash matching no row in `dim_court`. 1,238 of 2,000 fact rows had a broken FK.

*What makes this the most instructive failure in the project:* the output was **plausible**. Valid hashes, correct row count, model built cleanly, `not_null` passed, `unique` passed. Nothing looked wrong. Only the `relationships` test caught it — the test type most portfolio projects omit. It also validates giving `dim_court` an explicit `'Not applicable'` member instead of allowing NULL FKs: with a nullable FK the test would have been written permissively and 1,238 broken joins would have shipped silently.

**Fixing an incremental model's SQL does not fix the rows it already wrote.** The corrected model above then failed *the identical test with the identical row count*, because `fct_judgment` filters on `ingested_at > (select max(ingested_at) from {{ this }})`. That filter is about new **data**; it knows nothing about the model's **logic** changing. No new records had been parsed, so the merge matched nothing, logged `OK 0`, and left every stale row untouched.

The result is a log that actively misleads: a model reporting success next to a test failing on rows that model never touched, pointing at code that is already correct.

*The tell:* `OK 0` immediately followed by a test failure on thousands of rows. Those two numbers describe **different table states** — the model saw no input, the test read old output. A thirty-second query settles it, because the stale hash is a fingerprint of code that no longer exists:

```sql
SELECT f.court_key, count(*) FROM fct_judgment f
LEFT JOIN dim_court d ON f.court_key = d.court_key
WHERE d.court_key IS NULL GROUP BY 1;
-- f14cc5cdce0420f4a5a6b6d9d7b85f39 = md5('_dbt_utils_surrogate_key_null_')  -> stale rows
-- 6b0bcad613962dac31827e743eb75403 = md5('__not_applicable__')             -> dimension is wrong
```

`cd.yml` now exposes a `dbt_full_refresh` `workflow_dispatch` input for exactly this case. Rule of thumb: **new data → normal run; changed model SQL → one full refresh, then back to normal.**

**`s3_data_naming` must include `_unique` for Iceberg tables, or a full refresh deletes its own output.** Discovered by running the full refresh above. With plain `schema_table`, every build of a model resolves to the *same* S3 prefix. Harmless for Hive, where `DROP TABLE` is metadata-only — destructive for Iceberg, where `DROP TABLE` deletes the underlying files. On full refresh dbt creates the new table, then drops the old relation; sharing one prefix means that drop deletes the files it has just written:

```
ICEBERG_MISSING_METADATA: Metadata not found in metadata location
for table legal_lakehouse_prod_gold.fct_judgment
```

The Glue table survives, pointing at an S3 metadata file that no longer exists. `s3_data_naming: schema_table_unique` appends a UUID so each build writes to a fresh prefix.

*Two things this generalises to.* First, **a successful write step does not mean the table is readable** — `OK 2000` was true and useless, because the destructive operation ran afterwards during cleanup, outside what the model's own status covers. When a write succeeds and every subsequent read fails, suspect the teardown. Second, **a config that is correct for one table format can be silently wrong for another**: `schema_table` was fine across every Hive model and became data-destroying the moment one model gained `table_type='iceberg'` — but only on the code path (full refresh) that had never been exercised. The bug was introduced when Iceberg was adopted to enable `merge`, and simply waited.

**A deploy role cannot grant itself permissions through the pipeline it runs.** Terraform *refreshes* before it *plans*, and refresh uses the permissions the role holds **right now**. So when the thing being applied is the role's own policy, the run dies during refresh and the apply that would have granted the missing permissions never executes. Every retry fails identically, and pushing more fixes changes nothing — the code is correct, it just never reaches AWS.

Breaking the loop requires applying from **outside** the pipeline, with a higher-privilege identity:

```bash
cd infra/main
aws sts get-caller-identity      # confirm this is a human/admin, not the deploy role
terraform apply
```

This is the same bootstrap pattern as `infra/bootstrap` (which creates the state bucket that `infra/main`'s backend depends on) — a dependency that cannot be satisfied by the thing depending on it. In production the standard fix is to split the deploy role's *permissions* into a separate stack owned by a higher-privilege bootstrap role, so the CI role never manages its own grants.

**Terraform's refresh phase needs read permissions you never think to grant.** Related to the above, and the reason the policy is far longer than it looks like it should be. A least-privilege policy written from intent — "it creates a bucket, so it needs `CreateBucket`" — fails on refresh, because Terraform first reads the complete current state of every managed resource: tags, bucket policies, lifecycle rules, encryption settings, log-group descriptions, Lambda versions. `s3:GetBucketPolicy` is required even though this project never sets a bucket policy; Terraform reads it to confirm it's absent. Two non-obvious cases:

- **`logs:DescribeLogGroups` cannot be resource-scoped.** AWS evaluates it against `log-group::log-stream:` (note the empty segment), so a prefix-scoped ARN never matches and the error names a resource you didn't write.
- **`s3:DeleteObject` on the state bucket is not optional.** With `use_lockfile`, the lock *is* an S3 object. Without delete, every run leaves state locked and the next one fails — a slow-motion self-DoS.

**dbt-athena needs Glue permissions Terraform doesn't.** dbt manages tables and views *through* the Glue catalog, so it needs a wider set than the provider does. Table-version actions are the surprise: replacing a view makes dbt call `GetTableVersions` to find prior versions to clean up, and that call is authorised against the **catalog** ARN, not the table's.

**Athena rejects timezone-aware timestamps in Hive tables.** `from_iso8601_timestamp()` returns `timestamp(3) with time zone`, which Hive-format tables can't store: `Unsupported Hive type: timestamp(3) with time zone`. Wrap it — `cast(from_iso8601_timestamp(x) as timestamp)`. Safe here because the parser writes UTC exclusively, so there's no zone information to lose.

**Immutable tags make CD re-runs fail.** Images are tagged with the commit SHA and the ECR repo is `IMMUTABLE`, so re-running a workflow for the same commit tries to push an existing tag and fails. Since re-running is exactly what you do when a *later* step failed, `cd.yml` guards the build with an existence check. That's not a workaround: same commit means same image, so the tag existing is sufficient proof the correct image is present.

**Buildx + Lambda: `--provenance=false --sbom=false` is mandatory.** Buildx's default `--push` attaches provenance/SBOM attestations, which wrap the image in an OCI *image index*. Lambda only accepts a plain single-architecture manifest and fails with `InvalidParameterValueException: The image manifest, config or layer media type ... is not supported`. Worse, the push may report success — `aws ecr describe-images` showed the tag `ACTIVE` — while producing an artifact Lambda can't consume. Check `imageManifestMediaType` is `image.manifest`, not `image.index`.

**Terraform still requires an OIDC thumbprint.** AWS has verified GitHub's OIDC endpoint against its trusted root CAs since July 2023, so the thumbprint is ignored in practice — but `aws_iam_openid_connect_provider` still validates that `thumbprint_list` contains an exactly-40-character string.

**Partition keys must not be written into the Parquet file.** `jurisdiction` and `year` live in the S3 key path. Writing them into the file too duplicates data and risks a schema mismatch against a Glue table that declares them as `partition_keys`. The writer excludes them explicitly (`PARTITION_KEY_FIELDS` in `src/parser/handler.py`).

**The dataset moved and the split isn't `train`.** `umarbutler/open-australian-legal-corpus` now redirects to `isaacus/…`, and the dataset defines only a `corpus` split — `split="train"` fails outright.

**`dbt-athena-community` is deprecated** in favour of `dbt-athena`, which dbt Labs took over maintaining in late 2024. The community package is now a compatibility wrapper; installing both risks a conflict.

---

## What I'd do next

Scoped out deliberately, in the order I'd add them:

1. **Fix the two decisions with no court** (see *Example queries*). Two of 764 decisions carry a citation the parser can't resolve to a court abbreviation, so they sit in `dim_court`'s not-applicable member alongside legislation. `assert_decisions_have_a_court` already flags them at `warn` severity; the work is broadening the citation regex, then flipping that test to `error` so the class of bug can't silently return. Small, but it's the only known-wrong data in the warehouse, and the `warn`-not-`error` choice is deliberate: erroring would pressure the next person into a hasty regex change just to get CI green.
2. **Step Functions orchestration** — `Ingest → Map(parse) → dbt build → Notify`, with retries (`BackoffRate: 2.0`), a `Catch` routing to SNS, an SQS DLQ on the parser, and `MaxConcurrency` capped at ~5. Today ingest and parse are invoked manually; this is the clearest gap between "portfolio pipeline" and "production pipeline."
3. **CloudWatch dashboard and alarms in Terraform** — invocations/errors/duration p50 and p99, the EMF custom metrics, and S3 object counts per layer. Two alarms (parser error rate >5%, Step Functions failure) to SNS. The metrics are already being emitted; only the dashboard is missing.
4. **`dbt-expectations`** for distributional checks — `text_length` within reasonable bounds, jurisdiction distribution not shifting sharply between runs.
5. **Row-count anomaly detection** — fail if an ingest is under 50% of the trailing average. Crude, but it's the check that catches silently truncated upstream loads.
6. **A runbook** (`docs/runbook.md`) — what to do when the parser alarm fires, how to replay the DLQ, how to roll back a bad dbt deploy.
7. **Expand the court lookup table**, or replace it with a proper reference dataset. Deriving `court` from citation abbreviations works for the common cases and degrades gracefully, but it's the weakest link in `dim_court`.
8. **Move to Glue/Spark** if volume grows past the thresholds in the decisions section above — the parser is a pure function, so the transformation logic ports directly.

---

## Repository layout

```
infra/bootstrap/   Terraform state bucket (local state, applied once)
infra/main/        All other infrastructure (remote state, S3 native locking)
src/ingest/        HF corpus → bronze. sample.py is pure and unit-tested.
src/parser/        bronze → silver. parser.py is pure; handler.py adds S3 I/O.
src/ops/           reconcile, backfill, idempotency proof, reject fixture
dbt/               staging → dimensions → fact → mart
tests/             43 tests, no AWS credentials required
docs/              architecture diagram
```

The recurring pattern: **pure logic separated from I/O**. `sample.py`, `parser.py`, `observability.py` and the key-naming functions in `handler.py` contain no network or AWS calls, which is why the full suite runs in ~2 seconds with no credentials and no mocking — and why two real bugs (a stratification cap that silently under-filled samples, and partition keys leaking into Parquet) were caught by failing tests rather than in production.
