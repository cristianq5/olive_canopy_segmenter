# -*- coding: utf-8 -*-
"""
SegmentadorOlivosDialog – Main dialog
-------------------------------------
• "Select Parcel" button is only enabled when a raster is present.
• Live counter of sampling points.
• Auto-closes 2 seconds after generating the layer.
• Combo boxes update when layers are added/removed (without restarting).
• **New**: Accepts raster and AOI in different CRS (uses coordinate 
  transformations for sampling and segmentation).
• Segmentation is delegated to `segment_olivos()` (which already transforms AOI).
"""
from __future__ import annotations

from pathlib import Path
from typing import List, Tuple

import cv2
from qgis.PyQt import QtWidgets, uic
from qgis.PyQt.QtCore import QTimer
from qgis.PyQt.QtWidgets import QMessageBox
from qgis.core import (
    QgsProject,
    QgsRasterLayer,
    QgsVectorLayer,
    QgsCoordinateTransform,
    QgsCoordinateReferenceSystem,
    QgsWkbTypes,
)
from qgis.utils import iface

from .pixel_picker_tool import PixelPickerTool
from .otsu_segmentation import segment_olivos

# -------------------------------------------------------------------
_FORM, _ = uic.loadUiType(Path(__file__).with_name("segmentador_olivos_dialog_base.ui"))
_CLOSE_DELAY_MS = 2000
Sample = Tuple[float, float, List[int]]


class SegmentadorOlivosDialog(QtWidgets.QDialog, _FORM):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setupUi(self)

        self.pixelTool: PixelPickerTool | None = None
        self.canvas = iface.mapCanvas()
        self.muestras: List[Sample] = []

        self._fill_combos()
        self._toggle_sample_button()

        # Connections --------------------------------------------------
        self.comboRaster.currentIndexChanged.connect(self._toggle_sample_button)
        self.btnIniciarMuestras.clicked.connect(self._start_sampling)
        self.btnTerminarMuestras.clicked.connect(self._stop_sampling)
        self.buttonBox.accepted.connect(self.segmentar_con_otsu)

        # Listen to project changes
        self._prj = QgsProject.instance()
        self._prj.layerWasAdded.connect(self._on_layers_changed)
        self._prj.layerWillBeRemoved.connect(self._on_layers_changed)

        # Initial UI state ---------------------------------------------
        self.progressBar.setValue(0)
        self.labelEstado.setText("Select AOI (.shp) and raster (.tif/.tiff)")
        self.btnTerminarMuestras.setEnabled(False)

    # ----- dynamic combo update --------------------------------------
    def _on_layers_changed(self, *_):
        self._fill_combos()
        self._toggle_sample_button()

    def showEvent(self, e):
        super().showEvent(e)
        self._fill_combos()
        self._toggle_sample_button()

    # ----------------------------------------------------------------
    # combo / button helpers
    # ----------------------------------------------------------------
    def _fill_combos(self):
        self.comboAOI.clear()
        self.comboRaster.clear()
        for lyr in QgsProject.instance().mapLayers().values():
            if (
                isinstance(lyr, QgsVectorLayer)
                and lyr.geometryType() == QgsWkbTypes.PolygonGeometry
                and lyr.source().lower().endswith(".shp")
            ):
                self.comboAOI.addItem(lyr.name(), lyr)
            elif (
                isinstance(lyr, QgsRasterLayer)
                and lyr.source().lower().endswith((".tif", ".tiff"))
            ):
                self.comboRaster.addItem(lyr.name(), lyr)

    def _toggle_sample_button(self, *_):
        self.btnIniciarMuestras.setEnabled(self.comboRaster.count() > 0)

    # ----------------------------------------------------------------
    # Sample collection
    # ----------------------------------------------------------------
    def _start_sampling(self):
        self.pixelTool = PixelPickerTool(self.canvas, self._handle_pixel_click)
        self.canvas.setMapTool(self.pixelTool)
        self.btnIniciarMuestras.setEnabled(False)
        self.btnTerminarMuestras.setEnabled(True)
        self.muestras.clear()
        self.labelEstado.setText("Samples collected: 0")

    def _handle_pixel_click(self, x_geo: float, y_geo: float):
        layer_ras: QgsRasterLayer | None = self.comboRaster.currentData()
        if not isinstance(layer_ras, QgsRasterLayer):
            return

        # — Transform to raster CRS if different —
        dst_crs = layer_ras.crs()
        src_crs = self.canvas.mapSettings().destinationCrs()
        if src_crs != dst_crs:
            tr = QgsCoordinateTransform(src_crs, dst_crs, QgsProject.instance())
            pt = tr.transform(x_geo, y_geo)
            x_geo, y_geo = pt.x(), pt.y()

        img = cv2.imread(layer_ras.source().split("|")[0], cv2.IMREAD_COLOR)
        if img is None:
            QMessageBox.warning(self, "Error", "Unable to open raster with OpenCV.")
            return

        ext = layer_ras.extent()
        col = int((x_geo - ext.xMinimum()) / (ext.width() / layer_ras.width()))
        row = int((ext.yMaximum() - y_geo) / (ext.height() / layer_ras.height()))
        if 0 <= row < img.shape[0] and 0 <= col < img.shape[1]:
            b, g, r = img[row, col]
            self.muestras.append((x_geo, y_geo, [int(r), int(g), int(b)]))
            self.labelEstado.setText(f"Samples collected: {len(self.muestras)}")

    def _stop_sampling(self):
        if self.pixelTool:
            self.canvas.unsetMapTool(self.pixelTool)
            self.pixelTool.resetRubberBand()
        self.btnTerminarMuestras.setEnabled(False)
        self._toggle_sample_button()
        self.labelEstado.setText(
            f"SAMPLING COMPLETED. Total samples: {len(self.muestras)}"
        )

    # ----------------------------------------------------------------
    # Segmentation
    # ----------------------------------------------------------------
    def segmentar_con_otsu(self):
        layer_aoi: QgsVectorLayer | None = self.comboAOI.currentData()
        layer_ras: QgsRasterLayer | None = self.comboRaster.currentData()
        if not isinstance(layer_aoi, QgsVectorLayer) or not isinstance(layer_ras, QgsRasterLayer):
            QMessageBox.warning(self, "Error", "You must select valid AOI and raster.")
            return
        try:
            vlayer = segment_olivos(layer_ras, layer_aoi)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "Segmentation failed", str(exc))
            return
        QgsProject.instance().addMapLayer(vlayer)
        self.progressBar.setValue(100)
        self.labelEstado.setText(
            f"Done! {vlayer.featureCount()} canopies detected. Closing…"
        )
        if self.pixelTool:
            self.pixelTool.resetRubberBand()
        QTimer.singleShot(_CLOSE_DELAY_MS, self.accept)

    # ----------------------------------------------------------------
    # Disconnect signals
    # ----------------------------------------------------------------
    def closeEvent(self, e):  # noqa: D401
        try:
            self._prj.layerWasAdded.disconnect(self._on_layers_changed)
            self._prj.layerWillBeRemoved.disconnect(self._on_layers_changed)
        except Exception:
            pass
        super().closeEvent(e)
