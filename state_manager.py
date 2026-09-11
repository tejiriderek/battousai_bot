"""Per-pair state machine. Persists to disk so restarts do not double-alert."""

from __future__ import annotations

import json
import logging
import threading
from copy import deepcopy
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import config

log = logging.getLogger(__name__)

EMPTY_PAIR = {
    "state": "WATCHING",
    "direction": None,
    "daily_setup": None,
    "daily_level_type": None,
    "daily_level_price": None,
    "daily_bar_time": None,
    "h4_level_type": None,
    "h4_level_price": None,
    "h4_breakout_bar_time": None,
    "retest_bar_time": None,
    "level_flip": None,
    "rejection_status": "NONE",
    "breakout_status": "NONE",
    "retest_status": "NONE",
    "continuation_status": "NONE",
    "candle_close_status": "NONE",
    "daily_confirmed": False,
    "h4_confirmed": False,
    "h4_bars_since_breakout": 0,
    "last_daily_bar": None,
    "last_h4_bar": None,
    "last_alert_key": None,
    "last_alert_at": None,
    "updated_at": None,
}


class StateManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.STATE_PATH
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"pairs": {}, "meta": {}}
        self.load()

    def load(self) -> None:
        if not self.path.exists():
            self._data = {
                "pairs": {pair: deepcopy(EMPTY_PAIR) for pair in config.PAIRS},
                "meta": {"scanner_running": False, "last_scan_at": None},
            }
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            pairs = raw.get("pairs", {})
            for pair in config.PAIRS:
                current = deepcopy(EMPTY_PAIR)
                current.update(pairs.get(pair, {}))
                pairs[pair] = current
            raw["pairs"] = pairs
            raw.setdefault("meta", {})
            self._data = raw
        except (OSError, json.JSONDecodeError) as exc:
            log.warning("State file unreadable (%s); starting fresh", exc)
            self._data = {
                "pairs": {pair: deepcopy(EMPTY_PAIR) for pair in config.PAIRS},
                "meta": {"scanner_running": False, "last_scan_at": None},
            }

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps(self._data, indent=2, default=str), encoding="utf-8")
        tmp.replace(self.path)

    def get(self, pair: str) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data["pairs"].setdefault(pair, deepcopy(EMPTY_PAIR)))

    def update(self, pair: str, **changes: Any) -> dict[str, Any]:
        with self._lock:
            row = self._data["pairs"].setdefault(pair, deepcopy(EMPTY_PAIR))
            row.update(changes)
            row["updated_at"] = _now()
            self.save()
            return deepcopy(row)

    def reset(self, pair: str, **keep: Any) -> dict[str, Any]:
        preserved = {key: keep[key] for key in ("last_alert_key", "last_alert_at") if key in keep}
        restored = deepcopy(EMPTY_PAIR)
        restored.update(preserved)
        restored["state"] = "WATCHING"
        return self.update(pair, **restored)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def set_meta(self, **changes: Any) -> None:
        with self._lock:
            self._data.setdefault("meta", {}).update(changes)
            self.save()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
