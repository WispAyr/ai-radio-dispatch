"""SQLite persistence layer for the Dispatch Console."""

import os
import sqlite3
import json
import time
import threading
from contextlib import contextmanager

DB_PATH = os.path.join(os.path.dirname(__file__), "dispatch.db")


class DispatchDB:
    """Thread-safe SQLite database for transcripts, callsigns, bookmarks, sessions, and alerts."""

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._local = threading.local()  # thread-local connections
        self._init_schema()

    def _get_conn(self) -> sqlite3.Connection:
        """Get a thread-local database connection."""
        if not hasattr(self._local, 'conn') or self._local.conn is None:
            self._local.conn = sqlite3.connect(self.db_path)
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA foreign_keys=ON")
        return self._local.conn

    @contextmanager
    def _cursor(self):
        conn = self._get_conn()
        cur = conn.cursor()
        try:
            yield cur
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    def _init_schema(self):
        """Create tables if they don't exist."""
        conn = self._get_conn()
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                started_at  REAL NOT NULL,
                ended_at    REAL
            );

            CREATE TABLE IF NOT EXISTS transcripts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                channel_id  TEXT NOT NULL,
                speaker     TEXT NOT NULL,
                text        TEXT NOT NULL,
                timestamp   REAL NOT NULL,
                audio_file  TEXT,
                duration    REAL DEFAULT 0,
                waveform    TEXT,
                session_id  INTEGER,
                flagged     INTEGER DEFAULT 0,
                notes       TEXT,
                callsigns   TEXT,
                classification TEXT,
                FOREIGN KEY (session_id) REFERENCES sessions(id)
            );

            CREATE TABLE IF NOT EXISTS callsigns (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                callsign    TEXT NOT NULL,
                domain      TEXT DEFAULT 'general',
                first_seen  REAL NOT NULL,
                last_seen   REAL NOT NULL,
                last_channel TEXT,
                count       INTEGER DEFAULT 1,
                metadata    TEXT
            );

            CREATE TABLE IF NOT EXISTS bookmarks (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                transcript_id   INTEGER NOT NULL,
                label           TEXT DEFAULT '',
                created_at      REAL NOT NULL,
                FOREIGN KEY (transcript_id) REFERENCES transcripts(id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS alerts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                name        TEXT NOT NULL,
                pattern     TEXT NOT NULL,
                alert_type  TEXT NOT NULL DEFAULT 'keyword',
                is_regex    INTEGER DEFAULT 0,
                enabled     INTEGER DEFAULT 1,
                created_at  REAL NOT NULL
            );

            -- Full-text search on transcripts
            CREATE VIRTUAL TABLE IF NOT EXISTS transcripts_fts USING fts5(
                text, speaker, channel_id,
                content='transcripts',
                content_rowid='id'
            );

            -- Triggers to keep FTS index in sync
            CREATE TRIGGER IF NOT EXISTS transcripts_ai AFTER INSERT ON transcripts BEGIN
                INSERT INTO transcripts_fts(rowid, text, speaker, channel_id)
                VALUES (new.id, new.text, new.speaker, new.channel_id);
            END;

            CREATE TRIGGER IF NOT EXISTS transcripts_ad AFTER DELETE ON transcripts BEGIN
                INSERT INTO transcripts_fts(transcripts_fts, rowid, text, speaker, channel_id)
                VALUES ('delete', old.id, old.text, old.speaker, old.channel_id);
            END;

            CREATE TRIGGER IF NOT EXISTS transcripts_au AFTER UPDATE ON transcripts BEGIN
                INSERT INTO transcripts_fts(transcripts_fts, rowid, text, speaker, channel_id)
                VALUES ('delete', old.id, old.text, old.speaker, old.channel_id);
                INSERT INTO transcripts_fts(rowid, text, speaker, channel_id)
                VALUES (new.id, new.text, new.speaker, new.channel_id);
            END;

            -- Indexes
            CREATE INDEX IF NOT EXISTS idx_transcripts_channel ON transcripts(channel_id);
            CREATE INDEX IF NOT EXISTS idx_transcripts_timestamp ON transcripts(timestamp);
            CREATE INDEX IF NOT EXISTS idx_transcripts_session ON transcripts(session_id);
            CREATE INDEX IF NOT EXISTS idx_transcripts_flagged ON transcripts(flagged) WHERE flagged = 1;
            CREATE UNIQUE INDEX IF NOT EXISTS idx_callsigns_unique ON callsigns(callsign, domain);
            CREATE INDEX IF NOT EXISTS idx_callsigns_last_seen ON callsigns(last_seen);
        """)
        conn.commit()
        print(f"[DB] Database ready: {self.db_path}")

    # ===== Transcripts =====

    def insert_transcript(self, channel_id: str, speaker: str, text: str,
                          timestamp: float, audio_file: str = None,
                          duration: float = 0, waveform: list = None,
                          session_id: int = None, callsigns: list = None) -> int:
        """Insert a transcript entry. Returns the row ID."""
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO transcripts (channel_id, speaker, text, timestamp,
                    audio_file, duration, waveform, session_id, callsigns)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                channel_id, speaker, text, timestamp,
                audio_file, duration,
                json.dumps(waveform) if waveform else None,
                session_id,
                json.dumps(callsigns) if callsigns else None
            ))
            return cur.lastrowid

    def search_transcripts(self, query: str, channel_id: str = None,
                           speaker: str = None, from_ts: float = None,
                           to_ts: float = None, flagged_only: bool = False,
                           limit: int = 100, offset: int = 0) -> list:
        """Full-text search across transcripts with filters."""
        if query and query.strip():
            # FTS5 search
            sql = """
                SELECT t.*, rank
                FROM transcripts_fts fts
                JOIN transcripts t ON t.id = fts.rowid
                WHERE transcripts_fts MATCH ?
            """
            params = [query]
        else:
            sql = "SELECT t.*, 0 as rank FROM transcripts t WHERE 1=1"
            params = []

        if channel_id:
            sql += " AND t.channel_id = ?"
            params.append(channel_id)
        if speaker:
            sql += " AND t.speaker = ?"
            params.append(speaker)
        if from_ts:
            sql += " AND t.timestamp >= ?"
            params.append(from_ts)
        if to_ts:
            sql += " AND t.timestamp <= ?"
            params.append(to_ts)
        if flagged_only:
            sql += " AND t.flagged = 1"

        sql += " ORDER BY t.timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_dict(row) for row in cur.fetchall()]

    def get_transcripts(self, channel_id: str = None, session_id: int = None,
                        limit: int = 500, offset: int = 0) -> list:
        """Get recent transcripts, optionally filtered."""
        sql = "SELECT * FROM transcripts WHERE 1=1"
        params = []
        if channel_id:
            sql += " AND channel_id = ?"
            params.append(channel_id)
        if session_id:
            sql += " AND session_id = ?"
            params.append(session_id)
        sql += " ORDER BY timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])

        with self._cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_dict(row) for row in cur.fetchall()]

    def flag_transcript(self, transcript_id: int, flagged: bool = True, notes: str = None):
        """Flag or unflag a transcript entry."""
        with self._cursor() as cur:
            if notes is not None:
                cur.execute("UPDATE transcripts SET flagged=?, notes=? WHERE id=?",
                            (1 if flagged else 0, notes, transcript_id))
            else:
                cur.execute("UPDATE transcripts SET flagged=? WHERE id=?",
                            (1 if flagged else 0, transcript_id))

    def get_transcript_by_id(self, transcript_id: int) -> dict:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM transcripts WHERE id=?", (transcript_id,))
            row = cur.fetchone()
            return self._row_to_dict(row) if row else None

    # ===== Callsigns =====

    def upsert_callsign(self, callsign: str, domain: str = "general",
                        channel_id: str = None, metadata: dict = None):
        """Insert or update a callsign in the registry."""
        now = time.time()
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO callsigns (callsign, domain, first_seen, last_seen, last_channel, count, metadata)
                VALUES (?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(callsign, domain) DO UPDATE SET
                    last_seen = ?,
                    last_channel = COALESCE(?, last_channel),
                    count = count + 1,
                    metadata = COALESCE(?, metadata)
            """, (
                callsign.upper(), domain, now, now, channel_id,
                json.dumps(metadata) if metadata else None,
                now, channel_id,
                json.dumps(metadata) if metadata else None
            ))

    def get_callsigns(self, domain: str = None, since_minutes: int = None,
                      limit: int = 200) -> list:
        """Get callsigns from registry."""
        sql = "SELECT * FROM callsigns WHERE 1=1"
        params = []
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        if since_minutes:
            cutoff = time.time() - (since_minutes * 60)
            sql += " AND last_seen >= ?"
            params.append(cutoff)
        sql += " ORDER BY last_seen DESC LIMIT ?"
        params.append(limit)

        with self._cursor() as cur:
            cur.execute(sql, params)
            return [self._row_to_dict(row) for row in cur.fetchall()]

    def get_callsign(self, callsign: str, domain: str = None) -> dict:
        """Look up a specific callsign."""
        sql = "SELECT * FROM callsigns WHERE callsign = ?"
        params = [callsign.upper()]
        if domain:
            sql += " AND domain = ?"
            params.append(domain)
        with self._cursor() as cur:
            cur.execute(sql, params)
            row = cur.fetchone()
            return self._row_to_dict(row) if row else None

    # ===== Bookmarks =====

    def add_bookmark(self, transcript_id: int, label: str = "") -> int:
        with self._cursor() as cur:
            cur.execute("INSERT INTO bookmarks (transcript_id, label, created_at) VALUES (?, ?, ?)",
                        (transcript_id, label, time.time()))
            return cur.lastrowid

    def remove_bookmark(self, bookmark_id: int):
        with self._cursor() as cur:
            cur.execute("DELETE FROM bookmarks WHERE id=?", (bookmark_id,))

    def get_bookmarks(self, limit: int = 100) -> list:
        with self._cursor() as cur:
            cur.execute("""
                SELECT b.*, t.text, t.speaker, t.channel_id, t.timestamp as transcript_ts
                FROM bookmarks b
                JOIN transcripts t ON t.id = b.transcript_id
                ORDER BY b.created_at DESC LIMIT ?
            """, (limit,))
            return [self._row_to_dict(row) for row in cur.fetchall()]

    # ===== Sessions =====

    def start_session(self, name: str) -> int:
        with self._cursor() as cur:
            cur.execute("INSERT INTO sessions (name, started_at) VALUES (?, ?)",
                        (name, time.time()))
            return cur.lastrowid

    def end_session(self, session_id: int):
        with self._cursor() as cur:
            cur.execute("UPDATE sessions SET ended_at=? WHERE id=?",
                        (time.time(), session_id))

    def get_sessions(self, limit: int = 50) -> list:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM sessions ORDER BY started_at DESC LIMIT ?", (limit,))
            return [self._row_to_dict(row) for row in cur.fetchall()]

    # ===== Alerts =====

    def add_alert(self, name: str, pattern: str, alert_type: str = "keyword",
                  is_regex: bool = False) -> int:
        with self._cursor() as cur:
            cur.execute("""
                INSERT INTO alerts (name, pattern, alert_type, is_regex, enabled, created_at)
                VALUES (?, ?, ?, ?, 1, ?)
            """, (name, pattern, alert_type, 1 if is_regex else 0, time.time()))
            return cur.lastrowid

    def remove_alert(self, alert_id: int):
        with self._cursor() as cur:
            cur.execute("DELETE FROM alerts WHERE id=?", (alert_id,))

    def toggle_alert(self, alert_id: int) -> bool:
        with self._cursor() as cur:
            cur.execute("SELECT enabled FROM alerts WHERE id=?", (alert_id,))
            row = cur.fetchone()
            if not row:
                return False
            new_val = 0 if row["enabled"] else 1
            cur.execute("UPDATE alerts SET enabled=? WHERE id=?", (new_val, alert_id))
            return bool(new_val)

    def get_alerts(self) -> list:
        with self._cursor() as cur:
            cur.execute("SELECT * FROM alerts ORDER BY created_at DESC")
            return [self._row_to_dict(row) for row in cur.fetchall()]

    # ===== Export =====

    def export_transcripts(self, format: str = "json", channel_id: str = None,
                           from_ts: float = None, to_ts: float = None) -> str:
        """Export transcripts in the specified format."""
        sql = "SELECT * FROM transcripts WHERE 1=1"
        params = []
        if channel_id:
            sql += " AND channel_id = ?"
            params.append(channel_id)
        if from_ts:
            sql += " AND timestamp >= ?"
            params.append(from_ts)
        if to_ts:
            sql += " AND timestamp <= ?"
            params.append(to_ts)
        sql += " ORDER BY timestamp ASC"

        with self._cursor() as cur:
            cur.execute(sql, params)
            rows = [self._row_to_dict(row) for row in cur.fetchall()]

        if format == "json":
            return json.dumps(rows, indent=2)
        elif format == "csv":
            import csv
            import io
            output = io.StringIO()
            if rows:
                writer = csv.DictWriter(output, fieldnames=rows[0].keys())
                writer.writeheader()
                writer.writerows(rows)
            return output.getvalue()
        elif format == "txt":
            lines = []
            for r in rows:
                ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["timestamp"]))
                lines.append(f"[{ts}] [{r.get('channel_id','')}] {r['speaker']}: {r['text']}")
            return "\n".join(lines)
        else:
            return json.dumps(rows, indent=2)

    # ===== Stats =====

    def get_stats(self) -> dict:
        """Get database statistics."""
        with self._cursor() as cur:
            cur.execute("SELECT COUNT(*) as c FROM transcripts")
            total = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) as c FROM callsigns")
            callsign_count = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(*) as c FROM bookmarks")
            bookmark_count = cur.fetchone()["c"]
            cur.execute("SELECT COUNT(DISTINCT speaker) as c FROM transcripts")
            speaker_count = cur.fetchone()["c"]
            return {
                "total_transcripts": total,
                "total_callsigns": callsign_count,
                "total_bookmarks": bookmark_count,
                "total_speakers": speaker_count,
            }

    # ===== Helpers =====

    def _row_to_dict(self, row) -> dict:
        if row is None:
            return None
        d = dict(row)
        # Parse JSON fields
        for field in ("waveform", "callsigns", "metadata"):
            if field in d and d[field] and isinstance(d[field], str):
                try:
                    d[field] = json.loads(d[field])
                except (json.JSONDecodeError, TypeError):
                    pass
        return d
