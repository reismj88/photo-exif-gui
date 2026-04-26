# photo-exif-gui — Dev Log

---

## Session 1 — 2026-04-25

### Project context
Built as Phase 2 of photo-exif-scanner (CLI). Goal: full PyQt6 desktop GUI with EXIF table, Plotly charts, and XMP sidecar writing.

---

### Environment setup

**Python version:** 3.14.4 via Homebrew (`/opt/homebrew/bin/python3.14`)

**Why a dedicated venv:**
- The system has Anaconda Python (`/opt/homebrew/bin/anaconda3`) which is the default `python3` in the terminal
- VSCode was configured to use the system Python (`/usr/bin/python3`) via `"python-envs.defaultEnvManager": "ms-python.python:system"`
- Neither had the required packages
- Creating `.venv` with `python3.14 -m venv .venv` and installing there gives a self-contained, reproducible environment regardless of which Python is active in the shell

**Packages installed (working versions):**
```
PyQt6               6.11.0
PyQt6-Qt6           6.11.0
PyQt6_sip           13.11.1
PyQt6-WebEngine     6.11.0
PyQt6-WebEngine-Qt6 6.11.0
plotly              6.7.0
ExifRead            3.5.1
```

**Run command:** `.venv/bin/python main.py`

---

### Architecture decisions

**Module split:**
- `scanner.py` — all file I/O and EXIF extraction, no UI imports
- `xmp_writer.py` — XMP read/write, no UI imports, independently testable
- `charts.py` — Plotly HTML generation, takes plain `list[PhotoRecord]`, no UI imports
- `main.py` — all PyQt6 code, imports the three above

This keeps the business logic portable (can be reused by a CLI, a web app, or a different GUI framework without changes).

**Background scanning:**
- `ScanWorker(QThread)` emits `progress(int, int)` and `finished(list[PhotoRecord])`
- Never update widgets from the worker thread — only emit signals, let the main thread update UI in slots

**QWebEngineView + Plotly:**
- Charts are generated as full HTML strings via `fig.to_html(include_plotlyjs='cdn')`
- Loaded with `QWebEngineView.setHtml(html_string)`
- **Requires internet** for the Plotly CDN. If offline charts are needed, change `include_plotlyjs='cdn'` to `include_plotlyjs=True` in `charts.py` (embeds ~3MB of JS per chart)

---

### Bugs & fixes

**Bug 1 — QWebEngineView import order crash**
```
ImportError: QtWebEngineWidgets must be imported or
Qt.AA_ShareOpenGLContexts must be set before a QCoreApplication instance is created
```
- **Cause:** `QWebEngineView` must be imported (or `Qt.AA_ShareOpenGLContexts` set) *before* `QApplication` is instantiated
- **Fix in main.py:** `from PyQt6.QtWebEngineWidgets import QWebEngineView` is at the top of the file (module-level import), before `QApplication(sys.argv)` is called inside `main()`. The correct execution order is: module imports run → `main()` called → `QApplication` created. This is fine.
- **Trap to avoid:** If you ever create `QApplication` before importing `main.py` (e.g. in a test harness), you will hit this error. The workaround is to add `app.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)` before `QApplication(sys.argv)`.

**Bug 2 — macOS HIToolbox stack dumps on exit**
- On macOS, when a PyQt6/Qt app exits it prints multi-line stack traces from `HIToolbox`, `AppKit`, `libqcocoa` to stderr
- These are **not Python errors** and do not indicate a crash — they are macOS system-level exit logging
- Filter them from output with: `grep -Ev "HIToolbox|AppKit|libqcocoa|QtCore|QtWidgets|dyld"`

**Bug 3 — `timeout` command not available on macOS**
- `timeout <seconds> <cmd>` is a GNU coreutils command. macOS does not ship it
- Available via `brew install coreutils` as `gtimeout`
- Workaround: use `Popen` with a timer in Python, or just not rely on it in test scripts

---

### EXIF field notes

Field sources (exifread tag names):
| Field | Tag |
|---|---|
| Camera make | `Image Make` |
| Camera model | `Image Model` |
| Lens | `EXIF LensModel` or `MakerNote LensModel` |
| Shutter speed | `EXIF ExposureTime` (rational, e.g. `1/250`) |
| Aperture | `EXIF FNumber` (rational, e.g. `28/10`) |
| ISO | `EXIF ISOSpeedRatings` |
| Exposure mode | `EXIF ExposureMode` (int: 0=Auto, 1=Manual, 2=Bracket) |
| Exposure comp. | `EXIF ExposureBiasValue` (signed rational) |
| Metering mode | `EXIF MeteringMode` (int, see `METERING_MODES` dict) |
| Focal length | `EXIF FocalLength` (rational, convert to mm) |
| Focal length 35mm | `EXIF FocalLengthIn35mmFilm` (int) |
| Flash | `EXIF Flash` (int bitmask — bit 0 = fired) |
| Focus mode | `MakerNote FocusMode` or `MakerNote AFMode` (manufacturer-specific, often absent) |
| White balance | `EXIF WhiteBalance` (0=Auto, 1=Manual) |
| Date/time | `EXIF DateTimeOriginal` |
| GPS | `GPS GPSLatitude` + `GPS GPSLatitudeRef` + lon equivalents |
| Dimensions | `EXIF ExifImageWidth` / `EXIF ExifImageLength` (fallback: `Image ImageWidth` / `Image ImageLength`) |

**exifread call:** `exifread.process_file(f, details=False)` — `details=False` skips compressed thumbnails and speeds up scanning. Adequate for all standard fields. If MakerNote fields are missing on some cameras, try `details=True` (slower).

**GPS conversion:** values are `[degrees, minutes, seconds]` as IFDRational objects. Convert: `deg + min/60 + sec/3600`, negate if ref is `S` or `W`.

---

### XMP sidecar format

Written alongside image as `<filename>.xmp`. Structure:
```xml
<?xml version="1.0" encoding="UTF-8"?>
<x:xmpmeta xmlns:x="adobe:ns:meta/">
  <rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">
    <rdf:Description rdf:about=""
      xmlns:dc="http://purl.org/dc/elements/1.1/"
      xmlns:xmp="http://ns.adobe.com/xap/1.0/">
      <dc:title><rdf:Alt><rdf:li xml:lang="x-default">…</rdf:li></rdf:Alt></dc:title>
      <dc:description>…</dc:description>
      <dc:rights>…</dc:rights>
      <dc:subject><rdf:Bag><rdf:li>keyword</rdf:li>…</rdf:Bag></dc:subject>
      <xmp:Rating>5</xmp:Rating>
    </rdf:Description>
  </rdf:RDF>
</x:xmpmeta>
```
`dc:title`, `dc:description`, `dc:rights` use `rdf:Alt` + `rdf:li xml:lang="x-default"` (Lightroom/Bridge compatible). Keywords use `rdf:Bag`. Rating is written only when > 0.

---

### Known limitations / future work

- Focus mode field is often empty — it lives in manufacturer MakerNote and is not standardized across brands. Nikon Z bodies may expose it with `details=True`.
- Charts require internet (Plotly CDN). Change `include_plotlyjs='cdn'` → `True` in `charts.py` for offline use.
- XMP panel shows blank fields for multi-select; it does not detect or warn about conflicting existing XMP values across the selection.
- Table uses `QTableWidget` (simple). For 10k+ files, switch to `QAbstractTableModel` with lazy loading for better performance.
- No sharpness analysis yet (added Session 3 — see below).

---

## Session 2 — 2026-04-25

### Features added

**Photo preview panel**
- Right-hand panel added to the Table tab via a horizontal `QSplitter` (table | preview) nested inside the existing vertical `QSplitter` (table+preview / XMP panel)
- `_PreviewLabel(QLabel)` subclass overrides `resizeEvent` to re-scale the stored `QPixmap` whenever the splitter is dragged — image always fills available space proportionally
- `PreviewWorker(QThread)` loads images off the main thread on demand (row click, not at scan time):
  - JPG / TIFF: `QImage(str(path))` — Qt handles the decode natively
  - NEF / CR2 / ARW / DNG: `rawpy.imread()` → `extract_thumb()` → JPEG bytes → `QImage.loadFromData()` (or numpy bitmap array → `QImage` for the rare non-JPEG thumb format)
  - Worker emits `QImage` (thread-safe); main thread converts to `QPixmap` (`QPixmap` is **not** thread-safe and must only be created on the main thread)
- Generation counter (`_preview_generation: int`) guards against stale results when the user clicks rapidly — each load increments the counter and slots ignore results whose generation doesn't match

**New dependency:** `rawpy>=0.18.0` (installs numpy as a transitive dep)

---

### Bugs & fixes

**Bug 4 — Hard crash: `QThread::~QThread()` called while thread still running**

```
Exception Type: EXC_CRASH (SIGABRT)
abort() called

Thread 0:  QThread::~QThread() → sipQThread::~sipQThread() → forgetObject → sipWrapper_dealloc → subtype_dealloc
Thread 30 (PreviewWorker): take_gil → _PyThreadState_Attach → sip_api_end_thread (still running)
```

- **Cause:** PyQt6 wraps `QThread` in a sip C++ wrapper. When Python's reference count on a `PreviewWorker` drops to zero, the GC immediately calls the sip destructor → `QThread::~QThread()`. Qt calls `abort()` if the destructor runs while the OS thread is still executing. This was triggered by `self._preview_worker = None` (reassignment on new selection, or in `_clear_preview`) dropping the last reference while rawpy/QImage was still loading.
- **What doesn't work:** `worker.finished.connect(worker.deleteLater)` manages the *C++ object* lifecycle but does not prevent Python's GC from destroying the *Python wrapper* independently and earlier.
- **Fix:** Added `_running_workers: set[PreviewWorker]` on `MainWindow`. Every new worker is added to this set before `.start()`. The worker's `finished` signal (emitted by Qt only *after* `run()` returns, i.e., after the OS thread exits) removes it via `lambda w=worker: self._running_workers.discard(w)`. `_preview_worker` is now just a "current" pointer and can be set to `None` freely — the set is the authoritative live reference.

**Rule derived from this bug:** In PyQt6, whenever a `QThread` subclass may be cancelled/replaced before it finishes, keep it in a collection (set or list) and only remove it in a `finished` slot. Never rely solely on an instance variable whose reassignment might trigger GC.

---

### Architecture notes — preview threading

```
Main thread                     PreviewWorker thread
────────────────────────────    ────────────────────────────────────
_load_preview(path)
  cancel old worker (flag)
  increment generation
  worker = PreviewWorker(path)
  _running_workers.add(worker)
  worker.start()
                                run():
                                  load QImage (rawpy or Qt native)
                                  emit loaded(QImage)   ←── queued signal
                                  [run() returns]
                                  emit finished()       ←── queued signal
_on_preview_loaded(img, gen)
  check gen == current
  QPixmap.fromImage(img)        (QPixmap only on main thread)
  _preview_img.set_source(px)
_running_workers.discard(worker)
  [ref count → 0, safe GC]
```

**Key invariants:**
- `QImage` can be created and passed across threads
- `QPixmap` must only be created on the main thread
- Qt emits `finished` only after `run()` returns — safe to GC the thread object after that point

---

## Session 3 — 2026-04-25

### Features added

**Phase 4 — Sharpness analysis via vision model**

Four new modules, zero new required dependencies (stdlib for Ollama path; `anthropic` is a lazy import for the Claude path):

| File | Role |
|---|---|
| `config.py` | `VISION_BACKEND`, `OLLAMA_MODEL`, `OLLAMA_HOST`, `CLAUDE_MODEL` — swap backends with one line |
| `vision.py` | `analyze_sharpness()`: two-pass (people detection → adaptive sharpness prompt); `score_to_stars()`; Ollama + Claude backends |
| `cache.py` | `SharpnessCache`: SQLite in `.photo_exif_cache.db` at folder root; keyed on `(file_path, file_mtime)` |
| `analysis.py` | `AnalysisWorker(QThread)`: film-scan skip → cache check → JPEG prep → vision call; `get_image_bytes()` for all formats |

**scanner.py additions**
- `software: Optional[str]` field extracted from `Image Software` EXIF tag
- `sharpness_score`, `sharpness_type`, `sharpness_has_people` fields on `PhotoRecord`
- `is_film_scan(record)`: string check (make/model/software vs. `_SCANNER_STRINGS`) + fingerprint fallback (≥3 of aperture/shutter/lens/focal length missing)

**xmp_writer.py additions**
- `sharpness_score: Optional[int]` field on `XMPData`
- Written as `exifgui:SharpnessScore` under custom namespace `https://ns.photo-exif-gui/1.0/`

**main.py additions**
- "Analyze Sharpness" button (enabled after scan) + "Stop Analysis" button (visible only during analysis)
- Second progress bar dedicated to analysis (scan and analysis bars are independent)
- Two new table columns: `Sharpness` (0–100 score) and `Type` (subject / whole-frame / film)
- `_path_to_record: dict[str, PhotoRecord]` — O(1) record lookup by path string, replaces the O(n) `for r in self._records` loop that was in `_write_xmp`
- XMP rating spinner pre-filled from sharpness star rating when no manual rating exists
- Per-record `sharpness_score` included in XMP write
- `_analysis_running_workers: set[AnalysisWorker]` — same crash-safe lifetime pattern as `_running_workers` for `PreviewWorker`

---

### Architecture — analysis pipeline

```
Main thread                          AnalysisWorker thread
─────────────────────────────────    ──────────────────────────────────────────
_start_analysis()
  worker = AnalysisWorker(records, folder)
  _analysis_running_workers.add(worker)
  worker.start()
                                     _process():
                                       for each record:
                                         is_film_scan? → emit(100, 5, "film")
                                         cache hit?    → emit cached values
                                         get_image_bytes()
                                           JPG/TIFF  → QImage → resize → JPEG
                                           RAW       → rawpy thumb → resize → JPEG
                                         vision.analyze_sharpness(bytes, aperture)
                                           pass 1: "people? yes/no"
                                           pass 2: subject or whole-frame prompt
                                           parse int from free-text response
                                         cache.put(entry)
                                         emit image_done(path, score, stars, type, has_people)
                                       emit progress(i, total) each iteration
_on_analysis_image_done()
  update record fields in memory
  scan table rows for path → update Sharpness + Type cells
_analysis_running_workers.discard(worker)  ← on finished signal, safe GC
```

**Image sizing:** all formats are downscaled to `MAX_VISION_PX = 1024` before sending to the model — keeps API calls fast without sacrificing enough detail to assess focus.

**Cancellation:** `AnalysisWorker._cancelled` flag is checked between images. A slow vision API call cannot be interrupted mid-flight; cancellation takes effect at the next iteration boundary.

---

### Design decisions

**Why `QIODeviceBase.OpenModeFlag.WriteOnly` not `QBuffer.OpenModeFlag`**
In PyQt6 6.x, `OpenModeFlag` is defined on `QIODeviceBase`, not on subclasses like `QBuffer`. Using `QBuffer.OpenModeFlag.WriteOnly` may work on some versions but is not the authoritative form. Always import from `QIODeviceBase`.

**Why no `requests` dependency for Ollama**
`urllib.request` (stdlib) handles a single JSON POST with a base64 payload cleanly. Adding `requests` for one endpoint is not worth the extra dep.

**Why `anthropic` is a lazy import**
`import anthropic` inside `_claude_request()` means the app starts and the Ollama path works even if the package isn't installed. The error only surfaces when the Claude backend is actually used, with a clear install instruction in the exception message.

**`_parse_score` robustness**
Vision models return free text even when asked for a number. `re.search(r'\b(\d{1,3})\b', text)` extracts the first 1–3 digit number; fallback is 50 if no number found. The `\b` word boundaries prevent matching partial numbers inside larger strings like "100mm".

**Film scan detection — "scan" substring risk**
`"scan"` in `_SCANNER_STRINGS` is intentionally broad — any device with "scan" in its name is likely a scanner. If this causes false positives on a specific camera model, remove `"scan"` from the set and rely on the fingerprint fallback alone.

---

### Known issues / to-do

**Unverified / needs testing**
- [ ] `QBuffer` + `QIODeviceBase` in a worker thread — assumed safe (QImage is documented thread-safe; QBuffer is a local in-memory device), but not stress-tested under concurrent `PreviewWorker` + `AnalysisWorker` load
- [ ] Ollama response parsing on edge cases: model returns "I'd rate this 85 out of 100" → `_parse_score` returns 85 ✓; model returns "N/A" → fallback 50 ✓; model returns nothing → fallback 50 ✓
- [ ] Claude backend is code-complete but untested (no `anthropic` package in venv)
- [ ] `QBuffer.open()` returns a bool indicating success — currently unchecked; if it fails, `img.save(buf, "JPEG")` silently writes nothing and the API call gets empty bytes

**Known limitations**
- Analysis cancellation is between-image only; a slow Ollama call (~5–30s for llava:7b) blocks until complete
- `_on_analysis_image_done` does an O(n) table row scan to find the updated row — acceptable for <1000 images but degrades at scale; fix: build a `row_by_path` dict after `_populate_table` and invalidate it on sort
- No way to re-analyze a single image; "Analyze Sharpness" always processes all records (cache hits are cheap, but film scans still iterate)
- Sharpness score is not shown in the Summary tab charts
- `is_film_scan` fingerprint check (≥3 missing fields) can misfire on heavily corrupted digital EXIF — no warning is surfaced to the user
- SQLite cache is per-folder; if images are moved, the cache entries become orphaned (mtime key prevents stale hits, but the DB grows unbounded)
