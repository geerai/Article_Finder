#!/usr/bin/env python3
"""
abstract_triage.py — Task 3 Phase 4 (Stage 1 + Stage 2B).

Stage 1 (metadata-only screen) — runs against rows fresh from search_runner
(triage_stage='metadata_only', triage_decision IS NULL, abstract IS NULL).
Rejects ML/CS jargon and pre-2005 papers; never touches abstract collection.

Stage 2B (abstract decision) — runs after abstract_collector.py; classifies
each row as ACCEPT / EDGE_CASE / REJECT / MISSING_ABSTRACT using the
atlas_shared question constitutions and a VOI-style threshold.

Decision rule:
    relevance.verdict == 'accept' AND voi_score >= 0.50  → ACCEPT
    relevance.verdict == 'accept' AND voi_score <  0.50  → EDGE_CASE
    relevance.verdict == 'edge_case'                     → EDGE_CASE
    relevance.verdict == 'reject'                        → REJECT
    abstract is None / abstract_source='none'            → MISSING_ABSTRACT
                                                           (already set by collector)

Every triaged row gets a non-empty `triage_reason`.

Usage:
    python3 abstract_triage.py
    python3 abstract_triage.py --voi-threshold 0.55 --min-year 2010
"""

from __future__ import annotations
import argparse
import json
import re
import sqlite3
import sys
from pathlib import Path

from db_schema import open_db, DEFAULT_DB, DEFAULT_OUT_DIR, log_transition

import os

REPO_ROOT = Path(__file__).resolve().parent.parent


def _locate_atlas_shared() -> None:
    """Put atlas_shared on sys.path WITHOUT hardcoding one machine's layout.
    Order: already-importable -> $KA_ATLAS_SHARED_SRC -> sibling checkout.
    (Fixes the brittle sibling-only assumption flagged in review.)"""
    try:
        import atlas_shared.relevance  # noqa: F401
        return
    except Exception:
        pass
    cands = []
    if os.environ.get("KA_ATLAS_SHARED_SRC"):
        cands.append(Path(os.environ["KA_ATLAS_SHARED_SRC"]))
    cands += [REPO_ROOT.parent / "atlas_shared" / "src",
              REPO_ROOT.parent / "atlas_shared"]
    for c in cands:
        if (c / "atlas_shared").is_dir():
            sys.path.insert(0, str(c))
            return


_locate_atlas_shared()


def _resolve_constitutions() -> Path:
    """Constitutions path, honoring $KA_CONSTITUTIONS (previously ignored):
    $KA_CONSTITUTIONS -> AF-bundled fixture -> sibling atlas_shared."""
    if os.environ.get("KA_CONSTITUTIONS"):
        return Path(os.environ["KA_CONSTITUTIONS"])
    bundled = REPO_ROOT / "task3" / "fixtures" / "question_constitutions_starter.json"
    if bundled.exists():
        return bundled
    return (REPO_ROOT.parent / "atlas_shared" / "src" / "atlas_shared" / "data"
            / "question_constitutions_starter.json")


CONSTITUTIONS_PATH = _resolve_constitutions()


# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — metadata screen
# ─────────────────────────────────────────────────────────────────────────────
ML_REJECT_PATTERNS = [
    r"\bdeep learning\b", r"\bconvolutional neural\b", r"\btransformer\b",
    r"\bimagenet\b", r"\bGAN\b", r"\bbatch normalization\b",
    r"\bgradient descent\b",
]


def stage1_screen(title: str, year: int | None, min_year: int) -> tuple[str, list[str]]:
    reasons = []
    # Match patterns case-insensitively so e.g. \bGAN\b catches the
    # lowercase "gan" produced by .lower() above.
    t = (title or "").lower()
    for pat in ML_REJECT_PATTERNS:
        if re.search(pat, t, re.IGNORECASE):
            reasons.append(f"ml_jargon:{pat}")
    if year is not None and year < min_year:
        reasons.append(f"too_old:{year}<{min_year}")
    return ("rejected_at_metadata" if reasons else "pass", reasons)


# ─────────────────────────────────────────────────────────────────────────────
# Stage 2B — atlas_shared classifier + VOI threshold
# ─────────────────────────────────────────────────────────────────────────────
def _load_classifier():
    from atlas_shared.article_types import HeuristicArticleTypeClassifier
    from atlas_shared.relevance import (QuestionArticleRelevanceFilter,
                                        ArticleCandidate, QuestionConstitution)
    data = json.loads(CONSTITUTIONS_PATH.read_text())
    consts = [QuestionConstitution.from_panel_spec(q) for q in data["questions"]]
    return (HeuristicArticleTypeClassifier(),
            QuestionArticleRelevanceFilter(),
            ArticleCandidate, consts)


def stage2_decide(title: str, abstract: str, voi_score: float, *,
                  type_clf, rel_clf, Candidate, constitutions,
                  voi_threshold: float, ref_id: str
                  ) -> tuple[str, str, float]:
    """Returns (decision, reason, confidence)."""
    candidate = Candidate(paper_id=ref_id, title=title or "", abstract=abstract)
    type_dec = type_clf.classify(abstract=abstract, title=title or "")

    best = None
    for c in constitutions:
        a = rel_clf.assess(c, candidate)
        if a.verdict == "accept": best = (a, c); break
        if a.verdict == "edge_case":
            if best is None or best[0].confidence < a.confidence:
                best = (a, c)
    if best is None:
        all_a = [(rel_clf.assess(c, candidate), c) for c in constitutions]
        best = max(all_a, key=lambda x: x[0].confidence)
    a, c = best

    if a.verdict == "accept":
        if voi_score >= voi_threshold:
            return ("ACCEPT", f"on-topic for {c.topic}; voi={voi_score:.2f}≥{voi_threshold}; "
                              f"type={type_dec.value}", round(a.confidence, 3))
        return ("EDGE_CASE", f"on-topic for {c.topic} but voi={voi_score:.2f}<{voi_threshold}",
                round(a.confidence, 3))
    if a.verdict == "edge_case":
        hits = ",".join(list(a.environment_hits)[:2] + list(a.outcome_hits)[:2])
        return ("EDGE_CASE", f"borderline match on {c.topic}; hits=[{hits}]",
                round(a.confidence, 3))
    return ("REJECT", f"off-topic vs {c.topic}; "
                      f"reasons={'; '.join(list(a.reasons)[:2])}",
            round(a.confidence, 3))


# ─────────────────────────────────────────────────────────────────────────────
# Driver — runs Stage 1 then Stage 2B
# ─────────────────────────────────────────────────────────────────────────────
def run(db_path: Path, min_year: int, voi_threshold: float) -> dict:
    conn = open_db(db_path)
    cur = conn.execute("INSERT INTO run_log (stage) VALUES ('triage')")
    run_id = cur.lastrowid; conn.commit()

    type_clf, rel_clf, Candidate, consts = _load_classifier()
    print(f"Loaded {len(consts)} question constitution(s)")

    counts = {"stage1_reject": 0, "stage1_pass": 0,
              "ACCEPT": 0, "EDGE_CASE": 0, "REJECT": 0, "MISSING_ABSTRACT": 0}

    # ── Stage 1: rows that have not been screened yet ──
    rows = conn.execute(
        "SELECT reference_id, title_raw, publication_year FROM article_references "
        "WHERE triage_stage = 'metadata_only' AND triage_decision IS NULL"
    ).fetchall()
    for r in rows:
        s1, reasons = stage1_screen(r["title_raw"], r["publication_year"], min_year)
        if s1 == "rejected_at_metadata":
            conn.execute(
                "UPDATE article_references SET triage_stage='rejected_at_metadata', "
                "triage_decision='REJECT', "
                "triage_reason=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE reference_id=?",
                (f"stage1_metadata: {','.join(reasons)}", r["reference_id"]))
            log_transition(conn, reference_id=r["reference_id"],
                           from_stage="metadata_only",
                           to_stage="rejected_at_metadata",
                           agent="abstract_triage(stage1)",
                           outcome="reject",
                           notes=",".join(reasons))
            counts["stage1_reject"] += 1
            counts["REJECT"] += 1
        else:
            counts["stage1_pass"] += 1
    conn.commit()

    # ── Stage 2B: rows with collected abstracts (or MISSING_ABSTRACT already set) ──
    rows = conn.execute(
        "SELECT reference_id, title_raw, abstract, voi_score, triage_decision "
        "FROM article_references "
        "WHERE triage_stage = 'abstract_collected' AND triage_decision IS NULL"
    ).fetchall()
    for r in rows:
        decision, reason, conf = stage2_decide(
            r["title_raw"] or "", r["abstract"] or "", r["voi_score"] or 0.0,
            type_clf=type_clf, rel_clf=rel_clf, Candidate=Candidate,
            constitutions=consts, voi_threshold=voi_threshold,
            ref_id=r["reference_id"])
        conn.execute(
            "UPDATE article_references SET triage_decision=?, triage_reason=?, "
            "triage_confidence=?, "
            "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
            "WHERE reference_id=?",
            (decision, reason, conf, r["reference_id"]))
        log_transition(conn, reference_id=r["reference_id"],
                       from_stage="abstract_collected",
                       to_stage="abstract_collected",
                       agent="abstract_triage(stage2)",
                       outcome=decision.lower(), notes=reason[:120])
        counts[decision] = counts.get(decision, 0) + 1
    conn.commit()

    # MISSING_ABSTRACT rows (set by collector) — count them
    missing = conn.execute(
        "SELECT COUNT(*) c FROM article_references "
        "WHERE triage_decision='MISSING_ABSTRACT'"
    ).fetchone()["c"]
    counts["MISSING_ABSTRACT"] = missing

    conn.execute(
        "UPDATE run_log SET finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
        "n_in=?, n_out=?, notes=? WHERE run_id=?",
        (counts["stage1_pass"] + counts["stage1_reject"],
         counts.get("ACCEPT", 0) + counts.get("EDGE_CASE", 0),
         json.dumps(counts), run_id))
    conn.commit()

    # Export triage_results.json (rubric-required deliverable; per-row
    # decisions with non-empty triage_reason).
    rows_out = conn.execute(
        "SELECT reference_id, title_raw AS title, doi, abstract_source, "
        "       triage_decision, triage_reason, triage_confidence, "
        "       voi_score, gap_template_id "
        "FROM article_references WHERE triage_decision IS NOT NULL "
        "ORDER BY voi_score DESC, reference_id ASC"
    ).fetchall()
    triage_export = [{
        "reference_id": r["reference_id"],
        "title": r["title"],
        "doi": r["doi"],
        "gap_id": r["gap_template_id"],
        "classifier_topic": "Nature and Attention",
        "classifier_confidence": r["triage_confidence"],
        "voi_score": r["voi_score"],
        "abstract_source": r["abstract_source"],
        "triage_decision": r["triage_decision"],
        "triage_reason": r["triage_reason"],
        "triage_stage": "abstract_triage",
    } for r in rows_out]
    out_path = DEFAULT_OUT_DIR / "triage_results.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(triage_export, indent=2), encoding="utf-8")

    conn.close()

    print("Triage results:")
    for k, v in counts.items():
        print(f"  {k:<20} {v}")
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--min-year", type=int, default=2005)
    p.add_argument("--voi-threshold", type=float, default=0.50,
                   help="ACCEPT requires voi_score >= this; below → EDGE_CASE")
    args = p.parse_args()
    run(args.db, args.min_year, args.voi_threshold)


if __name__ == "__main__":
    main()
