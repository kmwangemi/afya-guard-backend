"""
scripts/generate_dataset.py
────────────────────────────
Generates a labelled SHA claims dataset with 23 clean, non-redundant features.

Removed vs previous version:
  ❌ is_weekend       — duplicate of weekend_submission (same boolean, different name)
  ❌ submitted_weekday — redundant, weekend_submission already captures Sat/Sun signal
"""

import os
import random
from datetime import date, datetime, timedelta

import numpy as np
import pandas as pd
from faker import Faker

fake = Faker("en_GB")
random.seed(42)
np.random.seed(42)

# ── Reference data ────────────────────────────────────────────────────────────

FACILITIES = [
    {"code": "FAC-NBI-001", "name": "KNH", "level": 6, "county": "Nairobi"},
    {"code": "FAC-NBI-002", "name": "Nairobi West", "level": 4, "county": "Nairobi"},
    {"code": "FAC-NBI-003", "name": "Mama Lucy", "level": 4, "county": "Nairobi"},
    {"code": "FAC-MSA-001", "name": "Coast General", "level": 5, "county": "Mombasa"},
    {"code": "FAC-KSM-001", "name": "JOOTRH", "level": 6, "county": "Kisumu"},
    {"code": "FAC-NKR-001", "name": "Nakuru PGH", "level": 5, "county": "Nakuru"},
    {"code": "FAC-ELD-001", "name": "Moi Teaching", "level": 6, "county": "Eldoret"},
    {"code": "FAC-THK-001", "name": "Thika Level 5", "level": 5, "county": "Kiambu"},
    {"code": "FAC-GAR-001", "name": "Garissa PGH", "level": 5, "county": "Garissa"},
    {"code": "FAC-KTI-001", "name": "Kitui County", "level": 4, "county": "Kitui"},
    {"code": "FAC-NBI-099", "name": "Tiny Clinic A", "level": 2, "county": "Nairobi"},
    {"code": "FAC-NBI-098", "name": "Tiny Clinic B", "level": 2, "county": "Nairobi"},
]

DIAGNOSES = [
    "J18.9",
    "J06.9",
    "A09.0",
    "K29.7",
    "E11.9",
    "I10",
    "B50.9",
    "A15.0",
    "Z34.0",
    "S52.5",
    "R50.9",
    "N39.0",
]

DIAGNOSIS_REQUIRES_LAB = {"E11.9", "B50.9", "A15.0", "N39.0", "I10"}
DIAGNOSIS_REQUIRES_SURGERY = {"S52.5"}

OUTPATIENT_SERVICES = [
    {
        "code": "CONSULT-GP",
        "name": "GP Consultation",
        "min": 500,
        "max": 1500,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "CONSULT-SPEC",
        "name": "Specialist Consult",
        "min": 2000,
        "max": 5000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "LAB-FBC",
        "name": "Full Blood Count",
        "min": 800,
        "max": 1500,
        "is_lab": True,
        "is_surgery": False,
    },
    {
        "code": "LAB-RBS",
        "name": "Random Blood Sugar",
        "min": 400,
        "max": 800,
        "is_lab": True,
        "is_surgery": False,
    },
    {
        "code": "LAB-URINE",
        "name": "Urinalysis",
        "min": 300,
        "max": 600,
        "is_lab": True,
        "is_surgery": False,
    },
    {
        "code": "DRUG-AMOX",
        "name": "Amoxicillin",
        "min": 150,
        "max": 500,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "DRUG-PARA",
        "name": "Paracetamol",
        "min": 50,
        "max": 200,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "XRAY-CHEST",
        "name": "Chest X-Ray",
        "min": 1500,
        "max": 3000,
        "is_lab": True,
        "is_surgery": False,
    },
]

INPATIENT_SERVICES = [
    {
        "code": "WARD-GEN",
        "name": "General Ward/day",
        "min": 1500,
        "max": 3000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "WARD-PRIV",
        "name": "Private Ward/day",
        "min": 5000,
        "max": 12000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "NURS-CARE",
        "name": "Nursing Care/day",
        "min": 500,
        "max": 1000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "CONSULT-SPEC",
        "name": "Specialist Consult",
        "min": 3000,
        "max": 6000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "LAB-FBC",
        "name": "Full Blood Count",
        "min": 800,
        "max": 1500,
        "is_lab": True,
        "is_surgery": False,
    },
    {
        "code": "DRUG-IV",
        "name": "IV Medications",
        "min": 500,
        "max": 3000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "PROC-MINOR",
        "name": "Minor Procedure",
        "min": 3000,
        "max": 8000,
        "is_lab": False,
        "is_surgery": True,
    },
    {
        "code": "THEATRE-FEE",
        "name": "Theatre Fee",
        "min": 5000,
        "max": 15000,
        "is_lab": False,
        "is_surgery": True,
    },
]

HIGH_LEVEL_SERVICES = [
    {
        "code": "ICU-DAY",
        "name": "ICU per day",
        "min": 15000,
        "max": 40000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "SURG-MAJOR",
        "name": "Major Surgery",
        "min": 30000,
        "max": 150000,
        "is_lab": False,
        "is_surgery": True,
    },
    {
        "code": "DIALYSIS",
        "name": "Dialysis session",
        "min": 8000,
        "max": 15000,
        "is_lab": False,
        "is_surgery": False,
    },
    {
        "code": "MRI-BRAIN",
        "name": "Brain MRI",
        "min": 12000,
        "max": 25000,
        "is_lab": True,
        "is_surgery": False,
    },
]

# ── Feature list — single source of truth ─────────────────────────────────────

ALL_FEATURES = [
    # Group A — read directly from ClaimFeature ORM fields (12)
    "provider_avg_cost_90d",  # provider rolling 90d average claim cost
    "provider_cost_zscore",  # std deviations from provider avg (upcoding)
    "member_visits_30d",  # member claim count in last 30 days
    "member_visits_7d",  # member claim count in last 7 days (spike)
    "member_unique_providers_30d",  # unique providers in 30d (card sharing)
    "duplicate_within_7d",  # same member + provider + diagnosis in 7d window
    "length_of_stay",  # inpatient days (0 for outpatient)
    "weekend_submission",  # submitted Saturday or Sunday (0/1)
    "diagnosis_cost_zscore",  # cost vs typical for this diagnosis code
    "service_count",  # number of service line items
    "has_lab_without_diagnosis",  # lab billed but no matching clinical diagnosis
    "has_surgery_without_theatre",  # surgery code present but no theatre fee
    # Group B — engineered in _features_to_dataframe (11)
    "claim_type_enc",  # INPATIENT=0, OUTPATIENT=1
    "county_enc",  # hash-encoded county (0–9)
    "facility_level",  # facility tier 1–6
    "log_amount",  # log(1 + total_claim_amount) — reduces skew
    "amount_per_service",  # total / service_count
    "amount_per_day",  # total / length_of_stay
    "submitted_hour",  # 0–23 (off-hours detection)
    "is_off_hours",  # submitted 11pm–5am (0/1)
    "no_eligibility_check",  # no eligibility check before claim (0/1)
    "high_service_count",  # service_count > 8 — unbundling signal (0/1)
    "level_amount_mismatch",  # low-level facility + high amount (0/1)
]

# ── Helpers ───────────────────────────────────────────────────────────────────


def random_date(start: date, end: date) -> date:
    return start + timedelta(days=random.randint(0, (end - start).days))


def make_member_id() -> str:
    return f"SHA-KE-{random.randint(10000000, 99999999)}"


def make_claim_id() -> str:
    return f"SHA-CLM-{random.randint(1000000, 9999999)}"


def pick_services(claim_type: str, n: int = None) -> list:
    pool = INPATIENT_SERVICES if claim_type == "INPATIENT" else OUTPATIENT_SERVICES
    n = n or random.randint(2, 5)
    selected = random.sample(pool, min(n, len(pool)))
    return [
        {
            "service_code": s["code"],
            "service_name": s["name"],
            "quantity": random.randint(1, 3),
            "unit_price": round(random.uniform(s["min"], s["max"]), 2),
            "is_lab": s["is_lab"],
            "is_surgery": s["is_surgery"],
        }
        for s in selected
    ]


def compute_provider_stats(provider_code: str, history: dict) -> tuple:
    h = history.get(provider_code, [])
    if len(h) < 3:
        return 0.0, 0.0
    avg = float(np.mean(h))
    std = float(np.std(h)) or 1.0
    zscore = (h[-1] - avg) / std
    return round(avg, 2), round(zscore, 4)


def compute_diagnosis_zscore(code: str, amount: float, history: dict) -> float:
    h = history.get(code, [])
    if len(h) < 3:
        return 0.0
    avg = float(np.mean(h))
    std = float(np.std(h)) or 1.0
    return round((amount - avg) / std, 4)


# ── Legitimate claim builder ──────────────────────────────────────────────────


def make_legitimate_claim(
    member_pool: list,
    provider_history: dict,
    member_visit_counts: dict,
    member_provider_sets: dict,
    diagnosis_history: dict,
    existing_claims: list,
) -> dict:
    facility = random.choice(FACILITIES)
    claim_type = random.choices(["INPATIENT", "OUTPATIENT"], weights=[0.3, 0.7])[0]
    adm_date = random_date(date(2025, 1, 1), date(2026, 2, 1))
    member_id = random.choice(member_pool)

    los = random.randint(1, 14) if claim_type == "INPATIENT" else 0
    dis_date = adm_date + timedelta(days=los)

    services = pick_services(claim_type)
    diagnosis = random.sample(DIAGNOSES, random.randint(1, 3))
    submitted_dt = datetime.combine(dis_date, datetime.min.time()) + timedelta(
        hours=random.randint(6, 22), minutes=random.randint(0, 59)
    )

    for svc in services:
        svc["total_price"] = round(svc["unit_price"] * svc["quantity"], 2)

    total = round(sum(s["total_price"] for s in services), 2)
    service_count = len(services)
    submitted_hour = submitted_dt.hour

    # Rolling statistics
    provider_history.setdefault(facility["code"], []).append(total)
    prov_avg, prov_zscore = compute_provider_stats(facility["code"], provider_history)

    diagnosis_history.setdefault(diagnosis[0], []).append(total)
    diag_zscore = compute_diagnosis_zscore(diagnosis[0], total, diagnosis_history)

    member_visit_counts.setdefault(member_id, {"30d": 0, "7d": 0})
    member_visit_counts[member_id]["30d"] += 1
    if random.random() < 0.2:
        member_visit_counts[member_id]["7d"] += 1

    member_provider_sets.setdefault(member_id, set()).add(facility["code"])

    dup_within_7d = any(
        c["member_id"] == member_id
        and c["provider_code"] == facility["code"]
        and abs((adm_date - date.fromisoformat(c["admission_date"])).days) <= 7
        for c in existing_claims[-500:]
    )

    has_lab = any(s["is_lab"] for s in services)
    has_surgery = any(s["is_surgery"] for s in services)
    needs_lab = bool(set(diagnosis) & DIAGNOSIS_REQUIRES_LAB)

    has_lab_without_dx = has_lab and not needs_lab and random.random() < 0.05
    has_surg_no_theatre = (
        has_surgery
        and not any(s["service_code"] == "THEATRE-FEE" for s in services)
        and random.random() < 0.05
    )

    return {
        # Identifiers — not features, kept for traceability
        "sha_claim_id": make_claim_id(),
        "member_id": member_id,
        "provider_code": facility["code"],
        "admission_date": adm_date.isoformat(),
        "discharge_date": dis_date.isoformat(),
        "claim_type": claim_type,
        # ── Group A (12) ──────────────────────────────────────────────────────
        "provider_avg_cost_90d": prov_avg,
        "provider_cost_zscore": prov_zscore,
        "member_visits_30d": member_visit_counts[member_id]["30d"],
        "member_visits_7d": member_visit_counts[member_id]["7d"],
        "member_unique_providers_30d": len(member_provider_sets[member_id]),
        "duplicate_within_7d": int(dup_within_7d),
        "length_of_stay": los,
        "weekend_submission": int(submitted_dt.weekday() >= 5),
        "diagnosis_cost_zscore": diag_zscore,
        "service_count": service_count,
        "has_lab_without_diagnosis": int(has_lab_without_dx),
        "has_surgery_without_theatre": int(has_surg_no_theatre),
        # ── Group B (11) ──────────────────────────────────────────────────────
        "claim_type_enc": 0 if claim_type == "INPATIENT" else 1,
        "county_enc": hash(facility["county"]) % 10,
        "facility_level": facility["level"],
        "log_amount": round(float(np.log1p(total)), 6),
        "amount_per_service": round(total / max(service_count, 1), 2),
        "amount_per_day": round(total / max(los, 1), 2),
        "submitted_hour": submitted_hour,
        "is_off_hours": int(submitted_hour >= 23 or submitted_hour <= 5),
        "no_eligibility_check": int(random.random() < 0.05),
        "high_service_count": int(service_count > 8),
        "level_amount_mismatch": int(facility["level"] <= 2 and total > 10000),
        # Meta
        "total_claim_amount": total,
        "is_fraud": 0,
        "fraud_type": None,
    }


# ── Fraud injectors ───────────────────────────────────────────────────────────


def inject_ghost_patient(claim: dict) -> dict:
    claim.update(
        {
            "no_eligibility_check": 1,
            "member_visits_30d": 0,
            "member_visits_7d": 0,
            "is_fraud": 1,
            "fraud_type": "ghost_patient",
        }
    )
    return claim


def inject_upcoding(claim: dict) -> dict:
    m = random.uniform(3.0, 6.0)
    claim["total_claim_amount"] = round(claim["total_claim_amount"] * m, 2)
    claim["log_amount"] = round(float(np.log1p(claim["total_claim_amount"])), 6)
    claim["amount_per_service"] = round(
        claim["total_claim_amount"] / max(claim["service_count"], 1), 2
    )
    claim["amount_per_day"] = round(
        claim["total_claim_amount"] / max(claim["length_of_stay"], 1), 2
    )
    claim["provider_cost_zscore"] = round(random.uniform(3.0, 7.0), 4)
    claim["diagnosis_cost_zscore"] = round(random.uniform(3.0, 6.0), 4)
    claim.update({"is_fraud": 1, "fraud_type": "upcoding"})
    return claim


def inject_duplicate(claim: dict, original: dict) -> dict:
    claim.update(
        {
            "sha_claim_id": make_claim_id(),
            "member_id": original["member_id"],
            "provider_code": original["provider_code"],
            "admission_date": original["admission_date"],
            "discharge_date": original["discharge_date"],
            "duplicate_within_7d": 1,
            "member_visits_7d": original.get("member_visits_7d", 0) + 1,
            "is_fraud": 1,
            "fraud_type": "duplicate",
        }
    )
    return claim


def inject_phantom_service(claim: dict) -> dict:
    facility = random.choice([f for f in FACILITIES if f["level"] <= 2])
    phantom = random.choice(HIGH_LEVEL_SERVICES)
    qty = random.randint(1, 5)
    price = round(random.uniform(phantom["min"], phantom["max"]), 2)
    extra = round(price * qty, 2)
    claim["provider_code"] = facility["code"]
    claim["facility_level"] = facility["level"]
    claim["total_claim_amount"] += extra
    claim["log_amount"] = round(float(np.log1p(claim["total_claim_amount"])), 6)
    claim["service_count"] += 1
    claim["amount_per_service"] = round(
        claim["total_claim_amount"] / claim["service_count"], 2
    )
    claim["level_amount_mismatch"] = 1
    claim["provider_cost_zscore"] = round(random.uniform(3.0, 8.0), 4)
    claim["high_service_count"] = int(claim["service_count"] > 8)
    if phantom["is_surgery"]:
        claim["has_surgery_without_theatre"] = 1
    claim.update({"is_fraud": 1, "fraud_type": "phantom_service"})
    return claim


def inject_off_hours(claim: dict) -> dict:
    claim.update(
        {
            "submitted_hour": random.randint(2, 4),
            "is_off_hours": 1,
            "weekend_submission": 0,
            "provider_cost_zscore": round(random.uniform(2.5, 5.0), 4),
            "is_fraud": 1,
            "fraud_type": "off_hours_bulk",
        }
    )
    return claim


def inject_unbundling(claim: dict) -> dict:
    extra_svcs = random.randint(4, 7)
    extra_amount = round(sum(random.uniform(200, 800) for _ in range(extra_svcs)), 2)
    claim["service_count"] += extra_svcs
    claim["total_claim_amount"] += extra_amount
    claim["log_amount"] = round(float(np.log1p(claim["total_claim_amount"])), 6)
    claim["amount_per_service"] = round(
        claim["total_claim_amount"] / claim["service_count"], 2
    )
    claim["high_service_count"] = int(claim["service_count"] > 8)
    claim["diagnosis_cost_zscore"] = round(random.uniform(2.0, 5.0), 4)
    claim.update({"is_fraud": 1, "fraud_type": "unbundling"})
    return claim


def inject_lab_without_diagnosis(claim: dict) -> dict:
    claim.update(
        {
            "has_lab_without_diagnosis": 1,
            "is_fraud": 1,
            "fraud_type": "lab_without_diagnosis",
        }
    )
    return claim


def inject_surgery_without_theatre(claim: dict) -> dict:
    claim.update(
        {
            "has_surgery_without_theatre": 1,
            "is_fraud": 1,
            "fraud_type": "surgery_without_theatre",
        }
    )
    return claim


def inject_member_churning(claim: dict) -> dict:
    claim.update(
        {
            "member_visits_30d": random.randint(8, 20),
            "member_visits_7d": random.randint(4, 10),
            "member_unique_providers_30d": random.randint(5, 12),
            "is_fraud": 1,
            "fraud_type": "member_churning",
        }
    )
    return claim


# ── Main ──────────────────────────────────────────────────────────────────────


def generate_dataset(n_legit: int = 8000, fraud_ratio: float = 0.15) -> pd.DataFrame:
    print(f"Generating {n_legit} legitimate claims...")

    member_pool = [make_member_id() for _ in range(n_legit // 10)]
    provider_history: dict = {}
    member_visit_counts: dict = {}
    member_provider_sets: dict = {}
    diagnosis_history: dict = {}
    claims: list = []

    for _ in range(n_legit):
        c = make_legitimate_claim(
            member_pool,
            provider_history,
            member_visit_counts,
            member_provider_sets,
            diagnosis_history,
            claims,
        )
        claims.append(c)

    n_fraud = int(n_legit * fraud_ratio / (1 - fraud_ratio))
    print(f"Injecting {n_fraud} fraud samples ({fraud_ratio*100:.0f}% of total)...")

    fraud_injectors = [
        inject_ghost_patient,
        inject_upcoding,
        inject_phantom_service,
        inject_off_hours,
        inject_unbundling,
        inject_lab_without_diagnosis,
        inject_surgery_without_theatre,
        inject_member_churning,
    ]

    fraud_claims = []
    for _ in range(n_fraud):
        base = make_legitimate_claim(
            member_pool,
            provider_history,
            member_visit_counts,
            member_provider_sets,
            diagnosis_history,
            claims,
        ).copy()
        injector = random.choice(fraud_injectors)
        base = (
            inject_duplicate(base, random.choice(claims))
            if injector == inject_duplicate and claims
            else injector(base)
        )
        fraud_claims.append(base)

    all_claims = claims + fraud_claims
    random.shuffle(all_claims)
    df = pd.DataFrame(all_claims)

    print(f"\n── Dataset Summary ───────────────────────────────────────")
    print(f"  Total rows : {len(df)}")
    print(f"  Legitimate : {(df['is_fraud']==0).sum()}")
    print(f"  Fraud      : {df['is_fraud'].sum()} ({df['is_fraud'].mean()*100:.1f}%)")
    print(f"  Features   : {len(ALL_FEATURES)} (Group A: 12, Group B: 11)")
    print(f"\n── Fraud type breakdown ──────────────────────────────────")
    print(df[df["is_fraud"] == 1]["fraud_type"].value_counts().to_string())
    print(f"\n── Features ──────────────────────────────────────────────")
    for i, f in enumerate(ALL_FEATURES, 1):
        group = "A" if i <= 12 else "B"
        print(f"  [{group}] {i:02d}. {f}")

    return df


if __name__ == "__main__":
    os.makedirs("data", exist_ok=True)
    df = generate_dataset(n_legit=8000, fraud_ratio=0.15)
    df.to_csv("data/sha_claims_dataset.csv", index=False)
    print("\nSaved → data/sha_claims_dataset.csv")


# Generate the dataset
# python app/scripts/generate_dataset.py
