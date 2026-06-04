# Track 2 VOI vs. Article Eater / BN VOI — comparison note

Author: Dhruv Sood · 2026-06-03

**Claim made by this repo:** the VOI score in `gap_extractor.py` is a **first-stage
search-ranking heuristic** — "which low-confidence, under-documented, possibly
central mechanisms should we search for first?" It is **not** a full
decision-theoretic value-of-information calculation, and this repo does not claim
it is.

## What Track 2 computes

```python
base             = 1.0 - confidence                       # lower confidence -> higher priority
centrality_bonus = 0.15 if framework_id == "cross_framework" else 0.0
temporal_bonus   = 0.08 if temporal in {chronic, long, years} else 0.0
coverage_penalty = min(word_count / 2000.0, 0.20)         # well-documented -> less urgent
voi              = base + centrality_bonus + temporal_bonus - coverage_penalty
```

A single scalar in [0, 1]. Good for a first pass; deliberately coarse.

## What the existing Article Eater / BN machinery computes (richer)

| System | Local file reviewed | Dimensions it separates |
|---|---|---|
| **Article Eater VOI search** | `../Article_Eater/src/services/voi_search.py` | **structural_voi** (counterfactual/coherence value of filling a structural gap) vs **epistemic_voi** (uncertainty reduction weighted by belief importance); gap-type priority (direction / validation / mechanism / boundary); combined VOI with gap-type-dependent alpha |
| **Article Eater research queue contract** | `../Article_Eater/contracts/research_queue.contract.md` | prioritized research targets with gap type, theory drivers, VOI score, priority rationale, suggested queries, cross-field terms, target databases, queue status, and closure metrics |
| **Article Eater paper-level VOI utility** | `../Article_Eater/src/cmr/voi_scoring.py` | finding-level VOI buckets for contradiction, gap, extension, and confirmation; aggregate paper-level expected information gain |
| **AE/AF bundle contract** | `../Article_Eater/contracts/ae_af/CLAUDE_HANDOFF_PROMPT.md` | not a VOI scorer, but it defines what real AE consumption means: AF creates an input bundle, AE produces `result.json` and extraction artifacts, and AF reads AE status rather than assuming success from a local file |

The earlier review also mentioned BN-style opportunity scoring dimensions
(`gap_severity`, `contestation`, `centrality`, `downstream_count`,
`feasibility`). Those are the correct conceptual comparison points even when
that separate BN repo is not mounted in this local COGS160 checkout.

## The cases the Track 2 scalar collapses (and the richer model distinguishes)
- A gap may be **uncertain but peripheral** (Track 2 over-ranks it).
- A gap may be **central but already well supported** (Track 2 may over- rank on centrality).
- A gap may be **contested** rather than merely under-studied (Track 2 has no contestation term).
- A gap may have large **downstream** consequences (Track 2 has no downstream term).
- A gap may be important but **hard to study** (Track 2 has no feasibility term).

In short: proper VOI asks *what expected posterior change / utility gain follows from
acquiring this information.* Track 2 asks *which mechanism looks weakly supported and
central.* The first is decision-theoretic; the second is a retrieval-priority heuristic.

## Where Track 2 fits
Use the Track 2 score as a **first-stage filter** over candidate search targets, **then**
defer to Article Eater / BN VOI for the final "which articles are most worth finding"
decision. Do not treat the scalar as the system VOI.

The clean future integration is to replace the `null` placeholders in
`voi_breakdown` by calling Article Eater's VOI services once the real AE/BN graph
is mounted. Until then, the Track 2 score is intentionally local: it chooses
which weak mechanism gaps to search first; it does not estimate expected
posterior change, downstream coherence impact, or study feasibility.

## Implemented now: `voi_breakdown` (transparency, not a claim of full VOI)
`gap_extractor.py` now emits a `voi_breakdown` object on every gap that names which
dimensions are **real** (computed here) and which are **placeholders** (would come from
the BN/AE services). Example:

```json
{
  "voi_score": 0.73,
  "voi_breakdown": {
    "local_confidence_gap": 0.58,     // REAL: 1 - confidence
    "evidence_sparsity":    0.40,     // REAL: coverage proxy from word_count
    "network_centrality":   0.15,     // REAL (coarse): cross_framework bonus
    "downstream_impact":    null,     // PLACEHOLDER: needs BN downstream_count
    "contestation":         null,     // PLACEHOLDER: needs BN contestation_score
    "feasibility":          null,     // PLACEHOLDER: needs BN feasibility_score
    "structural_voi":       null,     // PLACEHOLDER: needs Article Eater voi_search
    "epistemic_voi":        null      // PLACEHOLDER: needs Article Eater voi_search
  }
}
```

The `null` fields are an explicit, honest statement of what this heuristic does **not**
yet compute — not silent omission. Full integration with Article Eater's `voi_search`
structural/epistemic split is the documented next extension.
