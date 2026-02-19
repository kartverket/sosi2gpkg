# -*- coding: utf-8 -*-
"""
Kartverket – SOSI Import (Sosi2GpkgPlugin)

Fullversjon (oppdatert):

Håndterer:
1) Vanlig SOSI-vektor -> GeoPackage via ogr2ogr (som før)
2) "Raster-SOSI" (kun .RASTER) -> lager worldfile + prj til bildefil og laster raster i QGIS

VIKTIGE endringer for raster:
- Leser og prioriterer ...BILDE-SYS (i .RASTER..BILDE) for CRS (ofte dette FYSAK bruker)
- Leser ...PIXEL-STØRR og bruker dette i worldfile (eksakt pixelstørrelse)
- Skriver flere worldfile-varianter: .jgw/.jpgw/.wld + UPPER-case + .jpw/.JPW
- Fjerner *.aux.xml (kan overstyre/“låse” ungeoreferert raster)
- Tvinger reload og sjekker pixel-extent (0..W / -H..0) for å bekrefte at worldfile faktisk er lest
"""
from qgis.PyQt.QtCore import QCoreApplication, QProcess, Qt, QProcessEnvironment
from qgis.PyQt.QtGui import QIcon, QImage
from qgis.PyQt.QtWidgets import (
    QAction, QFileDialog, QMessageBox, QProgressDialog, QApplication,
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QLineEdit,
    QPushButton, QComboBox, QGroupBox
)
from qgis.core import (
    QgsProject, QgsVectorLayer, QgsApplication,
    QgsRasterLayer, QgsCoordinateReferenceSystem
)

import os
import sys
import shutil
import tempfile
from pathlib import Path
import codecs
import re
from typing import Optional, Tuple, List, Dict


# =========================================================
# Qt5/Qt6-robuste helpers
# =========================================================
def dialog_accepted_code():
    return QDialog.DialogCode.Accepted if hasattr(QDialog, "DialogCode") else QDialog.Accepted


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

        # Output (GPKG)
        self.out_edit = QLineEdit()
        self.out_edit.setReadOnly(True)
        self.btn_out = QPushButton("Lagre som…")
        self.btn_out.clicked.connect(self.pick_output)

        grid.addWidget(QLabel("GPKG utfil:"), 1, 0)
        grid.addWidget(self.out_edit, 1, 1)
        grid.addWidget(self.btn_out, 1, 2)

        root.addWidget(g)

        # Status/varsel
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

        # Preflight result (kun relevant for vektor-konvertering)
        self._sosi_available = bool(sosi_available)
        self._sosi_message = sosi_message or ""
        self.apply_preflight()
        self._update_ok()

    def apply_preflight(self):
        """
        Dersom SOSI-driver mangler: dialogen fungerer fortsatt,
        fordi raster-only SOSI kan importeres via worldfile. Vi varsler.
        """
        if self._sosi_available:
            self.status_label.setText("")
            self.status_label.setStyleSheet("")
            return

        msg = self._sosi_message.strip()
        if not msg:
            msg = (
                "SOSI-driver mangler i GDAL i denne QGIS-installasjonen.\n\n"
                "Vektorfiler (vanlig SOSI) kan da ikke konverteres med ogr2ogr.\n"
                "Raster-SOSI (kun .RASTER) kan likevel importeres ved å lage worldfile."
            )
        self.status_label.setStyleSheet("color: #b00020;")
        self.status_label.setText(msg)

    def _update_ok(self):
        ok = bool(self.in_edit.text().strip()) and bool(self.out_edit.text().strip())
        self.btn_ok.setEnabled(ok)

    def pick_input(self):
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

        if not self.out_edit.text().strip():
            self.out_edit.setText(str(Path(in_sos).with_suffix(".gpkg")))
        self._update_ok()

    def pick_output(self):
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
        self.setWindowTitle("SOSI Import – KOORDSYS/BILDE-SYS ukjent")
        self.setMinimumWidth(520)

        layout = QVBoxLayout(self)

        info = QLabel(
            "SOSI-fila har ukjent eller manglende KOORDSYS/BILDE-SYS.\n"
            "Velg hvilken projeksjon koordinatene faktisk er i."
        )
        info.setWordWrap(True)
        layout.addWidget(info)

        if koordsys_value is not None:
            layout.addWidget(QLabel(f"Oppgitt KOORDSYS/BILDE-SYS i fila: {koordsys_value} (ukjent)"))

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

    def write_vrt_for_jpeg(
        self,
        jpg_path: str,
        epsg: int,
        extent: Tuple[float, float, float, float],
        pixel_size: Optional[Tuple[float, float]] = None
    ) -> str:
        """
        Lager en .vrt som peker på JPEG og inneholder GeoTransform + SRS eksplisitt.
        Dette omgår worldfile/aux.xml-problemer og gir korrekt plassering i QGIS.
        """
        img = QImage(jpg_path)
        w = img.width()
        h = img.height()
        if w <= 0 or h <= 0:
            raise RuntimeError(f"Klarte ikke å lese bildefil (fikk ikke størrelse):\n{jpg_path}")

        minE, minN, maxE, maxN = extent

        # Pixelstørrelse: bruk PIXEL-STØRR hvis oppgitt, ellers beregn fra extent
        if pixel_size:
            xres = float(pixel_size[0])
            yres = float(pixel_size[1])
        else:
            xres = (maxE - minE) / float(w)
            yres = (maxN - minN) / float(h)

        # GDAL GeoTransform:
        # GT0 = top-left X, GT1 = pixel width, GT2 = 0
        # GT3 = top-left Y, GT4 = 0, GT5 = -pixel height
        gt0 = minE
        gt1 = xres
        gt2 = 0.0
        gt3 = maxN
        gt4 = 0.0
        gt5 = -yres

        crs = self.make_crs_from_epsg(int(epsg))
        if not crs.isValid():
            raise RuntimeError(f"Klarte ikke å lage CRS for EPSG:{epsg}")

        wkt = crs.toWkt(QgsCoordinateReferenceSystem.WktVariant.WKT2_2019) \
            if hasattr(QgsCoordinateReferenceSystem, "WktVariant") else crs.toWkt()

        base, _ = os.path.splitext(jpg_path)
        vrt_path = base + ".vrt"

        # Kildepath relativt til VRT er mest robust ved flytting av mappe
        src_name = os.path.basename(jpg_path)

        # Band: JPEG er vanligvis Byte / 3 band (RGB). GDAL håndterer dette fint når vi peker på JPEG direkte.
        # Vi kan lage "passthrough" VRT ved å bruke <SimpleSource> per band.
        # For enkelhet: bruk "VRTDataset" som bare refererer til JPEG som "SourceFilename" og lar GDAL lese bånd.
        # (Dette fungerer i praksis i QGIS/GDAL for JPEG.)
        vrt = f'''<VRTDataset rasterXSize="{w}" rasterYSize="{h}">
    <SRS>{wkt}</SRS>
    <GeoTransform>{gt0}, {gt1}, {gt2}, {gt3}, {gt4}, {gt5}</GeoTransform>
    <VRTRasterBand dataType="Byte" band="1">
        <SimpleSource>
        <SourceFilename relativeToVRT="1">{src_name}</SourceFilename>
        <SourceBand>1</SourceBand>
        <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        </SimpleSource>
    </VRTRasterBand>
    <VRTRasterBand dataType="Byte" band="2">
        <SimpleSource>
        <SourceFilename relativeToVRT="1">{src_name}</SourceFilename>
        <SourceBand>2</SourceBand>
        <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        </SimpleSource>
    </VRTRasterBand>
    <VRTRasterBand dataType="Byte" band="3">
        <SimpleSource>
        <SourceFilename relativeToVRT="1">{src_name}</SourceFilename>
        <SourceBand>3</SourceBand>
        <SrcRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        <DstRect xOff="0" yOff="0" xSize="{w}" ySize="{h}"/>
        </SimpleSource>
    </VRTRasterBand>
    </VRTDataset>
    '''

        with open(vrt_path, "w", encoding="utf-8") as f:
            f.write(vrt)

        return vrt_path


    # -------------------------
    # CRS helper
    # -------------------------
    def make_crs_from_epsg(self, epsg: int) -> QgsCoordinateReferenceSystem:
        epsg = int(epsg)
        crs = QgsCoordinateReferenceSystem(f"EPSG:{epsg}")
        if crs.isValid():
            return crs
        crs = QgsCoordinateReferenceSystem()
        if hasattr(crs, "createFromEpsgId"):
            crs.createFromEpsgId(epsg)
        return crs

    # -------------------------
    # Preflight
    # -------------------------
    def _run_preflight(self):
        if self._sosi_available is not None:
            return
        try:
            from osgeo import ogr  # noqa
            drv = ogr.GetDriverByName("SOSI")
            if drv is None:
                self._sosi_available = False
                self._sosi_message = (
                    "SOSI-driver mangler i GDAL i denne QGIS-installasjonen.\n\n"
                    "Vektorfiler (vanlig SOSI) kan da ikke konverteres med ogr2ogr.\n"
                    "Raster-SOSI (kun .RASTER) kan likevel importeres ved å lage worldfile."
                )
            else:
                self._sosi_available = True
                self._sosi_message = ""
        except Exception as e:
            self._sosi_available = False
            self._sosi_message = (
                "Kunne ikke laste GDAL/OGR (osgeo) i denne QGIS-installasjonen.\n\n"
                f"Feil: {e}"
            )

    # -------------------------
    # ogr2ogr helpers
    # -------------------------
    def _is_exec(self, path: str) -> bool:
        if not path:
            return False
        try:
            return os.path.isfile(path) and os.access(path, os.X_OK)
        except Exception:
            return False

    def find_ogr2ogr(self) -> str:
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
                os.path.join(prefix, "apps", "gdal", "bin", "ogr2ogr.exe"),
                os.path.join(prefix, "..", "bin", "ogr2ogr.exe"),
                os.path.join(prefix, "..", "..", "bin", "ogr2ogr.exe"),
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

        seen = set()
        normed = []
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

        raise RuntimeError("Fant ikke ogr2ogr. Sjekk QGIS/GDAL installasjon.")

    def build_ogr_env(self, ogr2ogr_path: str) -> QProcessEnvironment:
        env = QProcessEnvironment.systemEnvironment()

        ogr_dir = os.path.dirname(os.path.normpath(ogr2ogr_path))
        old_path = env.value("PATH") or ""
        if ogr_dir and ogr_dir not in old_path.split(os.pathsep):
            env.insert("PATH", ogr_dir + os.pathsep + old_path)

        try:
            from osgeo import gdal, osr  # type: ignore
        except Exception:
            return env

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

        gdal_data = gdal.GetConfigOption("GDAL_DATA") or os.environ.get("GDAL_DATA")
        if not gdal_data and qgis_app:
            cand = os.path.join(qgis_app, "Contents", "Resources", "qgis", "gdal")
            if os.path.isdir(cand):
                gdal_data = cand
        if gdal_data:
            env.insert("GDAL_DATA", gdal_data)

        gdal_driver_path = gdal.GetConfigOption("GDAL_DRIVER_PATH") or os.environ.get("GDAL_DRIVER_PATH")
        if not gdal_driver_path and qgis_app:
            for d in [
                os.path.join(qgis_app, "Contents", "PlugIns", "gdalplugins"),
                os.path.join(qgis_app, "Contents", "Resources", "qgis", "gdalplugins"),
                os.path.join(qgis_app, "Contents", "Resources", "gdalplugins"),
            ]:
                if os.path.isdir(d):
                    gdal_driver_path = d
                    break
        if gdal_driver_path:
            env.insert("GDAL_DRIVER_PATH", gdal_driver_path)

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

    # -------------------------
    # SOSI reading/parsing
    # -------------------------
    def _read_sosi_text(self, src_path: str) -> str:
        raw = Path(src_path).read_bytes()
        if raw.startswith(codecs.BOM_UTF8):
            raw = raw[len(codecs.BOM_UTF8):]
        while raw and raw[:1] in b" \t\r\n":
            raw = raw[1:]

        head = raw[:2000].decode("latin-1", errors="ignore")
        enc = None
        m = re.search(r"(?mi)^\s*\.{1,6}TEGNSETT\s+([A-Za-z0-9\-\_]+)\b", head)
        if m:
            ts = m.group(1).strip().upper()
            if "8859-10" in ts:
                enc = "iso-8859-10"
            elif "8859-1" in ts or "LATIN1" in ts:
                enc = "latin-1"
            elif "UTF" in ts:
                enc = "utf-8"

        tries = [enc] if enc else []
        tries += ["utf-8", "iso-8859-10", "latin-1"]
        for e in tries:
            if not e:
                continue
            try:
                return raw.decode(e, errors="strict")
            except Exception:
                continue
        return raw.decode("latin-1", errors="replace")

    def extract_koordsys(self, src_path: str) -> Optional[int]:
        try:
            txt = self._read_sosi_text(src_path)[:200000]
        except Exception:
            return None
        m = re.search(r"(?mi)^\s*\.{1,6}KOORDSYS\s+(\d+)\b", txt)
        if not m:
            return None
        try:
            return int(m.group(1))
        except Exception:
            return None

    def is_known_koordsys(self, k: Optional[int]) -> bool:
        return k in (22, 23, 24, 25)

    def koordsys_to_epsg(self, k: int) -> Optional[int]:
        return {22: 25832, 23: 25833, 24: 25834, 25: 25835}.get(k)

    def parse_raster_sosi(self, src_path: str) -> Optional[Dict]:
        """
        Raster-only SOSI:
        Leser også:
        - BILDE-SYS (prioriteres for CRS)
        - PIXEL-STØRR (brukes for worldfile)
        """
        try:
            txt = self._read_sosi_text(src_path)
        except Exception:
            return None

        if ".RASTER" not in txt:
            return None

        # Hvis vektorgrupper finnes, la ogr2ogr håndtere
        if any(k in txt for k in (".KURVE", ".PUNKT", ".FLATE", ".OBJEKT")):
            return None

        # ENHET
        unit = 1.0
        m_unit = re.search(r"(?mi)^\s*\.{1,6}ENHET\s+([0-9]+(?:\.[0-9]+)?)\b", txt)
        if m_unit:
            try:
                unit = float(m_unit.group(1))
            except Exception:
                unit = 1.0

        # Finn RASTER-blokk (første)
        idx_r = txt.find(".RASTER")
        if idx_r < 0:
            return None
        sub = txt[idx_r:]

        # BILDE-SYS (inne i ..BILDE)
        bilde_sys = None
        m_bs = re.search(r"(?mi)^\s*\.{1,6}BILDE-SYS\s+(\d+)\b", sub)
        if m_bs:
            try:
                bilde_sys = int(m_bs.group(1))
            except Exception:
                bilde_sys = None

        # PIXEL-STØRR
        pixel_size = None  # (x, y)
        m_ps = re.search(r"(?mi)^\s*\.{1,6}PIXEL-STØRR\s+([0-9]+(?:\.[0-9]+)?)\s+([0-9]+(?:\.[0-9]+)?)\b", sub)
        if m_ps:
            try:
                pixel_size = (float(m_ps.group(1)), float(m_ps.group(2)))
            except Exception:
                pixel_size = None

        # KOORDSYS fra hode (fallback)
        koordsys = None
        m_k = re.search(r"(?mi)^\s*\.{1,6}KOORDSYS\s+(\d+)\b", txt)
        if m_k:
            try:
                koordsys = int(m_k.group(1))
            except Exception:
                koordsys = None

        # BILDE-FIL
        m_b = re.search(r"(?mi)^\s*\.{1,6}BILDE-FIL\s+\"([^\"]+)\"\s*$", sub)
        if not m_b:
            m_b = re.search(r"(?mi)^\s*\.{1,6}BILDE-FIL\s+([^\r\n]+?)\s*$", sub)
        if not m_b:
            return None
        image_name = m_b.group(1).strip().strip('"')

        # NØ-seksjon
        m_no = re.search(r"(?mis)^\s*\.{1,6}NØ\s*\r?\n(.*?)(?=^\s*\.[A-ZÆØÅ]|^\s*\.SLUTT|\Z)", sub)
        if not m_no:
            return None

        coord_block = m_no.group(1)
        coords: List[Tuple[float, float]] = []
        for line in coord_block.splitlines():
            line = line.strip()
            if not line:
                continue
            parts = re.split(r"\s+", line)
            if len(parts) < 2:
                continue
            try:
                # NØ: N først, Ø etterpå
                n = float(parts[0]) * unit
                e = float(parts[1]) * unit
                coords.append((e, n))
            except Exception:
                continue

        if len(coords) < 4:
            return None

        es = [c[0] for c in coords]
        ns = [c[1] for c in coords]
        minE, maxE = min(es), max(es)
        minN, maxN = min(ns), max(ns)

        return {
            "unit": unit,
            "koordsys": koordsys,
            "bilde_sys": bilde_sys,
            "pixel_size": pixel_size,
            "image_name": image_name,
            "coords": coords,
            "extent": (minE, minN, maxE, maxN),
        }

    def resolve_image_path(self, sosi_path: str, image_name: str) -> Optional[str]:
        if not image_name:
            return None
        image_name = image_name.strip().strip('"')

        p = Path(image_name)
        if p.is_absolute():
            return str(p) if p.exists() else None

        cand = Path(sosi_path).parent / image_name
        if cand.exists():
            return str(cand)

        # fallback: case-insensitive
        try:
            parent = Path(sosi_path).parent
            low = image_name.lower()
            for f in parent.iterdir():
                if f.is_file() and f.name.lower() == low:
                    return str(f)
        except Exception:
            pass
        return None

    # -------------------------
    # Raster: worldfile + prj + reload
    # -------------------------
    def _delete_auxxml(self, jpg_path: str):
        aux_candidates = [
            jpg_path + ".aux.xml",                 # some drivers
            os.path.splitext(jpg_path)[0] + ".aux.xml",
            os.path.splitext(jpg_path)[0] + ".jpg.aux.xml",  # belt+suspenders
        ]
        for aux in aux_candidates:
            if os.path.exists(aux):
                try:
                    os.remove(aux)
                except Exception:
                    pass

    def write_worldfile_and_prj(
        self,
        jpg_path: str,
        epsg: int,
        extent: Tuple[float, float, float, float],
        pixel_size: Optional[Tuple[float, float]] = None
    ) -> Tuple[str, str]:
        img = QImage(jpg_path)
        w = img.width()
        h = img.height()
        if w <= 0 or h <= 0:
            raise RuntimeError(f"Klarte ikke å lese bildefil (fikk ikke størrelse):\n{jpg_path}")

        minE, minN, maxE, maxN = extent

        if pixel_size:
            xres = float(pixel_size[0])
            yres = float(pixel_size[1])
        else:
            xres = (maxE - minE) / float(w)
            yres = (maxN - minN) / float(h)

        # Worldfile origin = center of top-left pixel
        x0 = minE + xres / 2.0
        y0 = maxN - yres / 2.0

        base, _ = os.path.splitext(jpg_path)

        # Maks kompatibilitet på tvers av GDAL/QGIS/Windows/macOS
        worldfiles = [
            base + ".jgw", base + ".jpgw", base + ".wld", base + ".jpw",
            base + ".JGW", base + ".JPGW", base + ".WLD", base + ".JPW",
        ]
        for wf in worldfiles:
            with open(wf, "w", encoding="ascii") as f:
                f.write(
                    f"{xres:.12f}\n"
                    f"0.0\n"
                    f"0.0\n"
                    f"{-yres:.12f}\n"
                    f"{x0:.12f}\n"
                    f"{y0:.12f}\n"
                )

        prj = base + ".prj"
        crs = self.make_crs_from_epsg(epsg)
        if not crs.isValid():
            raise RuntimeError(f"Klarte ikke å lage CRS for EPSG:{epsg}")

        wkt = crs.toWkt(QgsCoordinateReferenceSystem.WktVariant.WKT2_2019) \
            if hasattr(QgsCoordinateReferenceSystem, "WktVariant") else crs.toWkt()

        with open(prj, "w", encoding="utf-8") as f:
            f.write(wkt)

        return worldfiles[0], prj

    def add_raster_layer(self, raster_path: str, epsg: Optional[int] = None) -> bool:
        name = Path(raster_path).stem

        def _load():
            rl = QgsRasterLayer(raster_path, name)
            if rl.isValid() and epsg:
                crs = self.make_crs_from_epsg(int(epsg))
                if crs.isValid():
                    rl.setCrs(crs)
            return rl

        rl = _load()
        if not rl.isValid():
            return False

        QgsProject.instance().addMapLayer(rl)

        ext = rl.extent()
        looks_pixel = (
            abs(ext.xMinimum()) < 1e-9 and abs(ext.yMaximum()) < 1e-9 and
            ext.xMaximum() > 100 and ext.yMinimum() < -100
        )

        if looks_pixel:
            QgsProject.instance().removeMapLayer(rl.id())
            rl = _load()
            if not rl.isValid():
                return False
            QgsProject.instance().addMapLayer(rl)

        return True

    # -------------------------
    # Workaround copy (tegnsett + SOSI-versjon)
    # -------------------------
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

    # -------------------------
    # Add layers from GPKG
    # -------------------------
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
    # ogr2ogr runner
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
                    if m:
                        val = max(0, min(100, int(m.group(1))))
                        if not got_determinate:
                            progress.setRange(0, 100)
                            got_determinate = True
                        if val != last_val:
                            last_val = val
                            progress.setValue(val)
                            progress.setLabelText(self.tr(f"{label} ({val}%)"))

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
        self._run_preflight()

        dlg = ImportDialog(
            self.iface.mainWindow(),
            sosi_available=bool(self._sosi_available),
            sosi_message=self._sosi_message
        )
        if dlg.exec() != dialog_accepted_code():
            return

        in_sos, out_gpkg = dlg.get_values()
        if not in_sos or not out_gpkg:
            return

        in_sos = os.path.normpath(in_sos)
        out_gpkg = os.path.normpath(out_gpkg)
        if not out_gpkg.lower().endswith(".gpkg"):
            out_gpkg += ".gpkg"

        progress = QProgressDialog(self.tr("Starter…"), self.tr("Avbryt"), 0, 0, self.iface.mainWindow())
        progress.setWindowTitle(self.tr("Kartverket – SOSI"))
        progress.setMinimumDuration(0)
        progress.show()
        QApplication.processEvents()

        try:
            progress.setLabelText(self.tr("Analyserer SOSI…"))
            QApplication.processEvents()

            raster_info = self.parse_raster_sosi(in_sos)
            if raster_info:
                koordsys = raster_info.get("koordsys")
                bilde_sys = raster_info.get("bilde_sys")

                # Prioriter BILDE-SYS hvis mulig
                sys_for_epsg = None
                if bilde_sys is not None and self.is_known_koordsys(int(bilde_sys)):
                    sys_for_epsg = int(bilde_sys)
                elif koordsys is not None and self.is_known_koordsys(int(koordsys)):
                    sys_for_epsg = int(koordsys)

                epsg = None
                if sys_for_epsg is not None:
                    epsg = self.koordsys_to_epsg(sys_for_epsg)

                if epsg is None:
                    progress.hide()
                    crs_dlg = UnknownCrsDialog(self.iface.mainWindow(), koordsys_value=bilde_sys or koordsys)
                    if crs_dlg.exec() != dialog_accepted_code():
                        return
                    chosen_in_epsg, _ = crs_dlg.get_values()
                    epsg = int(chosen_in_epsg)
                    progress.show()
                    QApplication.processEvents()

                image_name = raster_info["image_name"]
                jpg_path = self.resolve_image_path(in_sos, image_name)
                if not jpg_path:
                    raise RuntimeError(
                        "Raster-SOSI ble gjenkjent, men fant ikke bildefilen.\n\n"
                        f"BILDE-FIL: {image_name}\n"
                        f"SOSI-mappe: {Path(in_sos).parent}\n\n"
                        "Legg bildefilen i samme mappe som SOSI-fila (eller bruk absolutt sti i BILDE-FIL)."
                    )

                # Rydd aux.xml først
                self._delete_auxxml(jpg_path)

                progress.setLabelText(self.tr("Lager worldfile og .prj…"))
                QApplication.processEvents()

                extent = raster_info["extent"]
                pixel_size = raster_info.get("pixel_size")
                wf, prj = self.write_worldfile_and_prj(jpg_path, int(epsg), extent, pixel_size=pixel_size)

                # Rydd aux.xml igjen før/etter lasting (QGIS/GDAL kan skrive den)
                self._delete_auxxml(jpg_path)

                progress.setLabelText(self.tr("Laster raster i QGIS…"))
                QApplication.processEvents()

                # Lag VRT som tvinger GeoTransform+SRS
                vrt_path = self.write_vrt_for_jpeg(
                    jpg_path,
                    int(epsg),
                    extent,
                    pixel_size=raster_info.get("pixel_size")
                )

                # Last VRT (ikke JPG) – dette omgår worldfile/aux.xml/cache
                ok = self.add_raster_layer(vrt_path, epsg=int(epsg) if epsg else None)

                progress.close()

                if not ok:
                    raise RuntimeError(
                        "Worldfile ble laget, men QGIS klarte ikke å laste rasterlaget.\n\n"
                        f"Raster: {jpg_path}\nWorldfile: {wf}\nPRJ: {prj}"
                    )

                QMessageBox.information(
                    self.iface.mainWindow(), self.tr("SOSI Import – ferdig (Raster)"),
                    self.tr(
                        "Raster-SOSI importert.\n\n"
                        "Raster:\n• {0}\n\n"
                        "Worldfile:\n• {1}\n\n"
                        ".prj:\n• {2}\n\n"
                        "BILDE-SYS: {3} | KOORDSYS: {4} | EPSG:{5}\n"
                        "PIXEL-STØRR: {6}\n\n"
                        "Merk: Valgt GPKG-utfil ble ikke brukt."
                    ).format(
                        jpg_path, wf, prj,
                        str(bilde_sys), str(koordsys), str(epsg),
                        str(pixel_size) if pixel_size else "(ikke oppgitt)"
                    )
                )
                return

            # Vektor-løp
            if not self._sosi_available:
                progress.close()
                raise RuntimeError(
                    "Dette ser ut som en vektor-SOSI, men SOSI-driver mangler i GDAL.\n\n"
                    "Installer en QGIS/GDAL med SOSI-støtte (FYBA/OpenFYBA), eller bruk en ekstern ogr2ogr "
                    "som har SOSI-driver."
                )

            # KOORDSYS override hvis ukjent
            koordsys = self.extract_koordsys(in_sos)
            known = self.is_known_koordsys(koordsys)

            crs_args = []
            chosen_in_epsg = None
            chosen_out_epsg = None

            if not known:
                progress.hide()
                crs_dlg = UnknownCrsDialog(self.iface.mainWindow(), koordsys_value=koordsys)
                if crs_dlg.exec() != dialog_accepted_code():
                    return
                chosen_in_epsg, chosen_out_epsg = crs_dlg.get_values()
                progress.show()
                QApplication.processEvents()

                if chosen_out_epsg is None:
                    crs_args = ["-a_srs", f"EPSG:{chosen_in_epsg}"]
                else:
                    crs_args = ["-s_srs", f"EPSG:{chosen_in_epsg}", "-t_srs", f"EPSG:{chosen_out_epsg}"]

            # Overskriv-spørsmål for gpkg
            if os.path.exists(out_gpkg):
                reply = QMessageBox.question(
                    self.iface.mainWindow(), self.tr("Overskriv fil?"),
                    self.tr("Filen finnes allerede:\n{0}\n\nVil du overskrive?").format(out_gpkg),
                    mb_yes() | mb_no(),
                    mb_no()
                )
                if reply != mb_yes():
                    progress.close()
                    return

            # Konverter
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

            QMessageBox.information(
                self.iface.mainWindow(), self.tr("SOSI Import – ferdig"),
                self.tr("Lagret:\n{0}\n\nLa til {1} lag i prosjektet.").format(out_gpkg, added)
            )

        except Exception as e:
            progress.close()
            QMessageBox.critical(self.iface.mainWindow(), self.tr("SOSI Import – feil"), str(e))
