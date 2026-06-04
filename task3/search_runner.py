#!/usr/bin/env python3
"""
search_runner.py — Task 3 Phase 2.

Reads `query_results.json`, runs the Boolean query for each gap against one of
the four-scraper layer (rubric §2):

    serpapi    primary       → discovered_via='serpapi_scholar'
    scholarly  free fallback → discovered_via='scholarly_search'
    paperscraper preprints   → discovered_via='paperscraper_search' (uses AI Citation form)
    mock       offline demo  → discovered_via='mock_synthetic'

Every record lands in `article_references` (rubric §3 — cardinal rule).
Dedupe-on-insert: existing rows have their `discovered_via` extended rather
than being duplicated. SERPAPI_API_KEY is read from env ONLY — never logged or
committed.

Usage:
    SERPAPI_API_KEY=xxx python3 search_runner.py --backend serpapi --top-n 10
    python3 search_runner.py --backend mock --per-query 10 --top-n 10
"""

from __future__ import annotations
import argparse
import datetime as dt
import hashlib
import json
import os
import random
import re
import sqlite3
import sys
import time
import uuid
from pathlib import Path
from typing import Iterable

from db_schema import (open_db, DEFAULT_DB, DEFAULT_OUT_DIR, normalize_doi,
                       normalize_title, make_reference_id, log_transition)

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_QUERIES = REPO_ROOT / "query_results.json"

DOI_REGEX = re.compile(r"\b10\.\d{4,9}/[-._;()/:A-Z0-9]+\b", re.IGNORECASE)


# ─────────────────────────────────────────────────────────────────────────────
# DOI extraction (rubric Phase 2 — DOI extraction tested)
# ─────────────────────────────────────────────────────────────────────────────
def extract_doi(url: str | None, snippet: str | None = None) -> str | None:
    for src in (url, snippet):
        if not src:
            continue
        m = DOI_REGEX.search(src)
        if m:
            return normalize_doi(m.group(0))
    return None


# ─────────────────────────────────────────────────────────────────────────────
# Backends — each returns a list[dict] of harvested records
# ─────────────────────────────────────────────────────────────────────────────
def search_serpapi(query: str, n: int = 10, **_kw) -> list[dict]:
    key = os.environ.get("SERPAPI_API_KEY")
    if not key:
        raise RuntimeError("SERPAPI_API_KEY not set in env. Refusing to run.")
    import requests
    r = requests.get("https://serpapi.com/search.json",
                     params={"engine": "google_scholar", "q": query,
                             "api_key": key, "num": min(n, 20), "hl": "en"},
                     timeout=30)
    r.raise_for_status()
    out: list[dict] = []
    for entry in r.json().get("organic_results", [])[:n]:
        info = entry.get("publication_info", {}) or {}
        out.append({
            "title":   entry.get("title"),
            "snippet": entry.get("snippet"),
            "url":     entry.get("link"),
            "doi":     extract_doi(entry.get("link"), entry.get("snippet")),
            "authors": [a.get("name") for a in info.get("authors", []) if a.get("name")],
            "year":    _year_from(info.get("summary", "")),
            "venue":   info.get("summary", ""),
            "raw":     entry,
        })
    return out


def search_scholarly(query: str, n: int = 10, **_kw) -> list[dict]:
    from scholarly import scholarly  # type: ignore
    it = scholarly.search_pubs(query)
    out: list[dict] = []
    for _ in range(n):
        try: e = next(it)
        except StopIteration: break
        bib = e.get("bib", {})
        out.append({
            "title":   bib.get("title"),
            "snippet": bib.get("abstract"),
            "url":     e.get("pub_url"),
            "doi":     extract_doi(e.get("pub_url"), bib.get("abstract")),
            "authors": bib.get("author", []),
            "year":    int(bib["pub_year"]) if str(bib.get("pub_year","")).isdigit() else None,
            "venue":   bib.get("venue"),
            "raw":     e,
        })
        time.sleep(1.5)
    return out


def search_paperscraper(query_ai_citation: str, n: int = 10, **_kw) -> list[dict]:
    """Preprint channel — uses the AI Citation natural-language form (rubric note)."""
    try:
        from paperscraper.pubmed import get_pubmed_papers  # type: ignore
    except ImportError:
        raise RuntimeError("paperscraper not installed; pip install paperscraper")
    rows = get_pubmed_papers([query_ai_citation], "/tmp/_ps", n=n) or []
    out: list[dict] = []
    for r in rows[:n]:
        out.append({
            "title": r.get("title"), "snippet": r.get("abstract"),
            "url": r.get("link"), "doi": normalize_doi(r.get("doi")),
            "authors": r.get("authors", []),
            "year": int(r["year"]) if str(r.get("year","")).isdigit() else None,
            "venue": r.get("journal"), "raw": r,
        })
    return out


def search_mock(query: str, n: int = 10, *, gap_id: str = "", **_kw) -> list[dict]:
    """Deterministic synthetic generator. 60/20/10/10 mix.
       Used only when --backend mock; never makes a network call."""
    rng = random.Random(hash(gap_id or query) & 0xFFFFFFFF)
    venues = ["Environment & Behavior", "Building & Environment", "J. Environ. Psychol.",
              "Frontiers in Psychology", "Sci. Rep.", "Nature Human Behaviour",
              "PNAS", "ICML Proceedings", "NeurIPS"]
    on_topic_terms = ["natural environment", "built environment", "indoor light",
                      "thermal comfort", "biophilic design", "circadian", "interoception",
                      "multisensory", "spatial memory", "olfactory", "social affiliation"]
    outcome_terms = ["attention restoration", "directed attention", "stress recovery",
                     "cognitive restoration", "attention performance", "focus"]
    out: list[dict] = []
    for i in range(n):
        kind = rng.choices(["empirical", "theoretical", "ml", "dup"],
                            weights=[6, 2, 1, 1])[0]
        year = rng.randint(2008, 2024)
        idx = rng.randint(1000, 9999)
        if kind == "ml":
            title = f"Deep learning approach to {rng.choice(['ImageNet','NLP','GAN'])} ({idx})"
            snippet = "Convolutional neural networks. Batch normalization. ImageNet."
            doi, venue = None, rng.choice(["ICML Proceedings", "NeurIPS"])
        elif kind == "dup":
            title = f"Effects of {rng.choice(on_topic_terms)} on cognitive performance"
            snippet = "Repeated study (synthetic duplicate)."
            doi, venue = None, rng.choice(venues[:6])
        else:
            term = rng.choice(on_topic_terms); outcome = rng.choice(outcome_terms)
            verb = "modulates" if kind == "empirical" else "may modulate"
            title = (f"{term.capitalize()} {verb} {outcome} in adults: "
                     f"a {kind} study ({idx})")
            snippet = (f"We investigated whether {term} influences {outcome}. "
                       f"{'Randomized controlled trial with N=' + str(rng.randint(40,220)) if kind == 'empirical' else 'Conceptual review'}. "
                       f"Built environment, green space, biophilic design, attention. "
                       f"p < 0.0{rng.randint(1,5)}.")
            doi = f"10.1234/synth.{(gap_id or 'x').lower()}.{i}.{idx}" if rng.random() > 0.3 else None
            venue = rng.choice(venues[:7])
        out.append({
            "title":   title, "snippet": snippet,
            "url":     f"https://example.org/paper/{idx}",
            "doi":     doi,
            "authors": [f"Author {chr(65 + rng.randint(0,25))}.",
                        f"Author {chr(65 + rng.randint(0,25))}."],
            "year":    year, "venue": venue,
            "raw":     {"synthetic": True, "kind": kind},
        })
    return out


def _year_from(s: str) -> int | None:
    m = re.search(r"\b(19|20)\d{2}\b", s or "")
    return int(m.group(0)) if m else None


# ─────────────────────────────────────────────────────────────────────────────
# Insert with dedupe-on-insert (rubric §3B)
#   - normalise DOI
#   - if DOI matches existing row → UPDATE discovered_via to preserve provenance
#   - else INSERT new row with reference_id = REF-YYYY-MM-DD-NNNNNN
# ─────────────────────────────────────────────────────────────────────────────
def _next_seq(conn: sqlite3.Connection) -> int:
    today = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d")
    row = conn.execute(
        "SELECT COUNT(*) AS n FROM article_references WHERE reference_id LIKE ?",
        (f"REF-{today}-%",)
    ).fetchone()
    return (row["n"] or 0) + 1


def _title_jaccard(a: str, b: str) -> float:
    """Token Jaccard over normalized titles (tokens longer than 2 chars)."""
    ta = {t for t in (a or "").split() if len(t) > 2}
    tb = {t for t in (b or "").split() if len(t) > 2}
    return len(ta & tb) / len(ta | tb) if ta and tb else 0.0


# DOI-less near-duplicate threshold (token similarity, not just exact equality).
FUZZY_TITLE_THRESHOLD = 0.85


def insert_or_dedupe(conn: sqlite3.Connection, rec: dict, *,
                     discovered_via: str, discovered_query: str,
                     discovery_run_id: str, gap_template_id: str,
                     voi_score: float) -> tuple[str, str]:
    """Returns (reference_id, action) where action ∈ {'inserted','dedup_doi','dedup_title'}."""
    doi = normalize_doi(rec.get("doi"))
    title_raw = rec.get("title") or ""
    title_norm = normalize_title(title_raw)

    # Dedupe lookup
    existing = None
    if doi:
        existing = conn.execute(
            "SELECT reference_id, discovered_via FROM article_references WHERE doi = ?",
            (doi,),
        ).fetchone()
        action = "dedup_doi"
    if existing is None and title_norm:
        existing = conn.execute(
            "SELECT reference_id, discovered_via FROM article_references "
            "WHERE title_normalized = ? AND (doi IS NULL OR doi = '')",
            (title_norm,),
        ).fetchone()
        action = "dedup_title"
    # DOI-less FUZZY title dedup (token Jaccard) — catches near-duplicates that
    # exact normalized-title equality misses (e.g. trailing words, minor edits).
    if existing is None and title_norm and len(title_norm.split()) >= 4:
        for row in conn.execute(
                "SELECT reference_id, discovered_via, title_normalized "
                "FROM article_references "
                "WHERE (doi IS NULL OR doi = '') AND title_normalized IS NOT NULL"):
            if _title_jaccard(title_norm, row["title_normalized"]) >= FUZZY_TITLE_THRESHOLD:
                existing = row
                action = "dedup_title"
                break

    if existing is not None:
        # Append channel to provenance only if not already present
        prov = existing["discovered_via"]
        if discovered_via not in prov.split(","):
            prov = f"{prov},{discovered_via}"
            conn.execute(
                "UPDATE article_references SET discovered_via=?, "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') WHERE reference_id=?",
                (prov, existing["reference_id"]),
            )
        log_transition(conn, reference_id=existing["reference_id"],
                       from_stage="metadata_only", to_stage="metadata_only",
                       agent="search_runner", outcome="dedup",
                       notes=f"merged provenance from {discovered_via}")
        return existing["reference_id"], action

    # New row
    seq = _next_seq(conn)
    ref_id = make_reference_id(seq)
    authors = rec.get("authors") or []
    first_author = ""
    if authors:
        a0 = authors[0] if isinstance(authors[0], str) else (authors[0].get("name") or "")
        first_author = a0.split(",")[0].strip().split()[-1] if a0 else ""

    conn.execute(
        "INSERT INTO article_references (reference_id, doi, title_raw, title_normalized, "
        "first_author_surname, publication_year, venue, discovered_via, "
        "discovered_query, discovery_run_id, triage_stage, snippet, "
        "gap_template_id, voi_score, raw_citation) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (ref_id, doi, title_raw, title_norm, first_author,
         rec.get("year"), rec.get("venue"), discovered_via,
         discovered_query, discovery_run_id, "metadata_only",
         rec.get("snippet"), gap_template_id, voi_score,
         json.dumps(rec.get("raw") or {})),
    )
    log_transition(conn, reference_id=ref_id, from_stage=None,
                   to_stage="metadata_only", agent="search_runner",
                   outcome="success", notes=f"harvested via {discovered_via}")
    return ref_id, "inserted"


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────
DISCOVERED_VIA = {
    "serpapi": "serpapi_scholar", "scholarly": "scholarly_search",
    "paperscraper": "paperscraper_search", "mock": "mock_synthetic",
}


def run(queries_path: Path, backend: str, per_query: int, top_n: int,
        db_path: Path) -> dict:
    queries = json.loads(queries_path.read_text(encoding="utf-8"))[:top_n]
    discovery_run_id = f"run-{uuid.uuid4().hex[:12]}"
    conn = open_db(db_path)
    cur = conn.execute(
        "INSERT INTO run_log (stage, n_in, notes) VALUES ('search', ?, ?)",
        (len(queries), f"backend={backend} run_id={discovery_run_id}"))
    run_log_id = cur.lastrowid
    conn.commit()

    fn = {"serpapi": search_serpapi, "scholarly": search_scholarly,
          "paperscraper": search_paperscraper, "mock": search_mock}[backend]
    via = DISCOVERED_VIA[backend]

    counts = {"queries": len(queries), "harvested": 0,
              "inserted": 0, "dedup_doi": 0, "dedup_title": 0,
              "zero_results": 0, "errors": 0}

    for q in queries:
        # paperscraper uses AI Citation form, others use Boolean
        query_text = q["ai_citation_query"] if backend == "paperscraper" else q["boolean_query"]
        try:
            recs = fn(query_text, per_query, gap_id=q["gap_id"])
        except Exception as e:
            print(f"  [{q['gap_id']:<35}] ERROR: {e}", file=sys.stderr)
            counts["errors"] += 1
            continue
        if not recs:
            counts["zero_results"] += 1
            print(f"  [{q['gap_id']:<35}] ZERO results")
            continue
        ins = dd_doi = dd_t = 0
        for r in recs:
            counts["harvested"] += 1
            _, action = insert_or_dedupe(
                conn, r, discovered_via=via,
                discovered_query=query_text, discovery_run_id=discovery_run_id,
                gap_template_id=q.get("gap_id", ""),
                voi_score=float(q.get("voi_score", 0.0)))
            if action == "inserted":   ins += 1; counts["inserted"] += 1
            elif action == "dedup_doi": dd_doi += 1; counts["dedup_doi"] += 1
            else:                       dd_t += 1; counts["dedup_title"] += 1
        print(f"  [{q['gap_id']:<35}] harvested={len(recs):>3} new={ins:>3} "
              f"dedup_doi={dd_doi} dedup_title={dd_t}")
        conn.commit()

    conn.execute(
        "UPDATE run_log SET finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
        "n_out=?, notes=? WHERE run_id=?",
        (counts["inserted"], json.dumps(counts), run_log_id))
    conn.commit()

    # Also dump raw search_results.json for the deliverable list
    out_json = DEFAULT_OUT_DIR / "search_results.json"
    out_json.parent.mkdir(parents=True, exist_ok=True)
    summary_rows = conn.execute(
        "SELECT reference_id, doi, title_raw, discovered_via, discovered_query, "
        "gap_template_id, voi_score, snippet FROM article_references "
        "WHERE discovery_run_id = ?", (discovery_run_id,),
    ).fetchall()
    out_json.write_text(json.dumps([dict(r) for r in summary_rows], indent=2),
                        encoding="utf-8")

    conn.close()
    print(f"\n{json.dumps(counts, indent=2)}")
    print(f"Wrote raw harvest dump: {out_json}")
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--queries", type=Path, default=DEFAULT_QUERIES)
    p.add_argument("--backend", choices=list(DISCOVERED_VIA), default="mock")
    p.add_argument("--per-query", type=int, default=10)
    p.add_argument("--top-n", type=int, default=10)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = p.parse_args()
    if not args.queries.exists():
        print(f"ERROR: {args.queries} not found", file=sys.stderr); sys.exit(1)
    run(args.queries, args.backend, args.per_query, args.top_n, args.db)


if __name__ == "__main__":
    main()
