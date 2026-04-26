import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class CacheEntry:
    file_path: str
    file_mtime: float
    numeric_score: Optional[int]
    star_rating: Optional[int]
    sharpness_type: Optional[str]
    has_people: Optional[bool]
    analyzed_at: str


class SharpnessCache:
    def __init__(self, folder: Path) -> None:
        db = folder / ".photo_exif_cache.db"
        self._conn = sqlite3.connect(str(db), check_same_thread=False)
        self._conn.execute("""
            CREATE TABLE IF NOT EXISTS sharpness_cache (
                file_path      TEXT    NOT NULL PRIMARY KEY,
                file_mtime     REAL    NOT NULL,
                numeric_score  INTEGER,
                star_rating    INTEGER,
                sharpness_type TEXT,
                has_people     INTEGER,
                analyzed_at    TEXT    NOT NULL
            )
        """)
        self._conn.commit()

    def get(self, file_path: str, mtime: float) -> Optional[CacheEntry]:
        row = self._conn.execute(
            "SELECT file_path, file_mtime, numeric_score, star_rating, "
            "sharpness_type, has_people, analyzed_at "
            "FROM sharpness_cache WHERE file_path = ? AND file_mtime = ?",
            (file_path, mtime),
        ).fetchone()
        if row is None:
            return None
        return CacheEntry(
            file_path=row[0],
            file_mtime=row[1],
            numeric_score=row[2],
            star_rating=row[3],
            sharpness_type=row[4],
            has_people=bool(row[5]) if row[5] is not None else None,
            analyzed_at=row[6],
        )

    def put(self, entry: CacheEntry) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO sharpness_cache "
            "(file_path, file_mtime, numeric_score, star_rating, "
            "sharpness_type, has_people, analyzed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                entry.file_path,
                entry.file_mtime,
                entry.numeric_score,
                entry.star_rating,
                entry.sharpness_type,
                int(entry.has_people) if entry.has_people is not None else None,
                entry.analyzed_at,
            ),
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()
