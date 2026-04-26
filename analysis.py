import re
import time
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QBuffer, QIODeviceBase, QThread, pyqtSignal
from PyQt6.QtGui import QImage

from scanner import PhotoRecord, is_film_scan
from cache import SharpnessCache, CacheEntry
import vision

RAW_EXTENSIONS = {".nef", ".cr2", ".arw", ".dng"}
MAX_VISION_PX = 1024


# ---------------------------------------------------------------------------
# Image preparation

def get_image_bytes(path: Path) -> bytes:
    """Return JPEG bytes ≤ MAX_VISION_PX on each side, suitable for vision APIs."""
    ext = path.suffix.lower()
    if ext in RAW_EXTENSIONS:
        import rawpy
        with rawpy.imread(str(path)) as raw:
            thumb = raw.extract_thumb()
        if thumb.format == rawpy.ThumbFormat.JPEG:
            img = QImage()
            img.loadFromData(bytes(thumb.data))
        else:
            arr = thumb.data
            h, w, ch = arr.shape
            img = QImage(arr.tobytes(), w, h, w * ch, QImage.Format.Format_RGB888)
            img = img.copy()
    else:
        img = QImage(str(path))

    if img.isNull():
        raise ValueError(f"Cannot load image: {path.name}")
    return _to_vision_jpeg(img)


def _to_vision_jpeg(img: QImage) -> bytes:
    if img.width() > MAX_VISION_PX or img.height() > MAX_VISION_PX:
        img = img.scaled(
            MAX_VISION_PX, MAX_VISION_PX,
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
    buf = QBuffer()
    buf.open(QIODeviceBase.OpenModeFlag.WriteOnly)
    img.save(buf, "JPEG", 85)
    data = bytes(buf.data())
    buf.close()
    return data


# ---------------------------------------------------------------------------
# Worker thread

class AnalysisWorker(QThread):
    progress    = pyqtSignal(int, int)              # current, total
    image_done  = pyqtSignal(str, int, int, str, bool)  # path, score, stars, type, has_people
    error       = pyqtSignal(str)

    def __init__(self, records: list[PhotoRecord], cache_folder: Path) -> None:
        super().__init__()
        self._records = records
        self._cache_folder = cache_folder
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        cache = SharpnessCache(self._cache_folder)
        try:
            self._process(cache)
        except (ConnectionError, ImportError) as exc:
            self.error.emit(str(exc))
        finally:
            cache.close()

    def _process(self, cache: SharpnessCache) -> None:
        total = len(self._records)
        for i, record in enumerate(self._records):
            if self._cancelled:
                return
            self.progress.emit(i, total)

            # Film / scanner — skip analysis
            if is_film_scan(record):
                self.image_done.emit(str(record.path), 100, 5, "film", False)
                cache.put(CacheEntry(
                    file_path=str(record.path),
                    file_mtime=record.path.stat().st_mtime,
                    numeric_score=100,
                    star_rating=5,
                    sharpness_type="film",
                    has_people=False,
                    analyzed_at=_iso_now(),
                ))
                continue

            mtime = record.path.stat().st_mtime

            # Cache hit
            cached = cache.get(str(record.path), mtime)
            if cached is not None and cached.numeric_score is not None:
                stars = cached.star_rating or vision.score_to_stars(cached.numeric_score)
                self.image_done.emit(
                    str(record.path),
                    cached.numeric_score,
                    stars,
                    cached.sharpness_type or "whole-frame",
                    bool(cached.has_people),
                )
                continue

            # Load image bytes for vision
            try:
                image_bytes = get_image_bytes(record.path)
            except Exception as exc:
                print(f"[analysis] skip {record.path.name}: {exc}")
                continue

            if self._cancelled:
                return

            # Vision analysis (may raise ConnectionError / ImportError)
            score, sharpness_type, has_people = vision.analyze_sharpness(
                image_bytes, _parse_aperture(record.aperture)
            )
            stars = vision.score_to_stars(score)

            cache.put(CacheEntry(
                file_path=str(record.path),
                file_mtime=mtime,
                numeric_score=score,
                star_rating=stars,
                sharpness_type=sharpness_type,
                has_people=has_people,
                analyzed_at=_iso_now(),
            ))
            self.image_done.emit(str(record.path), score, stars, sharpness_type, has_people)

        self.progress.emit(total, total)


# ---------------------------------------------------------------------------
# Helpers

def _parse_aperture(aperture_str: Optional[str]) -> Optional[float]:
    if not aperture_str:
        return None
    m = re.search(r'[\d.]+', aperture_str)
    return float(m.group()) if m else None


def _iso_now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")
