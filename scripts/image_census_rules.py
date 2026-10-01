"""The rules the three image-ledger scripts share, written once.

``post_deploy_fiction_census.py``, ``image_rollup_dedupe_census.py`` and
``image_spend_provider_census.py`` each need two things the producer no longer has, and each used
to carry its own copy (#181 round-2 design lens, SHOULD-FIX 2: three copies of the per-clip rule
that already differed in how they read ``diagram_debug.kind``, and one owner classifier under two
names):

* **the LEGACY per-clip rule.** Every ``costs.visuals_images`` stamp written before workers #3239
  priced the clips the plan showed, once per CLIP, with ``worker._sum_visuals_image_cost``. #3239
  deleted that function, so the only way to read a legacy stamp by its own definition is this
  frozen copy. It is frozen on purpose: it describes stamps that already exist, so it must not
  follow the producer. Its rules, as the deleted function had them: skip a reused imagination-tree
  re-cut; price ``rendered_model_id`` over ``model_id``; price an ``ai_generated`` clip whose
  ``diagram_debug.kind`` is ``relimage`` at the relimage estimate; anything else is not paid.
* **the owner class** of a job, so a census can say whose traffic it measured without printing an
  address: ``test@`` / ``e2e`` / ``founder`` / ``other`` / ``unknown``. ``KFU_FOUNDER_EMAILS``
  names the founder's personal accounts and is never committed.

The CURRENT rule, how a ledger stamp is read, is not here: the scripts call workers'
``image_cost_ledger`` (``paid_assets``, ``asset_id``, ``clip_price``) directly.
"""

from __future__ import annotations

import os
from typing import Any, Callable, Optional

#: The model key the legacy rule priced a relimage under (the deleted function's literal).
RELIMAGE = "relimage"

_FOUNDER_EMAILS = frozenset(
    e.strip().lower() for e in os.environ.get("KFU_FOUNDER_EMAILS", "").split(",") if e.strip()
)


def legacy_clip_model(clip: Any) -> Optional[str]:
    """The model key the pre-#3239 per-clip rule priced ``clip`` under, or ``None``."""
    if not isinstance(clip, dict):
        return None
    ev = clip.get("imagination_event")
    if isinstance(ev, dict) and ev.get("reused") is True:
        return None
    mid = clip.get("rendered_model_id") or clip.get("model_id")
    if mid:
        return str(mid)
    # The deleted function read ``(c.get("diagram_debug") or {}).get("kind")`` inside a per-clip
    # ``try``, so a non-dict ``diagram_debug`` raised and the clip was skipped: the same answer.
    dd = clip.get("diagram_debug") or {}
    if isinstance(dd, dict) and clip.get("ai_generated") and str(dd.get("kind") or "") == RELIMAGE:
        return RELIMAGE
    return None


def legacy_per_clip_count(clips: Any) -> int:
    """How many clips the pre-#3239 rule priced: one per CLIP, a revisit once per showing."""
    return sum(1 for c in (clips if isinstance(clips, list) else ()) if legacy_clip_model(c))


def legacy_per_clip_usd(clips: Any, price: Callable[[str], float]) -> float:
    """What the pre-#3239 rule stamped, priced by ``price(model)``."""
    return sum(price(m) for m in (legacy_clip_model(c) for c in (clips if isinstance(clips, list) else ())) if m)


def owner_class(db: Any, user_id: str, cache: dict) -> str:
    """test@ / e2e / founder / other / unknown — never the address itself."""
    if user_id in cache:
        return cache[user_id]
    label = "unknown"
    try:
        u = db.collection("users").document(user_id).get()
        email = str(((u.to_dict() or {}) if u.exists else {}).get("email") or "").lower()
        # The E2E harness signs in as the same test@ address under its own user id (``…_e2e``),
        # so the id decides before the address does.
        if "e2e" in user_id.lower() or "e2e" in email:
            label = "e2e"
        elif email.startswith("test@"):
            label = "test@"
        elif email and email in _FOUNDER_EMAILS:
            label = "founder"
        elif email:
            label = "other"
    except Exception:  # noqa: BLE001 — a label lookup must not stop the census
        label = "unknown"
    cache[user_id] = label
    return label
