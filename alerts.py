"""Alert engine — keyword triggers, silence detection, and volume spike detection."""

import re
import time
from typing import List, Dict, Optional


class AlertEngine:
    """Monitors transcripts and audio for configurable alert conditions."""

    def __init__(self, db=None):
        self.db = db
        self._keyword_alerts = []  # [{id, name, pattern, is_regex, compiled}]
        self._silence_thresholds = {}  # channel_id -> seconds
        self._last_heard = {}  # channel_id -> timestamp
        self._volume_threshold = 0.8  # RMS spike threshold (0-1)
        self._on_alert = None  # callback(alert_data)

        # Load alerts from DB
        if db:
            self._load_from_db()

    def set_callback(self, on_alert=None):
        self._on_alert = on_alert

    def _load_from_db(self):
        """Load alert rules from database."""
        alerts = self.db.get_alerts()
        self._keyword_alerts = []
        for a in alerts:
            if a["alert_type"] == "keyword" and a["enabled"]:
                compiled = None
                if a["is_regex"]:
                    try:
                        compiled = re.compile(a["pattern"], re.IGNORECASE)
                    except re.error:
                        print(f"[Alerts] Invalid regex: {a['pattern']}")
                        continue
                self._keyword_alerts.append({
                    "id": a["id"],
                    "name": a["name"],
                    "pattern": a["pattern"],
                    "is_regex": a["is_regex"],
                    "compiled": compiled,
                })

    def reload(self):
        """Reload alerts from database."""
        if self.db:
            self._load_from_db()

    # ===== Keyword Alerts =====

    def add_keyword_alert(self, name: str, pattern: str, is_regex: bool = False) -> int:
        """Add a keyword alert rule. Returns the alert ID."""
        compiled = None
        if is_regex:
            compiled = re.compile(pattern, re.IGNORECASE)

        alert_id = None
        if self.db:
            alert_id = self.db.add_alert(name, pattern, "keyword", is_regex)

        self._keyword_alerts.append({
            "id": alert_id,
            "name": name,
            "pattern": pattern,
            "is_regex": is_regex,
            "compiled": compiled,
        })
        return alert_id

    def remove_keyword_alert(self, alert_id: int):
        """Remove a keyword alert."""
        self._keyword_alerts = [a for a in self._keyword_alerts if a["id"] != alert_id]
        if self.db:
            self.db.remove_alert(alert_id)

    def check_text(self, text: str, channel_id: str = None) -> List[Dict]:
        """Check text against keyword alert rules. Returns list of triggered alerts."""
        triggered = []
        text_lower = text.lower()

        for alert in self._keyword_alerts:
            matched = False
            if alert["is_regex"] and alert["compiled"]:
                matched = bool(alert["compiled"].search(text))
            else:
                matched = alert["pattern"].lower() in text_lower

            if matched:
                alert_data = {
                    "alert_id": alert["id"],
                    "alert_name": alert["name"],
                    "alert_type": "keyword",
                    "pattern": alert["pattern"],
                    "channel_id": channel_id,
                    "text": text,
                    "timestamp": time.time(),
                }
                triggered.append(alert_data)
                self._fire(alert_data)

        return triggered

    # ===== Silence Detection =====

    def set_silence_threshold(self, channel_id: str, seconds: float):
        """Set silence alert threshold for a channel."""
        self._silence_thresholds[channel_id] = seconds

    def remove_silence_threshold(self, channel_id: str):
        self._silence_thresholds.pop(channel_id, None)

    def record_activity(self, channel_id: str):
        """Record that audio activity was heard on a channel."""
        self._last_heard[channel_id] = time.time()

    def check_silence(self) -> List[Dict]:
        """Check all channels for silence exceeding threshold. Returns triggered alerts."""
        triggered = []
        now = time.time()
        for ch_id, threshold in self._silence_thresholds.items():
            last = self._last_heard.get(ch_id, 0)
            if last > 0 and (now - last) > threshold:
                alert_data = {
                    "alert_type": "silence",
                    "channel_id": ch_id,
                    "silence_duration": round(now - last, 1),
                    "threshold": threshold,
                    "timestamp": now,
                }
                triggered.append(alert_data)
                self._fire(alert_data)
                # Reset to avoid repeated alerts
                self._last_heard[ch_id] = now
        return triggered

    # ===== Volume Spike Detection =====

    def set_volume_threshold(self, threshold: float):
        """Set volume spike threshold (0.0 - 1.0)."""
        self._volume_threshold = threshold

    def check_volume(self, channel_id: str, level: float) -> Optional[Dict]:
        """Check if audio level exceeds spike threshold."""
        if level >= self._volume_threshold:
            alert_data = {
                "alert_type": "volume_spike",
                "channel_id": channel_id,
                "level": round(level, 4),
                "threshold": self._volume_threshold,
                "timestamp": time.time(),
            }
            self._fire(alert_data)
            return alert_data
        return None

    # ===== Callback =====

    def _fire(self, alert_data: dict):
        if self._on_alert:
            try:
                self._on_alert(alert_data)
            except Exception as e:
                print(f"[Alerts] Callback error: {e}")

    def get_rules(self) -> List[Dict]:
        """Get all active alert rules."""
        rules = []
        for a in self._keyword_alerts:
            rules.append({
                "id": a["id"],
                "name": a["name"],
                "pattern": a["pattern"],
                "type": "keyword",
                "is_regex": a["is_regex"],
            })
        for ch_id, secs in self._silence_thresholds.items():
            rules.append({
                "type": "silence",
                "channel_id": ch_id,
                "threshold_seconds": secs,
            })
        rules.append({
            "type": "volume_spike",
            "threshold": self._volume_threshold,
        })
        return rules
