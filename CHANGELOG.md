# Changelog

Alle vesentlige endringer i dette prosjektet dokumenteres her.

Format: [Keep a Changelog](https://keepachangelog.com/en/1.1.0/)  
Versjonering: [SemVer](https://semver.org/lang/no/)

## [1.2.0] - 2026-02-18

### Added

- Added support for Raster-SOSI (.RASTER) by generating georeferencing files and loading imagery directly in QGIS.

### Fixed

- Fixed incorrect raster placement in QGIS by creating a GDAL .vrt with explicit GeoTransform + CRS (avoids worldfile/aux.xml issues where rasters were loaded in pixel coordinates).

## [1.1.2] - 2026-01-07

### Fixed

- Added fix for checking path of ogr2ogr and proj on MAC.
- Added preflight to check if QGIS/GDAL build has FYBA/SOSI-library included and added a warning if that not is the case.

## [1.1.1] - 2025-12-27

### Added

- Compatibilityhelpers for Qt5/Qt6 (QDialog, QMessageBox, QProcess).

### Changed

- Enhanced internal compatibilitylogic (helpers) to support QGIS 3.x (Qt5) and QGIS 3.44+ (Qt6) without change of code.

## [1.1.1] - 2025-12-27

### Fixed

- Qt6-compatibility: updateted dialogcodes, QMessageBox-buttons and QProcess-enums so that the pluging works in both Qt5 and Qt6.

## [1.1.0] - 2025-12-27

### Added

- New maindialog for SOSI Import with choice of SOSI inputfile and GeoPackage outputfile.
- Support for SOSI-files with unknown or lack og `KOORDSYS`:
  - Extra dialog for choice of input CRS (required) and optional output CRS.
  - Use `-a_srs` with "same as input" and `-s_srs/-t_srs` with tranformation.

### Changed

- The importflow is now merged in a dialogbased workflow (no loger separate filedialogs in order without overview).

### Fixed

- More robust handling of SOSI-files where `KOORDSYS` is now supported by the SOSI-driver (f.eks. `99`), by letting a user set correct CRS before converting.

## [1.0.1] - 2025-12-21

### Changed

- Removed “direct open SOSI” mode due to stability issues and crashes.
- Plugin now focuses on converting SOSI to GeoPackage (GPKG) as the primary workflow.
- Changed icon to prepare for scaleability and bundle of plugins.

### Fixed

- The "direct open SOSI" is fixed, in the sence of it being removed.

### Notes

- The GeoPackage conversion uses fast defaults where possible and falls back to a more robust mode when required.

## [1.0.0] - 2025-12-16

### Added

- Rask åpning av SOSI direkte i QGIS.
- Konvertering til GeoPackage (rask) med progress/cancel.

### Changed

- Spatial index bygges ikke under import for bedre ytelse.

### Fixed

- Robust fallback når direkte konvertering feiler.
