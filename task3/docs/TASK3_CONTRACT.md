# Task 3 Contract — Search, Triage, PDF, PRISMA
**Author:** Dhruv Sood · **Date:** 2026-05-03 · **Repo:** Article_Finder · `task3/`

This contract is the spec the Task 3 implementation is bound to. The grader
should be able to read each section and run the listed test that proves it.

---

## 0. Substitutions & Limitations (read first)

### 0.0 Dependency setup: `atlas_shared`

Task 1 and the shared relevance/classification path depend on `atlas_shared`.
It must be supplied in exactly one of these supported ways:

1. installed import: `cd atlas_shared && pip install -e .`
2. explicit source path: `KA_ATLAS_SHARED_SRC=/path/to/atlas_shared/src`
3. sibling checkout: `Article_Finder`, `Knowledge_Atlas`, and `atlas_shared`
   checked out under the same parent directory.

The resolver order is installed package, then `$KA_ATLAS_SHARED_SRC`, then
sibling checkout. The Article Finder Task 2/3 fixtures bundle enough
constitutions/mechanisms for deterministic grading, but the full course
classification catalogue still lives in `atlas_shared`.

| Topic | Canonical | Substitute used | Justification |
|---|---|---|---|
| Lifecycle DB | `pipeline_lifecycle_full.db` (instructor-provided) | local `task3/data/pipeline_lifecycle_full.db` with the documented schema | The shipped file in `Knowledge_Atlas/data/ka_payloads/pipeline_lifecycle_full.db` is 0 bytes (verified). Local schema follows rubric §3A column-for-column; it's a drop-in replacement. |
| `scripts/coordination/lifecycle/schema.sql` | DDL referenced in rubric | re-implemented in `db_schema.py::SCHEMA_SQL` | File not present on this checkout. Schema mirrors the rubric §3A spec verbatim. |
| Search backends | SerpAPI primary + scholarly + paper-scraper + scidownl | all four wired; default demo run uses `mock` (deterministic, offline) | Real SerpAPI/scholarly/paperscraper calls work but cost credits. The mock backend produces a 60/20/10/10 mix so dedupe + Stage 1 + Stage 2 all exercise real branches. |
| `atlas_shared` constitutions | full constitution catalogue | only `SQ-ART-001 Nature & Attention` ships in `question_constitutions_starter.json` | data limitation; not a code limitation. Adding more constitutions changes verdicts without code changes. |
| **Article Eater handoff sink** | `data/handoff/*.json` read by the Eater (rubric `ka_track2_setup.html:101-102`: *"The Finder writes a well-defined handoff artefact (`data/handoff/*.json`) that the Eater reads; the contract between them is the only thing Track 2 needs to honour."*) | local writer `task3/ae_handoff.py` → `task3/data/handoff/<reference_id>.json`, plus a dedup probe `probe_pdf_against_article_eater()` that queries the local `pdf_identity_inventory` / corpus tables | The Eater repo and its inbox path live on the instructor VM, absent on this checkout. AF honors the **named contract** (the `data/handoff/*.json` artefact + schema, §0.1) so the bundle is drop-in when the Eater is mounted. AF does **not** run the Eater's pipeline — `track2_hub.html:102`: *"AF's contract with AE is the job bundle and its metadata, not the extraction result."* |
| **Abstract API clients** | `SemanticScholarClient` / `CrossRefClient` / `PubMedClient` in `Article_Eater/src/services/paper_fetcher.py` (rubric `t2_task3.html`) | local functions `fetch_s2()` / `fetch_crossref()` / `fetch_pubmed()` / `fetch_openalex()` in `abstract_collector.py` | The Article_Eater working tree is absent on this checkout (its `.git` checks out empty — verified). The local fetchers hit the same public endpoints (S2 graph API, CrossRef works, NCBI EFetch, OpenAlex) with the same DOI→abstract contract, gated behind `--enable-network` so the default run is deterministic/offline. Drop-in swap when the AE clients are importable. |

Default demo run command: `python3 task3/run_pipeline.py --backend mock --per-query 10 --top-n 10`.

Manual artifacts required: **none** for Task 3 — every check is automated by `task3/tests_task2_task3.py`.

---

## 0.1 Handoff Artefact (local schema)

**Status: documented LOCAL schema.** The rubric names the artefact path
(`data/handoff/*.json`, `ka_track2_setup.html:101-102`; the per-paper bundle is
called `paper.json` in `track2_hub.html:119`) but does **not** enumerate its
fields. AF therefore defines its own documented local schema below. `ae_handoff.py`
writes one JSON object per handed-off paper to `task3/data/handoff/<reference_id>.json`.
Fields AF cannot compute are emitted as `null` with a `source_note` — never invented.

| Field | Type | Source |
|---|---|---|
| `handoff_id` | string | `HANDOFF-<8 hex>` minted at write time |
| `article_id` | string | `article_references.reference_id` |
| `citation` | string \| null | `raw_citation` if present, else null |
| `title` | string \| null | `title_raw` |
| `doi` | string \| null | normalized `doi`, else null |
| `abstract` | string | **required** — handoff withheld if `MISSING_ABSTRACT` (the gate) |
| `article_type` | string \| null | classifier output if available, else null |
| `topic` | string \| null | matched question/topic |
| `subfocus_area` | string \| null | sub-topic if assigned, else null |
| `source_note` | string \| null | provenance: `discovered_via`, abstract_source, probe result |
| `handoff_status` | string | `written` on success |
| `blocked_reason` | string \| null | null on success; set when a candidate is withheld |
| `created_at` | datetime | ISO-8601 UTC |
| `updated_at` | datetime | ISO-8601 UTC |

**Gate (makes the abstract requirement real):** only rows in `v_acquisition_queue`
(`triage_decision='ACCEPT'`, which by construction excludes `MISSING_ABSTRACT`)
become handoff candidates, and `ae_handoff.py` re-asserts a non-empty `abstract`
before writing. A missing-abstract paper therefore **never** produces a
`data/handoff/*.json` file — proven by `tests_task2_task3.py`.

**Idempotency:** the writer skips a `reference_id` whose artefact already exists
and records each write once in the `handoff_log` table (`UNIQUE(reference_id)`).

**Out of AF scope (AE-owned):** `article_eater_running` / `article_eater_complete`
/ `article_eater_failed` are the Eater's downstream states. AF's terminal stage is
`handed_off`.

**AE boundary sentence:** this implementation writes and validates a local
Article Eater handoff artefact; it does not prove real Article Eater ingestion
unless a configured AE process consumes that artefact and reports success through
its own status surface.

`ae_handoff.deliver_to_ae()` is the delivery seam. It can attempt real delivery
in one of two configured modes; if neither is set, it returns an honest no-op
with `mode="local_substitute"`:

- `AE_INGEST_CMD="<cmd>"` — we run `<cmd> <artefact.json>`; AE consuming the
  artefact == the command exiting 0 (e.g. the instructor's
  `course_scaffolding.py ingest-handoff` or any AE ingest CLI).
- `AE_INBOX="<dir>"` — AE's watched inbox; the artefact is copied in, and with
  `AE_ACK_TIMEOUT>0` we poll for AE to consume it (file moved out, or a
  `<name>.ack` / `processed/<name>` marker).

Real AE integration therefore requires more than the local artefact: mount or
configure the real AE inbox/corpus inventory, deliver the artefact, and verify
that AE consumed it and reported success. Run the gated smoke test on a machine
that HAS Article Eater:

```bash
AE_INGEST_CMD="python3 /path/to/Article_Eater/scripts/course_scaffolding.py ingest-handoff" \
    python3 task3/ae_ingest_smoke.py      # verifies real AE ingestion only if AE consumes the artefact
# or
AE_INBOX=/path/to/Article_Eater/data/inbox AE_ACK_TIMEOUT=15 python3 task3/ae_ingest_smoke.py
```

On a checkout WITHOUT the AE repo (this one), `ae_ingest_smoke.py` SKIPs cleanly
(exit 0) and the local `ae_inbox_stub.py` remains the tested boundary. The seam
mechanics themselves are unit-tested offline (`tests_task2_task3.py`: command +
inbox + no-AE modes), so a grader can confirm the wiring works without AE present.
This makes the boundary a **runnable integration seam**, not just prose — but it
is honestly NOT a verified ingestion against the real Article Eater on this
checkout, because that repo is not present here.

---

## 1. Cardinal rules (rubric, non-negotiable)

1. **Every harvested reference lands in `article_references`.** No free-floating JSON. Free-floating outputs do not count for grading.
2. **Never download a PDF to decide relevance.** PDF cascade runs only after `triage_decision='ACCEPT'`.
3. **PRISMA counts come from a single SQL `GROUP BY` over `article_references`.** No parallel state.
4. **`SERPAPI_API_KEY` is read from the environment only.** Never logged, never committed. `.gitignore` blocks `.env`, `serpapi.key`, `policy_clearance.json`, `task3/data/*.db`.
5. **scidownl is gated by 4 conditions.** All four must hold or no call happens.
6. **Reproducibility:** Python ≥ 3.10. Mock backend is seeded by `gap_id` so runs are deterministic.

---

## A. Search runner contract

| Section | Spec |
|---|---|
| **Inputs** | `query_results.json` (Task 2 output) |
| **Auth** | `SERPAPI_API_KEY` from `os.environ.get("SERPAPI_API_KEY")` only. Refuses to start if backend=`serpapi` and the var is unset. |
| **Processing** | For each query, dispatch to one of 4 backends (`serpapi`, `scholarly`, `paperscraper`, `mock`). SerpAPI uses `engine='google_scholar'`. paperscraper uses the AI Citation form. |
| **Outputs** | (1) Rows in `article_references` (Contract B). (2) `task3/data/search_results.json` summary dump per run, keyed by `discovery_run_id`. |
| **Network discipline** | 15 s per-call timeout. Single try (no retry within a run; re-run the stage to retry). On HTTP error, the query is logged as `error` in `run_log.notes` and the loop continues. |
| **DOI extraction** | Regex `\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b` (case-insensitive) over `link` then `snippet`. Tested on 3 sample URLs in `tests_task2_task3.py`. |

### A.1 Success conditions

(a) SerpAPI engine is exactly `google_scholar`. (b) Each query is logged in `run_log.notes` with credits = 1 per call. (c) Zero-result queries are recorded (`run_log.notes.zero_results++`). (d) `discovered_via` is set correctly per backend: `serpapi_scholar`, `scholarly_search`, `paperscraper_search`, `mock_synthetic`. (e) Total planned searches stay under 250 / month (10 gaps × 1 query each = 10 credits per real run).

---

## B. article_references contract (rubric §3A)

| Column | Rule |
|---|---|
| `reference_id` | format `REF-YYYY-MM-DD-NNNNNN`, unique. Daily sequence resets at UTC midnight. |
| `doi` | normalised by `normalize_doi()` before insert: lowercase, strip `https://(dx.)?doi.org/`, strip leading `doi:`. |
| `title_normalized` | populated by `normalize_title()`: lowercase, strip punctuation, collapse whitespace. |
| `discovered_via` | comma-separated channel list. Initial value is the harvester tag; dedupe-on-insert appends new channels (rubric §3B "preserve multi-channel provenance"). |
| `discovered_query`, `discovery_run_id` | every row carries the originating query and run id. |
| `triage_stage` | starts at `'metadata_only'`; transitions logged to `lifecycle_transitions`. |
| `voi_score` | inherited from the gap that produced the query. |

### B.1 Insert rule (Contract 3 commitment, no weasel-words)

`search_runner.insert_or_dedupe()` ALWAYS performs one of three actions per candidate; "prepare insertion" is not an option:

1. **insert** new row, OR
2. **dedupe_doi** — existing row's `discovered_via` is UPDATEd to append the new channel, OR
3. **dedupe_title** — same as (2) but matched on `title_normalized` for DOI-less rows.

If the SQL fails (constraint violation, disk full, etc.) the candidate is logged in `run_log.notes` with `errors++` and explicitly NOT counted in the PRISMA `records_returned`.

### B.2 Transaction isolation

Every `insert_or_dedupe()` runs inside a single SQLite transaction with `BEGIN IMMEDIATE`. The transition log row + the `article_references` insert/update are atomic. On any exception, `ROLLBACK` and re-raise.

### B.3 Title fuzzy-match algorithm

When deduping a DOI-less candidate against an existing DOI-less row:

1. Both titles pass through `normalize_title()` (lowercase, strip punctuation, collapse whitespace).
2. Match if normalized titles are exactly equal.

(A future improvement is Levenshtein word-distance ≤ 1, but the implementation currently uses exact-equal on normalized titles. Documented limitation.)

### B.4 Dedupe schema

```sql
CREATE UNIQUE INDEX uq_ar_doi
    ON article_references(doi) WHERE doi IS NOT NULL AND doi <> '';
CREATE UNIQUE INDEX uq_ar_title_norm
    ON article_references(title_normalized) WHERE doi IS NULL OR doi = '';
```

These enforce the dedupe at the DB layer in addition to the code path; a bug in the dedupe helper cannot insert a real duplicate.

---

## C. Abstract collector contract (rubric §4C — Stage 2A)

| Section | Spec |
|---|---|
| **Inputs** | rows where `triage_stage='metadata_only'` AND `triage_decision IS NULL` (i.e. survivors of Stage 1) |
| **"Full abstract" defined** | text ≥ 120 characters from a structured source. SerpAPI snippets, even if longer, do NOT qualify by default — but the search-payload tier is allowed when the snippet ≥ 120 chars (mock-mode demo only; real runs disable this). |
| **Cascade order** | `search_payload` (snippets ≥ 120 chars, off in real mode) → Semantic Scholar → CrossRef → PubMed → OpenAlex |
| **Network discipline** | 15 s timeout per call. ≥ 3.5 s sleep between Semantic Scholar calls (≤ 20 req/min free-tier ceiling). 1 s between CrossRef. PubMed 3 req/sec. Single try. |
| **DOI vs. title strategy** | If DOI is present, try DOI lookup. If no DOI, try title search. **Ambiguous title rule:** the source returns a hit ONLY if exactly one match is returned at high confidence; on 0 or ≥ 2 candidates, fall through to the next source. |
| **Outputs** | `abstract`, `abstract_source ∈ {search_payload, s2, crossref, pubmed, openalex, none}`. |
| **Contact** | `CONTACT_EMAIL` env var; defaults to a generic project address. Used in CrossRef User-Agent and OpenAlex `mailto`. |

### C.1 Success conditions

Hit rate ≥ 70 % on rows that have a DOI (measured per run; logged in `run_log.notes`). All-source-empty rows tagged `triage_decision='MISSING_ABSTRACT'` immediately so they're never silently dropped.

---

## D. Abstract triage contract (Stage 1 + Stage 2B)

### D.1 Stage 1 — metadata-only screen

Runs at `triage_stage='metadata_only'` only. Title is matched case-insensitively against:

```python
ML_REJECT_PATTERNS = [
    r"\bdeep learning\b", r"\bconvolutional neural\b", r"\btransformer\b",
    r"\bimagenet\b", r"\bGAN\b", r"\bbatch normalization\b",
    r"\bgradient descent\b",
]
```

Reject if any pattern matches OR if `publication_year < min_year` (default `2005`). Survivors keep `triage_stage='metadata_only'` and proceed to abstract collection.

### D.2 Stage 2B — decision

| Verdict | Rule |
|---|---|
| `ACCEPT` | `relevance.verdict='accept'` AND `voi_score ≥ voi_threshold` (default `0.50`) |
| `EDGE_CASE` | `relevance.verdict='accept'` AND `voi_score < voi_threshold`, **or** `relevance.verdict='edge_case'` |
| `REJECT` | `relevance.verdict='reject'` |
| `MISSING_ABSTRACT` | already set by collector — never re-scored |

### D.3 Threshold justifications

- `voi_threshold = 0.50`: the gap-extractor's mid-band. Below this is "interesting but not the highest-priority hub." Configurable via `--voi-threshold`.
- `min_year = 2005`: the field's modern instrumentation cut-off (post-fMRI standardization, post-actigraphy commodity). Configurable via `--min-year`.
- ML-jargon list: empirical — these terms appear in a synthetic ML paper title with > 95 % specificity to non-target fields.

### D.4 Failure handling

If atlas_shared classifier or VOI scoring raises on a single row:
```
triage_decision = 'EDGE_CASE'
triage_reason   = 'Classifier/VOI failure requires manual review: <exception>'
```
Row is preserved, never dropped silently.

### D.5 Success conditions

Every triaged row gets a non-empty `triage_reason`. `triage_confidence` is populated for ACCEPT/EDGE_CASE/REJECT but NOT for MISSING_ABSTRACT (verified test). No row has both `triage_stage='abstract_collected'` AND `triage_decision IS NULL` after the run.

---

## E. PDF acquisition contract (Stage 3)

### E.1 Source cascade (in order)

| # | Source | When tried | discovered_via tag |
|---|---|---|---|
| 1 | Unpaywall | DOI present | `unpaywall` |
| 2 | OpenAlex OA URL | DOI present, Unpaywall missed | `openalex_oa` |
| 3 | scidownl | DOI present, sources 1+2 missed, AND policy gate (E.3) opens | `scidownl` |

(Semantic Scholar PDF and publisher-direct steps are NOT in this cascade — they're acknowledged as common Stage-3 sources but out of scope for this contract; documented limitation, not a silent gap.)

### E.2 Read-source

`v_acquisition_queue` view (rubric §5C): `WHERE triage_decision='ACCEPT' AND acquired_paper_id IS NULL ORDER BY voi_score DESC`. EDGE_CASE / REJECT / MISSING_ABSTRACT rows are absent from this view, so the cascade physically cannot reach them.

### E.3 scidownl 4-condition gate (rubric §5B)

All four must hold:

1. `--enable-scidownl` flag passed at the CLI.
2. `policy_clearance.json` exists at the repo root (file is `.gitignore`d; default state is closed).
3. The row has a DOI.
4. Both Unpaywall AND OpenAlex OA already failed for this `reference_id` in the current run.

Failure on any condition sets `pdf_acquisition_last_source='gated:<reason>'` and logs an `outcome='gated'` row in `lifecycle_transitions`. **No network call to scidownl is made.**

### E.4 Per-attempt logging

Every cascade attempt — hit, miss, or gated — increments `pdf_acquisition_attempts` and writes a `lifecycle_transitions` row with the source, outcome, and timestamp. PDF retrieval has a 30 s timeout. PDFs are validated post-download (magic bytes `%PDF-`, ≥ 1 KB) before they're written to disk.

### E.5 Fatal failures (the rubric calls these out as automatic deductions)

- [!] PDF download before abstract triage → impossible: cascade reads only from `v_acquisition_queue`.
- [!] scidownl runs by default → impossible: gate flag defaults false.
- [!] scidownl runs without clearance file → impossible: gate checks `policy_clearance.json` existence.
- [!] EDGE_CASE / REJECT / MISSING_ABSTRACT downloaded via scidownl → impossible: those verdicts are absent from the queue.

---

## F. PRISMA dashboard contract (rubric §6)

### F.1 Single SQL group-by

`prisma_dashboard.py::PRISMA_SQL` is one statement. Two supplemental queries count distinct `discovered_query` and `gap_template_id` (also against `article_references`) and `lifecycle_transitions WHERE outcome='dedup'`. No other tables are read; no parallel state.

### F.2 Persistence after refresh

Counts are persisted to `task3/data/prisma_funnel.json` on every dashboard regeneration. The HTML reads counts from a single GROUP BY at generate-time and is static between refreshes — re-running `prisma_dashboard.py` regenerates both files atomically (write-to-temp-then-rename pattern).

### F.3 Funnel surface (per rubric)

```
gaps_targeted, queries_executed, records_returned,
removed_at_metadata, abstracts_collected, missing_abstract,
screened_by_classifier, accept, edge_case, reject_topic,
pdf_acquired, pdf_gated, dedup_provenance_merges, dedupe_skipped,
included
```

Identity: `included = accept + edge_case`. Disjointness check: `removed_at_metadata + abstracts_collected = records_returned` for any clean run.

---

## G. Logging contract (cross-cutting)

Every stage writes a single row to `run_log` with:
- `stage` ∈ {`search`, `collect_abstract`, `triage`, `pdf`, `prisma`}
- `started_at`, `finished_at` (ISO-8601 UTC)
- `n_in`, `n_out`
- `notes` (JSON-encoded per-stage counts: harvested / inserted / dedup_doi / dedup_title / zero_results / errors / by_source / etc.)

`run_log.notes` is the audit trail the grader can `SELECT` to verify every claim in this contract.

---

## H. Success conditions (combined, runnable)

1. `python3 run_pipeline.py --backend mock` runs end-to-end without error.
2. `tests_task2_task3.py` reports **51/51 PASS** offline; `T2_LIVE=1` adds real network proof checks.
3. PRISMA `included == accept + edge_case`.
4. `lifecycle_transitions` has at least one row per `reference_id`.
5. `v_acquisition_queue` returns 0 rows when no ACCEPT exists; > 0 when ACCEPT exists.
6. `policy_clearance.json` is git-ignored AND its absence blocks every scidownl attempt.
7. SerpAPI key, if set, is read only via `os.environ.get("SERPAPI_API_KEY")`. Never logged.
8. No row in `article_references` has `triage_stage='abstract_collected'` AND `triage_decision IS NULL` after a triage run.
9. `removed_at_metadata + abstracts_collected = records_returned` in PRISMA.

### H.1 Exact verification commands

Run from the `Article_Finder` repo root unless noted:

```bash
python3 -m pytest task3/tests_task2_task3.py -q
python3 task3/tests_task2_task3.py
T2_LIVE=1 python3 task3/tests_task2_task3.py
python3 scripts/verify_track2_workflow.py

# Task 1, from the sibling Knowledge_Atlas repo:
KA_ATLAS_SHARED_SRC=/path/to/atlas_shared/src python3 data/test_pdfs/validate_task1.py
```

`task3/tests_task2_task3.py` uses a per-run `$TRACK2_DB` and `$TRACK2_OUT`
temp directory for the pipeline test, so repeated or parallel test runs do not
share mutable SQLite state. Manual `task3/run_pipeline.py` runs reset their
configured DB at step 0.

---

## I. Test checklist (rubric § 4 — "tests before build")

- [x] SerpAPI call uses `engine='google_scholar'` (search_runner.py:68)
- [x] `SERPAPI_API_KEY` read from env only (verified via grep test)
- [x] zero-result query is recorded (`run_log.notes`)
- [x] DOI regex extracts 3 sample URLs (test PASS)
- [x] duplicate DOI updates provenance instead of inserting (test PASS)
- [x] no abstract → MISSING_ABSTRACT (verified)
- [x] fallback chain tries S2 → CrossRef → PubMed → OpenAlex
- [x] every triage row has `triage_decision` and `triage_reason` (test PASS)
- [x] MISSING_ABSTRACT skips VOI scoring (test PASS)
- [x] ACCEPT appears in `v_acquisition_queue`, others do not (test PASS)
- [x] PDF acquisition refuses non-ACCEPT rows (test PASS)
- [x] scidownl cannot run unless all 4 policy conditions pass (test PASS)
- [x] dashboard counts match a separate manual GROUP BY (test PASS)
- [x] one paper traceable end-to-end (`docs/END_TO_END_TRACE.md`)

Run: `python3 task3/tests_task2_task3.py` → **51/51 PASS** offline; `T2_LIVE=1` adds real abstract and OA-PDF checks.
