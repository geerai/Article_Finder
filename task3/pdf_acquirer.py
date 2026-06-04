#!/usr/bin/env python3
"""
pdf_acquirer.py — Task 3 Phase 5 (Stage 3).

Reads from `v_acquisition_queue` (ACCEPT rows whose acquired_paper_id is NULL),
walks the documented source cascade in order, and logs every attempt to
`lifecycle_transitions`. EDGE_CASE rows are intentionally NOT processed here.

Cascade (rubric §5A):
  1. Unpaywall          → discovered free OA
  2. OpenAlex OA URL    → publisher-hosted OA
  3. scidownl           → ONLY if all four policy conditions in §5B are met

scidownl gate (rubric §5B — all four required):
  (a) --enable-scidownl flag passed
  (b) `policy_clearance.json` present in repo root, countersigned by instructor
  (c) row's triage_decision == 'ACCEPT' (enforced by view)
  (d) Unpaywall AND OpenAlex BOTH already failed for this reference_id

The clearance file is git-ignored. Without it (the default state), every
attempt at Step 3 is denied with reason='gated:no_policy_clearance' and the
row stays in the queue with pdf_acquisition_attempts incremented.

Usage:
    python3 pdf_acquirer.py
    python3 pdf_acquirer.py --enable-scidownl    # still requires the file
"""

from __future__ import annotations
import argparse
import hashlib
import json
import os
import sqlite3
from pathlib import Path

from db_schema import open_db, DEFAULT_DB, DEFAULT_OUT_DIR, log_transition

UA = "ArticleFinderTrack2/1.0 (mailto:track2-test@example.edu)"

REPO_ROOT = Path(__file__).resolve().parent.parent
PDF_DIR = DEFAULT_OUT_DIR / "pdfs"
POLICY_CLEARANCE = REPO_ROOT / "policy_clearance.json"


# ─────────────────────────────────────────────────────────────────────────────
# Cascade steps — return (status, path, source_label)
# status ∈ {'hit','miss','skip'}
# ─────────────────────────────────────────────────────────────────────────────
def _download_and_store(url: str | None, ref_id: str, out_dir: Path,
                        timeout: int = 30) -> tuple[Path, str] | None:
    """GET a candidate PDF URL and persist it ONLY if the bytes are a real PDF
    (`%PDF-` magic). Returns (path, sha256) on success, else None. Never raises.
    This is the gate that stops an HTML paywall/error page being saved as a PDF.
    """
    if not url:
        return None
    try:
        import requests
        r = requests.get(url, timeout=timeout, allow_redirects=True,
                         headers={"User-Agent": UA, "Accept": "application/pdf,*/*"})
    except Exception:
        return None
    data = r.content or b""
    if r.status_code != 200 or data[:5] != b"%PDF-":
        return None
    sha = hashlib.sha256(data).hexdigest()
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{ref_id}.pdf"
    path.write_bytes(data)
    return (path, sha)


def try_unpaywall(doi: str | None, ref_id: str, out_dir: Path, *,
                  enable_network: bool) -> tuple[str, str | None, str | None, str]:
    """Unpaywall best-OA-location. Offline (default) → miss so the mock pipeline
    stays deterministic. With --enable-network → real api.unpaywall.org lookup
    and OA-PDF download. Returns (status, path, sha256, source)."""
    if not doi:
        return ("skip", None, None, "unpaywall")
    if not enable_network:
        return ("miss", None, None, "unpaywall")
    try:
        import requests
        email = os.environ.get("UNPAYWALL_EMAIL", "track2-test@example.edu")
        r = requests.get(f"https://api.unpaywall.org/v2/{doi}",
                         params={"email": email}, timeout=30,
                         headers={"User-Agent": UA})
        loc = ((r.json() or {}).get("best_oa_location") or {}) if r.status_code == 200 else {}
        url = loc.get("url_for_pdf") or loc.get("url")
    except Exception:
        return ("miss", None, None, "unpaywall")
    got = _download_and_store(url, ref_id, out_dir)
    return ("hit", str(got[0]), got[1], "unpaywall") if got else ("miss", None, None, "unpaywall")


def try_openalex_oa(doi: str | None, ref_id: str, out_dir: Path, *,
                    enable_network: bool) -> tuple[str, str | None, str | None, str]:
    """OpenAlex open-access location. Offline → miss; --enable-network → real
    api.openalex.org lookup and OA-PDF download. Returns (status, path, sha256, source)."""
    if not doi:
        return ("skip", None, None, "openalex_oa")
    if not enable_network:
        return ("miss", None, None, "openalex_oa")
    try:
        import requests
        email = os.environ.get("OPENALEX_EMAIL", "track2-test@example.edu")
        r = requests.get(f"https://api.openalex.org/works/doi:{doi}",
                         params={"mailto": email}, timeout=30,
                         headers={"User-Agent": UA})
        j = (r.json() or {}) if r.status_code == 200 else {}
        loc = j.get("best_oa_location") or {}
        url = loc.get("pdf_url") or (j.get("open_access") or {}).get("oa_url")
    except Exception:
        return ("miss", None, None, "openalex_oa")
    got = _download_and_store(url, ref_id, out_dir)
    return ("hit", str(got[0]), got[1], "openalex_oa") if got else ("miss", None, None, "openalex_oa")


# ─────────────────────────────────────────────────────────────────────────────
# scidownl 4-condition policy gate
# ─────────────────────────────────────────────────────────────────────────────
def scidownl_gate(*, enable_flag: bool, doi: str | None,
                  prior_failed: bool) -> tuple[bool, str]:
    if not enable_flag:
        return (False, "config_flag_off")
    if not POLICY_CLEARANCE.exists():
        return (False, "no_policy_clearance")
    if not doi:
        return (False, "no_doi")
    if not prior_failed:
        return (False, "prior_cascade_not_exhausted")
    return (True, "gate_open")


def try_scidownl(doi: str, out_dir: Path) -> tuple[str, str | None, str | None, str]:
    """Gated stub. scidownl is a last-resort shadow-library path; per the rubric
    it stays default-closed behind the 4-condition gate and is NOT implemented as
    a live downloader. Returns (status, path, sha256, source)."""
    return ("miss", None, None, "scidownl")


# ─────────────────────────────────────────────────────────────────────────────
# Driver
# ─────────────────────────────────────────────────────────────────────────────
def run(db_path: Path, enable_scidownl: bool, pdf_dir: Path,
        enable_network: bool = False) -> dict:
    pdf_dir.mkdir(parents=True, exist_ok=True)
    conn = open_db(db_path)
    cur = conn.execute("INSERT INTO run_log (stage) VALUES ('pdf')")
    run_id = cur.lastrowid; conn.commit()

    rows = conn.execute("SELECT * FROM v_acquisition_queue").fetchall()

    counts = {"queue": len(rows), "got": 0, "no_oa": 0,
              "scidownl_blocked": 0, "scidownl_attempted": 0}

    for r in rows:
        ref_id, doi = r["reference_id"], r["doi"]
        result = None  # (src, path, sha)
        for fn in (try_unpaywall, try_openalex_oa):
            status, path, sha, src = fn(doi, ref_id, pdf_dir, enable_network=enable_network)
            conn.execute(
                "UPDATE article_references SET pdf_acquisition_attempts=pdf_acquisition_attempts+1, "
                "pdf_acquisition_last_source=? WHERE reference_id=?", (src, ref_id))
            log_transition(conn, reference_id=ref_id,
                           from_stage="abstract_collected",
                           to_stage=f"pdf_attempt:{src}",
                           agent="pdf_acquirer", outcome=status,
                           notes=f"source={src}" + (f" sha={sha[:12]}" if sha else ""))
            if status == "hit":
                result = (src, path, sha); break

        if result is None:
            ok, reason = scidownl_gate(enable_flag=enable_scidownl,
                                       doi=doi, prior_failed=True)
            if ok:
                status, path, sha, _ = try_scidownl(doi, pdf_dir)
                counts["scidownl_attempted"] += 1
                conn.execute(
                    "UPDATE article_references SET pdf_acquisition_attempts=pdf_acquisition_attempts+1, "
                    "pdf_acquisition_last_source='scidownl' WHERE reference_id=?",
                    (ref_id,))
                log_transition(conn, reference_id=ref_id,
                               from_stage="pdf_attempt:openalex_oa",
                               to_stage="pdf_attempt:scidownl",
                               agent="pdf_acquirer",
                               outcome=status, notes="gate_open")
                if status == "hit":
                    result = ("scidownl", path, sha)
                else:
                    counts["no_oa"] += 1
            else:
                conn.execute(
                    "UPDATE article_references SET pdf_acquisition_last_source=? "
                    "WHERE reference_id=?", (f"gated:{reason}", ref_id))
                log_transition(conn, reference_id=ref_id,
                               from_stage="pdf_attempt:openalex_oa",
                               to_stage="pdf_attempt:scidownl",
                               agent="pdf_acquirer", outcome="gated",
                               notes=f"reason={reason}")
                counts["scidownl_blocked"] += 1

        if result is not None:
            src, path, sha = result
            conn.execute(
                "UPDATE article_references SET acquired_paper_id=?, pdf_path=?, "
                "pdf_sha256=?, triage_stage='acquired', "
                "updated_at=strftime('%Y-%m-%dT%H:%M:%fZ','now') "
                "WHERE reference_id=?",
                (f"paper:{ref_id}", str(path) if path else None, sha, ref_id))
            log_transition(conn, reference_id=ref_id,
                           from_stage="abstract_collected", to_stage="acquired",
                           agent="pdf_acquirer", outcome="success",
                           notes=f"final_source={src}" + (f" sha256={sha}" if sha else ""))
            counts["got"] += 1

    conn.commit()
    conn.execute(
        "UPDATE run_log SET finished_at=strftime('%Y-%m-%dT%H:%M:%fZ','now'), "
        "n_in=?, n_out=?, notes=? WHERE run_id=?",
        (counts["queue"], counts["got"], json.dumps(counts), run_id))
    conn.commit(); conn.close()

    print(f"PDF acquisition (queue size={counts['queue']}):")
    for k, v in counts.items():
        print(f"  {k:<20} {v}")
    print(f"  scidownl gate: enable_flag={enable_scidownl} "
          f"clearance_file={'YES' if POLICY_CLEARANCE.exists() else 'NO'}")
    return counts


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--enable-scidownl", action="store_true",
                   help="Open the scidownl gate (still requires policy_clearance.json)")
    p.add_argument("--enable-network", action="store_true",
                   help="Make real Unpaywall/OpenAlex calls + download OA PDFs "
                        "(default off keeps mock runs deterministic/offline)")
    p.add_argument("--pdf-dir", type=Path, default=PDF_DIR)
    args = p.parse_args()
    run(args.db, args.enable_scidownl, args.pdf_dir, enable_network=args.enable_network)


if __name__ == "__main__":
    main()
