# SOSI Import (QGIS Plugin)

A QGIS plugin for importing **SOSI** files into QGIS.

- **Vector SOSI** files are converted to **GeoPackage (GPKG)** using `ogr2ogr`.
- **Raster-SOSI** files (`.RASTER`) are imported by generating georeferencing and loading the image directly in QGIS.

The resulting layers are automatically loaded into the current QGIS project.

## Features

- **Import Vector SOSI → GeoPackage (GPKG)**  
  Converts using `ogr2ogr` and loads all layers from the GeoPackage into the QGIS project.

- **Import Raster-SOSI (`.RASTER`) imagery**  
  If the SOSI file contains **only** a `.RASTER` section (no vector objects), the plugin will:
  - locate the referenced image file (`..BILDE-FIL`)
  - derive extent from the `.RASTER ..NØ` coordinates (respecting `..ENHET`)
  - use `..BILDE-SYS` (preferred) / `..KOORDSYS` to determine CRS when possible
  - generate georeferencing files (worldfile + `.prj`)
  - **create a GDAL `.vrt` with explicit GeoTransform + CRS** and load the VRT in QGIS for correct placement

  > Why VRT? In some setups QGIS/GDAL may list the worldfile/PRJ as “sidecar/attachments” but still load the image in
  > pixel coordinates (extent like `0..width, -height..0`). The VRT makes the georeferencing unambiguous.

- **Handles unknown/missing `KOORDSYS`**  
  If `KOORDSYS`/`BILDE-SYS` is missing or unknown, the plugin shows a dialog where you can:
  - choose the correct **Input CRS** (required)
  - optionally **transform** to a different **Output CRS** (vector import)

- **Preflight check: SOSI driver availability (vector import)**  
  On startup / when opening the dialog, the plugin checks whether GDAL/OGR has the **`SOSI`** driver available.
  - If **SOSI is missing**, the plugin shows an explanatory warning.
  - **Vector conversion is not available** without the SOSI driver.
  - **Raster-SOSI import still works** (it does not depend on the OGR SOSI driver).

> Note: During vector import the plugin **does not build spatial indexes** for performance.  
> Build spatial indexes afterwards in QGIS if needed.

## Requirements

- QGIS **>= 3.22**
- Tested on:
  - QGIS **3.40.x** (Qt5)
  - QGIS **3.44.6** (Qt 6.8.x)
- GDAL/OGR is bundled with QGIS (the plugin uses `ogr2ogr` from your QGIS installation)

## Raster-SOSI notes

### Image file location (`..BILDE-FIL`)

For Raster-SOSI, the SOSI file references an image file, for example:

- `..BILDE-FIL "TT-13242.jpg"`

If the path is relative, the plugin expects the image to be in the **same folder** as the SOSI file.

### CRS choice: `BILDE-SYS` vs `KOORDSYS`

Raster-SOSI commonly provides `..BILDE-SYS` inside the `.RASTER` group.  
The plugin **prefers `BILDE-SYS`** when present (this often matches how FYSAK places the raster).  
If `BILDE-SYS` is missing, the plugin falls back to `KOORDSYS`.

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

Even if `ogr2ogr` is found and PROJ works, **vector SOSI import** requires the **GDAL SOSI driver**.
If the driver is not present, GDAL will not be able to open `.sos` files for conversion.

You can verify driver availability in **QGIS Python Console**:

```python
from osgeo import ogr
print(ogr.GetDriverByName("SOSI"))
```

If this prints None, install/use a QGIS/GDAL build that includes SOSI/FYBA support (or use an external GDAL build that does).
