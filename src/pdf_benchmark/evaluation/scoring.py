from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, model_validator


CATEGORY_ORDER = [
    "text",
    "table",
    "math",
    "chemistry",
    "image",
    "diagram",
]


class ScoringConfig(BaseModel):
    overall_weights: dict[str, float] = Field(
        default_factory=lambda: {
            "text": 1 / 6,
            "table": 1 / 6,
            "math": 1 / 6,
            "chemistry": 1 / 6,
            "image": 1 / 6,
            "diagram": 1 / 6,
        }
    )
    bootstrap_resamples: int = Field(default=10_000, ge=100)
    bootstrap_seed: int = 42
    strict_overall_categories: bool = True

    @model_validator(mode="after")
    def validate_weights(self):
        if set(self.overall_weights) != set(CATEGORY_ORDER):
            raise ValueError(
                f"overall_weights must contain exactly: {CATEGORY_ORDER}"
            )
        if any(v < 0 for v in self.overall_weights.values()):
            raise ValueError("Overall weights must be non-negative")
        total = sum(self.overall_weights.values())
        if total <= 0:
            raise ValueError("At least one overall weight must be positive")
        # Normalize here to support user-friendly percentages / arbitrary sums.
        self.overall_weights = {
            k: v / total for k, v in self.overall_weights.items()
        }
        return self

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ScoringConfig":
        payload = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
        data = payload.get("evaluation", payload)
        return cls.model_validate(data)
