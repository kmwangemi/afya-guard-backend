"""
SHA Claims Form — Synthetic Data Generator & Filler
=====================================================
Generates realistic Kenyan patient data and fills it into the SHA claim form PDF.
Produces N filled PDF forms — both clean (normal) and fraudulent variants.

Usage:
    python generate_sha_claims.py --input SHA-FORM.pdf --count 10 --output ./output_forms
    python generate_sha_claims.py --input SHA-FORM.pdf --count 50 --fraud-ratio 0.3 --output ./output_forms

Requirements:
    pip install pypdf reportlab

Fraud patterns generated:
    - Phantom patient (fake SHA number that doesn't match any real patient)
    - Upcoding (bill amount far exceeds typical for the diagnosis)
    - Duplicate claim (same patient, same dates, same diagnosis)
    - Ghost provider (provider ID not in registry)
    - Inflated length of stay (discharge date much later than expected)
    - Unbundling (multiple procedure codes for what should be one procedure)
"""

import argparse
import copy
import io
import json
import os
import random
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Optional

from pypdf import PdfReader, PdfWriter
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase.pdfmetrics import stringWidth
from reportlab.pdfgen import canvas

# ──────────────────────────────────────────────────────────────────────────────
# Kenyan Synthetic Data Banks
# ──────────────────────────────────────────────────────────────────────────────

KENYAN_LAST_NAMES = [
    "KAMAU",
    "WANJIKU",
    "OTIENO",
    "AKINYI",
    "MWANGI",
    "NJERI",
    "OCHIENG",
    "WAMBUA",
    "KARIUKI",
    "ADHIAMBO",
    "MUTUA",
    "WAIRIMU",
    "OMONDI",
    "AUMA",
    "GITONGA",
    "WANJIRU",
    "OWINO",
    "AWINO",
    "NDUNGU",
    "NJOKI",
    "KIPCHOGE",
    "CHEBET",
    "KOECH",
    "RUTO",
    "KIBET",
    "CHEPKEMOI",
    "ROTICH",
    "BETT",
    "MUTAI",
    "LAGAT",
    "HASSAN",
    "OMAR",
    "ABDI",
    "FATUMA",
    "AHMED",
    "MWENDA",
    "GITHINJI",
    "MURIUKI",
    "WAWERU",
    "GATHONI",
    "NYAMBURA",
    "OYUGI",
    "OKOTH",
    "OGOLA",
    "AKELLO",
    "ANYANGO",
    "ODERO",
    "OLUOCH",
]

KENYAN_FIRST_NAMES = [
    "JAMES",
    "MARY",
    "JOHN",
    "GRACE",
    "PETER",
    "FAITH",
    "DAVID",
    "JOYCE",
    "JOSEPH",
    "ESTHER",
    "DANIEL",
    "MERCY",
    "SAMUEL",
    "AGNES",
    "MICHAEL",
    "CAROLINE",
    "PAUL",
    "BEATRICE",
    "GEORGE",
    "DOROTHY",
    "STEPHEN",
    "MARGARET",
    "CHARLES",
    "ALICE",
    "THOMAS",
    "LYDIA",
    "WILLIAM",
    "ROSE",
    "ROBERT",
    "JANE",
    "RICHARD",
    "SARAH",
    "HENRY",
    "ANNE",
    "FRANCIS",
    "ELIZABETH",
    "EDWARD",
    "LUCY",
    "PATRICK",
    "RUTH",
    "BRIAN",
    "EUNICE",
    "KEVIN",
    "PHYLLIS",
    "ERIC",
    "GLADYS",
    "ALEX",
    "HELEN",
    "FELIX",
    "JUDITH",
    "VICTOR",
    "LILIAN",
    "DENNIS",
    "WINNIE",
    "OSCAR",
    "DIANA",
]

KENYAN_MIDDLE_NAMES = [
    "WANJIRU",
    "OTIENO",
    "KAMAU",
    "AKINYI",
    "MWANGI",
    "OCHIENG",
    "NJERI",
    "MUTUA",
    "WAIRIMU",
    "ADHIAMBO",
    "KARIUKI",
    "WAMBUA",
    "GITONGA",
    "CHEBET",
    "KOECH",
    "KIBET",
    "KIPCHOGE",
    "CHEPKEMOI",
    "HASSAN",
    "OMAR",
    "WAWERU",
    "GATHONI",
    "AKELLO",
    "ANYANGO",
    "ODERO",
]

KENYAN_COUNTIES = [
    "NAIROBI",
    "MOMBASA",
    "KISUMU",
    "NAKURU",
    "ELDORET",
    "THIKA",
    "MACHAKOS",
    "NYERI",
    "MERU",
    "KISII",
    "KAKAMEGA",
    "KERICHO",
    "EMBU",
    "GARISSA",
    "ISIOLO",
    "KITALE",
    "MALINDI",
    "NANYUKI",
    "VOI",
    "KILIFI",
    "BUNGOMA",
    "BUSIA",
    "MIGORI",
    "HOMA BAY",
]

KENYAN_STREETS = [
    "KENYATTA AVENUE",
    "MOI AVENUE",
    "NGONG ROAD",
    "UHURU HIGHWAY",
    "WAIYAKI WAY",
    "THIKA ROAD",
    "JOGOO ROAD",
    "LANDHIES ROAD",
    "RIVER ROAD",
    "KIRINYAGA ROAD",
    "BIASHARA STREET",
    "BANDA STREET",
    "TOM MBOYA STREET",
    "HAILE SELASSIE AVENUE",
    "ENTERPRISE ROAD",
    "LUSAKA ROAD",
    "COMMERCIAL STREET",
    "INDUSTRIAL AREA ROAD",
]

KENYAN_HOSPITALS = [
    ("KNH001", "KENYATTA NATIONAL HOSPITAL"),
    ("MNH002", "MOMBASA NATIONAL HOSPITAL"),
    ("KNH003", "KISUMU COUNTY REFERRAL HOSPITAL"),
    ("NKR004", "NAKURU COUNTY HOSPITAL"),
    ("ELD005", "MOI TEACHING AND REFERRAL HOSPITAL"),
    ("THK006", "THIKA LEVEL 5 HOSPITAL"),
    ("MCH007", "MACHAKOS LEVEL 5 HOSPITAL"),
    ("NYR008", "NYERI COUNTY REFERRAL HOSPITAL"),
    ("MRU009", "MERU LEVEL 5 HOSPITAL"),
    ("KSI010", "KISII TEACHING AND REFERRAL HOSPITAL"),
    ("KKM011", "KAKAMEGA COUNTY GENERAL HOSPITAL"),
    ("KRC012", "KERICHO COUNTY HOSPITAL"),
    ("EMB013", "EMBU LEVEL 5 HOSPITAL"),
    ("AGK014", "AGA KHAN HOSPITAL NAIROBI"),
    ("NRB015", "NAIROBI HOSPITAL"),
    ("MPC016", "MP SHAH HOSPITAL"),
    ("GRL017", "GERTRUDES CHILDREN HOSPITAL"),
    ("STR018", "STRATHMORE MEDICAL CENTRE"),
    ("AAR019", "AAR HOSPITAL NAIROBI"),
    # Ghost / fraudulent providers (IDs that don't match real registry)
    ("XXX999", "PHANTOM CLINIC NAIROBI"),
    ("ZZZ888", "GHOST MEDICAL CENTRE"),
]

DIAGNOSES = [
    # (description, icd11_code, typical_procedure, typical_bill_min, typical_bill_max, typical_stay_days)
    ("MALARIA, UNCOMPLICATED", "1F40", "BLOOD SMEAR TEST", 3000, 8000, 3),
    ("MALARIA, SEVERE", "1F41", "INTRAVENOUS QUININE THERAPY", 15000, 45000, 7),
    ("TYPHOID FEVER", "1A07", "WIDAL TEST; IV ANTIBIOTICS", 8000, 25000, 5),
    ("PNEUMONIA", "CA22", "CHEST X-RAY; IV ANTIBIOTICS", 20000, 60000, 7),
    ("ACUTE GASTROENTERITIS", "1A00", "IV REHYDRATION THERAPY", 5000, 18000, 3),
    ("URINARY TRACT INFECTION", "GC08", "URINALYSIS; ANTIBIOTICS", 4000, 12000, 3),
    ("HYPERTENSIVE CRISIS", "BA80", "ECG; ANTIHYPERTENSIVES", 10000, 35000, 4),
    ("DIABETIC KETOACIDOSIS", "5A10", "IV INSULIN; ICU MONITORING", 40000, 120000, 5),
    ("APPENDICITIS", "DC80", "APPENDECTOMY", 60000, 180000, 5),
    ("CAESAREAN SECTION", "JA05", "SPINAL ANAESTHESIA; C-SECTION", 80000, 200000, 4),
    ("NORMAL DELIVERY", "JA00", "MIDWIFERY CARE", 15000, 40000, 2),
    (
        "ROAD TRAFFIC ACCIDENT - FRACTURE",
        "NA12",
        "X-RAY; ORTHOPAEDIC SURGERY",
        50000,
        200000,
        7,
    ),
    ("ACUTE KIDNEY INJURY", "GB60", "DIALYSIS", 80000, 300000, 10),
    (
        "CEREBROVASCULAR ACCIDENT (STROKE)",
        "8B20",
        "CT SCAN; PHYSIOTHERAPY",
        60000,
        250000,
        14,
    ),
    (
        "HIV/AIDS WITH COMPLICATIONS",
        "1C62",
        "CD4 COUNT; ARV INITIATION",
        25000,
        80000,
        7,
    ),
    ("TUBERCULOSIS", "1B10", "SPUTUM CULTURE; ANTI-TB DRUGS", 30000, 90000, 14),
    (
        "NEONATAL SEPSIS",
        "KA61",
        "BLOOD CULTURE; IV ANTIBIOTICS (NBU)",
        35000,
        100000,
        10,
    ),
    ("PREECLAMPSIA", "JA21", "MAGNESIUM SULPHATE; MONITORING", 40000, 120000, 5),
    (
        "ACUTE APPENDICITIS WITH PERFORATION",
        "DC81",
        "LAPAROTOMY; IRRIGATION",
        100000,
        350000,
        10,
    ),
    (
        "CHILDHOOD PNEUMONIA",
        "CA22.0",
        "CHEST X-RAY; NEBULISATION; IV ANTIBIOTICS",
        15000,
        50000,
        5,
    ),
]

ACCOMMODATION_TYPES = [
    "Female Medical",
    "Male Medical",
    "Female Surgical",
    "Male Surgical",
    "Gynaecology",
    "Maternity",
    "NBU",
    "Psychiatric Unit",
    "Burns",
    "ICU",
    "HDU",
    "NICU",
    "Isolation",
]

PHYSICIANS = [
    "DR. J. KAMAU - REG: MMB/04521",
    "DR. A. OTIENO - REG: MMB/07832",
    "DR. P. MWANGI - REG: MMB/12043",
    "DR. M. WANJIKU - REG: MMB/05678",
    "DR. S. AKINYI - REG: MMB/09134",
    "DR. D. OCHIENG - REG: MMB/03290",
    "DR. R. KARIUKI - REG: MMB/15671",
    "DR. G. MUTUA - REG: MMB/08823",
    "DR. F. KIPCHOGE - REG: MMB/11234",
    "DR. L. OMAR - REG: MMB/06789",
]

INSURANCE_PROVIDERS = [
    "NHIF",
    "JUBILEE INSURANCE",
    "AAR INSURANCE",
    "BRITAM",
    "CIC INSURANCE",
    None,
    None,
    None,
]

REFERRAL_INSTITUTIONS = [
    "NAIROBI WEST HOSPITAL",
    "KAREN HOSPITAL",
    "PUMWANI MATERNITY HOSPITAL",
    "MBAGATHI HOSPITAL",
    "COAST GENERAL HOSPITAL",
    "RIFT VALLEY PROVINCIAL HOSPITAL",
    "N/A",
]

DISPOSITIONS = [
    "Improved",
    "Recovered",
    "Improved",
    "Recovered",
    "Improved",
    "Leave Against Medical Advice",
    "Absconded",
    "Died",
]

# ──────────────────────────────────────────────────────────────────────────────
# Synthetic Patient Generator
# ──────────────────────────────────────────────────────────────────────────────


def draw_tick(c, x, y, size=6):
    c.setLineWidth(1.2)
    c.line(x - size / 2, y, x - size / 6, y - size / 2)
    c.line(x - size / 6, y - size / 2, x + size / 2, y + size / 3)


def random_sha_number(phantom: bool = False) -> str:
    """Generate a realistic-looking SHA number. Phantom ones have wrong format."""
    if phantom:
        # Wrong prefix or length — fraud signal
        return f"SHA{random.randint(1000, 9999)}-{random.randint(100, 999)}"
    prefix = random.choice(["SHA", "SHI", "KEN"])
    return f"{prefix}-{random.randint(10000000, 99999999)}-{random.randint(100, 999)}"


def random_date(start_year: int = 2024, end_year: int = 2025) -> datetime:
    start = datetime(start_year, 1, 1)
    end = datetime(end_year, 12, 31)
    delta = end - start
    return start + timedelta(days=random.randint(0, delta.days))


def random_claim_number() -> str:
    return f"SHA-CLM-{random.randint(100000, 999999)}-{datetime.now().year}"


def generate_patient(fraud_type: Optional[str] = None) -> dict:
    """Generate a full synthetic patient record."""

    last = random.choice(KENYAN_LAST_NAMES)
    first = random.choice(KENYAN_FIRST_NAMES)
    middle = random.choice(KENYAN_MIDDLE_NAMES)
    county = random.choice(KENYAN_COUNTIES)
    street = random.choice(KENYAN_STREETS)

    # Provider
    is_ghost_provider = fraud_type == "ghost_provider"
    if is_ghost_provider:
        provider_id, provider_name = random.choice(
            [h for h in KENYAN_HOSPITALS if "PHANTOM" in h[1] or "GHOST" in h[1]]
        )
    else:
        provider_id, provider_name = random.choice(
            [
                h
                for h in KENYAN_HOSPITALS
                if "PHANTOM" not in h[1] and "GHOST" not in h[1]
            ]
        )

    # Diagnosis
    dx = random.choice(DIAGNOSES)
    dx_name, icd11, procedure, bill_min, bill_max, stay_days = dx

    # Dates
    admission = random_date()
    is_inflated_stay = fraud_type == "inflated_stay"
    if is_inflated_stay:
        stay_days = stay_days * random.randint(4, 8)  # 4-8x normal stay

    discharge = admission + timedelta(days=stay_days + random.randint(-1, 2))
    if discharge < admission:
        discharge = admission + timedelta(days=1)

    # Amounts
    is_upcoding = fraud_type == "upcoding"
    bill = random.randint(bill_min, bill_max)
    if is_upcoding:
        bill = bill * random.randint(3, 8)  # 3-8x inflated

    # Claim amount is typically slightly less than bill (after SHA negotiation)
    claim = int(bill * random.uniform(0.7, 0.95))

    # Visit type based on diagnosis
    if any(
        term in dx_name
        for term in [
            "CAESAREAN",
            "NORMAL DELIVERY",
            "APPENDICITIS",
            "FRACTURE",
            "STROKE",
            "ACCIDENT",
        ]
    ):
        visit_type = "Inpatient"
    elif stay_days <= 1:
        visit_type = random.choice(["Outpatient", "Day-care"])
    else:
        visit_type = "Inpatient"

    # Accommodation
    if (
        "MATERNITY" in dx_name
        or "DELIVERY" in dx_name
        or "CAESAREAN" in dx_name
        or "PREECLAMPSIA" in dx_name
    ):
        accommodation = "Maternity"
    elif "NEONATAL" in dx_name:
        accommodation = "NBU"
    elif (
        "ICU" in procedure or "DIABETIC KETOACIDOSIS" in dx_name or "STROKE" in dx_name
    ):
        accommodation = "ICU"
    elif "Female" in first or first in [
        "MARY",
        "GRACE",
        "FAITH",
        "JOYCE",
        "ESTHER",
        "MERCY",
        "AGNES",
        "CAROLINE",
        "BEATRICE",
        "DOROTHY",
        "MARGARET",
        "ALICE",
        "LYDIA",
        "ROSE",
        "JANE",
        "SARAH",
        "ANNE",
        "ELIZABETH",
        "LUCY",
        "RUTH",
        "EUNICE",
        "PHYLLIS",
        "GLADYS",
        "HELEN",
        "JUDITH",
        "LILIAN",
        "WINNIE",
        "DIANA",
    ]:
        accommodation = random.choice(
            ["Female Medical", "Female Surgical", "Gynaecology"]
        )
    else:
        accommodation = random.choice(["Male Medical", "Male Surgical"])

    # Referral
    was_referred = random.random() < 0.3
    referral_institution = (
        random.choice(REFERRAL_INSTITUTIONS[:-1]) if was_referred else "N/A"
    )
    referral_reason = f"SPECIALIST REVIEW FOR {dx_name}" if was_referred else "N/A"

    # Disposition
    if "SEVERE" in dx_name or "KETOACIDOSIS" in dx_name:
        disposition = random.choice(["Improved", "Recovered", "Died"])
    else:
        disposition = random.choice(["Improved", "Recovered"])

    # SHA number
    is_phantom = fraud_type == "phantom_patient"
    sha_number = random_sha_number(phantom=is_phantom)

    # Other insurance
    other_insurance = random.choice(INSURANCE_PROVIDERS)

    # Preauth number
    preauth = (
        f"PA-{random.randint(100000, 999999)}" if visit_type == "Inpatient" else "N/A"
    )

    # Case code
    case_code = f"C{random.randint(100, 999)}"

    return {
        # Metadata
        "fraud_type": fraud_type,
        "is_fraudulent": fraud_type is not None,
        # Part I
        "claim_number": random_claim_number(),
        "provider_id": provider_id,
        "provider_name": provider_name,
        # Part II
        "patient_last_name": last,
        "patient_first_name": first,
        "patient_middle_name": middle,
        "sha_number": sha_number,
        "residence": f"{street}, {county}",
        "other_insurance": other_insurance or "NONE",
        "relationship_to_principal": random.choice(
            ["SELF", "SPOUSE", "CHILD", "SELF", "SELF"]
        ),
        # Part III
        "was_referred": was_referred,
        "referral_provider": referral_institution,
        "referral_reason": referral_reason,
        "visit_type": visit_type,
        "admission_date": admission.strftime("%d/%m/%Y"),
        "discharge_date": discharge.strftime("%d/%m/%Y"),
        "op_ip_number": f"{'IP' if visit_type == 'Inpatient' else 'OP'}/{random.randint(10000, 99999)}",
        "new_return_visit": random.choice(["NEW", "NEW", "NEW", "RETURN"]),
        "physician": random.choice(PHYSICIANS),
        "accommodation": accommodation,
        # Field 9
        "disposition": disposition,
        # Field 10
        "discharge_referral_institution": "N/A",
        "discharge_referral_reason": "N/A",
        # Fields 11 & 12
        "admission_diagnosis": dx_name,
        "discharge_diagnosis": dx_name,
        "icd11_code": icd11,
        "related_procedure": procedure,
        "procedure_date": admission.strftime("%d/%m/%Y"),
        # Field 14
        "case_code": case_code,
        "icd11_procedure_code": icd11,
        "description": procedure,
        "preauth_no": preauth,
        "bill_amount": bill,
        "claim_amount": claim,
        "total_bill_amount": bill,
        "total_claim_amount": claim,
        # Declaration
        "patient_name_declaration": f"{last} {first} {middle}",
        "declaration_date": discharge.strftime("%d/%m/%Y"),
    }


def generate_duplicate(original: dict) -> dict:
    """Clone a patient record with a new claim number — simulates duplicate billing."""
    dupe = copy.deepcopy(original)
    dupe["claim_number"] = random_claim_number()
    dupe["fraud_type"] = "duplicate_claim"
    dupe["is_fraudulent"] = True
    # Slightly different provider to try to hide the duplicate
    dupe["provider_id"] = random.choice(
        [h[0] for h in KENYAN_HOSPITALS if "PHANTOM" not in h[1]]
    )
    return dupe


# ──────────────────────────────────────────────────────────────────────────────
# PDF Form Filler
# ──────────────────────────────────────────────────────────────────────────────

# All coordinates are in PDF points (72 pts/inch), y=0 at TOP of page.
# Page dimensions: 612 x 792 pts (A4-ish)
# Derived from form_structure.json analysis.

FORM_FIELDS = {
    # ── PAGE 1 ──────────────────────────────────────────────────────────────
    # All y values are PDF points from TOP of page (converted to bottom-origin
    # inside fill_form via: y_pdf = page_h - y). Row height ≈ 19pt.
    # Text baseline is drawn at y_pdf; body of text sits just above baseline.
    # Claim number — placed after 'CLAIM NO:' label (ends ~x=412)
    "claim_number": {"page": 1, "x": 415, "y": 353, "size": 8},
    # Part I — provider fields; label ends at ~x=297 so entry starts at 300
    "provider_id": {"page": 1, "x": 300, "y": 407, "size": 9},
    "provider_name": {"page": 1, "x": 300, "y": 426, "size": 9},
    # Part II — patient name bullets; 'Last Name' label ends at ~x=195
    "patient_last_name": {"page": 1, "x": 210, "y": 487, "size": 9},
    "patient_first_name": {"page": 1, "x": 210, "y": 506, "size": 9},
    "patient_middle_name": {"page": 1, "x": 210, "y": 525, "size": 9},
    # SHA number — label 'Social Health Authority Number:' ends at ~x=237
    "sha_number": {"page": 1, "x": 240, "y": 544, "size": 9},
    # Residence — label ends at ~x=138
    "residence": {"page": 1, "x": 141, "y": 563, "size": 9},
    # Other insurance — label fills entire row to ~x=376; entry on same row after
    "other_insurance": {"page": 1, "x": 380, "y": 583, "size": 8},
    # Relationship — label 'Relationship to the Principal:' ends at ~x=222
    "relationship": {"page": 1, "x": 228, "y": 602, "size": 9},
    # Part III referral — X placed just after the bullet for NO (bullet at ~x=132-138)
    "referral_no": {"page": 1, "x": 150, "y": 666, "size": 9},
    # Referring provider name — label ends at ~x=356
    "referral_provider": {"page": 1, "x": 358, "y": 704, "size": 8},
    # ── PAGE 2 ──────────────────────────────────────────────────────────────
    # Visit type row (y=94 in PDF top-coords). X marks placed inside each checkbox (☐).
    # Checkboxes: Inpatient ~x=130, Outpatient ~x=208, Day-care ~x=285
    # (handled inline in fill_form, not via this dict)
    # Dates row 1: Visit/Admission Date + OP/IP No. + New/Return Visit
    # Labels end at: 'Visit/Admission Date:' x1=177, 'OP/IP No.:' x1=260, 'New/Return Visit:' x1=455
    # Entry starts just after each label. Row y=111-153, baseline at centre ~132.
    "admission_date": {"page": 2, "x": 160, "y": 130, "size": 9},
    "op_ip_number": {"page": 2, "x": 263, "y": 130, "size": 9},
    "new_return_visit": {"page": 2, "x": 458, "y": 130, "size": 9},
    # Dates row 2: Discharge Date + Rendering Physician
    # Labels end at: 'Discharge Date:' x1=150, 'Rendering Physician...' x1=447
    # Row y=153-195, baseline at centre ~174.
    "discharge_date": {"page": 2, "x": 153, "y": 172, "size": 9},
    "physician": {"page": 2, "x": 250, "y": 172, "size": 8},
    # Accommodation — label 'Type of Accommodation:' at y=220; entry in box to right
    "accommodation": {"page": 2, "x": 172, "y": 224, "size": 8},
    # Disposition checkboxes (y per row from form_structure.json):
    # Improved=267, Recovered=288, Leave Against=310, Absconded=331, Died=353
    # X is drawn inline in fill_form using dispo_y_map
    # Field 10 — discharge referral (label 'Name of Referral Institution:' ends ~x=236)
    "referral_institution": {"page": 2, "x": 238, "y": 403, "size": 8},
    "referral_reason": {"page": 2, "x": 213, "y": 416, "size": 8},
    # Field 11 — Admission Diagnosis (label ends ~x=207)
    "admission_diagnosis": {"page": 2, "x": 209, "y": 430, "size": 8},
    # Field 12 — Discharge Diagnosis bullet fields
    "discharge_diagnosis": {"page": 2, "x": 196, "y": 469, "size": 8},
    "icd11_code": {"page": 2, "x": 217, "y": 488, "size": 8},
    "related_procedure": {"page": 2, "x": 276, "y": 507, "size": 8},
    "procedure_date": {"page": 2, "x": 231, "y": 526, "size": 8},
    # Field 14 — Benefits table data row 1
    # Column x positions taken directly from form_structure.json header labels:
    #   Date Admission: 83-132  | Date Discharge: 142-188 | Case Code: 199-223
    #   ICD11/Proc: 241-316     | Description: 330-384    | Preauth: 397-434
    #   Bill Amount: 445-481    | Claim Amount: 492-529
    "adm_date_row1": {"page": 2, "x": 83, "y": 670, "size": 7},
    "dis_date_row1": {"page": 2, "x": 142, "y": 670, "size": 7},
    "case_code_row1": {"page": 2, "x": 199, "y": 670, "size": 7},
    "icd_proc_row1": {"page": 2, "x": 241, "y": 670, "size": 7},
    "description_row1": {"page": 2, "x": 328, "y": 670, "size": 7},
    "preauth_row1": {"page": 2, "x": 397, "y": 670, "size": 7},
    "bill_amount_row1": {"page": 2, "x": 445, "y": 670, "size": 7},
    "claim_amount_row1": {"page": 2, "x": 492, "y": 670, "size": 7},
    # ── PAGE 3 ──────────────────────────────────────────────────────────────
    # Benefits table continues on page 3; Total row at y=229
    # Total columns align with table Bill/Claim columns: 445 and 492
    "total_bill": {"page": 3, "x": 445, "y": 233, "size": 8},
    "total_claim": {"page": 3, "x": 492, "y": 233, "size": 8},
    # Patient declaration — the form has two lines:
    # Line 1: 'Names (Majina):___...Signature(Sahihi):___...Date(Tarehe):' at y=451
    # Line 2: '________________________' (underscores) at y=462
    # Write name after 'Names (Majina):' (label ends x~108) on line 1
    # Write date on line 2 (underscores) at y=468, left side
    "patient_decl_name": {"page": 3, "x": 155, "y": 450, "size": 8},
    "declaration_date": {"page": 3, "x": 120, "y": 462, "size": 8},
    "approved_amount": {"page": 3, "x": 470, "y": 532, "size": 9},
}


def fill_form(patient: dict, input_pdf: str, output_pdf: str) -> None:
    """
    Fill the SHA form with patient data by overlaying text annotations.
    Uses pypdf to read original + reportlab to draw text overlay.
    """

    BASELINE_OFFSET = 5  # Global vertical correction (fixes alignment)

    reader = PdfReader(input_pdf)
    num_pages = len(reader.pages)

    overlays = {}
    for pg_num in range(1, num_pages + 1):
        page = reader.pages[pg_num - 1]
        w = float(page.mediabox.width)
        h = float(page.mediabox.height)

        packet = io.BytesIO()
        c = canvas.Canvas(packet, pagesize=(w, h))
        c.setFont("Helvetica", 9)
        c.setFillColorRGB(0, 0, 0.6)

        overlays[pg_num] = (c, packet, h)

    def write(field_key: str, value: str):
        if field_key not in FORM_FIELDS:
            return

        f = FORM_FIELDS[field_key]
        pg = f["page"]
        if pg not in overlays:
            return

        c, _, page_h = overlays[pg]
        x = f["x"]

        # Corrected baseline positioning
        y_pdf = page_h - f["y"] - BASELINE_OFFSET

        c.setFont("Helvetica", f.get("size", 9))
        c.drawString(x, y_pdf, str(value))

    # ─────────────────────────────────────────────
    # Fill Page 1
    # ─────────────────────────────────────────────

    write("claim_number", patient["claim_number"])
    write("provider_id", patient["provider_id"])
    write("provider_name", patient["provider_name"])
    write("patient_last_name", patient["patient_last_name"])
    write("patient_first_name", patient["patient_first_name"])
    write("patient_middle_name", patient["patient_middle_name"])
    write("sha_number", patient["sha_number"])
    write("residence", patient["residence"])
    write("other_insurance", patient["other_insurance"])
    write("relationship", patient["relationship_to_principal"])

    if not patient["was_referred"]:
        write("referral_no", "X")
    else:
        write("referral_provider", patient["referral_provider"])

    # ─────────────────────────────────────────────
    # Page 2
    # ─────────────────────────────────────────────

    c2, _, h2 = overlays[2]
    c2.setFont("Helvetica-Bold", 9)

    checkbox_y = h2 - 95 - 5

    # Visit type checkboxes
    if patient["visit_type"] == "Inpatient":
        # c2.drawString(130, h2 - 98 - BASELINE_OFFSET, "X")
        draw_tick(c2, 135, checkbox_y)
    elif patient["visit_type"] == "Outpatient":
        # c2.drawString(208, h2 - 98 - BASELINE_OFFSET, "X")
        draw_tick(c2, 213, checkbox_y)
    else:
        # c2.drawString(285, h2 - 98 - BASELINE_OFFSET, "X")
        draw_tick(c2, 290, checkbox_y)

    write("admission_date", patient["admission_date"])
    write("op_ip_number", patient["op_ip_number"])
    write("new_return_visit", patient["new_return_visit"])
    write("discharge_date", patient["discharge_date"])
    write("physician", patient["physician"])
    write("accommodation", patient["accommodation"])

    # Disposition checkboxes
    dispo_map = {
        "Improved": (196, 271),
        "Recovered": (200, 290),
        "Leave Against Medical Advice": (365, 314),
        "Absconded": (201, 335),
        "Died": (172, 357),
    }

    dispo = patient["disposition"]
    if dispo in dispo_map:
        dx, dy = dispo_map[dispo]
        draw_tick(c2, dx, h2 - dy - 4)

    write("referral_institution", patient["discharge_referral_institution"])
    write("referral_reason", patient["discharge_referral_reason"])
    write("admission_diagnosis", patient["admission_diagnosis"])
    write("discharge_diagnosis", patient["discharge_diagnosis"])
    write("icd11_code", patient["icd11_code"])
    write("related_procedure", patient["related_procedure"])
    write("procedure_date", patient["procedure_date"])

    # Benefits table row
    write("adm_date_row1", patient["admission_date"])
    write("dis_date_row1", patient["discharge_date"])
    write("case_code_row1", patient["case_code"])
    write("icd_proc_row1", patient["icd11_code"])

    def fit_text(text, max_width, font="Helvetica", size=7):
        while stringWidth(text, font, size) > max_width and len(text) > 0:
            text = text[:-1]
        return text

    desc_fitted = fit_text(patient["description"], 65)  # 65pt fits column
    write("description_row1", desc_fitted)

    write("preauth_row1", patient["preauth_no"])
    write("bill_amount_row1", f"{patient['bill_amount']:,}")
    write("claim_amount_row1", f"{patient['claim_amount']:,}")

    # ─────────────────────────────────────────────
    # Page 3
    # ─────────────────────────────────────────────

    write("total_bill", f"{patient['total_bill_amount']:,}")
    write("total_claim", f"{patient['total_claim_amount']:,}")
    write("patient_decl_name", patient["patient_name_declaration"])
    write("declaration_date", patient["declaration_date"])
    write("approved_amount", f"{patient['total_claim_amount']:,}")

    # ─────────────────────────────────────────────
    # Merge overlays
    # ─────────────────────────────────────────────

    writer = PdfWriter()

    for pg_idx in range(num_pages):
        pg_num = pg_idx + 1
        original_page = reader.pages[pg_idx]

        if pg_num in overlays:
            c_overlay, packet, _ = overlays[pg_num]
            c_overlay.save()
            packet.seek(0)
            overlay_reader = PdfReader(packet)
            overlay_page = overlay_reader.pages[0]
            original_page.merge_page(overlay_page)

        writer.add_page(original_page)

    with open(output_pdf, "wb") as f:
        writer.write(f)


# ──────────────────────────────────────────────────────────────────────────────
# Main
# ──────────────────────────────────────────────────────────────────────────────


def main():
    parser = argparse.ArgumentParser(
        description="Generate synthetic Kenyan SHA claim forms (normal + fraudulent).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python generate_sha_claims.py --input SHA-FORM.pdf --count 20 --output ./forms
  python generate_sha_claims.py --input SHA-FORM.pdf --count 100 --fraud-ratio 0.4
  python generate_sha_claims.py --input SHA-FORM.pdf --count 10 --fraud-ratio 0 --output ./clean_forms

Fraud types generated (when --fraud-ratio > 0):
  phantom_patient    - SHA number with wrong format / non-existent patient
  upcoding           - Bill amount 3-8x higher than typical for the diagnosis
  duplicate_claim    - Same patient/dates/diagnosis submitted under different claim number
  ghost_provider     - Provider ID not in official registry
  inflated_stay      - Length of stay 4-8x longer than typical
        """,
    )
    parser.add_argument("--input", required=True, help="Blank SHA form PDF path")
    parser.add_argument(
        "--count",
        type=int,
        default=10,
        help="Number of forms to generate (default: 10)",
    )
    parser.add_argument(
        "--fraud-ratio",
        type=float,
        default=0.3,
        help="Fraction of forms that are fraudulent 0.0-1.0 (default: 0.3)",
    )
    parser.add_argument(
        "--output",
        default="./sha_output_forms",
        help="Output directory (default: ./sha_output_forms)",
    )
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"Error: input file '{args.input}' not found.")
        sys.exit(1)

    os.makedirs(args.output, exist_ok=True)

    fraud_types = [
        "phantom_patient",
        "upcoding",
        "duplicate_claim",
        "ghost_provider",
        "inflated_stay",
    ]
    n_fraud = int(args.count * args.fraud_ratio)
    n_clean = args.count - n_fraud

    patients = []
    # Generate clean records
    for _ in range(n_clean):
        patients.append(generate_patient(fraud_type=None))

    # Generate fraudulent records
    for i in range(n_fraud):
        ft = fraud_types[i % len(fraud_types)]
        if ft == "duplicate_claim" and patients:
            # Clone an existing clean record
            original = random.choice(
                [p for p in patients if not p["is_fraudulent"]] or patients
            )
            patients.append(generate_duplicate(original))
        else:
            patients.append(generate_patient(fraud_type=ft))

    random.shuffle(patients)

    # Metadata ledger
    ledger = []
    clean_count = 0
    fraud_count = 0

    print(f"\nGenerating {args.count} SHA claim forms → {args.output}/")
    print(f"  Clean forms  : {n_clean}")
    print(f"  Fraud forms  : {n_fraud}")
    print()

    for i, patient in enumerate(patients, 1):
        label = (
            f"FRAUD_{patient['fraud_type']}" if patient["is_fraudulent"] else "CLEAN"
        )
        filename = f"sha_claim_{i:04d}_{label}_{patient['patient_last_name']}.pdf"
        output_path = os.path.join(args.output, filename)

        try:
            fill_form(patient, args.input, output_path)
            status = "✓"
            if patient["is_fraudulent"]:
                fraud_count += 1
            else:
                clean_count += 1
        except Exception as e:
            status = f"✗ ERROR: {e}"

        ledger.append(
            {
                "index": i,
                "filename": filename,
                "is_fraudulent": patient["is_fraudulent"],
                "fraud_type": patient["fraud_type"],
                "patient": f"{patient['patient_last_name']} {patient['patient_first_name']}",
                "sha_number": patient["sha_number"],
                "provider_id": patient["provider_id"],
                "provider_name": patient["provider_name"],
                "diagnosis": patient["admission_diagnosis"],
                "admission_date": patient["admission_date"],
                "discharge_date": patient["discharge_date"],
                "bill_amount": patient["bill_amount"],
                "claim_amount": patient["claim_amount"],
                "claim_number": patient["claim_number"],
            }
        )

        print(f"  [{i:4d}/{args.count}] {status}  {label:30s}  {filename}")

    # Save ledger as JSON (ground truth for model training)
    ledger_path = os.path.join(args.output, "claims_ledger.json")
    with open(ledger_path, "w") as f:
        json.dump(ledger, f, indent=2)

    # Save ledger as CSV for easy inspection
    csv_path = os.path.join(args.output, "claims_ledger.csv")
    with open(csv_path, "w") as f:
        headers = list(ledger[0].keys())
        f.write(",".join(headers) + "\n")
        for row in ledger:
            f.write(",".join(str(row.get(h, "")) for h in headers) + "\n")

    print(
        f"""
Done!
  ✓ {clean_count} clean forms
  ✓ {fraud_count} fraudulent forms
  ✓ Ground truth ledger : {ledger_path}
  ✓ CSV summary         : {csv_path}
"""
    )


if __name__ == "__main__":
    main()


# # Install requirements (already in your project)
# pip install pypdf reportlab

# # Generate 100 forms with 30% fraud
# python generate_sha_claims.py --input SHA-FORM.pdf --count 100 --fraud-ratio 0.3 --output ./forms

# # Generate 500 forms for model training
# python generate_sha_claims.py --input SHA-FORM.pdf --count 500 --fraud-ratio 0.4 --output ./training_data
