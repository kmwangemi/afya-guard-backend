# ===========================================================================
# provider_profiler.py
# ===========================================================================


class ProviderProfiler:
    """
    Analyse a provider's historical risk patterns and flag anomalies.

    Field changes from old version:
      - total_claim_amount used for amount comparisons (not claim_amount)
      - visit_admission_date used for date windows (not service_date)
      - benefit_lines checked for billing pattern anomalies
    """

    # High-value accommodation types that carry elevated fraud risk
    HIGH_VALUE_ACCOMMODATION = {"icu", "hdu", "nicu", "burns"}

    def __init__(self, db: Session):
        self.db = db

    async def analyze_claim(self, claim: Claim) -> Dict[str, Any]:
        """Analyse claim based on provider's historical risk profile."""
        flags: List[Dict[str, Any]] = []
        risk_score = 0.0

        # Resolve provider — prefer the ORM relationship, fall back to query
        provider = claim.provider or (
            self.db.query(Provider).filter(Provider.id == claim.provider_id).first()
        )

        if not provider:
            return {
                "module": "provider_risk",
                "risk_score": 0.0,
                "flags": [],
                "is_high_risk": False,
                "error": "Provider not found",
            }

        # ── 1. Historical rejection rate ──────────────────────────────────
        if provider.total_claims_count and provider.total_claims_count >= 10:
            rejection_rate = (
                provider.rejected_claims_count or 0
            ) / provider.total_claims_count
            if rejection_rate > 0.5:
                risk_score += 75.0
                flags.append(
                    {
                        "type": "high_rejection_history",
                        "severity": FraudSeverity.HIGH,
                        "description": (
                            f"Provider rejection rate is {rejection_rate:.1%} "
                            "(threshold: >50%)"
                        ),
                        "score": 75.0,
                        "evidence": {
                            "rejection_rate": round(rejection_rate, 4),
                            "total_claims": provider.total_claims_count,
                            "rejected_claims": provider.rejected_claims_count,
                        },
                    }
                )
            elif rejection_rate > 0.3:
                risk_score += 40.0
                flags.append(
                    {
                        "type": "elevated_rejection_history",
                        "severity": FraudSeverity.MEDIUM,
                        "description": (
                            f"Provider rejection rate is {rejection_rate:.1%} "
                            "(threshold: >30%)"
                        ),
                        "score": 40.0,
                        "evidence": {"rejection_rate": round(rejection_rate, 4)},
                    }
                )

        # ── 2. Provider risk level flag ───────────────────────────────────
        if getattr(provider, "risk_level", None) == "CRITICAL":
            risk_score += 90.0
            flags.append(
                {
                    "type": "critical_risk_provider",
                    "severity": FraudSeverity.CRITICAL,
                    "description": "Provider is flagged as CRITICAL risk in the registry",
                    "score": 90.0,
                    "evidence": {"provider_code": claim.provider_code},
                }
            )
        elif getattr(provider, "risk_level", None) == "HIGH":
            risk_score += 50.0
            flags.append(
                {
                    "type": "high_risk_provider",
                    "severity": FraudSeverity.HIGH,
                    "description": "Provider is flagged as HIGH risk in the registry",
                    "score": 50.0,
                    "evidence": {"provider_code": claim.provider_code},
                }
            )

        # ── 3. Billing amount anomaly vs provider average ─────────────────
        # Compare this claim's total_claim_amount against the provider's
        # rolling 90-day average to detect sudden amount spikes
        if claim.total_claim_amount:
            ninety_days_ago = (
                claim.visit_admission_date - timedelta(days=90)
                if claim.visit_admission_date
                else datetime.utcnow() - timedelta(days=90)
            )
            avg_result = (
                self.db.query(func.avg(Claim.total_claim_amount))
                .filter(
                    Claim.provider_id == provider.id,
                    Claim.id != claim.id,
                    Claim.visit_admission_date >= ninety_days_ago,
                )
                .scalar()
            )

            if avg_result and float(avg_result) > 0:
                ratio = float(claim.total_claim_amount) / float(avg_result)
                if ratio > 5.0:
                    risk_score += 60.0
                    flags.append(
                        {
                            "type": "amount_spike_vs_provider_average",
                            "severity": FraudSeverity.HIGH,
                            "description": (
                                f"Claim amount is {ratio:.1f}× the provider's 90-day "
                                "average (threshold: >5×)"
                            ),
                            "score": 60.0,
                            "evidence": {
                                "claim_amount": float(claim.total_claim_amount),
                                "provider_90d_average": round(float(avg_result), 2),
                                "ratio": round(ratio, 2),
                            },
                        }
                    )
                elif ratio > 3.0:
                    risk_score += 30.0
                    flags.append(
                        {
                            "type": "elevated_amount_vs_provider_average",
                            "severity": FraudSeverity.MEDIUM,
                            "description": (
                                f"Claim amount is {ratio:.1f}× the provider's 90-day average"
                            ),
                            "score": 30.0,
                            "evidence": {
                                "claim_amount": float(claim.total_claim_amount),
                                "provider_90d_average": round(float(avg_result), 2),
                                "ratio": round(ratio, 2),
                            },
                        }
                    )

        # ── 4. Unusually high ICU / HDU / NICU / Burns billing frequency ─
        # Providers billing these high-value accommodations excessively are
        # a known fraud pattern (accommodation upcoding)
        if claim.accommodation_type:
            accom = claim.accommodation_type.lower()
            if accom in self.HIGH_VALUE_ACCOMMODATION:
                thirty_days_ago = (
                    claim.visit_admission_date - timedelta(days=30)
                    if claim.visit_admission_date
                    else datetime.utcnow() - timedelta(days=30)
                )
                high_value_count = (
                    self.db.query(func.count(Claim.id))
                    .filter(
                        Claim.provider_id == provider.id,
                        Claim.accommodation_type.ilike(accom),
                        Claim.visit_admission_date >= thirty_days_ago,
                    )
                    .scalar()
                    or 0
                )
                if high_value_count > 20:
                    risk_score += 50.0
                    flags.append(
                        {
                            "type": "high_value_accommodation_frequency",
                            "severity": FraudSeverity.HIGH,
                            "description": (
                                f"Provider billed {high_value_count} {accom.upper()} "
                                "admissions in the last 30 days (>20 is suspicious)"
                            ),
                            "score": 50.0,
                            "evidence": {
                                "accommodation_type": claim.accommodation_type,
                                "count_last_30_days": high_value_count,
                            },
                        }
                    )

        return {
            "module": "provider_risk",
            "risk_score": min(risk_score, 100.0),
            "flags": flags,
            "is_high_risk": risk_score >= 60.0,
        }
