import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor, QImage, QPixmap
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QPushButton, QFileDialog, QProgressBar,
    QGroupBox, QFormLayout, QLineEdit, QPlainTextEdit,
    QSpinBox, QSplitter, QScrollArea, QGridLayout, QSizePolicy,
    QFrame, QMessageBox,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from scanner import scan_folder, PhotoRecord, SUPPORTED_EXTENSIONS
from xmp_writer import XMPData, write_xmp, read_xmp
from analysis import AnalysisWorker
import charts
import config
import vision

RAW_EXTENSIONS = {".nef", ".cr2", ".arw", ".dng"}

COLUMNS = [
    ("File",              lambda r: r.path.name),
    ("Date/Time",         lambda r: r.datetime_original or ""),
    ("Camera",            lambda r: r.camera),
    ("Lens",              lambda r: r.lens_model or ""),
    ("Focal Length",      lambda r: r.focal_length or ""),
    ("FL (35mm)",         lambda r: r.focal_length_35mm or ""),
    ("Aperture",          lambda r: r.aperture or ""),
    ("Shutter",           lambda r: r.shutter_speed or ""),
    ("ISO",               lambda r: r.iso or ""),
    ("Exp. Mode",         lambda r: r.exposure_mode or ""),
    ("Exp. Comp.",        lambda r: r.exposure_compensation or ""),
    ("Metering",          lambda r: r.metering_mode or ""),
    ("White Balance",     lambda r: r.white_balance or ""),
    ("Flash",             lambda r: ("Yes" if r.flash_fired else "No") if r.flash_fired is not None else ""),
    ("Focus Mode",        lambda r: r.focus_mode or ""),
    ("GPS",               lambda r: r.gps_str),
    ("Dimensions",        lambda r: r.dimensions_str),
    ("Sharpness",         lambda r: str(r.sharpness_score) if r.sharpness_score is not None else ""),
    ("Type",              lambda r: r.sharpness_type or ""),
    ("XMP",               lambda r: "✓ XMP" if r.xmp_exists else "— None"),
]

_COL = {name: i for i, (name, _) in enumerate(COLUMNS)}
SHARPNESS_COL = _COL["Sharpness"]
TYPE_COL      = _COL["Type"]
XMP_COL       = _COL["XMP"]


class _PreviewLabel(QLabel):
    """QLabel that scales its pixmap to fill available space on resize."""

    def __init__(self):
        super().__init__()
        self._source: QPixmap | None = None
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setMinimumSize(100, 100)
        self.setStyleSheet("background: #1a1a1a; border: 1px solid #3a3a3a;")

    def set_source(self, pixmap: QPixmap) -> None:
        self._source = pixmap
        self._refresh()

    def clear_source(self) -> None:
        self._source = None
        self.clear()

    def resizeEvent(self, event) -> None:
        super().resizeEvent(event)
        self._refresh()

    def _refresh(self) -> None:
        if self._source is None:
            return
        scaled = self._source.scaled(
            self.width(), self.height(),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation,
        )
        self.setPixmap(scaled)


class PreviewWorker(QThread):
    loaded = pyqtSignal(QImage)
    failed = pyqtSignal(str)

    def __init__(self, path: Path):
        super().__init__()
        self._path = path
        self._cancelled = False

    def cancel(self) -> None:
        self._cancelled = True

    def run(self) -> None:
        if self._cancelled:
            return
        try:
            ext = self._path.suffix.lower()
            if ext in RAW_EXTENSIONS:
                import rawpy
                with rawpy.imread(str(self._path)) as raw:
                    thumb = raw.extract_thumb()
                if self._cancelled:
                    return
                if thumb.format == rawpy.ThumbFormat.JPEG:
                    img = QImage()
                    img.loadFromData(bytes(thumb.data))
                else:
                    arr = thumb.data
                    h, w, ch = arr.shape
                    img = QImage(
                        arr.tobytes(), w, h, w * ch,
                        QImage.Format.Format_RGB888,
                    )
                    img = img.copy()
            else:
                img = QImage(str(self._path))

            if self._cancelled:
                return
            if img.isNull():
                self.failed.emit(f"Could not load {self._path.name}")
            else:
                self.loaded.emit(img)
        except Exception as e:
            if not self._cancelled:
                self.failed.emit(str(e))


class ScanWorker(QThread):
    progress = pyqtSignal(int, int)
    finished = pyqtSignal(list)

    def __init__(self, folder: Path):
        super().__init__()
        self.folder = folder

    def run(self) -> None:
        files = sorted(
            p for p in self.folder.rglob("*")
            if p.suffix.lower() in SUPPORTED_EXTENSIONS and p.is_file()
        )
        records: list[PhotoRecord] = []
        total = len(files)
        for i, path in enumerate(files):
            from scanner import extract_exif
            records.append(extract_exif(path))
            self.progress.emit(i + 1, total)
        self.finished.emit(records)


class StatCard(QFrame):
    def __init__(self, label: str, value: str = "—"):
        super().__init__()
        self.setFrameShape(QFrame.Shape.Box)
        self.setStyleSheet("QFrame { border: 1px solid #444; border-radius: 4px; padding: 8px; }")
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        self._val = QLabel(value)
        self._val.setStyleSheet("font-size: 22px; font-weight: bold; color: #4a9eff;")
        self._val.setAlignment(Qt.AlignmentFlag.AlignCenter)
        lbl = QLabel(label)
        lbl.setStyleSheet("font-size: 11px; color: #888;")
        lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self._val)
        layout.addWidget(lbl)

    def set_value(self, v: str) -> None:
        self._val.setText(v)


class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("Photo EXIF Scanner")
        self.resize(1280, 820)
        self._records: list[PhotoRecord] = []
        self._path_to_record: dict[str, PhotoRecord] = {}
        self._worker: ScanWorker | None = None
        self._preview_worker: PreviewWorker | None = None
        self._preview_generation: int = 0
        self._running_workers: set[PreviewWorker] = set()
        self._analysis_worker: AnalysisWorker | None = None
        self._analysis_running_workers: set[AnalysisWorker] = set()
        self._setup_ui()

    # ------------------------------------------------------------------ setup

    def _setup_ui(self) -> None:
        root = QWidget()
        self.setCentralWidget(root)
        vbox = QVBoxLayout(root)
        vbox.setContentsMargins(8, 8, 8, 8)
        vbox.setSpacing(6)

        vbox.addLayout(self._build_toolbar())

        self._progress = QProgressBar()
        self._progress.setVisible(False)
        self._progress.setMaximumHeight(6)
        self._progress.setTextVisible(False)
        vbox.addWidget(self._progress)

        self._analysis_progress = QProgressBar()
        self._analysis_progress.setVisible(False)
        self._analysis_progress.setMaximumHeight(6)
        self._analysis_progress.setTextVisible(False)
        vbox.addWidget(self._analysis_progress)

        self._tabs = QTabWidget()
        vbox.addWidget(self._tabs, stretch=1)

        h_splitter = QSplitter(Qt.Orientation.Horizontal)
        h_splitter.addWidget(self._build_table_widget())
        h_splitter.addWidget(self._build_preview_panel())
        h_splitter.setSizes([860, 400])

        v_splitter = QSplitter(Qt.Orientation.Vertical)
        v_splitter.addWidget(h_splitter)
        v_splitter.addWidget(self._build_xmp_panel())
        v_splitter.setSizes([600, 200])
        self._tabs.addTab(v_splitter, "Table")
        self._tabs.addTab(self._build_summary_tab(), "Summary")

    def _build_toolbar(self) -> QHBoxLayout:
        hbox = QHBoxLayout()
        self._path_label = QLabel("No folder selected")
        self._path_label.setStyleSheet("color: #888; font-style: italic;")
        self._path_label.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)

        btn_pick = QPushButton("Choose Folder…")
        btn_pick.clicked.connect(self._pick_folder)

        self._btn_scan = QPushButton("Scan")
        self._btn_scan.setEnabled(False)
        self._btn_scan.setStyleSheet("QPushButton { font-weight: bold; padding: 4px 16px; }")
        self._btn_scan.clicked.connect(self._start_scan)

        self._btn_analyze = QPushButton("Analyze Sharpness")
        self._btn_analyze.setEnabled(False)
        self._btn_analyze.clicked.connect(self._start_analysis)

        self._btn_stop_analysis = QPushButton("Stop Analysis")
        self._btn_stop_analysis.setVisible(False)
        self._btn_stop_analysis.clicked.connect(self._stop_analysis)

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888;")

        hbox.addWidget(btn_pick)
        hbox.addWidget(self._path_label)
        hbox.addWidget(self._status_label)
        hbox.addWidget(self._btn_scan)
        hbox.addWidget(self._btn_analyze)
        hbox.addWidget(self._btn_stop_analysis)
        return hbox

    def _build_table_widget(self) -> QWidget:
        w = QWidget()
        vbox = QVBoxLayout(w)
        vbox.setContentsMargins(0, 0, 0, 0)

        self._table = QTableWidget(0, len(COLUMNS))
        self._table.setHorizontalHeaderLabels([c[0] for c in COLUMNS])
        self._table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self._table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self._table.setAlternatingRowColors(True)
        self._table.verticalHeader().setDefaultSectionSize(24)
        self._table.horizontalHeader().setStretchLastSection(False)
        self._table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Interactive)
        self._table.setSortingEnabled(True)
        self._table.selectionModel().selectionChanged.connect(self._on_selection_changed)

        vbox.addWidget(self._table)
        return w

    def _build_preview_panel(self) -> QWidget:
        panel = QWidget()
        panel.setMinimumWidth(180)
        panel.setMaximumWidth(420)
        vbox = QVBoxLayout(panel)
        vbox.setContentsMargins(4, 4, 4, 4)
        vbox.setSpacing(4)

        self._preview_img = _PreviewLabel()

        self._preview_name = QLabel("Select a single image\nto preview")
        self._preview_name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_name.setStyleSheet("color: #888; font-size: 11px;")
        self._preview_name.setMaximumHeight(36)

        vbox.addWidget(self._preview_img, stretch=1)
        vbox.addWidget(self._preview_name)
        return panel

    def _build_summary_tab(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        container = QWidget()
        scroll.setWidget(container)
        vbox = QVBoxLayout(container)
        vbox.setSpacing(12)

        # Stat cards row
        stats_row = QHBoxLayout()
        self._stat_total = StatCard("Total Images")
        self._stat_missing = StatCard("Missing EXIF")
        self._stat_cameras = StatCard("Unique Cameras")
        self._stat_lenses = StatCard("Unique Lenses")
        for card in (self._stat_total, self._stat_missing, self._stat_cameras, self._stat_lenses):
            stats_row.addWidget(card)
        vbox.addLayout(stats_row)

        # 2×2 chart grid
        grid = QGridLayout()
        grid.setSpacing(8)
        self._chart_fl = QWebEngineView()
        self._chart_ap = QWebEngineView()
        self._chart_iso = QWebEngineView()
        self._chart_cl = QWebEngineView()
        for view in (self._chart_fl, self._chart_ap, self._chart_iso, self._chart_cl):
            view.setMinimumHeight(300)
        grid.addWidget(self._chart_fl, 0, 0)
        grid.addWidget(self._chart_ap, 0, 1)
        grid.addWidget(self._chart_iso, 1, 0)
        grid.addWidget(self._chart_cl, 1, 1)
        vbox.addLayout(grid, stretch=1)

        return scroll

    def _build_xmp_panel(self) -> QGroupBox:
        self._xmp_group = QGroupBox("XMP Metadata  (0 images selected)")
        self._xmp_group.setVisible(False)
        form_layout = QFormLayout(self._xmp_group)
        form_layout.setContentsMargins(8, 8, 8, 8)

        self._xmp_title = QLineEdit()
        self._xmp_caption = QPlainTextEdit()
        self._xmp_caption.setMaximumHeight(56)
        self._xmp_keywords = QLineEdit()
        self._xmp_keywords.setPlaceholderText("comma separated")
        self._xmp_copyright = QLineEdit()
        self._xmp_rating = QSpinBox()
        self._xmp_rating.setRange(0, 5)
        self._xmp_rating.setSpecialValueText("Unrated")

        form_layout.addRow("Title:", self._xmp_title)
        form_layout.addRow("Caption:", self._xmp_caption)
        form_layout.addRow("Keywords:", self._xmp_keywords)
        form_layout.addRow("Copyright:", self._xmp_copyright)
        form_layout.addRow("Rating:", self._xmp_rating)

        btn_write = QPushButton("Write XMP")
        btn_write.setStyleSheet("QPushButton { font-weight: bold; padding: 4px 20px; }")
        btn_write.clicked.connect(self._write_xmp)
        form_layout.addRow("", btn_write)

        return self._xmp_group

    # ---------------------------------------------------------------- actions

    def _pick_folder(self) -> None:
        folder = QFileDialog.getExistingDirectory(self, "Select Folder")
        if folder:
            self._folder = Path(folder)
            self._path_label.setText(str(self._folder))
            self._path_label.setStyleSheet("color: #ccc;")
            self._btn_scan.setEnabled(True)

    def _start_scan(self) -> None:
        if not hasattr(self, "_folder"):
            return
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()
        self._btn_scan.setEnabled(False)
        self._btn_analyze.setEnabled(False)
        self._analysis_progress.setVisible(False)
        self._btn_stop_analysis.setVisible(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_label.setText("Scanning…")
        self._table.setRowCount(0)
        self._records = []
        self._path_to_record = {}

        self._worker = ScanWorker(self._folder)
        self._worker.progress.connect(self._on_progress)
        self._worker.finished.connect(self._on_scan_done)
        self._worker.start()

    def _on_progress(self, current: int, total: int) -> None:
        if total:
            self._progress.setMaximum(total)
            self._progress.setValue(current)
            self._status_label.setText(f"{current} / {total}")

    def _on_scan_done(self, records: list[PhotoRecord]) -> None:
        self._records = records
        self._path_to_record = {str(r.path): r for r in records}
        self._populate_table(records)
        self._update_summary(records)
        self._progress.setVisible(False)
        self._btn_scan.setEnabled(True)
        self._btn_analyze.setEnabled(bool(records))
        missing = sum(1 for r in records if r.missing_exif)
        self._status_label.setText(
            f"{len(records)} images scanned  ·  {missing} missing EXIF"
        )

    def _populate_table(self, records: list[PhotoRecord]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(records))

        for row, record in enumerate(records):
            for col, (_, getter) in enumerate(COLUMNS):
                item = QTableWidgetItem(getter(record))
                item.setData(Qt.ItemDataRole.UserRole, str(record.path))
                if col == XMP_COL and record.xmp_exists:
                    item.setForeground(QColor("#4a9eff"))
                self._table.setItem(row, col, item)

        self._table.resizeColumnsToContents()
        self._table.setSortingEnabled(True)

    def _update_summary(self, records: list[PhotoRecord]) -> None:
        unique_cameras = {r.camera for r in records if not r.missing_exif}
        unique_lenses = {r.lens_model for r in records if r.lens_model}
        self._stat_total.set_value(str(len(records)))
        self._stat_missing.set_value(str(sum(1 for r in records if r.missing_exif)))
        self._stat_cameras.set_value(str(len(unique_cameras)))
        self._stat_lenses.set_value(str(len(unique_lenses)))

        self._chart_fl.setHtml(charts.focal_length_chart(records))
        self._chart_ap.setHtml(charts.aperture_chart(records))
        self._chart_iso.setHtml(charts.iso_chart(records))
        self._chart_cl.setHtml(charts.camera_lens_chart(records))

    def _load_preview(self, path: Path) -> None:
        if self._preview_worker is not None:
            self._preview_worker.cancel()
        self._preview_generation += 1
        gen = self._preview_generation
        self._preview_name.setText(path.name)
        self._preview_img.clear_source()
        self._preview_img.setText("Loading…")
        worker = PreviewWorker(path)
        worker.loaded.connect(lambda img, g=gen: self._on_preview_loaded(img, g))
        worker.failed.connect(lambda msg, g=gen: self._on_preview_failed(msg, g))
        # Keep worker in _running_workers until the thread exits so Python GC
        # never destroys the QThread wrapper while the OS thread is still live.
        worker.finished.connect(lambda w=worker: self._running_workers.discard(w))
        self._running_workers.add(worker)
        self._preview_worker = worker
        worker.start()

    def _clear_preview(self) -> None:
        if self._preview_worker is not None:
            self._preview_worker.cancel()
            self._preview_worker = None
        self._preview_img.clear_source()
        self._preview_img.setText("")
        self._preview_name.setText("Select a single image\nto preview")

    def _on_preview_loaded(self, img: QImage, gen: int) -> None:
        if gen != self._preview_generation:
            return
        self._preview_worker = None
        self._preview_img.set_source(QPixmap.fromImage(img))

    def _on_preview_failed(self, _msg: str, gen: int) -> None:
        if gen != self._preview_generation:
            return
        self._preview_worker = None
        self._preview_img.clear_source()
        self._preview_img.setText("Preview unavailable")

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()

        if len(rows) == 1:
            path_str = self._table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            self._load_preview(Path(path_str))
        else:
            self._clear_preview()

        if not rows:
            self._xmp_group.setVisible(False)
            return

        self._xmp_group.setVisible(True)
        n = len(rows)
        self._xmp_group.setTitle(f"XMP Metadata  ({n} image{'s' if n != 1 else ''} selected)")

        if n == 1:
            path_str = self._table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            record = self._path_to_record.get(path_str)
            existing = read_xmp(Path(path_str))
            if existing:
                self._xmp_title.setText(existing.title)
                self._xmp_caption.setPlainText(existing.caption)
                self._xmp_keywords.setText(", ".join(existing.keywords))
                self._xmp_copyright.setText(existing.copyright)
                rating = existing.rating
                if rating == 0 and record and record.sharpness_score is not None:
                    rating = vision.score_to_stars(record.sharpness_score)
                self._xmp_rating.setValue(rating)
                return

        self._xmp_title.clear()
        self._xmp_caption.clear()
        self._xmp_keywords.clear()
        self._xmp_copyright.clear()
        record = self._path_to_record.get(
            self._table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
        ) if n == 1 else None
        self._xmp_rating.setValue(
            vision.score_to_stars(record.sharpness_score)
            if record and record.sharpness_score is not None else 0
        )

    def _start_analysis(self) -> None:
        if not self._records or not hasattr(self, "_folder"):
            return
        self._btn_analyze.setEnabled(False)
        self._btn_stop_analysis.setVisible(True)
        self._analysis_progress.setVisible(True)
        self._analysis_progress.setValue(0)
        self._status_label.setText("Analyzing…")

        worker = AnalysisWorker(self._records, self._folder)
        worker.progress.connect(self._on_analysis_progress)
        worker.image_done.connect(self._on_analysis_image_done)
        worker.error.connect(self._on_analysis_error)
        worker.finished.connect(lambda w=worker: self._analysis_running_workers.discard(w))
        worker.finished.connect(self._on_analysis_finished)
        self._analysis_running_workers.add(worker)
        self._analysis_worker = worker
        worker.start()

    def _stop_analysis(self) -> None:
        if self._analysis_worker is not None:
            self._analysis_worker.cancel()

    def _on_analysis_progress(self, current: int, total: int) -> None:
        self._analysis_progress.setMaximum(total)
        self._analysis_progress.setValue(current)
        self._status_label.setText(f"Analyzing: {current} / {total} photos")

    def _on_analysis_image_done(
        self, path_str: str, score: int, _stars: int, sharpness_type: str, has_people: bool
    ) -> None:
        record = self._path_to_record.get(path_str)
        if record is None:
            return
        record.sharpness_score = score
        record.sharpness_type = sharpness_type
        record.sharpness_has_people = has_people

        for row in range(self._table.rowCount()):
            item = self._table.item(row, 0)
            if item and item.data(Qt.ItemDataRole.UserRole) == path_str:
                self._table.item(row, SHARPNESS_COL).setText(str(score))
                self._table.item(row, TYPE_COL).setText(sharpness_type)
                break

    def _on_analysis_error(self, msg: str) -> None:
        self._on_analysis_finished()
        QMessageBox.critical(
            self,
            "Analysis Error",
            f"{msg}\n\n"
            f"Make sure Ollama is running:  ollama serve\n"
            f"Pull the model if needed:     ollama pull {config.OLLAMA_MODEL}",
        )

    def _on_analysis_finished(self) -> None:
        self._analysis_worker = None
        self._btn_analyze.setEnabled(bool(self._records))
        self._btn_stop_analysis.setVisible(False)
        self._analysis_progress.setVisible(False)
        analyzed = sum(1 for r in self._records if r.sharpness_score is not None)
        self._status_label.setText(
            f"Analysis complete — {analyzed} / {len(self._records)} images scored."
        )

    def _write_xmp(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            return

        data = XMPData(
            title=self._xmp_title.text().strip(),
            caption=self._xmp_caption.toPlainText().strip(),
            keywords=[k.strip() for k in self._xmp_keywords.text().split(",") if k.strip()],
            copyright=self._xmp_copyright.text().strip(),
            rating=self._xmp_rating.value(),
        )

        for idx in rows:
            row = idx.row()
            path_str = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            record = self._path_to_record.get(path_str)
            write_xmp(Path(path_str), XMPData(
                title=data.title,
                caption=data.caption,
                keywords=data.keywords,
                copyright=data.copyright,
                rating=data.rating,
                sharpness_score=record.sharpness_score if record else None,
            ))
            item = self._table.item(row, XMP_COL)
            item.setText("✓ XMP")
            item.setForeground(QColor("#4a9eff"))
            if record:
                record.xmp_exists = True

        self._status_label.setText(f"XMP written for {len(rows)} image{'s' if len(rows) != 1 else ''}.")


def main() -> None:
    app = QApplication(sys.argv)
    app.setStyle("Fusion")

    # Dark palette
    from PyQt6.QtGui import QPalette
    palette = QPalette()
    palette.setColor(QPalette.ColorRole.Window, QColor(30, 30, 30))
    palette.setColor(QPalette.ColorRole.WindowText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Base, QColor(37, 37, 38))
    palette.setColor(QPalette.ColorRole.AlternateBase, QColor(45, 45, 48))
    palette.setColor(QPalette.ColorRole.Text, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Button, QColor(60, 60, 60))
    palette.setColor(QPalette.ColorRole.ButtonText, QColor(212, 212, 212))
    palette.setColor(QPalette.ColorRole.Highlight, QColor(74, 158, 255))
    palette.setColor(QPalette.ColorRole.HighlightedText, QColor(255, 255, 255))
    app.setPalette(palette)

    win = MainWindow()
    win.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
