# Track 2 — Deliverable Map & Dependency Contract

Author: Dhruv Sood · Branch: `track2/dhruv-sood` · Updated: 2026-06-03

This is the grader's entry point. It declares (1) exactly how the one external
dependency must be supplied, (2) the verifier commands, and (3) which files are
the deliverable surface vs. support / generated / inherited material. The local
COGS160 sibling layout used during remediation was:
`Article_Finder/`, `Knowledge_Atlas/`, `atlas_shared/`, and `Article_Eater/`
under one parent directory.

---

## 1. Dependency contract — `atlas_shared`

The pipeline is **not** fully standalone: the classifier + relevance code lives
in `atlas_shared` (a shared course module, per the assignment setup). It is the
**only** external code dependency. Supply it in **any one** of these ways:

| Mode | How | When the verifier uses it |
|---|---|---|
| **Installed** | `cd atlas_shared && pip install -e .` | nothing extra needed — `import atlas_shared` just works |
| **Env var** | `export KA_ATLAS_SHARED_SRC=/path/to/atlas_shared/src` | prepend to the commands below |
| **Sibling checkout** | clone `atlas_shared` next to `Article_Finder` / `Knowledge_Atlas` | auto-detected |

Resolution order in code (`abstract_triage.py`, `gap_extractor.py`,
`Knowledge_Atlas/data/test_pdfs/validate_task1.py`): **installed → `$KA_ATLAS_SHARED_SRC` → sibling**.
If none resolve, Task 1 SKIPs cleanly (exit 0) with setup instructions; Task 2/3
use bundled fixtures where possible and otherwise surface a clear environment
error. No `/private/tmp` or other absolute-path assumptions remain.

The two **data** files `atlas_shared` would otherwise supply are **bundled in this repo**
at `task3/fixtures/` (`question_constitutions_starter.json`, `mechanisms.json`), so no
sibling `Knowledge_Atlas` checkout is needed for Task 2/3. Override with
`$KA_CONSTITUTIONS` / `$TRACK2_MECHANISMS` if desired.

---

## 2. Verifier commands

```bash
# --- Article_Finder (Tasks 2 & 3) — run from the Article_Finder repo root ---
python3 -m pytest task3/tests_task2_task3.py -q     # collects + passes (1 passed)
python3 task3/tests_task2_task3.py                  # 51/51 offline (deterministic, isolated temp DB)
T2_LIVE=1 python3 task3/tests_task2_task3.py         # opt-in real abstract + real OA PDF checks
python3 scripts/verify_track2_workflow.py            # CHAIN 9/9 (incl. handoff + AE-consume)

# --- Knowledge_Atlas (Task 1) — needs atlas_shared (declared above) ---
KA_ATLAS_SHARED_SRC=/path/to/atlas_shared/src python3 data/test_pdfs/validate_task1.py   # 42/42
```

**Test isolation:** the Task 3 suite runs the pipeline against a per-run temp DB
(`$TRACK2_DB`) and temp outputs (`$TRACK2_OUT`); repeated or parallel test runs
do not share SQLite state and do not depend on the committed `task3/data/` tree.
For a manual non-test run, `task3/run_pipeline.py` still resets the configured DB
at step 0.

---

## 3. Deliverable map

Use this section to audit the PR quickly. The first table is the required
review surface; the following tables separate tests, docs, fixtures/generated
outputs, and inherited scaffolding.

### Core implementation

| File | Category | Why it matters |
|---|---|---|
| `gap_extractor.py` | Core implementation | Extracts low-confidence mechanism gaps and assigns the Track 2 first-pass VOI heuristic. |
| `query_generator.py` | Core implementation | Converts high-VOI gaps into AI-Citation and Boolean search queries. |
| `task3/run_pipeline.py` | Core implementation | Runs the end-to-end Article Finder chain in the correct order. |
| `task3/search_runner.py` | Core implementation | Executes search backends and inserts/dedupes every candidate into `article_references`. |
| `task3/abstract_collector.py` | Core implementation | Collects abstracts through the S2, CrossRef, PubMed, and OpenAlex cascade. |
| `task3/abstract_triage.py` | Core implementation | Performs metadata screening and abstract-level relevance triage. |
| `task3/pdf_acquirer.py` | Core implementation | Acquires PDFs only after ACCEPT triage and keeps scidownl gated/default-closed. |
| `task3/ae_handoff.py` | Core implementation | Writes the local Article Eater handoff artefact and exposes the real-AE delivery seam. |
| `task3/db_schema.py` | Core implementation | Defines `article_references`, lifecycle logging, acquisition queue, PRISMA inputs, and handoff log. |
| `Knowledge_Atlas/ka_article_endpoints.py` + `Knowledge_Atlas/ka_contribute_public.html` | Core implementation | Implements the Knowledge Atlas article-intake surface that feeds Track 2. |

### Tests and verification

| File | Category | Why it matters |
|---|---|---|
| `task3/tests_task2_task3.py` | Tests | Automated Task 2/3 checklist; currently 51/51 offline with live checks opt-in. |
| `scripts/verify_track2_workflow.py` | Tests | One-command chain verifier for Task 1, Task 2/3, and the handoff boundary; currently CHAIN 9/9. |
| `Knowledge_Atlas/data/test_pdfs/validate_task1.py` | Tests | Knowledge Atlas intake validator; currently 42/42 when `atlas_shared` is supplied. |
| `task3/eval_triage.py` + `task3/fixtures/labeled_abstracts.json` | Tests | Small labeled abstract-triage evaluation set for precision/recall evidence. |
| `task3/ae_inbox_stub.py` + `task3/ae_ingest_smoke.py` | Tests | Proves handoff artefacts are locally consumable and defines the gated real-AE smoke test. |

### Docs and contracts

| File | Category | Why it matters |
|---|---|---|
| `TRACK2_DELIVERABLE_MAP.md` | Docs | This audit map: dependency contract, verification commands, and deliverable surface. |
| `task3/docs/TASK3_CONTRACT.md` | Docs | Main Task 3 contract: search, triage, PDF acquisition, PRISMA, handoff, and boundaries. |
| `docs/GAP_EXTRACTOR_CONTRACT_TASK2.md` | Docs | Contract for gap extraction and VOI heuristic behavior. |
| `docs/QUERY_GENERATOR_CONTRACT_TASK2.md` | Docs | Contract for query generation and query quality flags. |
| `TRACK2_VOI_COMPARISON.md` / `VOI_COMPARISON.md` | Docs | Explains Track 2 heuristic VOI versus Article Eater/BN structural and epistemic VOI. |
| `docs/module_deliverable/` | Docs | Sprint diagram, box specs, branch notes, self-audit, and support material. |
| `task3/docs/SUBMISSION_TASK3.md` + `task3/docs/END_TO_END_TRACE.md` | Docs | Submission report and one-paper trace through the pipeline. |

### Fixtures and generated outputs

| File or directory | Category | Why it matters |
|---|---|---|
| `task3/fixtures/question_constitutions_starter.json` | Fixtures | Bundled constitution fixture so Task 2/3 can run without a sibling dependency. |
| `task3/fixtures/mechanisms.json` | Fixtures | Bundled mechanism manifest for deterministic gap extraction. |
| `task3/data/*.json`, `task3/data/*.html` | Generated outputs | Search, triage, PRISMA, and dashboard evidence snapshots; regenerated by the pipeline. |
| `gap_results.json` | Generated outputs | Output of `gap_extractor.py`. |
| `query_results.json` | Generated outputs | Output of `query_generator.py`. |
| `Knowledge_Atlas/data/question_constitutions_starter.json` + `Knowledge_Atlas/data/contracts/` | Fixtures | Task 1 bundled assets and attribution/contract material. |

During tests, generated outputs route through `$TRACK2_OUT`, so the committed
`task3/data/` snapshots are evidence artifacts, not shared test state.

### Downstream references, not Track 2 implementation

| File | Category | Why it matters |
|---|---|---|
| `Article_Eater/contracts/ae_af/CLAUDE_HANDOFF_PROMPT.md` | Downstream reference | Defines what real Article Eater consumption means beyond a local handoff file. |
| `Article_Eater/src/services/voi_search.py` | Downstream reference | Supplies the richer structural/epistemic VOI model used for comparison. |
| `Article_Eater/contracts/research_queue.contract.md` | Downstream reference | Shows the product-level research queue and VOI priority target shape. |

### Inherited scaffolding

These are useful project context, but they are not the Track 2 contribution:

| File or directory | Category | Why it matters |
|---|---|---|
| `article_finder_v2/` | Inherited scaffolding | Separate upgraded module; not the Task 2/3 grading surface. |
| `cli/`, `core/`, `ui/`, `search/`, `ingest/`, `triage/`, `knowledge/` | Inherited scaffolding | Pre-existing Article Finder product modules. |
| `contracts/`, `config/`, `schemas/` outside the files named above | Inherited scaffolding | Product infrastructure and schemas not introduced as the Track 2 deliverable. |

---

## 4. Honest boundaries
- **Article Eater handoff** is local handoff now, not completed AE ingestion:
  `ae_handoff.py` writes `data/handoff/*.json`, and `ae_inbox_stub.py` validates
  that the artefact is consumable. Real AE ingestion requires a mounted/configured
  Article Eater (`AE_INGEST_CMD` or `AE_INBOX`) and independent proof that AE
  consumed the artefact and reported success. Without those settings,
  `deliver_to_ae()` returns an honest `local_substitute` no-op. See
  `task3/docs/TASK3_CONTRACT.md §0`.
- **VOI** is a first-stage search-ranking **heuristic**, not the full Article Eater/BN VOI model.
  See `TRACK2_VOI_COMPARISON.md`.
- **scidownl** stays gated + default-closed (not a live downloader).
- Live abstract/PDF proofs are opt-in (`T2_LIVE=1`); the captured evidence (PLOS OA PDF, 829,365 B,
  sha `616f6081…`) is recorded in the self-audit.
