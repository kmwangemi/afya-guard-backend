"""
app/detectors/upcoding_medical_db.py
──────────────────────────────────────
Medical logic database for upcoding detection.

Contains:
  1. ICD-11 → expected CPT/service code mappings
  2. Expected cost ranges per diagnosis (KES)
  3. Peer facility cost benchmarks by facility level
  4. Mutually exclusive service code pairs (cannot bill both)
  5. Service codes that require specific diagnoses to be valid
"""

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# ── ICD-11 → Expected service codes ──────────────────────────────────────────
# Maps diagnosis code → set of commonly expected/appropriate service codes
# Used to flag procedures that don't match the stated diagnosis

ICD11_TO_EXPECTED_SERVICES: Dict[str, Set[str]] = {
    # Respiratory
    "J18.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "XRAY", "WARD"},  # Pneumonia
    "J06.9": {"CONSULT01", "LAB01"},  # URTI
    "J45.9": {"CONSULT01", "CONSULT02", "LAB01"},  # Asthma
    # Endocrine
    "E11.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "LAB03"},  # T2DM
    "E10.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "LAB03"},  # T1DM
    "E03.9": {"CONSULT01", "CONSULT02", "LAB02"},  # Hypothyroid
    # Cardiovascular
    "I10": {"CONSULT01", "CONSULT02", "LAB02", "LAB03"},  # Hypertension
    "I21.9": {"CONSULT02", "CONSULT03", "LAB02", "LAB03", "CT", "ICU", "WARD"},  # MI
    "I50.9": {"CONSULT02", "CONSULT03", "LAB02", "WARD", "ICU"},  # Heart failure
    # Infectious
    "B50.9": {"CONSULT01", "LAB01", "LAB02"},  # Malaria
    "A15.0": {"CONSULT01", "CONSULT02", "LAB01", "XRAY"},  # Pulm TB
    "A09.0": {"CONSULT01", "LAB01"},  # Gastroenteritis
    # Renal
    "N39.0": {"CONSULT01", "LAB01", "LAB02"},  # UTI
    "N18.9": {"CONSULT02", "CONSULT03", "LAB02", "LAB03", "WARD"},  # CKD
    # Surgical
    "S52.5": {"CONSULT02", "SURG01", "XRAY"},  # Radius fracture
    "K35.8": {"CONSULT02", "CONSULT03", "SURG01", "SURG02", "WARD"},  # Appendicitis
    "K40.9": {"CONSULT02", "SURG01", "SURG02", "WARD"},  # Hernia
    # Obstetric
    "Z34.0": {"CONSULT01", "CONSULT02", "LAB01", "LAB02"},  # Normal pregnancy
    "O80": {"CONSULT02", "MATERNITY", "WARD"},  # Normal delivery
    "O82": {"CONSULT02", "CONSULT03", "CS", "WARD", "ICU"},  # C-section
}

# High-value services that are NEVER appropriate for these diagnoses
ICD11_INCOMPATIBLE_SERVICES: Dict[str, Set[str]] = {
    "J06.9": {"CT", "MRI", "SURG02", "SURG03", "ICU", "CS"},  # URTI → no surgery/ICU
    "A09.0": {"CT", "MRI", "SURG02", "SURG03", "ICU"},  # Gastro → no major surgery
    "B50.9": {"CT", "MRI", "SURG01", "SURG02", "SURG03", "ICU"},  # Malaria → no surgery
    "N39.0": {"CT", "MRI", "SURG02", "SURG03", "ICU"},  # UTI → no major surgery
    "E11.9": {"SURG02", "SURG03", "CS", "MATERNITY"},  # DM → no OB surgery
    "I10": {"SURG02", "SURG03", "CS", "MATERNITY"},  # Hypertension → no OB
}

# ── Expected cost ranges per diagnosis (KES) ─────────────────────────────────
# (min_typical, max_typical, absolute_max)
# absolute_max = hard ceiling beyond which claim is almost certainly fraudulent

DIAGNOSIS_COST_RANGES: Dict[str, Tuple[float, float, float]] = {
    "J18.9": (5_000, 45_000, 120_000),  # Pneumonia
    "J06.9": (500, 3_000, 8_000),  # URTI
    "J45.9": (1_000, 5_000, 15_000),  # Asthma
    "E11.9": (2_000, 12_000, 35_000),  # T2DM
    "E10.9": (2_000, 15_000, 40_000),  # T1DM
    "I10": (1_500, 8_000, 25_000),  # Hypertension
    "I21.9": (50_000, 250_000, 600_000),  # MI
    "I50.9": (20_000, 100_000, 300_000),  # Heart failure
    "B50.9": (1_000, 8_000, 25_000),  # Malaria
    "A15.0": (5_000, 30_000, 80_000),  # Pulm TB
    "A09.0": (500, 5_000, 15_000),  # Gastroenteritis
    "N39.0": (500, 4_000, 12_000),  # UTI
    "N18.9": (10_000, 60_000, 200_000),  # CKD
    "S52.5": (8_000, 40_000, 100_000),  # Radius fracture
    "K35.8": (25_000, 80_000, 200_000),  # Appendicitis
    "K40.9": (20_000, 70_000, 150_000),  # Hernia
    "Z34.0": (2_000, 8_000, 20_000),  # Normal pregnancy ANC
    "O80": (8_000, 25_000, 60_000),  # Normal delivery
    "O82": (40_000, 120_000, 250_000),  # C-section
}

# ── Peer facility cost benchmarks ────────────────────────────────────────────
# Average cost per claim by facility level (1–6)
# Used in peer comparison: flag if provider is >2σ above their level's mean

PEER_BENCHMARKS_BY_LEVEL: Dict[int, Dict[str, float]] = {
    1: {"mean": 1_200, "std": 400},  # dispensary
    2: {"mean": 3_500, "std": 1_200},  # health centre
    3: {"mean": 8_000, "std": 3_000},  # sub-county hospital
    4: {"mean": 18_000, "std": 7_000},  # county hospital
    5: {"mean": 35_000, "std": 15_000},  # referral hospital
    6: {"mean": 75_000, "std": 30_000},  # national referral
}

# ── Mutually exclusive service code pairs ────────────────────────────────────
# Cannot legitimately bill both codes in the same claim
MUTUALLY_EXCLUSIVE_PAIRS: List[FrozenSet[str]] = [
    frozenset({"CONSULT01", "CONSULT03"}),  # GP + top-tier specialist same visit
    frozenset({"MATERNITY", "CS"}),  # Can't bill both normal + C-section delivery
    frozenset({"SURG01", "SURG03"}),  # Minor + complex surgery same day
    frozenset({"ICU", "WARD"}),  # Can't be in ICU and general ward simultaneously
]

# ── Service codes requiring specific conditions ───────────────────────────────
# service_code → minimum facility_level required
MIN_FACILITY_LEVEL_FOR_SERVICE: Dict[str, int] = {
    "CT": 3,
    "MRI": 4,
    "ICU": 4,
    "SURG02": 3,
    "SURG03": 5,
    "CS": 3,
}

# ── Reference unit price table (KES) ─────────────────────────────────────────
REFERENCE_PRICES: Dict[str, float] = {
    "CONSULT01": 1_500,
    "CONSULT02": 3_500,
    "CONSULT03": 8_000,
    "LAB01": 500,
    "LAB02": 800,
    "LAB03": 2_500,
    "XRAY": 3_000,
    "CT": 18_000,
    "MRI": 35_000,
    "SURG01": 25_000,
    "SURG02": 80_000,
    "SURG03": 150_000,
    "ICU": 45_000,
    "WARD": 3_500,
    "MATERNITY": 15_000,
    "CS": 55_000,
}

# Service codes that require inpatient admission
INPATIENT_ONLY_CODES: Set[str] = {"ICU", "WARD", "SURG02", "SURG03", "CS"}

# High-value threshold for 0-day stay scrutiny
HIGH_VALUE_THRESHOLD: float = 50_000


def get_expected_services(diagnosis_codes: List[str]) -> Set[str]:
    """Return union of expected services across all given diagnosis codes."""
    expected: Set[str] = set()
    for code in diagnosis_codes:
        expected |= ICD11_TO_EXPECTED_SERVICES.get(code, set())
    return expected


def get_incompatible_services(diagnosis_codes: List[str]) -> Set[str]:
    """Return union of incompatible services across all given diagnosis codes."""
    incompatible: Set[str] = set()
    for code in diagnosis_codes:
        incompatible |= ICD11_INCOMPATIBLE_SERVICES.get(code, set())
    return incompatible


def get_diagnosis_cost_range(
    diagnosis_codes: List[str],
) -> Optional[Tuple[float, float, float]]:
    """
    Return the most permissive cost range covering all diagnoses.
    Returns (min, max_typical, absolute_max) or None if no mapping found.
    """
    ranges = [
        DIAGNOSIS_COST_RANGES[c] for c in diagnosis_codes if c in DIAGNOSIS_COST_RANGES
    ]
    if not ranges:
        return None
    return (
        min(r[0] for r in ranges),
        max(r[1] for r in ranges),
        max(r[2] for r in ranges),
    )
