# SOSI Import (QGIS Plugin)

A QGIS plugin for importing **SOSI** files and converting them to **GeoPackage (GPKG)**.  
The resulting layers are automatically loaded into the current QGIS project.

## Features

- **Import SOSI → GeoPackage (GPKG)**  
  Converts using `ogr2ogr` and loads all layers from the GeoPackage into the QGIS project.

- **Handles unknown/missing `KOORDSYS`**  
  If `KOORDSYS` is missing or unknown (e.g. `99`), the plugin shows a dialog where you can:

  - choose the correct **Input CRS** (required)
  - optionally **transform** to a different **Output CRS**

- **Preflight check: SOSI driver availability**  
  On startup / when opening the dialog, the plugin checks whether GDAL/OGR has the **`SOSI`** driver available.
  - If **SOSI is missing**, the dialog shows an explanatory warning and the action buttons are disabled.
  - This is common on **macOS** where some QGIS/GDAL builds do not include SOSI/FYBA support.

> Note: During import the plugin **does not build spatial indexes** for performance.  
> Build spatial indexes afterwards in QGIS if needed.

## Requirements

- QGIS **>= 3.22**
- Tested on:
  - QGIS **3.40.x** (Qt5)
  - QGIS **3.44.6** (Qt 6.8.x)
- GDAL/OGR is bundled with QGIS (the plugin uses `ogr2ogr` from your QGIS installation)

## macOS notes (ogr2ogr, PROJ and SOSI support)

### 1) `ogr2ogr` discovery on macOS

On macOS, QGIS is distributed as an app bundle (`QGIS.app`), and `ogr2ogr` is often located inside the bundle.
The plugin includes a robust `ogr2ogr` lookup that works across:

- Windows (OSGeo4W / standalone)
- macOS (QGIS.app bundle paths)
- Linux

### 2) PROJ database (`proj.db`) when running external `ogr2ogr`

When `ogr2ogr` is launched as an external process on macOS, it may not automatically inherit QGIS’ internal PROJ/GDAL
configuration. If PROJ cannot find its database (`proj.db`), conversions can fail with messages like:

- `PROJ: ... no database context specified`
- `Cannot parse CRS ...`

To avoid this, the plugin passes relevant environment variables to the `ogr2ogr` process, including:

- `PROJ_DATA` / `PROJ_LIB` (pointing to a directory that contains `proj.db`)
- `GDAL_DATA`
- (optionally) `GDAL_DRIVER_PATH` when available

### 3) SOSI driver availability (most important on macOS)

Even if `ogr2ogr` is found and PROJ works, SOSI import requires the **GDAL SOSI driver**.
If the driver is not present, GDAL will not be able to open `.sos` files.

You can verify driver availability in **QGIS Python Console**:

```python
from osgeo import ogr
print(ogr.GetDriverByName("SOSI"))
```
