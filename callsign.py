"""Callsign extraction and registry — passively analyzes transcripts to identify radio callsigns."""

import re
import time
from typing import List, Dict, Optional


# ===== Pattern Definitions =====

# Amateur / RAYNET callsigns: e.g. MM0RFN, 2E0XYZ, G4ABC, VK3ABC
AMATEUR_PATTERN = re.compile(
    r'\b([A-Z]{1,2}\d[A-Z]{1,4})\b'
    r'|'
    r'\b(\d[A-Z]\d[A-Z]{1,4})\b',   # e.g. 2E0XYZ
    re.IGNORECASE
)

# Aviation callsigns: airline + flight number, or ICAO format
AVIATION_AIRLINES = {
    "speedbird": "BAW", "ryanair": "RYR", "easyjet": "EZY", "shamrock": "EIN",
    "delta": "DAL", "united": "UAL", "american": "AAL", "southwest": "SWA",
    "jetblue": "JBU", "lufthansa": "DLH", "air france": "AFR", "klm": "KLM",
    "british": "BAW", "virgin": "VIR", "emirates": "UAE", "qantas": "QFA",
    "cactus": "AWE", "citrus": "SWR", "springbok": "SAA",
}

AVIATION_CALLSIGN_PATTERN = re.compile(
    r'\b(' + '|'.join(re.escape(k) for k in AVIATION_AIRLINES.keys()) + r')\s*(\d{1,4}[A-Z]{0,2})\b',
    re.IGNORECASE
)

# ICAO 3-letter + digits (e.g. BAW142, RYR4512)
ICAO_PATTERN = re.compile(r'\b([A-Z]{3}\d{1,4}[A-Z]?)\b', re.IGNORECASE)

# General / emergency service unit patterns
UNIT_PATTERNS = [
    # Compound unit types first (so "air truck 2" matches before plain "truck 2")
    re.compile(r'\b(air truck|air rescue|tower ladder|hazmat|brush truck|water tender|fire boat|district chief|deputy chief|division chief)\s*(\d{1,4})\b', re.IGNORECASE),
    re.compile(r'\b(unit|car|engine|truck|squad|medic|rescue|ladder|battalion|chief|drone)\s*(\d{1,4})\b', re.IGNORECASE),
    re.compile(r'\b(alpha|bravo|charlie|delta|echo|foxtrot|golf|hotel)\s*(\d{1,3})\b', re.IGNORECASE),
    re.compile(r'\b(patrol|ambulance|tanker|pumper|quint)\s*(\d{1,4})\b', re.IGNORECASE),
]

# Maritime: "this is <vessel name>" or MMSI (9 digits)
MARITIME_VESSEL_PATTERN = re.compile(
    r'(?:this is|calling)\s+(?:the\s+)?(?:motor vessel |mv |vessel )([A-Z][A-Za-z\s]{2,25}?)(?:\s*,|\s*\.|\s+on|\s+over|\s+requesting)',
    re.IGNORECASE
)
MMSI_PATTERN = re.compile(r'\b(\d{9})\b')

# Common radio phrases that should never be detected as callsigns
FALSE_POSITIVE_PHRASES = {
    "standby", "request", "standby request", "copy", "roger", "affirmative",
    "negative", "acknowledged", "dispatch", "central", "control",
    "en route", "on scene", "clear", "available", "out of service",
    "in service", "responding", "arrived", "complete", "disregard",
    "repeat", "say again", "go ahead", "stand by", "standing by",
    "thanks for watching", "thank you", "you're welcome",
    "that standby request", "the standby request",
}

# NATO phonetic alphabet for decoding
NATO_PHONETIC = {
    "alfa": "A", "alpha": "A", "bravo": "B", "charlie": "C", "delta": "D",
    "echo": "E", "foxtrot": "F", "golf": "G", "hotel": "H", "india": "I",
    "juliet": "J", "juliett": "J", "kilo": "K", "lima": "L", "mike": "M",
    "november": "N", "oscar": "O", "papa": "P", "quebec": "Q", "romeo": "R",
    "sierra": "S", "tango": "T", "uniform": "U", "victor": "V", "whiskey": "W",
    "x-ray": "X", "xray": "X", "yankee": "Y", "zulu": "Z",
}

# Detect phonetic spelled sequences (3+ consecutive NATO words)
NATO_SEQUENCE_PATTERN = re.compile(
    r'\b(' + '|'.join(NATO_PHONETIC.keys()) + r')(?:\s+(' + '|'.join(NATO_PHONETIC.keys()) + r')){2,}\b',
    re.IGNORECASE
)


class CallsignExtractor:
    """Extracts callsigns from transcribed text using domain-aware patterns."""

    def __init__(self):
        # Custom patterns can be added at runtime
        self._custom_patterns = []

    def add_pattern(self, name: str, pattern: re.Pattern, domain: str = "custom"):
        """Add a custom regex pattern for callsign extraction."""
        self._custom_patterns.append({"name": name, "pattern": pattern, "domain": domain})

    def extract(self, text: str, domain: str = None) -> List[Dict]:
        """
        Extract callsigns from text. Returns list of dicts:
        [{"callsign": "MM0RFN", "domain": "amateur", "raw_match": "MM0RFN", "type": "amateur_callsign"}]
        """
        results = []
        seen = set()

        def _add(callsign: str, domain: str, raw_match: str, match_type: str):
            cs_upper = callsign.upper().strip()
            if cs_upper and cs_upper not in seen and len(cs_upper) >= 2:
                # Filter out common radio phrases
                if cs_upper.lower() in FALSE_POSITIVE_PHRASES:
                    return
                seen.add(cs_upper)
                results.append({
                    "callsign": cs_upper,
                    "domain": domain,
                    "raw_match": raw_match,
                    "type": match_type
                })

        # Amateur callsigns (always check — they're distinctive enough)
        if domain in (None, "amateur", "raynet"):
            for m in AMATEUR_PATTERN.finditer(text):
                cs = m.group(1) or m.group(2)
                if cs and self._is_valid_amateur(cs):
                    _add(cs, "amateur", m.group(), "amateur_callsign")

        # Aviation callsigns
        if domain in (None, "aviation"):
            for m in AVIATION_CALLSIGN_PATTERN.finditer(text):
                airline = m.group(1).lower()
                flight = m.group(2)
                icao = AVIATION_AIRLINES.get(airline, airline.upper()[:3])
                _add(f"{icao}{flight}", "aviation", m.group(), "airline_callsign")

            for m in ICAO_PATTERN.finditer(text):
                cs = m.group(1)
                if self._is_valid_icao(cs):
                    _add(cs, "aviation", m.group(), "icao_callsign")

        # Emergency service units
        if domain in (None, "scanner", "emergency"):
            for pat in UNIT_PATTERNS:
                for m in pat.finditer(text):
                    unit_type = m.group(1).title()
                    unit_num = m.group(2)
                    _add(f"{unit_type} {unit_num}", "emergency", m.group(), "unit_designator")

        # Maritime
        if domain in (None, "maritime"):
            for m in MARITIME_VESSEL_PATTERN.finditer(text):
                vessel = m.group(1).strip()
                if len(vessel) > 2 and vessel.lower() not in FALSE_POSITIVE_PHRASES:
                    _add(vessel, "maritime", m.group(), "vessel_name")

            for m in MMSI_PATTERN.finditer(text):
                mmsi = m.group(1)
                if self._is_valid_mmsi(mmsi):
                    _add(mmsi, "maritime", m.group(), "mmsi")

        # NATO phonetic sequences → decoded callsign
        self._extract_nato_sequences(text, results, seen, _add)

        # Custom patterns
        for cp in self._custom_patterns:
            if domain and cp["domain"] != domain:
                continue
            for m in cp["pattern"].finditer(text):
                _add(m.group(), cp["domain"], m.group(), cp["name"])

        return results

    def _extract_nato_sequences(self, text, results, seen, add_fn):
        """Find NATO phonetic spelled sequences and decode them."""
        words = text.lower().split()
        i = 0
        while i < len(words):
            word = words[i].strip(".,!?;:")
            if word in NATO_PHONETIC:
                # Start collecting a sequence
                seq = [word]
                j = i + 1
                while j < len(words):
                    w = words[j].strip(".,!?;:")
                    if w in NATO_PHONETIC:
                        seq.append(w)
                        j += 1
                    else:
                        # Check if it's a digit (e.g. "alpha bravo 4 charlie")
                        if w.isdigit() and len(w) == 1:
                            seq.append(w)
                            j += 1
                        else:
                            break
                if len(seq) >= 3:
                    decoded = ""
                    for s in seq:
                        if s.isdigit():
                            decoded += s
                        else:
                            decoded += NATO_PHONETIC.get(s, "?")
                    raw = " ".join(seq)
                    add_fn(decoded, "phonetic", raw, "nato_phonetic")
                    i = j
                    continue
            i += 1

    def _is_valid_amateur(self, callsign: str) -> bool:
        """Basic validation for amateur radio callsigns."""
        cs = callsign.upper()
        # Must have at least one digit and at least one letter
        has_digit = any(c.isdigit() for c in cs)
        has_letter = any(c.isalpha() for c in cs)
        if not (has_digit and has_letter):
            return False
        # Filter out common false positives
        false_pos = {"THE", "AND", "FOR", "BUT", "NOT", "YOU", "ALL", "CAN", "HER",
                     "WAS", "ONE", "OUR", "OUT", "ARE", "HAS", "HIS", "HOW", "ITS"}
        if cs in false_pos:
            return False
        # Must be 4-7 chars
        if len(cs) < 4 or len(cs) > 7:
            return False
        return True

    def _is_valid_icao(self, callsign: str) -> bool:
        """Validate ICAO-format callsigns."""
        cs = callsign.upper()
        if len(cs) < 4 or len(cs) > 7:
            return False
        # First 3 must be letters
        if not cs[:3].isalpha():
            return False
        # Must have at least one digit
        if not any(c.isdigit() for c in cs[3:]):
            return False
        # Filter common words that look like ICAO
        false_pos = {"THE1", "AND1", "FOR1", "NOT1"}
        if cs in false_pos:
            return False
        return True

    def _is_valid_mmsi(self, mmsi: str) -> bool:
        """Validate MMSI numbers."""
        if len(mmsi) != 9:
            return False
        # MID starts with 2-7 for normal vessels
        first = int(mmsi[0])
        return 2 <= first <= 7


class CallsignRegistry:
    """In-memory + database-backed registry of detected callsigns."""

    def __init__(self, db=None):
        self.db = db
        self._cache = {}  # callsign -> {domain, first_seen, last_seen, count, channel_id}
        self._extractor = CallsignExtractor()

    def process_transcript(self, text: str, channel_id: str = None,
                           domain: str = None) -> List[Dict]:
        """Extract callsigns from text and register them. Returns list of detected callsigns."""
        detected = self._extractor.extract(text, domain)
        for cs in detected:
            self.register(cs["callsign"], cs["domain"], channel_id)
        return detected

    def register(self, callsign: str, domain: str = "general",
                 channel_id: str = None, metadata: dict = None):
        """Register or update a callsign."""
        cs = callsign.upper()
        now = time.time()

        if cs in self._cache:
            self._cache[cs]["last_seen"] = now
            self._cache[cs]["count"] += 1
            if channel_id:
                self._cache[cs]["last_channel"] = channel_id
        else:
            self._cache[cs] = {
                "callsign": cs,
                "domain": domain,
                "first_seen": now,
                "last_seen": now,
                "count": 1,
                "last_channel": channel_id,
            }

        # Persist to database
        if self.db:
            self.db.upsert_callsign(cs, domain, channel_id, metadata)

    def lookup(self, callsign: str) -> Optional[Dict]:
        """Look up a callsign."""
        cs = callsign.upper()
        if cs in self._cache:
            return self._cache[cs]
        if self.db:
            return self.db.get_callsign(cs)
        return None

    def get_active(self, since_minutes: int = 30) -> List[Dict]:
        """Get recently heard callsigns."""
        cutoff = time.time() - (since_minutes * 60)
        active = [v for v in self._cache.values() if v["last_seen"] >= cutoff]
        active.sort(key=lambda x: x["last_seen"], reverse=True)
        return active

    def get_all(self) -> List[Dict]:
        """Get all known callsigns."""
        if self.db:
            return self.db.get_callsigns()
        return list(self._cache.values())
