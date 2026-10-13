"""Plain-text → CRM entity resolution (Phase 4 §4.2.1, first cut).

Three steps per plan v2.1:

1. NER (regex match against known CRM record names — first cut keeps it
   simple; LLM fallback deferred to Phase 4.5).
2. Identity resolve via ``hermes/crm/memory_identity`` — but its
   ``resolve_for_insert`` API is for write-side dedup, NOT plain-text
   resolution. Day 6-7 only ships step 1 + a stub step 3; Week 3 adds
   real identity scoring.
3. Disambiguation when one mention has multiple candidates — for now,
   pick the candidate the user most-recently interacted with (cheap
   heuristic that beats picking arbitrary).

Output: ``List[ExtractedEntity]`` — one per resolved mention. Caller
decides how to inject into the hermes system prompt.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

from hermes.crm.db import db_session

logger = logging.getLogger(__name__)


# CRM modules whose record names we'll scan against. Order matters
# slightly — earlier modules win on ties (accounts > potentials >
# contacts is the rough sales-pipeline precedence).
_NER_MODULES: Tuple[str, ...] = (
    "accounts", "potentials", "contacts", "leads", "salesorders",
)


@dataclass
class ExtractedEntity:
    module: str
    record_id: int
    name: str
    confidence: float           # 1.0 = exact regex match; lower if fuzzy
    disambig_needed: bool = False


def extract_entities(
    text: str, user_id: int, *, max_results: int = 5,
) -> List[ExtractedEntity]:
    """Return up to ``max_results`` entities mentioned in ``text``.

    Day 6-7 first cut: regex/substring match against name columns of
    the CRM modules in ``_NER_MODULES``. Each match is exact (no
    fuzzy). When a name matches multiple records (e.g. "麒麟" → 麒麟
    汽车 OR 麒麟科技), we pick the one with the most-recent interaction
    by ``user_id`` and flag ``disambig_needed=True`` for the caller.

    Empty result is normal — most chat messages don't reference a
    specific record. Caller should fall back to "no record context".
    """
    if not text or len(text) < 2:
        return []
    out: List[ExtractedEntity] = []
    seen_keys: set = set()  # de-dup (module, record_id) across modules

    try:
        with db_session() as db:
            for module in _NER_MODULES:
                pk_col = f"{module}_id"
                name_col = f"{module}_name"
                rows = db.fetchall(
                    f"SELECT {pk_col} AS id, {name_col} AS name "
                    f"  FROM {module} "
                    f" WHERE deleted = false AND {name_col} IS NOT NULL "
                    f"   AND {name_col} <> ''",
                ) or []
                for r in rows:
                    name = str(r["name"]).strip()
                    if not name or len(name) < 2:
                        continue
                    if name not in text:
                        continue
                    key = (module, int(r["id"]))
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                    out.append(ExtractedEntity(
                        module=module,
                        record_id=int(r["id"]),
                        name=name,
                        confidence=1.0,
                    ))
                    if len(out) >= max_results:
                        return out
    except Exception:
        logger.exception("entity_extract failed user=%s", user_id)
        return []
    return _disambiguate(out, user_id)


def _disambiguate(
    entities: List[ExtractedEntity], user_id: int,
) -> List[ExtractedEntity]:
    """When two entities share the same ``name``, mark them as
    ambiguous. Caller is expected to ask a clarification turn rather
    than guess. Order-preserving."""
    by_name: dict = {}
    for e in entities:
        by_name.setdefault(e.name, []).append(e)
    if not any(len(v) > 1 for v in by_name.values()):
        return entities  # no collisions
    # Mark ambiguous entries — leave order intact.
    out = []
    for e in entities:
        if len(by_name[e.name]) > 1:
            out.append(ExtractedEntity(
                module=e.module, record_id=e.record_id, name=e.name,
                confidence=e.confidence * 0.7,
                disambig_needed=True,
            ))
        else:
            out.append(e)
    return out
