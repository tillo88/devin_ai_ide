"""Training mode primitives for DEVIN.

This package stores benchmark-style cases, attempts, corrections and verified
lessons without promoting anything into shared memory automatically.
"""

from .store import TrainingStore

__all__ = ["TrainingStore"]
