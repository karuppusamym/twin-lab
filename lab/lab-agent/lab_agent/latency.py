"""Latency drawn from a lognormal fitted to p50 and p95.

For a lognormal, the median is exp(mu) and the 95th percentile is exp(mu + 1.6449 * sigma), so

    mu = ln(p50),  sigma = (ln(p95) - ln(p50)) / 1.6449

A draw is capped at CAP_FACTOR * p95 so one unlucky draw cannot hang a demo for minutes.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass

Z95 = 1.6448536269514722  # standard normal quantile at 0.95
CAP_FACTOR = 10


@dataclass(frozen=True)
class LogNormal:
    p50: float
    p95: float

    def __post_init__(self) -> None:
        if self.p50 < 0 or self.p95 < self.p50:
            raise ValueError("need 0 <= p50 <= p95")

    @property
    def mu(self) -> float:
        return math.log(self.p50) if self.p50 > 0 else float("-inf")

    @property
    def sigma(self) -> float:
        if self.p50 <= 0 or self.p95 == self.p50:
            return 0.0
        return (math.log(self.p95) - math.log(self.p50)) / Z95

    def from_normal(self, z: float) -> float:
        """The quantile for a standard normal value z (deterministic)."""
        if self.p50 <= 0:
            # p50 = 0: treat as "mostly instant"; only the tail above the median adds delay.
            return 0.0 if z <= 0 or self.p95 <= 0 else min(self.p95 * z / Z95, self.cap)
        return min(math.exp(self.mu + self.sigma * z), self.cap)

    @property
    def cap(self) -> float:
        return CAP_FACTOR * self.p95

    def sample(self, rng: random.Random) -> float:
        return self.from_normal(rng.gauss(0.0, 1.0))
