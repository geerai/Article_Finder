#!/usr/bin/env python3
"""
ae_handoff.py — Task 3 LAST MILE: write the Article Eater handoff artefact.

Implements the Track 2 -> Article Eater boundary named in the rubric
(ka_track2_setup.html:101-102):

    "The Finder writes a well-defined handoff artefact (data/handoff/*.json)
     that the Eater reads; the contract between them is the only thing Track 2
     needs to honour."

Field schema: see TASK3_CONTRACT.md §0.1. It is a documented LOCAL schema — the
rubric names the artefact PATH, not its fields, so AF defines its own and labels
it as such. Fields AF cannot compute are emitted as null with a source_note,
never invented.

Pipeline position: runs AFTER abstract_triage (Stage 2) and pdf_acquirer
(Stage 3). Candidates are triage_decision='ACCEPT' rows with a non-empty
abstract. Because MISSING_ABSTRACT and ACCEPT are mutually exclusive, a
missing-abstract paper can NEVER produce a handoff file — this is the abstract
gate, and tests_task2_task3.py proves it.

Dedup: probe_pdf_against_article_eater() is the named rubric hook
(t2_task1.html:194 / t2_task3.html). The canonical implementation lives in
ae_waiting_room_probe.py on the instructor VM and checks pdf_identity_inventory;
that path is absent on this checkout, so the function here is the documented
local substitute (TASK3_CONTRACT.md §0) checking the local corpus surface.

Idempotent: a reference_id already in handoff_log (UNIQUE) or with an existing
artefact file is skipped. Re-running never duplicates.

Usage:
    python3 ae_handoff.py                 # write handoffs for all eligible rows
    python3 ae_handoff.py --db PATH --out DIR
"""
from __future__ import annotations

import argparse
import datetime as _dt
import json
import secrets
import sqlite3
from pathlib import Path

from db_schema import DEFAULT_DB, DEFAULT_OUT_DIR, log_transition, open_db

HANDOFF_DIR = DEFAULT_OUT_DIR / "handoff"

# The documented local handoff schema (TASK3_CONTRACT.md §0.1).
HANDOFF_SCHEMA_FIELDS = [
    "handoff_id", "article_id", "citation", "title", "doi", "abstract",
    "article_type", "topic", "subfocus_area", "source_note",
    "handoff_status", "blocked_reason", "created_at", "updated_at",
]


def _utcnow() -> str:
    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%fZ")


def probe_pdf_against_article_eater(
    conn: sqlite3.Connection,
    *,
    reference_id: str,
    doi: str | None = None,
) -> dict:
    """Local substitute for the Article Eater waiting-room probe.

    Canonical hook: probe_pdf_against_article_eater() in ae_waiting_room_probe.py
    (instructor VM) checks pdf_identity_inventory / pdf_corpus_inventory. Those
    inventory tables are not on this checkout, so this substitute checks the
    LOCAL corpus surface:
      - the handoff_log (already handed off?), and
      - any OTHER article_references row with the same DOI already acquired.

    Returns {"in_corpus": bool, "source": str}. A True result means the paper is
    already downstream and must not be handed off again (rubric: "a submission
    flow that stores duplicates is a bug").
    """
    row = conn.execute(
        "SELECT handoff_id FROM handoff_log WHERE reference_id = ?", (reference_id,)
    ).fetchone()
    if row:
        return {"in_corpus": True, "source": "handoff_log"}

    if doi:
        row = conn.execute(
            "SELECT reference_id FROM article_references "
            "WHERE doi = ? AND reference_id <> ? AND acquired_paper_id IS NOT NULL",
            (doi, reference_id),
        ).fetchone()
        if row:
            return {"in_corpus": True, "source": f"corpus:{row['reference_id']}"}

    return {"in_corpus": False, "source": "local_substitute"}


def build_handoff(row: sqlite3.Row, probe_source: str) -> dict:
    """Assemble one handoff object conforming to the §0.1 local schema."""
    now = _utcnow()
    obj = {
        "handoff_id": f"HANDOFF-{secrets.token_hex(4).upper()}",
        "article_id": row["reference_id"],
        "citation": row["raw_citation"],
        "title": row["title_raw"],
        "doi": row["doi"],
        "abstract": row["abstract"],
        # AF Task 3 triage does not persist a fine-grained article_type or a
        # subfocus column; emit null rather than invent (contract §0.1 rule).
        "article_type": None,
        "topic": row["gap_template_id"],   # the gap template is the topical origin
        "subfocus_area": None,
        "source_note": (
            f"discovered_via={row['discovered_via']}; "
            f"abstract_source={row['abstract_source']}; "
            f"voi_score={row['voi_score']}; probe={probe_source}"
        ),
        "handoff_status": "written",
        "blocked_reason": None,
        "created_at": now,
        "updated_at": now,
    }
    # Guarantee schema shape (no extra/missing keys).
    assert set(obj.keys()) == set(HANDOFF_SCHEMA_FIELDS), "handoff schema drift"
    return obj


def write_handoffs(
    conn: sqlite3.Connection,
    out_dir: Path = HANDOFF_DIR,
    *,
    agent: str = "ae_handoff",
) -> dict:
    """Write a handoff artefact for every eligible ACCEPT row. Idempotent.

    Returns {"written": [...], "skipped_idempotent": [...], "skipped_in_corpus":
    [...], "blocked_missing_abstract": [...]}.
    """
    out_dir.mkdir(parents=True, exist_ok=True)

    # The gate: ACCEPT + non-empty abstract. MISSING_ABSTRACT is excluded by
    # construction (it is a distinct triage_decision), so this query can never
    # return a missing-abstract paper.
    rows = conn.execute(
        "SELECT * FROM article_references "
        "WHERE triage_decision = 'ACCEPT' "
        "AND abstract IS NOT NULL AND length(trim(abstract)) > 0"
    ).fetchall()

    written: list[str] = []
    skipped_idempotent: list[str] = []
    skipped_in_corpus: list[str] = []

    for row in rows:
        rid = row["reference_id"]
        artefact = out_dir / f"{rid}.json"

        # Idempotency: already logged or file already on disk.
        already = conn.execute(
            "SELECT 1 FROM handoff_log WHERE reference_id = ?", (rid,)
        ).fetchone()
        if already or artefact.exists():
            skipped_idempotent.append(rid)
            continue

        # Dedup probe (rubric-named hook; local substitute).
        probe = probe_pdf_against_article_eater(conn, reference_id=rid, doi=row["doi"])
        if probe["in_corpus"]:
            log_transition(
                conn, reference_id=rid, from_stage=row["triage_stage"],
                to_stage="duplicate", agent=agent, outcome="skipped",
                notes=f"probe: already in corpus ({probe['source']})",
            )
            skipped_in_corpus.append(rid)
            continue

        obj = build_handoff(row, probe["source"])
        artefact.write_text(json.dumps(obj, indent=2))
        conn.execute(
            "INSERT INTO handoff_log (handoff_id, reference_id, artefact_path, "
            "handoff_status) VALUES (?,?,?,?)",
            (obj["handoff_id"], rid, str(artefact), "written"),
        )
        log_transition(
            conn, reference_id=rid, from_stage=row["triage_stage"],
            to_stage="handed_off", agent=agent, outcome="success",
            notes=f"artefact={artefact.name}",
        )
        written.append(rid)

    conn.commit()
    return {
        "written": written,
        "skipped_idempotent": skipped_idempotent,
        "skipped_in_corpus": skipped_in_corpus,
    }


def deliver_to_ae(artefact_path: Path, *, ack_timeout: float | None = None) -> dict:
    """Deliver ONE handoff artefact to a REAL Article Eater, IF one is configured.

    This is the integration seam that turns the documented local substitute into
    real AE ingestion without faking it. Resolution (honest, env-driven):
      - $AE_INGEST_CMD : a command; we run `<cmd> <artefact_path>`. AE consuming
        the artefact == the command exiting 0 (e.g. the instructor's
        `course_scaffolding.py ingest-handoff` or any AE ingest CLI).
      - $AE_INBOX : a directory the real AE watches; we copy the artefact in and,
        if $AE_ACK_TIMEOUT > 0, poll for AE to consume it (file moved out, or a
        `<name>.ack` / `processed/<name>` marker appears).
      - neither set : NOT delivered. The honest default on a checkout without AE;
        the local substitute (ae_inbox_stub.py) remains the tested boundary.

    Returns {"delivered": bool, "consumed": bool|None, "mode": str, "detail": str}.
    Never raises — a real AE error is surfaced in `detail`.
    """
    import os
    import shlex
    import shutil
    import subprocess
    import time

    cmd = os.environ.get("AE_INGEST_CMD")
    inbox = os.environ.get("AE_INBOX")
    if ack_timeout is None:
        try:
            ack_timeout = float(os.environ.get("AE_ACK_TIMEOUT", "0"))
        except ValueError:
            ack_timeout = 0.0

    if cmd:
        try:
            r = subprocess.run(shlex.split(cmd) + [str(artefact_path)],
                               capture_output=True, text=True, timeout=120)
        except Exception as e:  # noqa: BLE001
            return {"delivered": False, "consumed": False, "mode": "command",
                    "detail": f"AE_INGEST_CMD failed: {type(e).__name__}: {e}"}
        ok = r.returncode == 0
        return {"delivered": ok, "consumed": ok, "mode": "command",
                "detail": f"exit={r.returncode}; {((r.stdout or r.stderr) or '').strip()[:200]}"}

    if inbox:
        ibx = Path(inbox)
        try:
            ibx.mkdir(parents=True, exist_ok=True)
            dest = ibx / artefact_path.name
            shutil.copy2(artefact_path, dest)
        except Exception as e:  # noqa: BLE001
            return {"delivered": False, "consumed": False, "mode": "inbox",
                    "detail": f"copy to $AE_INBOX failed: {e}"}
        consumed = None
        if ack_timeout and ack_timeout > 0:
            consumed = False
            deadline = time.monotonic() + ack_timeout
            ack = ibx / (artefact_path.name + ".ack")
            processed = ibx / "processed" / artefact_path.name
            while time.monotonic() < deadline:
                if (not dest.exists()) or ack.exists() or processed.exists():
                    consumed = True
                    break
                time.sleep(0.5)
        return {"delivered": True, "consumed": consumed, "mode": "inbox",
                "detail": f"delivered to {dest}"
                          + ("" if consumed is None else f"; consumed={consumed}")}

    return {"delivered": False, "consumed": None, "mode": "local_substitute",
            "detail": "no real AE configured; set $AE_INBOX (a dir AE watches) or "
                      "$AE_INGEST_CMD (an AE ingest command) to enable real delivery"}


def main() -> None:
    ap = argparse.ArgumentParser(description="Write Article Eater handoff artefacts.")
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--out", type=Path, default=HANDOFF_DIR)
    ap.add_argument("--deliver", action="store_true",
                    help="Also deliver each artefact to a real AE if $AE_INBOX / "
                         "$AE_INGEST_CMD is set (else a no-op local substitute).")
    args = ap.parse_args()

    conn = open_db(args.db)
    try:
        res = write_handoffs(conn, args.out)
    finally:
        conn.close()

    if args.deliver:
        for rid in res["written"]:
            out = deliver_to_ae(args.out / f"{rid}.json")
            print(f"  → AE deliver {rid}: mode={out['mode']} delivered={out['delivered']} "
                  f"consumed={out['consumed']} ({out['detail']})")

    print(
        f"handoff: wrote {len(res['written'])}, "
        f"skipped {len(res['skipped_idempotent'])} (idempotent), "
        f"{len(res['skipped_in_corpus'])} (already in corpus)"
    )
    for rid in res["written"]:
        print(f"  ✓ {rid} -> data/handoff/{rid}.json")


if __name__ == "__main__":
    main()
