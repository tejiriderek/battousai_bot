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
    "setup_id": None,
    "gap_override": False,
    "news_override": False,
    "aging_override": False,
    "weekend_gap_candle_time": None,
    "weekend_gap_pips": None,
    "weekend_gap_override": False,
    "last_aging_warning_bars": 0,
    "last_news_warning_key": None,
    "last_reset_reason": None,
    "last_reset_details": None,
    "last_reset_at": None,
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
            log.warning("State file not found; initializing all pairs to WATCHING")
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            pairs = raw.get("pairs", {})
            for pair in config.PAIRS:
                if pair in pairs:
                    current = deepcopy(EMPTY_PAIR)
                    current.update(pairs[pair])
                    pairs[pair] = current
                else:
                    log.warning("Pair %s not in state file; initializing to WATCHING", pair)
                    pairs[pair] = deepcopy(EMPTY_PAIR)
            raw["pairs"] = pairs
            raw.setdefault("meta", {})
            raw["meta"].setdefault("events", [])
            self._data = raw
        except (OSError, json.JSONDecodeError) as exc:
            log.error("State file unreadable (%s); initializing all pairs to WATCHING - ACTIVE SETUPS MAY BE LOST", exc)
            self._data = {
                "pairs": {pair: deepcopy(EMPTY_PAIR) for pair in config.PAIRS},
                "meta": {"scanner_running": False, "last_scan_at": None, "events": []},
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
            previous_state = row.get("state") or "WATCHING"
            row.update(changes)
            row["updated_at"] = _now()
            next_state = row.get("state") or "WATCHING"
            if next_state != previous_state:
                self._data.setdefault("meta", {}).setdefault("events", []).append(
                    {
                        "type": "state_transition",
                        "pair": pair,
                        "from_state": previous_state,
                        "to_state": next_state,
                        "at": row["updated_at"],
                        "reason": row.get("last_reset_reason"),
                        "details": row.get("last_reset_details") or {},
                    }
                )
            self.save()
            return deepcopy(row)

    def reset(
        self,
        pair: str,
        reason: str = "unspecified_reset",
        details: dict[str, Any] | None = None,
        **keep: Any,
    ) -> dict[str, Any]:
        previous = self.get(pair)
        previous_state = previous.get("state", "WATCHING")
        preserved = {key: keep[key] for key in ("last_alert_key", "last_alert_at") if key in keep}
        restored = deepcopy(EMPTY_PAIR)
        restored.update(preserved)
        restored["state"] = "WATCHING"
        restored["last_reset_reason"] = reason
        restored["last_reset_details"] = details or {}
        restored["last_reset_at"] = _now()
        result = self.update(pair, **restored)
        log.warning(
            "%s | %s -> WATCHING | reason=%s | details=%s",
            pair,
            previous_state,
            reason,
            details or {},
        )
        self.record_event(
            {
                "type": "setup_invalidated",
                "pair": pair,
                "previous_state": previous_state,
                "reason": reason,
                "details": details or {},
                "setup_id": previous.get("setup_id"),
                "at": _now(),
            }
        )
        return result

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return deepcopy(self._data)

    def set_meta(self, **changes: Any) -> None:
        with self._lock:
            self._data.setdefault("meta", {}).update(changes)
            self.save()

    def record_event(self, event: dict[str, Any]) -> None:
        with self._lock:
            self._data.setdefault("meta", {}).setdefault("events", []).append(event)
            self.save()

    def drain_events(self) -> list[dict[str, Any]]:
        with self._lock:
            events = list(self._data.setdefault("meta", {}).setdefault("events", []))
            self._data["meta"]["events"] = []
            self.save()
            return events

    def apply_warning_decision(
        self,
        pair: str,
        setup_id: str,
        warning_type: str,
        decision: str,
    ) -> bool:
        current = self.get(pair)
        if current.get("setup_id") != setup_id or current.get("state") == "WATCHING":
            return False
        if warning_type not in {"gap", "news", "aging"} or decision not in {"yes", "no"}:
            return False
        if decision == "yes":
            field = {
                "gap": "gap_override",
                "news": "news_override",
                "aging": "aging_override",
            }[warning_type]
            self.update(pair, **{field: True})
            self.record_event(
                {
                    "type": "warning_decision",
                    "pair": pair,
                    "setup_id": setup_id,
                    "warning_type": warning_type,
                    "decision": "yes",
                    "resulting_state": current.get("state"),
                    "override_applied": True,
                    "at": _now(),
                }
            )
            return True

        self.reset(
            pair,
            reason=f"user_ended_{warning_type}",
            details={"setup_id": setup_id, "warning_type": warning_type},
        )
        self.record_event(
            {
                "type": "warning_decision",
                "pair": pair,
                "setup_id": setup_id,
                "warning_type": warning_type,
                "decision": "no",
                "resulting_state": "WATCHING",
                "override_applied": False,
                "at": _now(),
            }
        )
        return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
