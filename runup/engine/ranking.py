"""Runup V2 후보 ranking (Step 22). eligible만 순위화한다."""
from __future__ import annotations


def rank(candidates, weights, min_importance=50):
    """가중 평균 순위. 동률은 rank·유동성·이른 경계·ID 순.

    candidate: {security_id, strength, setup, importance, adv,
    boundary, event_ids}. strength/setup/importance 없으면 제외.
    """
    scored = []
    total_weight = sum(weights.get(k, 0) for k in
                       ("strength", "setup", "importance"))
    if total_weight <= 0:
        return []
    for candidate in candidates:
        strength = candidate.get("strength")
        setup = candidate.get("setup")
        importance = candidate.get("importance")
        if strength is None or setup is None or importance is None:
            continue
        try:
            strength, setup, importance = (float(strength), float(setup),
                                           float(importance))
        except (TypeError, ValueError):
            continue
        if importance < min_importance:
            continue
        score = (weights.get("strength", 0) * strength
                 + weights.get("setup", 0) * setup
                 + weights.get("importance", 0) * importance) / total_weight
        scored.append((score, candidate))
    best = {}
    for score, candidate in scored:
        key = candidate.get("security_id")
        if key not in best or score > best[key][0]:
            best[key] = (score, candidate)
    ranked = sorted(
        best.values(),
        key=lambda item: (-item[0], -(item[1].get("adv") or 0),
                          str(item[1].get("boundary") or "9999-12-31"),
                          str(item[1].get("security_id"))))
    return [{"security_id": candidate.get("security_id"),
             "rank_score": score,
             "strength": candidate.get("strength"),
             "setup": candidate.get("setup"),
             "importance": candidate.get("importance"),
             "adv": candidate.get("adv"),
             "boundary": candidate.get("boundary"),
             "event_ids": list(candidate.get("event_ids") or ()),
             "decision_id": candidate.get("decision_id"),
             "issuer_id": candidate.get("issuer_id"),
             "sector": candidate.get("sector")}
            for score, candidate in ranked]
