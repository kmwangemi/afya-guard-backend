"""
SHA Fraud Detection — upcoding_medical_db.py (PATCHED)

Changes vs original:
  [FIX 1] Added CODE_PREFIX_MAP + normalise_service_code()
          Real SHA codes like "CONSULT-SPEC-001" now map to canonical
          DB keys like "CONSULT02". Import and use in upcoding_detector.py.
  [FIX 4] Added is_inpatient_code() helper that handles both exact codes
          ("ICU") and suffixed codes ("WARD-GEN-DAY"). Use everywhere
          instead of bare `code in INPATIENT_ONLY_CODES` set checks.
"""

from typing import Dict, FrozenSet, List, Optional, Set, Tuple

# ── ICD-11 → Expected service codes ──────────────────────────────────────────
ICD11_TO_EXPECTED_SERVICES: Dict[str, Set[str]] = {
    "J18.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "XRAY", "WARD"},
    "J06.9": {"CONSULT01", "LAB01"},
    "J45.9": {"CONSULT01", "CONSULT02", "LAB01"},
    "E11.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "LAB03"},
    "E10.9": {"CONSULT01", "CONSULT02", "LAB01", "LAB02", "LAB03"},
    "E03.9": {"CONSULT01", "CONSULT02", "LAB02"},
    "I10": {"CONSULT01", "CONSULT02", "LAB02", "LAB03"},
    "I21.9": {"CONSULT02", "CONSULT03", "LAB02", "LAB03", "CT", "ICU", "WARD"},
    "I50.9": {"CONSULT02", "CONSULT03", "LAB02", "WARD", "ICU"},
    "B50.9": {"CONSULT01", "LAB01", "LAB02"},
    "A15.0": {"CONSULT01", "CONSULT02", "LAB01", "XRAY"},
    "A09.0": {"CONSULT01", "LAB01"},
    "N39.0": {"CONSULT01", "LAB01", "LAB02"},
    "N18.9": {"CONSULT02", "CONSULT03", "LAB02", "LAB03", "WARD"},
    "S52.5": {"CONSULT02", "SURG01", "XRAY"},
    "K35.8": {"CONSULT02", "CONSULT03", "SURG01", "SURG02", "WARD"},
    "K40.9": {"CONSULT02", "SURG01", "SURG02", "WARD"},
    "Z34.0": {"CONSULT01", "CONSULT02", "LAB01", "LAB02"},
    "O80": {"CONSULT02", "MATERNITY", "WARD"},
    "O82": {"CONSULT02", "CONSULT03", "CS", "WARD", "ICU"},
}

ICD11_INCOMPATIBLE_SERVICES: Dict[str, Set[str]] = {
    "J06.9": {"CT", "MRI", "SURG02", "SURG03", "ICU", "CS"},
    "A09.0": {"CT", "MRI", "SURG02", "SURG03", "ICU"},
    "B50.9": {"CT", "MRI", "SURG01", "SURG02", "SURG03", "ICU"},
    "N39.0": {"CT", "MRI", "SURG02", "SURG03", "ICU"},
    "E11.9": {"SURG02", "SURG03", "CS", "MATERNITY"},
    "I10": {"SURG02", "SURG03", "CS", "MATERNITY"},
}

DIAGNOSIS_COST_RANGES: Dict[str, Tuple[float, float, float]] = {
    "J18.9": (5_000, 45_000, 120_000),
    "J06.9": (500, 3_000, 8_000),
    "J45.9": (1_000, 5_000, 15_000),
    "E11.9": (2_000, 12_000, 35_000),
    "E10.9": (2_000, 15_000, 40_000),
    "I10": (1_500, 8_000, 25_000),
    "I21.9": (50_000, 250_000, 600_000),
    "I50.9": (20_000, 100_000, 300_000),
    "B50.9": (1_000, 8_000, 25_000),
    "A15.0": (5_000, 30_000, 80_000),
    "A09.0": (500, 5_000, 15_000),
    "N39.0": (500, 4_000, 12_000),
    "N18.9": (10_000, 60_000, 200_000),
    "S52.5": (8_000, 40_000, 100_000),
    "K35.8": (25_000, 80_000, 200_000),
    "K40.9": (20_000, 70_000, 150_000),
    "Z34.0": (2_000, 8_000, 20_000),
    "O80": (8_000, 25_000, 60_000),
    "O82": (40_000, 120_000, 250_000),
}

PEER_BENCHMARKS_BY_LEVEL: Dict[int, Dict[str, float]] = {
    1: {"mean": 1_200, "std": 400},
    2: {"mean": 3_500, "std": 1_200},
    3: {"mean": 8_000, "std": 3_000},
    4: {"mean": 18_000, "std": 7_000},
    5: {"mean": 35_000, "std": 15_000},
    6: {"mean": 75_000, "std": 30_000},
}

MUTUALLY_EXCLUSIVE_PAIRS: List[FrozenSet[str]] = [
    frozenset({"CONSULT01", "CONSULT03"}),
    frozenset({"MATERNITY", "CS"}),
    frozenset({"SURG01", "SURG03"}),
    frozenset({"ICU", "WARD"}),
]

MIN_FACILITY_LEVEL_FOR_SERVICE: Dict[str, int] = {
    "CT": 3,
    "MRI": 4,
    "ICU": 4,
    "SURG02": 3,
    "SURG03": 5,
    "CS": 3,
}

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

INPATIENT_ONLY_CODES: Set[str] = {"ICU", "WARD", "SURG02", "SURG03", "CS"}

HIGH_VALUE_THRESHOLD: float = 50_000


# ── FIX 1: SHA service code normalisation ─────────────────────────────────────
# Real SHA claim submissions use codes like "CONSULT-SPEC-001", "LAB-FBC-001",
# "WARD-GEN-DAY" — suffixed formats that don't match the canonical DB keys above.
# This map and helper bridge that gap without changing the reference tables.

CODE_PREFIX_MAP: Dict[str, Optional[str]] = {
    # Consultations
    "CONSULT-GP": "CONSULT01",
    "CONSULT-SPEC": "CONSULT02",
    "CONSULT-SURG": "CONSULT03",
    "CONSULT-PAED": "CONSULT02",
    "CONSULT-OBS": "CONSULT02",
    # Labs
    "LAB-FBC": "LAB01",
    "LAB-RBS": "LAB01",
    "LAB-UDS": "LAB01",
    "LAB-UREA": "LAB02",
    "LAB-LFT": "LAB02",
    "LAB-TSH": "LAB02",
    "LAB-HBA1C": "LAB03",
    "LAB-ECHO": "LAB03",
    "LAB-CULT": "LAB03",
    # Wards
    "WARD-GEN": "WARD",
    "WARD-PVT": "WARD",
    "WARD-PED": "WARD",
    "WARD-MAT": "WARD",
    # Drugs — no price reference, skip gracefully
    "DRUG": None,
    "MED": None,
    "PHARM": None,
}


def normalise_service_code(code: str) -> Optional[str]:
    """
    Map a real SHA service code to its canonical medical DB key.

    Resolution order:
      1. Exact match in REFERENCE_PRICES           → return as-is
      2. Prefix match in CODE_PREFIX_MAP            → return mapped key
      3. Progressive suffix-strip                   → e.g. WARD-GEN-DAY → WARD-GEN → WARD
      4. No match                                   → return original code

    Returns None only when the prefix map explicitly maps to None
    (e.g. "DRUG-METF-500" → None, meaning: no reference price, skip check).

    Usage in upcoding_detector.py:
        canonical = normalise_service_code(raw_code)
        ref_price = REFERENCE_PRICES.get(canonical) if canonical else None
    """
    code = (code or "").upper().strip()

    # 1. Exact match
    if code in REFERENCE_PRICES:
        return code

    # 2. Prefix map
    for prefix, canonical in CODE_PREFIX_MAP.items():
        if code.startswith(prefix):
            return canonical  # may be None — caller must check

    # 3. Progressive suffix strip: "WARD-GEN-DAY" → try "WARD-GEN" → try "WARD"
    parts = code.split("-")
    for length in range(len(parts) - 1, 0, -1):
        candidate = "-".join(parts[:length])
        if candidate in REFERENCE_PRICES:
            return candidate
        mapped = CODE_PREFIX_MAP.get(candidate)
        if mapped is not None:
            return mapped
        if candidate in CODE_PREFIX_MAP:
            return CODE_PREFIX_MAP[candidate]  # handles None explicitly

    return code  # unknown code — return original, let caller deal with missing ref


# ── FIX 4: Inpatient code check that handles suffixed codes ───────────────────


def is_inpatient_code(code: str) -> bool:
    """
    Return True if the service code represents an inpatient-only service.

    Works for both canonical codes ("ICU") and suffixed real codes
    ("WARD-GEN-DAY", "WARD-PVT-002", "SURG02-LAPAROSCOPIC").

    Use this everywhere instead of the bare set check:
        WRONG:   if code in INPATIENT_ONLY_CODES
        CORRECT: if is_inpatient_code(code)
    """
    code = (code or "").upper().strip()
    if code in INPATIENT_ONLY_CODES:
        return True
    canonical = normalise_service_code(code)
    if canonical and canonical in INPATIENT_ONLY_CODES:
        return True
    # Belt-and-suspenders prefix check
    return any(code.startswith(ioc) for ioc in INPATIENT_ONLY_CODES)


# ── Query helpers (unchanged) ─────────────────────────────────────────────────


def get_expected_services(diagnosis_codes: List[str]) -> Set[str]:
    expected: Set[str] = set()
    for code in diagnosis_codes:
        expected |= ICD11_TO_EXPECTED_SERVICES.get(code, set())
    return expected


def get_incompatible_services(diagnosis_codes: List[str]) -> Set[str]:
    incompatible: Set[str] = set()
    for code in diagnosis_codes:
        incompatible |= ICD11_INCOMPATIBLE_SERVICES.get(code, set())
    return incompatible


def get_diagnosis_cost_range(
    diagnosis_codes: List[str],
) -> Optional[Tuple[float, float, float]]:
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
