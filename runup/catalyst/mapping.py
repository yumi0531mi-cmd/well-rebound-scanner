"""Runup V2 sponsor·issuer mapping 후보 (Step 05). 확정은 검토 단계다.

인공 confidence 점수를 만들지 않는다. 근거·사유만 기록한다.
"""
from __future__ import annotations

import dataclasses

from runup.domain.base import FrozenDTO

_MATCH_KINDS = ("exact", "alias", "partial", "hospital", "none")


@dataclasses.dataclass(frozen=True)
class MappingProposal(FrozenDTO):
    proposal_id: str
    sponsor_text: str
    reason: str
    review_status: str
    evidence: tuple = ()
    issuer_id: str | None = None
    security_id: str | None = None


MappingProposal.__kind_hints__ = {
    "proposal_id": "id", "sponsor_text": "str", "reason": "str",
    "review_status": "str", "evidence": "tuple[str]",
    "issuer_id": "str?", "security_id": "str?",
}


def propose_mapping(sponsor_text, candidates) -> list:
    """후보 관계만 만든다. 자동 확정 없음. 전부 REVIEW 대기.

    candidates: [(issuer_id, security_id|None, match_kind, evidence)].
    match_kind은 exact/alias/partial/hospital/none 중 하나다.
    """
    proposals = []
    for issuer_id, security_id, match_kind, evidence in candidates:
        if match_kind not in _MATCH_KINDS:
            raise ValueError(f"unknown match kind: {match_kind}")
        if match_kind == "none" or issuer_id is None:
            reason = (f"sponsor '{sponsor_text}'에 연결된 상장 issuer 없음 "
                      f"({match_kind}). 검토 필요.")
        else:
            reason = (f"sponsor '{sponsor_text}'와 issuer {issuer_id} 관계 "
                      f"후보 ({match_kind}). 부분일치·유사명만으로 확정 금지.")
        proposals.append(MappingProposal(
            proposal_id=f"{sponsor_text}::{issuer_id or 'unmapped'}",
            sponsor_text=str(sponsor_text), reason=reason,
            review_status="REVIEW",
            evidence=tuple(evidence or ()),
            issuer_id=issuer_id, security_id=security_id))
    return proposals


def requires_review(proposal: MappingProposal) -> bool:
    return proposal.review_status in ("PENDING", "REVIEW")


def review_mapping(proposal: MappingProposal, reviewer: str, decision: str,
                   evidence=()) -> dict:
    """검토 기록 builder. 확정 저장은 Step11 이후 저장소가 담당."""
    if decision not in ("APPROVED", "REJECTED"):
        raise ValueError(f"unknown review decision: {decision}")
    if not reviewer:
        raise ValueError("reviewer가 필요하다")
    return {"proposal_id": proposal.proposal_id,
            "sponsor_text": proposal.sponsor_text,
            "issuer_id": proposal.issuer_id,
            "security_id": proposal.security_id,
            "decision": decision, "reviewer": reviewer,
            "evidence": list(evidence or ()),
            "status": "REVIEWED"}
