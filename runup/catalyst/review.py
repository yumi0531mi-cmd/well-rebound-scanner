"""Runup V2 검토 command (Step 11). provenance·권한 계약 준수."""
from __future__ import annotations


def approve_review(command, auth_context, expected_revision,
                   current_status="PENDING"):
    """검토 승인. (record|None, DomainIssue|None).

    auth 미검증·권한 없음·ID 불일치·근거 없음은 거부한다.
    """
    from runup.domain.base import DomainIssue
    from runup.domain.enums import Severity

    def _deny(code, path, message):
        return None, DomainIssue(
            code=code, path=path, message=message,
            severity=Severity.CRITICAL, evidence_ids=())

    auth = auth_context or {}
    if not auth.get("verified"):
        return _deny("REVIEW_REJECTED", "review.auth",
                     "검증되지 않은 auth")
    permissions = auth.get("permissions") or ()
    if "review" not in permissions and "admin" not in permissions:
        return _deny("REVIEW_REJECTED", "review.auth",
                     "review 권한 없음")
    if not command.get("command_id"):
        return _deny("MISSING", "review.command_id", "command_id 없음")
    if not command.get("evidence_document_ids"):
        return _deny("MISSING", "review.evidence",
                     "근거 원문 없음")
    if (expected_revision is not None
            and command.get("expected_revision") != expected_revision):
        return _deny("IDEMPOTENCY_CONFLICT", "review.expected_revision",
                     "revision 불일치")
    if command.get("decision") not in ("APPROVED", "REJECTED"):
        return _deny("TYPE_ERROR", "review.decision",
                     "APPROVED/REJECTED만 가능")
    return {"command_id": command["command_id"],
            "candidate_id": command.get("candidate_id"),
            "revision_id": command.get("revision_id"),
            "decision": command["decision"],
            "reviewer": auth.get("actor_id", "unknown"),
            "evidence_document_ids": list(
                command["evidence_document_ids"]),
            "previous_status": current_status,
            "status": "REVIEWED"}, None
