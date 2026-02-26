"""
SHA Fraud Detection — Base Detector Interface

Every pluggable fraud detector must extend BaseDetector.
This enforces a consistent interface across all detection modules.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, Optional

from sqlalchemy.ext.asyncio import AsyncSession

from app.models.claim_model import Claim, ClaimFeature


@dataclass
class DetectorResult:
    """
    Standardised output from any detector.

    Attributes:
        detector_name:   Class name of the detector that produced this result.
        score:           Risk contribution 0–100. Higher = more suspicious.
        fired:           True if the detector's primary condition was met.
        explanation:     Human-readable reason for the score.
        feature_name:    Key feature that drove the score (for SHAP-style display).
        feature_value:   Actual value observed (as string for logging).
        metadata:        Optional extra data for the alert/case payload.
    """

    detector_name: str
    score: float
    fired: bool
    explanation: str
    feature_name: Optional[str] = None
    feature_value: Optional[str] = None
    metadata: Dict[str, Any] = field(default_factory=dict)


class BaseDetector(ABC):
    """
    Abstract base class for all SHA fraud detectors.

    Subclasses must implement:
        - detect()   → DetectorResult
        - explain()  → dict  (feature → weight mapping for SHAP-style output)
    """

    def __init__(self, db: AsyncSession):
        self.db = db

    @property
    def name(self) -> str:
        return self.__class__.__name__

    @abstractmethod
    async def detect(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> DetectorResult:
        """
        Analyse the claim and return a DetectorResult.

        Args:
            claim:    The Claim ORM object (with relationships loaded).
            features: Pre-computed ClaimFeature object (may be None if not yet computed).

        Returns:
            DetectorResult with score 0–100 and explanation.
        """

    @abstractmethod
    async def explain(
        self,
        claim: Claim,
        features: Optional[ClaimFeature] = None,
    ) -> Dict[str, float]:
        """
        Return a mapping of feature_name → weight for explainability logging.
        Analogous to SHAP values for rule/detector-based signals.

        Example:
            {"duplicate_count_7d": 75.0, "same_provider": 1.0}
        """
