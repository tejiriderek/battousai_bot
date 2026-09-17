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

try:
    import redis
    REDIS_AVAILABLE = True
except ImportError:
    REDIS_AVAILABLE = False
    redis = None

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
    "last_twelve_data_price": None,
    "last_twelve_data_at": None,
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
    "warning_sent_at": None,
    "warning_type": None,
    "warning_acknowledged": False,
    "confirmation_prompt_count": 0,
    "retest_allowed": None,
    "second_chance_allowed": None,
}


class StateManager:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or config.STATE_PATH
        self._lock = threading.Lock()
        self._data: dict[str, Any] = {"pairs": {}, "meta": {}}
        self._redis_client = None
        self._redis_key = "battousai:scanner:state"
        
        # Initialize Redis client if available
        if REDIS_AVAILABLE and config.UPSTASH_REDIS_URL:
            try:
                # Force SSL for Upstash
                redis_url = config.UPSTASH_REDIS_URL
                if redis_url.startswith("redis://"):
                    redis_url = redis_url.replace("redis://", "rediss://", 1)
                self._redis_client = redis.from_url(redis_url, ssl_cert_reqs=None)
                self._redis_client.ping()
                log.info("Redis connection established")
            except Exception as exc:
                log.warning("Redis connection failed: %s; falling back to file storage", exc)
                self._redis_client = None
        
        self.load()

    def _retire_legacy_alert_sent(self) -> None:
        if not config.RETIRE_LEGACY_ALERT_SENT:
            return
        for pair in config.PAIRS:
            current = self._data["pairs"].get(pair, {})
            if current.get("state") != "ALERT_SENT":
                continue
            self.reset(
                pair,
                reason="legacy_alert_sent_retired",
                details={
                    "alert_key": current.get("last_alert_key"),
                    "alert_at": current.get("last_alert_at"),
                },
                last_alert_key=current.get("last_alert_key"),
                last_alert_at=current.get("last_alert_at"),
            )
            log.warning("Retired legacy ALERT_SENT state for %s", pair)

    def load(self) -> None:
        # Try loading from Redis first
        if self._redis_client:
            try:
                redis_data = self._redis_client.get(self._redis_key)
                if redis_data:
                    raw = json.loads(redis_data)
                    pairs = raw.get("pairs", {})
                    for pair in config.PAIRS:
                        if pair in pairs:
                            current = deepcopy(EMPTY_PAIR)
                            current.update(pairs[pair])
                            pairs[pair] = current
                        else:
                            log.warning("Pair %s not in Redis state; initializing to WATCHING", pair)
                            pairs[pair] = deepcopy(EMPTY_PAIR)
                    raw["pairs"] = pairs
                    raw.setdefault("meta", {})
                    raw["meta"].setdefault("events", [])
                    self._data = raw
                    self._retire_legacy_alert_sent()
                    log.info("State loaded from Redis")
                    return
            except Exception as exc:
                log.warning("Failed to load from Redis: %s; falling back to file", exc)
        
        # Fallback to file storage
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
            self._retire_legacy_alert_sent()
            log.info("State loaded from file")
        except (OSError, json.JSONDecodeError) as exc:
            log.error("State file unreadable (%s); initializing all pairs to WATCHING - ACTIVE SETUPS MAY BE LOST", exc)
            self._data = {
                "pairs": {pair: deepcopy(EMPTY_PAIR) for pair in config.PAIRS},
                "meta": {"scanner_running": False, "last_scan_at": None, "events": []},
            }

    def save(self) -> None:
        # Save to Redis if available
        if self._redis_client:
            try:
                self._redis_client.set(
                    self._redis_key,
                    json.dumps(self._data, indent=2, default=str),
                )
            except Exception as exc:
                log.warning("Failed to save to Redis: %s", exc)
        
        # Always save to file as backup
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
                "type": (
                    "setup_completed"
                    if reason == "setup_completed"
                    else "setup_invalidated"
                ),
                "pair": pair,
                "previous_state": previous_state,
                "reason": reason,
                "details": details or {},
                "setup_id": previous.get("setup_id"),
                "pair_state": previous,
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

    def has_processed_tradingview_email(self, message_id: str) -> bool:
        with self._lock:
            return message_id in self._data.setdefault("meta", {}).setdefault("tradingview_email_ids", [])

    def mark_tradingview_email_processed(self, message_id: str) -> None:
        with self._lock:
            ids = self._data.setdefault("meta", {}).setdefault("tradingview_email_ids", [])
            if message_id not in ids:
                ids.append(message_id)
                del ids[:-1000]
                self.save()

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
        valid_types = {"gap", "news", "aging", "retest", "second_chance"}
        if warning_type not in valid_types or decision not in {"yes", "no"}:
            return False
        if decision == "yes":
            field = {
                "gap": "gap_override",
                "news": "news_override",
                "aging": "aging_override",
                "retest": "retest_allowed",
                "second_chance": "second_chance_allowed",
            }[warning_type]
            self.update(pair, **{field: True, "warning_acknowledged": True})
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

        if warning_type in {"retest", "second_chance"}:
            self.update(pair, **{f"{warning_type}_allowed": False, "warning_acknowledged": True})
            self.record_event(
                {
                    "type": "warning_decision",
                    "pair": pair,
                    "setup_id": setup_id,
                    "warning_type": warning_type,
                    "decision": "no",
                    "resulting_state": current.get("state"),
                    "override_applied": False,
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

    def record_warning_sent(self, pair: str, warning_type: str) -> None:
        """Record a prompt and preserve its count across restarts."""
        current = self.get(pair)
        prompt_count = current.get("confirmation_prompt_count", 0)
        if current.get("warning_type") != warning_type or current.get("warning_acknowledged"):
            prompt_count = 0
        self.update(
            pair,
            warning_sent_at=_now(),
            warning_type=warning_type,
            warning_acknowledged=False,
            confirmation_prompt_count=int(prompt_count) + 1,
        )

    def should_reprompt_warning(self, pair: str, timeout_minutes: int = 5) -> bool:
        """Check if a warning should be re-prompted (not acknowledged and timed out)."""
        current = self.get(pair)
        if current.get("state") == "WATCHING":
            return False
        if current.get("warning_acknowledged"):
            return False
        warning_sent_at = current.get("warning_sent_at")
        if not warning_sent_at:
            return False
        try:
            sent_time = datetime.fromisoformat(warning_sent_at)
            elapsed = (datetime.now(timezone.utc) - sent_time).total_seconds()
            return elapsed >= (timeout_minutes * 60)
        except (ValueError, TypeError):
            return False

    def auto_apply_warning_timeout(self, pair: str, timeout_minutes: int = 10) -> bool:
        """Auto-apply YES decision if warning timed out without response."""
        current = self.get(pair)
        if current.get("state") == "WATCHING":
            return False
        if current.get("warning_acknowledged"):
            return False
        warning_sent_at = current.get("warning_sent_at")
        warning_type = current.get("warning_type")
        if not warning_sent_at or not warning_type:
            return False
        try:
            sent_time = datetime.fromisoformat(warning_sent_at)
            elapsed = (datetime.now(timezone.utc) - sent_time).total_seconds()
            if elapsed >= (timeout_minutes * 60):
                setup_id = current.get("setup_id")
                if setup_id:
                    field = {
                        "gap": "gap_override",
                        "news": "news_override",
                        "aging": "aging_override",
                    }[warning_type]
                    self.update(pair, **{field: True, "warning_acknowledged": True})
                    self.record_event(
                        {
                            "type": "warning_decision",
                            "pair": pair,
                            "setup_id": setup_id,
                            "warning_type": warning_type,
                            "decision": "auto_yes",
                            "resulting_state": current.get("state"),
                            "override_applied": True,
                            "timeout_seconds": int(elapsed),
                            "at": _now(),
                        }
                    )
                    log.info("Auto-applied YES for %s warning on %s after %d seconds timeout", warning_type, pair, int(elapsed))
                    return True
        except (ValueError, TypeError):
            pass
        return False


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()
