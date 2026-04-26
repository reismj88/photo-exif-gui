from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import exifread

SUPPORTED_EXTENSIONS = {".nef", ".jpg", ".jpeg", ".tiff", ".tif", ".cr2", ".arw", ".dng"}

_SCANNER_STRINGS = frozenset({
    "noritsu", "koki", "epson", "plustek", "coolscan", "canoscan",
    "vuescan", "silverfast", "opticfilm", "fujifilm frontier", "scan",
})

EXPOSURE_MODES = {0: "Auto", 1: "Manual", 2: "Auto Bracket"}
METERING_MODES = {
    0: "Unknown", 1: "Average", 2: "Center-weighted", 3: "Spot",
    4: "Multi-spot", 5: "Multi-segment", 6: "Partial", 255: "Other",
}
WHITE_BALANCE = {0: "Auto", 1: "Manual"}


@dataclass
class PhotoRecord:
    path: Path
    camera_make: Optional[str] = None
    camera_model: Optional[str] = None
    lens_model: Optional[str] = None
    shutter_speed: Optional[str] = None
    aperture: Optional[str] = None
    iso: Optional[str] = None
    exposure_mode: Optional[str] = None
    exposure_compensation: Optional[str] = None
    metering_mode: Optional[str] = None
    focal_length: Optional[str] = None
    focal_length_35mm: Optional[str] = None
    flash_fired: Optional[bool] = None
    focus_mode: Optional[str] = None
    datetime_original: Optional[str] = None
    gps_lat: Optional[float] = None
    gps_lon: Optional[float] = None
    width: Optional[int] = None
    height: Optional[int] = None
    white_balance: Optional[str] = None
    software: Optional[str] = None
    missing_exif: bool = False
    xmp_exists: bool = False
    sharpness_score: Optional[int] = None
    sharpness_type: Optional[str] = None
    sharpness_has_people: Optional[bool] = None

    @property
    def camera(self) -> str:
        parts = [p for p in (self.camera_make, self.camera_model) if p]
        return " ".join(parts) if parts else "Unknown"

    @property
    def gps_str(self) -> str:
        if self.gps_lat is not None and self.gps_lon is not None:
            return f"{self.gps_lat:.5f}, {self.gps_lon:.5f}"
        return ""

    @property
    def dimensions_str(self) -> str:
        if self.width and self.height:
            return f"{self.width}×{self.height}"
        return ""


def _rational_to_float(tag) -> Optional[float]:
    try:
        v = tag.values[0]
        if hasattr(v, "num") and hasattr(v, "den") and v.den != 0:
            return v.num / v.den
        return float(str(v))
    except Exception:
        return None


def _tag_str(tags: dict, key: str) -> Optional[str]:
    tag = tags.get(key)
    return str(tag).strip() if tag else None


def _tag_int(tags: dict, key: str) -> Optional[int]:
    tag = tags.get(key)
    if tag is None:
        return None
    try:
        return int(str(tag))
    except (ValueError, TypeError):
        return None


def _format_shutter(tag) -> Optional[str]:
    v = _rational_to_float(tag)
    if v is None:
        return None
    if v >= 1:
        return f"{v:.0f}s" if v == int(v) else f"{v:.1f}s"
    denom = round(1 / v)
    return f"1/{denom}s"


def _format_aperture(tag) -> Optional[str]:
    v = _rational_to_float(tag)
    return f"f/{v:.1f}".rstrip("0").rstrip(".") if v is not None else None


def _format_focal_length(tag) -> Optional[str]:
    v = _rational_to_float(tag)
    if v is None:
        return None
    return f"{round(v)}mm"


def _parse_gps_coord(values, ref: str) -> Optional[float]:
    try:
        def rat(v):
            return v.num / v.den if hasattr(v, "num") and v.den != 0 else float(str(v))
        deg = rat(values[0])
        mn = rat(values[1])
        sec = rat(values[2])
        decimal = deg + mn / 60 + sec / 3600
        if ref in ("S", "W"):
            decimal = -decimal
        return decimal
    except Exception:
        return None


def extract_exif(path: Path) -> PhotoRecord:
    record = PhotoRecord(path=path, xmp_exists=path.with_suffix(".xmp").exists())
    try:
        with path.open("rb") as f:
            tags = exifread.process_file(f, details=False)

        if not tags:
            record.missing_exif = True
            return record

        record.camera_make = _tag_str(tags, "Image Make")
        record.camera_model = _tag_str(tags, "Image Model")
        record.software = _tag_str(tags, "Image Software")
        record.lens_model = (
            _tag_str(tags, "EXIF LensModel")
            or _tag_str(tags, "MakerNote LensModel")
        )
        record.datetime_original = _tag_str(tags, "EXIF DateTimeOriginal")
        record.iso = _tag_str(tags, "EXIF ISOSpeedRatings")

        if t := tags.get("EXIF ExposureTime"):
            record.shutter_speed = _format_shutter(t)
        if t := tags.get("EXIF FNumber"):
            record.aperture = _format_aperture(t)
        if t := tags.get("EXIF FocalLength"):
            record.focal_length = _format_focal_length(t)
        if t := tags.get("EXIF FocalLengthIn35mmFilm"):
            v = _tag_int(tags, "EXIF FocalLengthIn35mmFilm")
            record.focal_length_35mm = f"{v}mm" if v else None

        exp = _tag_int(tags, "EXIF ExposureMode")
        if exp is not None:
            record.exposure_mode = EXPOSURE_MODES.get(exp, str(exp))

        if t := tags.get("EXIF ExposureBiasValue"):
            v = _rational_to_float(t)
            if v is not None:
                record.exposure_compensation = f"{v:+.1f} EV"

        meter = _tag_int(tags, "EXIF MeteringMode")
        if meter is not None:
            record.metering_mode = METERING_MODES.get(meter, str(meter))

        wb = _tag_int(tags, "EXIF WhiteBalance")
        if wb is not None:
            record.white_balance = WHITE_BALANCE.get(wb, str(wb))

        if t := tags.get("EXIF Flash"):
            try:
                record.flash_fired = bool(int(str(t)) & 1)
            except (ValueError, TypeError):
                pass

        record.focus_mode = (
            _tag_str(tags, "MakerNote FocusMode")
            or _tag_str(tags, "MakerNote AFMode")
        )

        w = (
            _tag_int(tags, "EXIF ExifImageWidth")
            or _tag_int(tags, "Image ImageWidth")
        )
        h = (
            _tag_int(tags, "EXIF ExifImageLength")
            or _tag_int(tags, "Image ImageLength")
        )
        record.width, record.height = w, h

        lat_tag = tags.get("GPS GPSLatitude")
        lat_ref = _tag_str(tags, "GPS GPSLatitudeRef")
        lon_tag = tags.get("GPS GPSLongitude")
        lon_ref = _tag_str(tags, "GPS GPSLongitudeRef")
        if lat_tag and lat_ref and lon_tag and lon_ref:
            record.gps_lat = _parse_gps_coord(lat_tag.values, lat_ref)
            record.gps_lon = _parse_gps_coord(lon_tag.values, lon_ref)

    except Exception:
        record.missing_exif = True

    return record


def is_film_scan(record: PhotoRecord) -> bool:
    """Return True if the record looks like a film scan rather than a digital capture."""
    text = " ".join(
        p for p in (record.camera_make, record.camera_model, record.software) if p
    ).lower()
    if any(s in text for s in _SCANNER_STRINGS):
        return True
    missing = sum(
        1 for v in (record.aperture, record.shutter_speed, record.lens_model, record.focal_length)
        if not v
    )
    return missing >= 3


def scan_folder(folder: Path) -> list[PhotoRecord]:
    records = []
    for path in sorted(folder.rglob("*")):
        if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
            records.append(extract_exif(path))
    return records
