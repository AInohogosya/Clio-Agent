from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from clio.observability.logger import get_logger

logger = get_logger("clio.gateway.calibration")

#: The file the nightly consolidation writes and the router reads.
QUALITY_FILE_NAME = "model_quality.json"

#: How often the router re-reads the file. A select happens many times a
#: second at tick rate; an mtime check each select is cheaper than a stale
#: score for a day.
RELOAD_INTERVAL_S = 30.0

#: The penalty curve. A model's effective quality is its catalogue opinion
#: scaled by its measured ok-rate, at half weight: a model that failed every
#: solver call it made loses half its stated quality -- enough to fall behind
#: any healthy competitor in the argmax -- and no more, because a model that
#: is bad at `gsl.step` may still be the only thing that can serve a tier.
#: Zero successful-but-erroring calls is the ceiling of the penalty; a model
#: with no measured record keeps its catalogue quality untouched.
PENALTY_WEIGHT = 0.5


def quality_path(data_dir: Path) -> Path:
    return Path(data_dir) / QUALITY_FILE_NAME


def load_model_quality(data_dir: Path) -> dict[str, Any]:
    path = quality_path(data_dir)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except Exception:
        logger.warning("calibration.read_failed", path=str(path))
        return {}


def write_model_quality(data_dir: Path, stats: dict[str, Any]) -> None:
    """Persist measured per-model ok-rates. Atomic: write-then-rename."""
    path = quality_path(data_dir)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(stats, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
    except Exception:
        logger.exception("calibration.write_failed", path=str(path))


def effective_quality(base: float, record: dict[str, Any] | None) -> float:
    """The catalogue opinion adjusted by what the model actually did.

    `effective = base * (1 - PENALTY_WEIGHT * error_rate)` for a model with a
    measured record, and `base` for one without. The multiplication keeps the
    adjustment proportional (a 0.90 opinion with a 30% error rate scores
    0.765, below a healthy 0.90 rival and above a 0.70 fallback), and the
    half weight keeps a measured weakness from erasing a stated strength.
    """
    if not record:
        return base
    try:
        calls = int(record.get("calls") or 0)
        ok = int(record.get("ok") or 0)
    except (TypeError, ValueError):
        return base
    if calls <= 0:
        return base
    error_rate = 1.0 - min(1.0, max(0.0, ok / calls))
    adjusted = float(base) * (1.0 - PENALTY_WEIGHT * error_rate)
    return max(0.0, min(float(base), adjusted))


class QualityCalibration:
    """Per-model quality overrides read from the nightly calibration file.

    The router asks this object for every entry's quality instead of reading
    the catalogue value directly. The file is re-read at most once per
    `RELOAD_INTERVAL_S`; a missing file means "no record", which is the same
    as "keep the catalogue opinion".
    """

    def __init__(self, data_dir: Path):
        self._path = quality_path(data_dir)
        self._stats: dict[str, Any] = {}
        self._loaded_at: float = 0.0
        self._mtime: float | None = None

    def _refresh(self) -> None:
        now = time.monotonic()
        if now - self._loaded_at < RELOAD_INTERVAL_S:
            return
        self._loaded_at = now
        try:
            mtime = self._path.stat().st_mtime
        except OSError:
            if self._stats:
                self._stats = {}
                self._mtime = None
            return
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self._stats = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            self._stats = {}
        except Exception:
            logger.warning("calibration.read_failed", path=str(self._path))
            self._stats = {}

    def entry_key(self, provider: str | None, model: str | None) -> str:
        return f"{provider or ''}::{model or ''}"

    def effective_quality(self, base: float, provider: str | None, model: str | None) -> float:
        self._refresh()
        return effective_quality(base, self._stats.get(self.entry_key(provider, model)))
