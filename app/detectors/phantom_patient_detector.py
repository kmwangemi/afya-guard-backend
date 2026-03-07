"""
SHA Fraud Detection — Phantom Patient Detector

Detects claims submitted for members who don't exist in SHA records, have
suspicious demographic data, or whose coverage is inactive / expired.

Fraud pattern: providers fabricate patient records to bill for services never rendered.
"""

from datetime import date, datetime
from typing import Dict, Optional

from app.detectors.base_detector import BaseDetector, DetectorResult
from app.models.claim_feature_model import ClaimFeature
from app.models.claim_model import Claim
from app.models.member_model import Member


class PhantomPatientDetector(BaseDetector):
    """
    Scoring logic:
        - Member not found in DB              → 100 (hard stop)
        - Member coverage INACTIVE/SUSPENDED  → +60
        - Member has no national_id on record → +20
        - Member date_of_birth missing        → +10
        - Age calculated > 120 or < 0        → +30 (data integrity issue)
        - Claim date before member's DOB      → +40
    """

    INACTIVE_STATUSES = {"INACTIVE", "SUSPENDED", "EXPIRED", "CANCELLED"}

    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        if not claim.member:
            return DetectorResult(
                detector_name=self.name,
                score=100.0,
                fired=True,
                explanation="Claim submitted for a member that does not exist in the system",
                feature_name="member_exists",
                feature_value="False",
                metadata={"member_id": str(claim.member_id)},
            )
        member: Member = claim.member
        score: float = 0.0
        flags: list = []
        # ── Coverage status ───────────────────────────────────────────────────
        if (
            member.coverage_status
            and member.coverage_status.upper() in self.INACTIVE_STATUSES
        ):
            score += 60.0
            flags.append(f"Coverage is {member.coverage_status}")
        # ── National ID missing ───────────────────────────────────────────────
        if not member.national_id:
            score += 20.0
            flags.append("National ID not on record")
        # ── Date of birth missing ─────────────────────────────────────────────
        if not member.date_of_birth:
            score += 10.0
            flags.append("Date of birth missing")
        else:
            today = date.today()
            age = (today - member.date_of_birth).days // 365
            # Impossible age
            if age < 0 or age > 120:
                score += 30.0
                flags.append(f"Impossible age calculated: {age} years")
            # Claim submitted before member was born
            admission = (
                claim.admission_date.date()
                if isinstance(claim.admission_date, datetime)
                else claim.admission_date
            )
            dob = (
                member.date_of_birth.date()
                if isinstance(member.date_of_birth, datetime)
                else member.date_of_birth
            )
            if admission and dob and admission < dob:
                score += 40.0
                flags.append("Admission date is before member's date of birth")
            # if claim.admission_date and claim.admission_date < member.date_of_birth:
            #     score += 40.0
            #     flags.append("Admission date is before member's date of birth")
        score = min(score, 100.0)
        fired = score > 0
        explanation = (
            "Phantom patient signals detected: " + "; ".join(flags)
            if flags
            else "Member record appears valid"
        )
        return DetectorResult(
            detector_name=self.name,
            score=round(score, 4),
            fired=fired,
            explanation=explanation,
            feature_name="member_validity_score",
            feature_value=str(round(score, 2)),
            metadata={
                "sha_member_id": member.sha_member_id,
                "coverage_status": member.coverage_status,
                "has_national_id": bool(member.national_id),
                "has_dob": bool(member.date_of_birth),
                "flags": flags,
            },
        )

    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        if not claim.member:
            return {"member_exists": 0.0}
        member = claim.member
        inactive = (
            member.coverage_status
            and member.coverage_status.upper() in self.INACTIVE_STATUSES
        )
        return {
            "member_exists": 1.0,
            "coverage_active": 0.0 if inactive else 1.0,
            "has_national_id": 1.0 if member.national_id else 0.0,
            "has_date_of_birth": 1.0 if member.date_of_birth else 0.0,
        }
