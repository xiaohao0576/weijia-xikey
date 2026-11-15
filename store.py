"""Durable operation journal; never stores credentials or request payloads."""
import sqlite3
from contextlib import contextmanager
from pathlib import Path


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.execute("""
                CREATE TABLE IF NOT EXISTS operations (
                    token TEXT PRIMARY KEY, kind TEXT NOT NULL, employee_id INTEGER NOT NULL,
                    subject TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL,
                    partner_id INTEGER, detail TEXT NOT NULL DEFAULT ''
                )
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, token: str):
        with self.connect() as db:
            row = db.execute("SELECT * FROM operations WHERE token=?", (token,)).fetchone()
            return dict(row) if row else None

    def start(self, token: str, kind: str, employee_id: int, subject: str, created_at: str, partner_id=None):
        with self.connect() as db:
            try:
                db.execute(
                    "INSERT INTO operations (token, kind, employee_id, subject, status, created_at, partner_id) VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                    (token, kind, employee_id, subject, created_at, partner_id),
                )
                return True
            except sqlite3.IntegrityError:
                return False

    def finish(self, token: str, status: str, detail: str = "", partner_id=None):
        with self.connect() as db:
            db.execute(
                "UPDATE operations SET status=?, detail=?, partner_id=COALESCE(?, partner_id) WHERE token=?",
                (status, detail, partner_id, token),
            )
