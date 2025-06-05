# -*- coding: utf-8 -*-
"""
Utility functions for olive tree canopy segmentation using Otsu.
Author: yourself :)

This version uses GDAL/OGR directly for rasterization, avoiding dependency
on Processing algorithms. It writes the vector layer to a temporary file
if it's in memory. CRS reprojection and export are handled without using QgsVectorFileWriter.
"""
from __future__ import annotations

import os
import tempfile
from pathlib import Path
from typing import Tuple, List, Union

import cv2
import numpy as np
from osgeo import gdal, ogr
from qgis import processing
from qgis.core import (
    QgsRasterLayer,
    QgsVectorLayer,
    QgsFeature,
    QgsGeometry,
    QgsPointXY,
    QgsField,
    QgsFillSymbol,
    QgsProject,
    QgsWkbTypes,
)

# ---------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------

def _load_layer_from_output(output: Union[str, QgsVectorLayer]) -> QgsVectorLayer:
    """If output is a path, load layer; if it's already a layer, return it as is."""
    if isinstance(output, QgsVectorLayer):
        return output
    if isinstance(output, str):
        layer = QgsVectorLayer(output, "aoi_temp", "ogr")
        if not layer.isValid():
            raise RuntimeError(f"Could not load layer from: {output}")
        return layer
    raise RuntimeError("Unexpected type when loading reprojected layer")


def _reproject_aoi_to_raster(aoi: QgsVectorLayer, ras: QgsRasterLayer) -> QgsVectorLayer:
    """If CRS differs, returns a memory-reprojected AOI layer to match raster CRS."""
    if aoi.crs() == ras.crs():
        return aoi

    params = {
        "INPUT": aoi,
        "TARGET_CRS": ras.crs().authid(),
        "OUTPUT": "memory:"
    }
    result = processing.run("native:reprojectlayer", params, is_child_algorithm=True)
    out = result.get("OUTPUT")
    return _load_layer_from_output(out)


def _prepare_aoi_file(aoi: QgsVectorLayer) -> str:
    """If the AOI is in memory, save it to a temporary shapefile and return path."""
    source = aoi.dataProvider().dataSourceUri()
    if source.lower().endswith(".shp"):
        return source

    tmp_dir = Path(tempfile.mkdtemp())
    shp_path = tmp_dir / "aoi_temp.shp"
    result = processing.run(
        "native:savefeatures",
        {"INPUT": aoi, "OUTPUT": str(shp_path)},
        is_child_algorithm=True
    )
    shp_result = result.get("OUTPUT")
    if not shp_result or not Path(shp_result).exists():
        raise RuntimeError("Failed to write temporary AOI shapefile")
    return shp_result


def _rasterize_aoi(
    layer_aoi: QgsVectorLayer, layer_ras: QgsRasterLayer
) -> Tuple[np.ndarray, float, float, float, float, int, int]:
    """Rasterizes the AOI reprojected to the raster CRS and returns the mask + metadata."""
    aoi_proj = _reproject_aoi_to_raster(layer_aoi, layer_ras)

    aoi_file = _prepare_aoi_file(aoi_proj)
    
    extent = layer_ras.extent()
    w, h = layer_ras.width(), layer_ras.height()
    gt = (
        extent.xMinimum(),
        (extent.xMaximum() - extent.xMinimum()) / w,
        0,
        extent.yMaximum(),
        0,
        -(extent.yMaximum() - extent.yMinimum()) / h,
    )

    mem_driver = gdal.GetDriverByName("MEM")
    dst_ds = mem_driver.Create("", w, h, 1, gdal.GDT_Byte)
    dst_ds.SetGeoTransform(gt)
    proj_wkt = layer_ras.crs().toWkt()
    dst_ds.SetProjection(proj_wkt)

    src_ds = ogr.Open(aoi_file)
    if src_ds is None:
        raise RuntimeError("Failed to open AOI layer with OGR")
    ogr_layer = src_ds.GetLayer()

    err = gdal.RasterizeLayer(dst_ds, [1], ogr_layer, burn_values=[1])
    src_ds = None
    if err != 0:
        raise RuntimeError("GDAL RasterizeLayer error")

    mask_arr = dst_ds.GetRasterBand(1).ReadAsArray()
    px_x = gt[1]
    px_y = abs(gt[5])
    dst_ds = None

    mask_arr = (mask_arr > 0).astype(np.uint8)
    return mask_arr, extent, px_x, px_y, w, h


# ---------------------------------------------------------------------
# MAIN FUNCTION
# ---------------------------------------------------------------------

def segment_olivos(layer_ras: QgsRasterLayer, layer_aoi: QgsVectorLayer) -> QgsVectorLayer:
    """Generates temporary layer with segmented canopies – handles mixed CRS."""
    mask_arr, extent, px_x, px_y, w, h = _rasterize_aoi(layer_aoi, layer_ras)

    raster_path = layer_ras.source().split("|")[0].split("?")[0]
    img_gray = cv2.imread(raster_path, cv2.IMREAD_GRAYSCALE)
    if img_gray is None:
        raise RuntimeError("OpenCV could not read the raster (grayscale)")

    _, otsu_full = cv2.threshold(img_gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    bin_img = np.zeros_like(otsu_full, dtype=np.uint8)
    bin_img[mask_arr == 1] = otsu_full[mask_arr == 1]

    mask_trees = np.zeros_like(bin_img, dtype=np.uint8)
    mask_trees[np.logical_and(mask_arr == 1, bin_img == 0)] = 255

    contornos, _ = cv2.findContours(
        mask_trees, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE
    )

    vlayer = QgsVectorLayer(f"Polygon?crs={layer_ras.crs().authid()}", "Olive_Canopies", "memory")
    prov = vlayer.dataProvider()
    prov.addAttributes([QgsField("ID", 4), QgsField("Area_px", 4)])
    vlayer.updateFields()

    for i, cnt in enumerate(contornos, start=1):
        if cnt.shape[0] < 3:
            continue
        area_px = int(cv2.contourArea(cnt))
        pts: List[QgsPointXY] = []
        for pt in cnt[:, 0, :]:
            px, py = int(pt[0]), int(pt[1])
            x_geo = extent.xMinimum() + (px + 0.5) * px_x
            y_geo = extent.yMaximum() - (py + 0.5) * px_y
            pts.append(QgsPointXY(x_geo, y_geo))
        feat = QgsFeature()
        feat.setGeometry(QgsGeometry.fromPolygonXY([pts]))
        feat.setAttributes([i, area_px])
        prov.addFeature(feat)

    vlayer.updateExtents()
    symbol = QgsFillSymbol.createSimple(
        {"color": "0,255,0,120", "outline_color": "0,100,0", "outline_width": "0.3"}
    )
    vlayer.renderer().setSymbol(symbol)
    return vlayer
