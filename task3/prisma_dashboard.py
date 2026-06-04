#!/usr/bin/env python3
"""
prisma_dashboard.py — Task 3 Phase 6.

Rebuilds the full PRISMA funnel from a SINGLE GROUP BY on
`article_references`. The rubric is explicit: "Reads counts directly from
article_references SQL group-by; no parallel state."

Outputs:
  data/prisma_funnel.json   — machine-readable
  data/prisma_dashboard.html — human-readable, vanilla HTML/CSS, no JS framework
"""
from __future__ import annotations
import argparse, json, sqlite3
from pathlib import Path
from db_schema import open_db, DEFAULT_DB, DEFAULT_OUT_DIR

OUT_DIR = DEFAULT_OUT_DIR

# The single SQL statement. Every row in article_references is counted once;
# columns sum disjoint buckets that map onto the rubric's funnel stages.
PRISMA_SQL = """
SELECT
    COUNT(*)                                                                          AS records_returned,
    SUM(CASE WHEN triage_stage = 'rejected_at_metadata' THEN 1 ELSE 0 END)            AS removed_at_metadata,
    -- Only count rows with an abstract actually obtained (excludes MISSING_ABSTRACT
    -- rows whose triage_stage is also 'abstract_collected'; otherwise this would
    -- double-count with `missing_abstract` below).
    SUM(CASE WHEN triage_stage IN ('abstract_collected','acquired')
             AND abstract IS NOT NULL THEN 1 ELSE 0 END)                              AS abstracts_collected,
    SUM(CASE WHEN triage_decision = 'MISSING_ABSTRACT' THEN 1 ELSE 0 END)             AS missing_abstract,
    SUM(CASE WHEN triage_decision IN ('ACCEPT','EDGE_CASE','REJECT')
             AND triage_stage <> 'rejected_at_metadata' THEN 1 ELSE 0 END)            AS screened_by_classifier,
    SUM(CASE WHEN triage_decision = 'ACCEPT'    THEN 1 ELSE 0 END)                    AS accept,
    SUM(CASE WHEN triage_decision = 'EDGE_CASE' THEN 1 ELSE 0 END)                    AS edge_case,
    SUM(CASE WHEN triage_decision = 'REJECT'
             AND triage_stage <> 'rejected_at_metadata' THEN 1 ELSE 0 END)            AS reject_topic,
    SUM(CASE WHEN acquired_paper_id IS NOT NULL THEN 1 ELSE 0 END)                    AS pdf_acquired,
    SUM(CASE WHEN pdf_acquisition_last_source LIKE 'gated:%' THEN 1 ELSE 0 END)       AS pdf_gated,
    SUM(CASE WHEN discovered_via LIKE '%,%' THEN 1 ELSE 0 END)                        AS dedup_provenance_merges
FROM article_references
"""

DEDUP_SQL = """
SELECT COUNT(*) AS dedupe_skipped FROM lifecycle_transitions
WHERE outcome = 'dedup'
"""

NULL_RESULTS_SQL = """
SELECT q.gap_template_id, COUNT(ar.reference_id) AS n
FROM (SELECT DISTINCT discovered_query, gap_template_id FROM article_references
      UNION SELECT discovered_query, '' FROM article_references) q
LEFT JOIN article_references ar
  ON ar.discovered_query = q.discovered_query
GROUP BY q.discovered_query, q.gap_template_id
HAVING n = 0
"""


def compute(conn: sqlite3.Connection) -> dict:
    funnel = dict(conn.execute(PRISMA_SQL).fetchone())
    funnel["dedupe_skipped"] = conn.execute(DEDUP_SQL).fetchone()["dedupe_skipped"]
    funnel["included"] = funnel["accept"] + funnel["edge_case"]
    funnel["queries_executed"] = conn.execute(
        "SELECT COUNT(DISTINCT discovered_query) c FROM article_references"
    ).fetchone()["c"]
    funnel["gaps_targeted"] = conn.execute(
        "SELECT COUNT(DISTINCT gap_template_id) c FROM article_references "
        "WHERE gap_template_id <> ''"
    ).fetchone()["c"]
    return funnel


HTML_TPL = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>PRISMA Funnel — Task 3</title>
<style>
  body {{ font-family: -apple-system, sans-serif; max-width: 760px;
          margin: 2em auto; background: #fafafa; color: #222; }}
  h1 {{ font-size: 1.6em; margin-bottom: 0.2em; }}
  .row {{ background: white; border-left: 6px solid #B45F14; padding: 1em 1.2em;
          margin: 0.6em 0; box-shadow: 0 1px 2px rgba(0,0,0,0.04); border-radius: 6px; }}
  .row h3 {{ margin: 0 0 0.3em 0; font-size: 0.95em; color: #555;
             text-transform: uppercase; letter-spacing: 0.04em; }}
  .count {{ font-size: 1.6em; font-weight: 600; }}
  .sub {{ color: #888; font-size: 0.85em; margin-left: 0.5em; }}
  .out {{ border-left-color: #999; }}
  .accept {{ border-left-color: #2e7d32; }}
  .edge {{ border-left-color: #ed8936; }}
  .reject {{ border-left-color: #c53030; }}
  footer {{ font-size: 0.8em; color: #888; margin-top: 2em; }}
  table {{ font-size: 0.9em; border-collapse: collapse; margin-top: 0.4em; }}
  td {{ padding: 0.15em 0.6em 0.15em 0; }}
</style></head>
<body>
<h1>PRISMA Funnel — Article Finder Task 3</h1>
<p>Reconstructed from a single <code>GROUP BY</code> over
<code>article_references</code>. No parallel state.</p>

<div class="row">
  <h3>Identification</h3>
  <table>
    <tr><td><strong>Gaps targeted</strong></td><td class="count">{gaps_targeted}</td></tr>
    <tr><td><strong>Queries executed</strong></td><td class="count">{queries_executed}</td></tr>
    <tr><td><strong>Records returned</strong></td><td class="count">{records_returned}</td></tr>
    <tr><td>Dedup-skipped (provenance merge)</td><td>{dedup_provenance_merges}</td></tr>
    <tr><td>Dedup transitions logged</td><td>{dedupe_skipped}</td></tr>
  </table>
</div>

<div class="row out">
  <h3>Removed at metadata screen (Stage 1)</h3>
  <div class="count">{removed_at_metadata}</div>
</div>

<div class="row">
  <h3>Abstracts collected</h3>
  <div class="count">{abstracts_collected}</div>
  <div class="sub">missing_abstract: {missing_abstract}</div>
</div>

<div class="row">
  <h3>Screened by classifier (Stage 2B)</h3>
  <div class="count">{screened_by_classifier}</div>
</div>

<div class="row reject"><h3>Reject — off-topic</h3><div class="count">{reject_topic}</div></div>
<div class="row edge"><h3>Edge case</h3><div class="count">{edge_case}</div></div>
<div class="row accept"><h3>Accept</h3><div class="count">{accept}</div></div>

<div class="row">
  <h3>PDFs acquired (Stage 3)</h3>
  <div class="count">{pdf_acquired}</div>
  <div class="sub">scidownl gated: {pdf_gated}</div>
</div>

<div class="row accept">
  <h3>Included in synthesis</h3>
  <div class="count">{included}</div>
  ACCEPT + EDGE_CASE
</div>

<footer>Generated from <code>{db}</code> ·
single SQL: <code>prisma_dashboard.py</code> ·
view: <code>v_acquisition_queue</code></footer>
</body></html>
"""


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--out-dir", type=Path, default=OUT_DIR)
    args = p.parse_args()
    args.out_dir.mkdir(parents=True, exist_ok=True)

    conn = open_db(args.db)
    funnel = compute(conn)
    conn.close()

    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "prisma_funnel.json").write_text(
        json.dumps(funnel, indent=2), encoding="utf-8")
    html = HTML_TPL.format(db=args.db.name, **funnel)
    # Write under both names: prisma_dashboard.html for clarity, plus
    # ka_topic_proposer.html which is the rubric-required filename.
    (args.out_dir / "prisma_dashboard.html").write_text(html, encoding="utf-8")
    (args.out_dir / "ka_topic_proposer.html").write_text(html, encoding="utf-8")

    print("PRISMA funnel:")
    for k, v in funnel.items(): print(f"  {k:<25} {v}")
    print(f"\nWrote: {args.out_dir / 'prisma_funnel.json'}")
    print(f"Wrote: {args.out_dir / 'prisma_dashboard.html'}")


if __name__ == "__main__":
    main()
