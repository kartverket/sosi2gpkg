# -*- coding: utf-8 -*-
from qgis.PyQt.QtCore import QCoreApplication, QProcess, Qt, QProcessEnvironment
from qgis.PyQt.QtGui import QIcon
from qgis.PyQt.QtWidgets import (
    QAction, QFileDialog, QMessageBox, QProgressDialog, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QGroupBox
)
from qgis.core import QgsProject, QgsVectorLayer, QgsApplication
import os
import sys
import shutil
import tempfile
from pathlib import Path
import codecs
import re
from typing import Optional, Tuple


# =========================================================
# Qt5/Qt6-robuste helpers (DialogCode + StandardButton)
# =========================================================
def dialog_accepted_code():
    return QDialog.DialogCode.Accepted if hasattr(QDialog, "DialogCode") else QDialog.Accepted


def dialog_rejected_code():
    return QDialog.DialogCode.Rejected if hasattr(QDialog, "DialogCode") else QDialog.Rejected


def mb_yes():
    return QMessageBox.StandardButton.Yes if hasattr(QMessageBox, "StandardButton") else QMessageBox.Yes


def mb_no():
    return QMessageBox.StandardButton.No if hasattr(QMessageBox, "StandardButton") else QMessageBox.No


def qproc_merged_channels():
    return (
        QProcess.ProcessChannelMode.MergedChannels
        if hasattr(QProcess, "ProcessChannelMode")
        else QProcess.MergedChannels
    )


def qproc_not_running():
    return (
        QProcess.ProcessState.NotRunning
        if hasattr(QProcess, "ProcessState")
        else QProcess.NotRunning
    )


# -------------------------
# Hoveddialog: velg SOSI inn + GPKG ut
# + Preflight: SOSI-driver tilgjengelig?
# -------------------------
class ImportDialog(QDialog):
    def __init__(self, parent=None, sosi_available: bool = True, sosi_message: str = ""):
        super().__init__(parent)
        self.setWindowTitle("Kartverket – SOSI Import")
        self.setMinimumWidth(720)

        root = QVBoxLayout(self)

        g = QGroupBox("Input / Output")
        grid = QGridLayout(g)

        # Input
        self.in_edit = QLineEdit()
        self.in_edit.setReadOnly(True)
        self.btn_in = QPushButton("Velg SOSI…")
        self.btn_in.clicked.connect(self.pick_input)

        grid.addWidget(QLabel("SOSI innfil:"), 0, 0)
        grid.addWidget(self.in_edit, 0, 1)
        grid.addWidget(self.btn_in, 0, 2)

        # Output
        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        self.btn_out = QPushButton("Lagre som…")
        self.btn_out.clicked.connect(self.pick_output)

        grid.addWidget(QLabel("GPKG utfil:"), 1, 0)
        grid.addWidget(self.out_edit, 1, 1)
        grid.addWidget(self.btn_out, 1, 2)

        root.addWidget(g)

        # Warning/status label (nederst)
        self.status_label = QLabel("")
        self.status_label.setWordWrap(True)
        self.status_label.setTextInteractionFlags(Qt.TextSelectableByMouse)

        root.addWidget(self.status_label)

        # Buttons
        row = QHBoxLayout()
        row.addStretch(1)
        self.btn_cancel = QPushButton("Avbryt")
        self.btn_ok = QPushButton("Importer")
        self.btn_cancel.clicked.connect(self.reject)
        self.btn_ok.clicked.connect(self.accept)
        row.addWidget(self.btn_cancel)
        row.addWidget(self.btn_ok)
        root.addLayout(row)

        # Preflight result
        self._sosi_available = bool(sosi_available)
        self._sosi_message = sosi_message or ""
        self.apply_preflight()

        self._update_ok()

    def apply_preflight(self):
        """
        Dersom SOSI-driver mangler: deaktiver alt bortsett fra Avbryt,
        og vis forklaring nederst.
        """
        if self._sosi_available:
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            self.btn_in.setEnabled(True)
            self.btn_out.setEnabled(True)
            # btn_ok styres av _update_ok()
            return

        # Disable
        self.btn_in.setEnabled(False)
        self.btn_out.setEnabled(False)
        self.btn_ok.setEnabled(False)

        # Red warning
        msg = self._sosi_message.strip()
        if not msg:
            msg = (
                "SOSI-driver mangler i GDAL i denne QGIS-installasjonen.\n\n"
                "Dette skjer ofte på macOS hvis QGIS.app/GDAL er bygget uten FYBA/OpenFYBA.\n"
                "Da kan ikke SOSI-filer åpnes/konverteres.\n\n"
                "Løsning: Bruk en QGIS/GDAL-build som inkluderer SOSI-støtte, "
                "eller en egen ogr2ogr (GDAL) som har SOSI-driver."
            )
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setText(msg)

    def _update_ok(self):
        if not self._sosi_available:
            self.btn_ok.setEnabled(False)
            return
        ok = bool(self.in_edit.text().strip()) and bool(self.out_edit.text().strip())
        self.btn_ok.setEnabled(ok)

    def pick_input(self):
        if not self._sosi_available:
            return
        in_sos, _ = QFileDialog.getOpenFileName(
            self,
            "Velg SOSI-fil",
            "",
            "SOSI (*.sos *.SOS);;Alle filer (*.*)"
        )
        if not in_sos:
            return
        in_sos = os.path.normpath(in_sos)
        self.in_edit.setText(in_sos)

        # auto-foreslå utfil hvis ikke satt
        if not self.out_edit.text().strip():
            self.out_edit.setText(str(Path(in_sos).with_suffix(".gpkg")))

        self._update_ok()

    def pick_output(self):
        if not self._sosi_available:
            return
        suggested = self.out_edit.text().strip()
        if not suggested and self.in_edit.text().strip():
            suggested = str(Path(self.in_edit.text().strip()).with_suffix(".gpkg"))

        out_gpkg, _ = QFileDialog.getSaveFileName(
            self,
            "Lagre GeoPackage som",
            suggested or "",
            "GeoPackage (*.gpkg)"
        )
        if not out_gpkg:
            return

        out_gpkg = os.path.normpath(out_gpkg)
        if not out_gpkg.lower().endswith(".gpkg"):
            out_gpkg += ".gpkg"
        self.out_edit.setText(out_gpkg)
        self._update_ok()

    def get_values(self) -> Tuple[Optional[str], Optional[str]]:
        return (
            self.in_edit.text().strip() or None,
            self.out_edit.text().strip() or None
        )


# -------------------------
# Dialog (kun når KOORDSYS er ukjent/mangler)
# -------------------------
class UnknownCrsDialog(QDialog):
    CRS_CHOICES = [
        ("EPSG:25832 (UTM 32N)", 25832),
        ("EPSG:25833 (UTM 33N)", 25833),
        ("EPSG:25834 (UTM 34N)", 25834),
        ("EPSG:25835 (UTM 35N)", 25835),
        ("EPSG:3857  (WebMercator)", 3857),
    ]

    OUT_CHOICES = [
        ("Samme som input (standard)", None),
        ("EPSG:25832 (UTM 32N)", 25832),
        ("EPSG:25833 (UTM 33N)", 25833),
        ("EPSG:25834 (UTM 34N)", 25834),
        ("EPSG:25835 (UTM 35N)", 25835),
        ("EPSG:3857  (WebMercator)", 3857),
    ]

    def __init__(self, parent=None, koordsys_value: Optional[int] = None):
        super().__init__(parent)
        self.setWindowTitle("SOSI Import – KOORDSYS ukjent")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        info = QLabel(
            "SOSI-fila har ukjent eller manglende KOORDSYS.\n"
            "Velg hvilken projeksjon koordinatene faktisk er i."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        if koordsys_value is not None:
            layout.addWidget(QLabel(f"Oppgitt KOORDSYS i fila: {koordsys_value} (ukjent)"))

        grid = QGridLayout()

        self.cmb_in = QComboBox()
        for txt, epsg in self.CRS_CHOICES:
            self.cmb_in.addItem(txt, epsg)

        self.cmb_out = QComboBox()
        for txt, epsg in self.OUT_CHOICES:
            self.cmb_out.addItem(txt, epsg)

        grid.addWidget(QLabel("Input CRS (påkrevd):"), 0, 0)
        grid.addWidget(self.cmb_in, 0, 1)
        grid.addWidget(QLabel("Output CRS:"), 1, 0)
        grid.addWidget(self.cmb_out, 1, 1)

        layout.addLayout(grid)

        row = QHBoxLayout()
        row.addStretch(1)
        btn_cancel = QPushButton("Avbryt")
        btn_ok = QPushButton("OK")
        btn_cancel.clicked.connect(self.reject)
        btn_ok.clicked.connect(self.accept)
        row.addWidget(btn_cancel)
        row.addWidget(btn_ok)
        layout.addLayout(row)

    def get_values(self) -> Tuple[int, Optional[int]]:
        in_epsg = int(self.cmb_in.currentData())
        out_epsg = self.cmb_out.currentData()
        out_epsg = int(out_epsg) if out_epsg is not None else None
        return in_epsg, out_epsg


# -------------------------
# Plugin
# -------------------------
class Sosi2GpkgPlugin:
    def __init__(self, iface):
        self.iface = iface
        self.action = None
        self.toolbar = None

        # cache preflight
        self._sosi_available: Optional[bool] = None
        self._sosi_message: str = ""

    def tr(self, text):
        return QCoreApplication.translate("Sosi2GpkgPlugin", text)

    def initGui(self):
        icon_path = os.path.join(os.path.dirname(__file__), "icon_sosi2gpkg.svg")
        self.action = QAction(QIcon(icon_path), self.tr("SOSI Import"), self.iface.mainWindow())
        self.action.triggered.connect(self.run)
        self.iface.addPluginToMenu(self.tr("&Kartverket"), self.action)
        self.toolbar = self.iface.addToolBar("Kartverket")
        self.toolbar.addAction(self.action)

        # Kjør preflight ved oppstart av plugin (første gang)
        self._run_preflight()

    def unload(self):
        if self.action:
            self.iface.removePluginMenu(self.tr("&Kartverket"), self.action)
            try:
                if self.toolbar:
                    self.toolbar.removeAction(self.action)
            except Exception:
                pass
            self.action = None

    # -------------------------
    # Preflight
    # -------------------------
    def _run_preflight(self):
        """
        Sjekker om GDAL/OGR har SOSI-driver tilgjengelig.
        Cache resultatet slik at dialogen kan bruke det.
        """
        if self._sosi_available is not None:
            return

        try:
            from osgeo import ogr  # noqa
            drv = ogr.GetDriverByName("SOSI")
            if drv is None:
                self._sosi_available = False
                self._sosi_message = (
                    "SOSI-driver mangler i GDAL i denne QGIS-installasjonen.\n\n"
                    "Dette skjer ofte på macOS hvis QGIS.app/GDAL er bygget uten FYBA/OpenFYBA.\n"
                    "Da kan ikke SOSI-filer åpnes eller konverteres.\n\n"
                    "Løsning:\n"
                    "• Bruk en QGIS/GDAL-build som inkluderer SOSI-støtte, eller\n"
                    "• Installer/bygg GDAL med FYBA/OpenFYBA og bruk en ogr2ogr derfra."
                )
            else:
                self._sosi_available = True
                self._sosi_message = ""
        except Exception as e:
            # Hvis osgeo import feiler, skal dialogen fortsatt kunne åpnes,
            # men den skal være deaktivert med forklaring.
            self._sosi_available = False
            self._sosi_message = (
                "Kunne ikke laste GDAL/OGR (osgeo) i denne QGIS-installasjonen.\n\n"
                f"Feil: {e}"
            )

    # -------------------------
    # Helpers
    # -------------------------
    def _is_exec(self, path: str) -> bool:
        if not path:
            return False
        try:
            return os.path.isfile(path) and os.access(path, os.X_OK)
        except Exception:
            return False

    def find_ogr2ogr(self) -> str:
        # 1) PATH først
        which_path = shutil.which("ogr2ogr")
        if which_path and os.path.isfile(which_path):
            return which_path

        prefix = os.path.normpath(QgsApplication.prefixPath() or "")

        appdir = None
        try:
            if hasattr(QgsApplication, "applicationDirPath"):
                appdir = os.path.normpath(QgsApplication.applicationDirPath() or "")
        except Exception:
            appdir = None

        candidates = []

        if sys.platform.startswith("win"):
            candidates += [
                os.path.join(prefix, "bin", "ogr2ogr.exe"),
                os.path.join(prefix, "..", "..", "bin", "ogr2ogr.exe"),
                os.path.join(prefix, "..", "bin", "ogr2ogr.exe"),
                os.path.join(prefix, "apps", "gdal", "bin", "ogr2ogr.exe"),
            ]
        elif sys.platform == "darwin":
            if prefix.lower().endswith(".app"):
                candidates += [
                    os.path.join(prefix, "Contents", "MacOS", "bin", "ogr2ogr"),
                    os.path.join(prefix, "Contents", "MacOS", "ogr2ogr"),
                ]
            candidates += [
                os.path.join(prefix, "bin", "ogr2ogr"),
                os.path.join(prefix, "..", "MacOS", "bin", "ogr2ogr"),
            ]
            if appdir:
                candidates += [
                    os.path.join(appdir, "bin", "ogr2ogr"),
                    os.path.join(appdir, "ogr2ogr"),
                ]
        else:
            candidates += [
                os.path.join(prefix, "bin", "ogr2ogr"),
                os.path.join(prefix, "..", "bin", "ogr2ogr"),
            ]

        # de-dupe + normalize
        normed = []
        seen = set()
        for c in candidates:
            if not c:
                continue
            c = os.path.normpath(c)
            if c in seen:
                continue
            seen.add(c)
            normed.append(c)

        for c in normed:
            if self._is_exec(c):
                return c

        if sys.platform.startswith("win"):
            alt = shutil.which("ogr2ogr.exe")
            if alt and os.path.isfile(alt):
                return alt

        msg = (
            "Fant ikke ogr2ogr.\n\n"
            f"platform: {sys.platform}\n"
            f"prefixPath: {prefix}\n"
            f"applicationDirPath: {appdir}\n"
            f"which('ogr2ogr'): {which_path}\n"
            "Forsøkte stier:\n - " + "\n - ".join(normed)
        )
        raise RuntimeError(msg)

    def build_ogr_env(self, ogr2ogr_path: str) -> QProcessEnvironment:
        """
        Sørger for at ekstern ogr2ogr-prosess på macOS (og noen Linux-oppsett)
        får PROJ/GDAL paths.
        """
        env = QProcessEnvironment.systemEnvironment()

        # Prepend PATH med mappen til ogr2ogr
        ogr_dir = os.path.dirname(os.path.normpath(ogr2ogr_path))
        old_path = env.value("PATH") or ""
        if ogr_dir and ogr_dir not in old_path.split(os.pathsep):
            env.insert("PATH", ogr_dir + os.pathsep + old_path)

        try:
            from osgeo import gdal, osr  # type: ignore
        except Exception:
            return env

        # Finn QGIS.app root (macOS fallbacks)
        qgis_app = None
        prefix = os.path.normpath(QgsApplication.prefixPath() or "")
        appdir = None
        try:
            if hasattr(QgsApplication, "applicationDirPath"):
                appdir = os.path.normpath(QgsApplication.applicationDirPath() or "")
        except Exception:
            appdir = None

        if ".app" in prefix:
            qgis_app = prefix[: prefix.lower().rfind(".app") + 4]
        elif appdir and ".app" in appdir:
            qgis_app = appdir[: appdir.lower().rfind(".app") + 4]

        # GDAL_DATA
        gdal_data = gdal.GetConfigOption("GDAL_DATA") or os.environ.get("GDAL_DATA")
        if not gdal_data and qgis_app:
            cand = os.path.join(qgis_app, "Contents", "Resources", "qgis", "gdal")
            if os.path.isdir(cand):
                gdal_data = cand
        if gdal_data:
            env.insert("GDAL_DATA", gdal_data)

        # GDAL_DRIVER_PATH (plugins)
        gdal_driver_path = gdal.GetConfigOption("GDAL_DRIVER_PATH") or os.environ.get("GDAL_DRIVER_PATH")
        if not gdal_driver_path and qgis_app:
            driver_candidates = [
                os.path.join(qgis_app, "Contents", "PlugIns", "gdalplugins"),
                os.path.join(qgis_app, "Contents", "Resources", "qgis", "gdalplugins"),
                os.path.join(qgis_app, "Contents", "Resources", "gdalplugins"),
            ]
            for d in driver_candidates:
                if os.path.isdir(d):
                    gdal_driver_path = d
                    break
        if gdal_driver_path:
            env.insert("GDAL_DRIVER_PATH", gdal_driver_path)

        # PROJ: velg path som faktisk inneholder proj.db
        proj_db_dir = None
        try:
            paths = osr.GetPROJSearchPaths()
        except Exception:
            paths = []

        for p in (paths or []):
            if p and os.path.isfile(os.path.join(p, "proj.db")):
                proj_db_dir = p
                break

        if not proj_db_dir and qgis_app:
            cand = os.path.join(qgis_app, "Contents", "Resources", "qgis", "proj")
            if os.path.isfile(os.path.join(cand, "proj.db")):
                proj_db_dir = cand

        if not proj_db_dir:
            proj_db_dir = os.environ.get("PROJ_DATA") or os.environ.get("PROJ_LIB")

        if proj_db_dir:
            env.insert("PROJ_DATA", proj_db_dir)
            env.insert("PROJ_LIB", proj_db_dir)

        return env

    def extract_koordsys(self, src_path: str) -> Optional[int]:
        try:
            raw = Path(src_path).read_bytes()
        except Exception:
            return None

        if raw.startswith(codecs.BOM_UTF8):
            raw = raw[len(codecs.BOM_UTF8):]
        while raw and raw[:1] in b" \t\r\n":
            raw = raw[1:]

        txt = raw[:200000].decode("utf-8", errors="replace")
        m = re.search(r"(?mi)^\s*\.{1,6}KOORDSYS\s+(\d+)\b", txt)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def is_known_koordsys(self, k: Optional[int]) -> bool:
        return k in (22, 23, 24, 25)

    def make_workaround_copy(self, src_path: str, force_45: bool = True, target_encoding: str = "iso-8859-10") -> str:
        src = Path(src_path)
        tmpdir = Path(tempfile.mkdtemp(prefix="qgis_sosi_"))
        dst = tmpdir / (src.stem + "_workaround.sos")

        raw = src.read_bytes()
        if raw.startswith(codecs.BOM_UTF8):
            raw = raw[len(codecs.BOM_UTF8):]

        while raw and raw[:1] in b" \t\r\n":
            raw = raw[1:]

        text_lines = raw.decode("utf-8", errors="replace").splitlines(True)

        out_lines = []
        for line in text_lines:
            if line.startswith("..TEGNSETT"):
                out_lines.append("..TEGNSETT ISO8859-10\n")
                continue
            if force_45 and line.startswith("..SOSI-VERSJON"):
                out_lines.append("..SOSI-VERSJON 4.5\n")
                continue
            out_lines.append(line)

        dst.write_text("".join(out_lines), encoding=target_encoding, errors="replace", newline="\n")
        return str(dst)

    def add_all_layers(self, datasource_path: str, progress: QProgressDialog) -> int:
        from osgeo import ogr
        ds = ogr.Open(datasource_path)
        if ds is None:
            raise RuntimeError(f"Klarte ikke å åpne: {datasource_path}")

        layer_count = ds.GetLayerCount()
        if layer_count <= 0:
            return 0

        canvas = getattr(self.iface, "mapCanvas", None)
        old_render = None
        if canvas:
            old_render = canvas().renderFlag()
            canvas().setRenderFlag(False)

        try:
            progress.setRange(0, layer_count)
            progress.setValue(0)
            added = 0
            for i in range(layer_count):
                if progress.wasCanceled():
                    break
                lyr = ds.GetLayerByIndex(i)
                name = lyr.GetName()
                uri = f"{datasource_path}|layername={name}"
                vl = QgsVectorLayer(uri, name, "ogr")
                if vl.isValid():
                    QgsProject.instance().addMapLayer(vl)
                    added += 1
                progress.setLabelText(self.tr(f"Laster lag: {name} ({i+1}/{layer_count})"))
                progress.setValue(i + 1)
                QApplication.processEvents()
            return added
        finally:
            if canvas and old_render is not None:
                canvas().setRenderFlag(old_render)
                canvas().refresh()

    # -------------------------
    # ogr2ogr runner (progress + cancel + output capture)
    # -------------------------
    def run_ogr2ogr(self, ogr2ogr_path: str, args: list, progress: QProgressDialog, label: str):
        progress.setRange(0, 0)
        progress.setValue(0)
        progress.setLabelText(label)
        QApplication.processEvents()

        proc = QProcess()
        proc.setProgram(ogr2ogr_path)
        proc.setArguments(args)
        proc.setProcessChannelMode(qproc_merged_channels())
        proc.setProcessEnvironment(self.build_ogr_env(ogr2ogr_path))

        rx_pct = re.compile(r"(\d{1,3})\s*%")
        rx_dots = re.compile(r"(?:^|\s)(\d{1,3})(?=\.+)")
        got_determinate = False
        last_val = -1
        out_all = ""

        proc.start()
        if not proc.waitForStarted(8000):
            raise RuntimeError(
                "Klarte ikke å starte ogr2ogr-prosessen.\n\n"
                f"Program: {ogr2ogr_path}\n"
                f"Args: {' '.join(args)}"
            )

        while True:
            QApplication.processEvents()

            if progress.wasCanceled():
                proc.kill()
                proc.waitForFinished(2000)
                raise RuntimeError("Avbrutt av bruker.")

            if proc.waitForReadyRead(50):
                chunk = bytes(proc.readAll()).decode("utf-8", errors="replace")
                out_all += chunk

                for line in re.split(r"[\r\n]+", chunk):
                    line = line.strip()
                    if not line:
                        continue

                    m = rx_pct.search(line)
                    val = None
                    if m:
                        val = max(0, min(100, int(m.group(1))))
                    else:
                        hits = rx_dots.findall(line)
                        if hits:
                            try:
                                val = max(0, min(100, int(hits[-1])))
                            except Exception:
                                val = None

                    if val is not None:
                        if not got_determinate:
                            progress.setRange(0, 100)
                            got_determinate = True
                        if val != last_val:
                            last_val = val
                            progress.setValue(val)
                            progress.setLabelText(self.tr(f"{label} ({val}%)"))
                    else:
                        if not got_determinate:
                            progress.setLabelText(self.tr(label))

            if proc.state() == qproc_not_running():
                break

        rest = bytes(proc.readAll()).decode("utf-8", errors="replace")
        if rest:
            out_all += rest

        exit_code = proc.exitCode()
        if exit_code != 0:
            msg = out_all.strip() or "(ingen output fra ogr2ogr)"
            raise RuntimeError(f"ogr2ogr feilet (exit code {exit_code}).\n\nOutput:\n{msg}")

        progress.setRange(0, 100)
        progress.setValue(100)
        QApplication.processEvents()
        return out_all

    # -------------------------
    # Converter: GeoPackage (fast + robust fallback) + OPTIONAL CRS override
    # -------------------------
    def convert_gpkg(self, in_sos: str, out_gpkg: str, progress: QProgressDialog,
                     crs_args: Optional[list] = None):
        ogr2ogr_path = self.find_ogr2ogr()
        crs_args = crs_args or []

        if os.path.exists(out_gpkg):
            try:
                os.remove(out_gpkg)
            except Exception:
                pass

        base_args = [
            "--config", "OGR_SQLITE_SYNCHRONOUS", "OFF",
            "--config", "OGR_SQLITE_JOURNAL_MODE", "MEMORY",
            "-lco", "SPATIAL_INDEX=NO",
            "-nlt", "PROMOTE_TO_MULTI",
            "-progress",
        ]

        fast_args = ["-f", "GPKG", out_gpkg] + crs_args + [in_sos, "-gt", "50000"] + base_args

        try:
            self.run_ogr2ogr(ogr2ogr_path, fast_args, progress, self.tr("Konverterer til GeoPackage (rask)"))
            return "fast"
        except Exception:
            if os.path.exists(out_gpkg):
                try:
                    os.remove(out_gpkg)
                except Exception:
                    pass
            robust_args = ["-f", "GPKG", out_gpkg] + crs_args + [in_sos, "-skipfailures"] + base_args
            self.run_ogr2ogr(ogr2ogr_path, robust_args, progress, self.tr("Konverterer til GeoPackage (robust)"))
            return "robust"

    # -------------------------
    # Main
    # -------------------------
    def run(self):
        # Sørg for at preflight er kjørt
        self._run_preflight()

        # Åpne dialogen uansett, men deaktiver den hvis SOSI ikke finnes
        dlg = ImportDialog(
            self.iface.mainWindow(),
            sosi_available=bool(self._sosi_available),
            sosi_message=self._sosi_message
        )
        if dlg.exec() != dialog_accepted_code():
            return

        # Hvis SOSI ikke finnes skal OK aldri være tilgjengelig,
        # men for sikkerhets skyld:
        if not self._sosi_available:
            return

        in_sos, out_gpkg = dlg.get_values()
        if not in_sos or not out_gpkg:
            return

        in_sos = os.path.normpath(in_sos)
        out_gpkg = os.path.normpath(out_gpkg)
        if not out_gpkg.lower().endswith(".gpkg"):
            out_gpkg += ".gpkg"

        if os.path.exists(out_gpkg):
            reply = QMessageBox.question(
                self.iface.mainWindow(), self.tr("Overskriv fil?"),
                self.tr("Filen finnes allerede:\n{0}\n\nVil du overskrive?").format(out_gpkg),
                mb_yes() | mb_no(),
                mb_no()
            )
            if reply != mb_yes():
                return

        koordsys = self.extract_koordsys(in_sos)
        known = self.is_known_koordsys(koordsys)

        crs_args = []
        chosen_in_epsg = None
        chosen_out_epsg = None

        if not known:
            crs_dlg = UnknownCrsDialog(self.iface.mainWindow(), koordsys_value=koordsys)
            if crs_dlg.exec() != dialog_accepted_code():
                return
            chosen_in_epsg, chosen_out_epsg = crs_dlg.get_values()

            if chosen_out_epsg is None:
                crs_args = ["-a_srs", f"EPSG:{chosen_in_epsg}"]
            else:
                crs_args = ["-s_srs", f"EPSG:{chosen_in_epsg}", "-t_srs", f"EPSG:{chosen_out_epsg}"]

        progress = QProgressDialog(self.tr("Starter…"), self.tr("Avbryt"), 0, 0, self.iface.mainWindow())
        progress.setWindowTitle(self.tr("Kartverket – SOSI"))
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()

        try:
            try:
                mode = self.convert_gpkg(in_sos, out_gpkg, progress, crs_args=crs_args)
            except Exception:
                progress.setLabelText(self.tr("Direkte konvertering feilet – prøver workaround…"))
                QApplication.processEvents()
                workaround = self.make_workaround_copy(in_sos, force_45=True, target_encoding="iso-8859-10")
                mode = self.convert_gpkg(workaround, out_gpkg, progress, crs_args=crs_args)

            if progress.wasCanceled():
                progress.close()
                return

            progress.setLabelText(self.tr("Konvertert. Laster lag i QGIS…"))
            QApplication.processEvents()
            added = self.add_all_layers(out_gpkg, progress)

            progress.close()

            if known:
                crs_txt = f"KOORDSYS: {koordsys} (kjent)"
            else:
                crs_txt = (
                    f"KOORDSYS: {koordsys if koordsys is not None else 'mangler'} (ukjent) | "
                    f"Input EPSG:{chosen_in_epsg} | Output: {'samme' if chosen_out_epsg is None else f'EPSG:{chosen_out_epsg}'}"
                )

            QMessageBox.information(
                self.iface.mainWindow(), self.tr("SOSI Import – ferdig"),
                self.tr("Lagret:\n{0}\n\nLa til {1} lag i prosjektet.\n\n{2}\n\nModus: {3}")
                .format(out_gpkg, added, crs_txt, mode)
            )

        except Exception as e:
            progress.close()
            QMessageBox.critical(self.iface.mainWindow(), self.tr("SOSI Import – feil"), str(e))
