import sys
from pathlib import Path

from PyQt6.QtCore import Qt, QThread, pyqtSignal
from PyQt6.QtGui import QColor
from PyQt6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTabWidget, QTableWidget, QTableWidgetItem, QHeaderView,
    QLabel, QPushButton, QFileDialog, QProgressBar,
    QGroupBox, QFormLayout, QLineEdit, QPlainTextEdit,
    QSpinBox, QSplitter, QScrollArea, QGridLayout, QSizePolicy,
    QFrame,
)
from PyQt6.QtWebEngineWidgets import QWebEngineView

from scanner import scan_folder, PhotoRecord, SUPPORTED_EXTENSIONS
from xmp_writer import XMPData, write_xmp, read_xmp
import charts

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
    ("XMP",               lambda r: "✓ XMP" if r.xmp_exists else "— None"),
]


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
        self._worker: ScanWorker | None = None
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

        self._tabs = QTabWidget()
        vbox.addWidget(self._tabs, stretch=1)

        splitter = QSplitter(Qt.Orientation.Vertical)
        splitter.addWidget(self._build_table_widget())
        splitter.addWidget(self._build_xmp_panel())
        splitter.setSizes([600, 200])
        self._tabs.addTab(splitter, "Table")
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

        self._status_label = QLabel("")
        self._status_label.setStyleSheet("color: #888;")

        hbox.addWidget(btn_pick)
        hbox.addWidget(self._path_label)
        hbox.addWidget(self._status_label)
        hbox.addWidget(self._btn_scan)
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
        self._btn_scan.setEnabled(False)
        self._progress.setVisible(True)
        self._progress.setValue(0)
        self._status_label.setText("Scanning…")
        self._table.setRowCount(0)
        self._records = []

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
        self._populate_table(records)
        self._update_summary(records)
        self._progress.setVisible(False)
        self._btn_scan.setEnabled(True)
        missing = sum(1 for r in records if r.missing_exif)
        self._status_label.setText(
            f"{len(records)} images scanned  ·  {missing} missing EXIF"
        )

    def _populate_table(self, records: list[PhotoRecord]) -> None:
        self._table.setSortingEnabled(False)
        self._table.setRowCount(len(records))
        xmp_col = len(COLUMNS) - 1

        for row, record in enumerate(records):
            for col, (_, getter) in enumerate(COLUMNS):
                item = QTableWidgetItem(getter(record))
                item.setData(Qt.ItemDataRole.UserRole, str(record.path))
                if col == xmp_col and record.xmp_exists:
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

    def _on_selection_changed(self) -> None:
        rows = self._table.selectionModel().selectedRows()
        if not rows:
            self._xmp_group.setVisible(False)
            return

        self._xmp_group.setVisible(True)
        n = len(rows)
        self._xmp_group.setTitle(f"XMP Metadata  ({n} image{'s' if n != 1 else ''} selected)")

        if n == 1:
            path_str = self._table.item(rows[0].row(), 0).data(Qt.ItemDataRole.UserRole)
            existing = read_xmp(Path(path_str))
            if existing:
                self._xmp_title.setText(existing.title)
                self._xmp_caption.setPlainText(existing.caption)
                self._xmp_keywords.setText(", ".join(existing.keywords))
                self._xmp_copyright.setText(existing.copyright)
                self._xmp_rating.setValue(existing.rating)
                return

        self._xmp_title.clear()
        self._xmp_caption.clear()
        self._xmp_keywords.clear()
        self._xmp_copyright.clear()
        self._xmp_rating.setValue(0)

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

        xmp_col = len(COLUMNS) - 1
        for idx in rows:
            row = idx.row()
            path_str = self._table.item(row, 0).data(Qt.ItemDataRole.UserRole)
            write_xmp(Path(path_str), data)
            # refresh XMP status cell
            item = self._table.item(row, xmp_col)
            item.setText("✓ XMP")
            item.setForeground(QColor("#4a9eff"))
            # update record in memory
            for r in self._records:
                if str(r.path) == path_str:
                    r.xmp_exists = True

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
