"""
scripts/debug_score.py
───────────────────────
Drop this in your project root and run:
    python scripts/debug_score.py <sha_claim_id>

It bypasses the HTTP layer entirely and runs the scoring pipeline directly,
printing exactly what each detector sees and why flags fire or don't.

Usage:
    python scripts/debug_score.py SHA-CLM-2025-00087241
"""

import asyncio
import sys
import os

# Resolve the project root (the directory that contains the 'app' package)
# Works regardless of whether the script lives in project_root/scripts/
# or project_root/app/scripts/
_this_file = os.path.abspath(__file__)  # .../app/scripts/debug_score.py
_scripts_dir = os.path.dirname(_this_file)  # .../app/scripts/
_maybe_app = os.path.dirname(_scripts_dir)  # .../app/   OR project_root
_project_root = os.path.dirname(_maybe_app)  # project_root (one more level up)

# Insert both candidates — whichever one has an 'app' subdirectory wins
for candidate in (_maybe_app, _project_root):
    if os.path.isdir(os.path.join(candidate, "app")):
        sys.path.insert(0, candidate)
        break
else:
    # Last resort: just add the grandparent of this file
    sys.path.insert(0, _project_root)

SHA_CLAIM_ID = sys.argv[1] if len(sys.argv) > 1 else "SHA-CLM-2025-00087241"


async def main():
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    from app.core.database import AsyncSessionLocal
    from app.models.claim_model import Claim
    from app.models.fraud_score_model import FraudScore
    from app.models.claim_service_model import ClaimService
    from app.services.fraud_service import FraudService, _xgb_model, _county_encoder
    from app.services.fraud_service import load_ml_artifacts

    SEP = "─" * 60

    print(f"\n{SEP}")
    print(f"  DEBUG SCORING: {SHA_CLAIM_ID}")
    print(SEP)

    async with AsyncSessionLocal() as db:

        # ── Step 1: Check the claim exists ────────────────────────────────────
        result = await db.execute(
            select(Claim).filter(Claim.sha_claim_id == SHA_CLAIM_ID)
        )
        raw_claim = result.scalars().first()
        if not raw_claim:
            print(f"\nERROR: Claim '{SHA_CLAIM_ID}' not found in DB.")
            print("Run the ingest POST first, then re-run this script.")
            return

        print(f"\n[1] Claim found in DB: id={raw_claim.id}")
        print(f"    sha_status       : {raw_claim.sha_status}")
        print(f"    claim_type       : {raw_claim.claim_type}")
        print(f"    total_amount     : {raw_claim.total_claim_amount}")
        print(f"    provider_id      : {raw_claim.provider_id}")
        print(f"    member_id        : {raw_claim.member_id}")

        # ── Step 2: Fetch WITH selectinload ───────────────────────────────────
        result = await db.execute(
            select(Claim)
            .options(
                selectinload(Claim.provider),
                selectinload(Claim.member),
                selectinload(Claim.services),
                selectinload(Claim.features),
                selectinload(Claim.fraud_scores),
                selectinload(Claim.fraud_case),
            )
            .filter(Claim.sha_claim_id == SHA_CLAIM_ID)
        )
        claim = result.scalars().first()

        # ── Step 3: Relationship check ────────────────────────────────────────
        print(f"\n[2] Relationship check (this is the #1 failure point):")
        print(f"    claim.provider   : {claim.provider}")
        print(f"    claim.member     : {claim.member}")
        print(f"    claim.services   : {claim.services}")
        print(f"    services count   : {len(claim.services) if claim.services else 0}")

        if claim.provider:
            print(f"\n    Provider detail:")
            print(f"      name           : {claim.provider.name}")
            print(f"      county         : {claim.provider.county}")
            print(
                f"      facility_level : {getattr(claim.provider, 'facility_level', 'NOT SET')}"
            )
            print(
                f"      phone_number   : {getattr(claim.provider, 'phone_number', 'NOT SET')}"
            )
            print(
                f"      phone          : {getattr(claim.provider, 'phone', 'NOT SET')}"
            )
            print(
                f"      latitude       : {getattr(claim.provider, 'latitude', 'NOT SET')}"
            )
            print(
                f"      license_number : {getattr(claim.provider, 'license_number', 'NOT SET')}"
            )
        else:
            print("\n    !! claim.provider is None — GhostProvider will return 0")

        if claim.member:
            print(f"\n    Member detail:")
            print(f"      sha_member_id  : {claim.member.sha_member_id}")
            print(f"      coverage_status: {claim.member.coverage_status}")
            print(f"      national_id    : {claim.member.national_id}")
            print(f"      date_of_birth  : {claim.member.date_of_birth}")
        else:
            print("\n    !! claim.member is None — PhantomPatient will return 0")

        if claim.services:
            print(f"\n    Services ({len(claim.services)}):")
            for s in claim.services:
                print(
                    f"      {s.service_code:<22} qty={s.quantity}  "
                    f"unit={s.unit_price}  total={s.total_price}"
                )
        else:
            print(
                "\n    !! claim.services is empty — UpcodingDetector returns immediately"
            )

        # ── Step 4: ML artifacts check ────────────────────────────────────────
        print(f"\n[3] ML artifacts:")
        load_ml_artifacts()
        # Re-import after load
        from app.services.fraud_service import _xgb_model as xgb, _county_encoder as enc

        print(
            f"    _xgb_model       : {'LOADED' if xgb else 'NOT LOADED — will use fallback'}"
        )
        print(
            f"    _county_encoder  : {enc if enc else 'NOT LOADED — hash() fallback will be used'}"
        )

        # ── Step 5: Run feature service ───────────────────────────────────────
        print(f"\n[4] Running FeatureService.compute_features()...")
        from app.services.feature_service import FeatureService

        try:
            features = await FeatureService.compute_features(
                db, claim, triggered_by=None
            )
            print(f"    length_of_stay           : {features.length_of_stay}")
            print(f"    service_count            : {features.service_count}")
            print(
                f"    has_lab_without_diagnosis: {features.has_lab_without_diagnosis}"
            )
            print(
                f"    has_surgery_without_theatre: {features.has_surgery_without_theatre}"
            )
            print(f"    duplicate_within_7d      : {features.duplicate_within_7d}")
            print(f"    member_visits_30d        : {features.member_visits_30d}")
            print(f"    provider_cost_zscore     : {features.provider_cost_zscore}")
            print(f"    diagnosis_cost_zscore    : {features.diagnosis_cost_zscore}")
            print(f"    submitted_hour           : {features.submitted_hour}")
            print(f"    eligibility_checked      : {features.eligibility_checked}")
        except Exception as e:
            print(f"    !! FeatureService FAILED: {e}")
            features = None

        # ── Step 6: Run each detector individually ────────────────────────────
        print(f"\n[5] Running detectors individually:")
        from app.detectors.phantom_patient_detector import PhantomPatientDetector
        from app.detectors.upcoding_detector import UpcodingDetector
        from app.detectors.ghost_provider_detector import GhostProviderDetector
        from app.detectors.duplicate_detector import DuplicateDetector
        from app.detectors.provider_profiler_detector import ProviderProfiler

        detectors = [
            ("PhantomPatientDetector", PhantomPatientDetector(db)),
            ("DuplicateDetector", DuplicateDetector(db)),
            ("UpcodingDetector", UpcodingDetector(db)),
            ("ProviderProfiler", ProviderProfiler(db)),
            ("GhostProviderDetector", GhostProviderDetector(db)),
        ]

        for name, detector in detectors:
            print(f"\n  [{name}]")
            try:
                result = await detector.detect(claim, features)
                print(f"    fired      : {result.fired}")
                print(f"    score      : {result.score}")
                print(f"    explanation: {result.explanation}")
                if result.metadata:
                    # Print key metadata fields only
                    for k in (
                        "flags",
                        "flag_reasons",
                        "method_scores",
                        "signal_scores",
                        "missing_fields",
                        "risk_category",
                    ):
                        if k in result.metadata:
                            print(f"    {k}: {result.metadata[k]}")
            except Exception as e:
                import traceback

                print(f"    !! DETECTOR RAISED EXCEPTION: {e}")
                traceback.print_exc()

        # ── Step 7: Full pipeline score ───────────────────────────────────────
        print(f"\n[6] Running full FraudService.score_claim()...")
        try:
            engine = FraudService(db)
            fraud_score = await engine.score_claim(
                claim,
                scored_by="debug",
                triggered_by_user_id=None,
            )
            print(f"\n  FINAL SCORE      : {fraud_score.final_score}")
            print(f"  RISK LEVEL       : {fraud_score.risk_level}")
            print(f"  rule_score       : {fraud_score.rule_score}")
            print(f"  ml_probability   : {fraud_score.ml_probability}")
            print(f"  detector_scores  : {fraud_score.detector_scores}")
        except Exception as e:
            import traceback

            print(f"\n  !! FraudService.score_claim RAISED: {e}")
            traceback.print_exc()

    print(f"\n{SEP}\n")


if __name__ == "__main__":
    asyncio.run(main())
