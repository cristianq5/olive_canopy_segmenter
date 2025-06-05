# -*- coding: utf-8 -*-
"""
SegmentadorOlivos — Main plugin class
-------------------------------------
• Creates the "Segment Olive Trees" menu action.
• Adds a button with an icon to the main toolbar.
• Opens the `SegmentadorOlivosDialog` dialog.
• After pressing *OK*, runs post-processing steps:
    – Applies translucent green symbology with dark green outline
      (compatible with QGIS ≥3.16 without using `setStrokeColor` in QgsFillSymbol).
    – Clears temporary markers if present.
    – Closes the dialog after 2 seconds.
"""
from __future__ import annotations

import os
from pathlib import Path

from qgis.PyQt.QtGui import QIcon, QColor
from qgis.PyQt.QtWidgets import QAction, QMessageBox
from qgis.PyQt.QtCore import QTimer
from qgis.core import QgsProject, QgsVectorLayer

from .segmentador_olivos_dialog import SegmentadorOlivosDialog


class SegmentadorOlivos:  # ---------------------------------------------
    """Class registered by QGIS."""

    def __init__(self, iface):
        self.iface = iface  # QgisInterface
        self.plugin_dir = Path(__file__).parent
        self.dialog: SegmentadorOlivosDialog | None = None
        self.action: QAction | None = None

    # ------------------------------------------------------------------
    # GUI
    # ------------------------------------------------------------------
    def initGui(self):
        icon_path = self.plugin_dir / "icon.png"
        self.action = QAction(
            QIcon(str(icon_path)), "Segment Olive Trees", self.iface.mainWindow()
        )
        self.action.triggered.connect(self.run)

        # 1) Add to the Plugins menu
        self.iface.addPluginToMenu("&SegmentadorOlivos", self.action)
        # 2) Also add as a button in the main toolbar
        self.iface.addToolBarIcon(self.action)

    def unload(self):
        if self.action:
            # 1) Remove from the menu
            self.iface.removePluginMenu("&SegmentadorOlivos", self.action)
            # 2) Remove from the toolbar
            self.iface.removeToolBarIcon(self.action)

    # ------------------------------------------------------------------
    # EXECUTION
    # ------------------------------------------------------------------
    def run(self):
        if self.dialog is None:
            self.dialog = SegmentadorOlivosDialog(None)
            self.dialog.accepted.connect(self._postprocesar_resultado)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    # ------------------------------------------------------------------
    # POST-PROCESSING
    # ------------------------------------------------------------------
    def _postprocesar_resultado(self):
        ruta_shp: str | Path | None = None
        capa_resultado: QgsVectorLayer | None = None

        # If dialog returns a physical path (optional)
        try:
            ruta_shp = self.dialog.procesar()  # might not exist
        except AttributeError:
            pass
        except Exception as exc:
            QMessageBox.critical(None, "Error", f"Processing failed:\n{exc}")
            return

        # If shapefile exists -> load it
        if ruta_shp and os.path.exists(ruta_shp):
            capa_resultado = QgsVectorLayer(str(ruta_shp), "Olive_Canopies", "ogr")
            if not capa_resultado.isValid():
                QMessageBox.warning(None, "Error", "The shapefile layer is not valid.")
                return
            QgsProject.instance().addMapLayer(capa_resultado)

        # Otherwise, look for in-memory layer
        if capa_resultado is None:
            capas = QgsProject.instance().mapLayersByName("Olive_Canopies")
            if not capas:
                QMessageBox.warning(None, "Error", "Layer 'Olive_Canopies' not found.")
                return
            capa_resultado = capas[0]

        # ----------------------------------------------------------------
        # SYMBOLIZATION (compatible with all QGIS versions)
        # ----------------------------------------------------------------
        symbol = capa_resultado.renderer().symbol()
        symbol.setColor(QColor(0, 255, 0, 120))  # translucent green fill
        sl = symbol.symbolLayer(0)
        if hasattr(sl, "setStrokeColor"):
            sl.setStrokeColor(QColor(0, 100, 0))
            sl.setStrokeWidth(0.5)
        capa_resultado.triggerRepaint()

        # ----------------------------------------------------------------
        # CLEAR TOOL MARKERS (if `resetRubberBand` is exposed)
        # ----------------------------------------------------------------
        try:
            if self.dialog and self.dialog.pixelTool:
                self.dialog.pixelTool.resetRubberBand()
        except Exception:
            pass

        # ----------------------------------------------------------------
        # Close the dialog after 2 seconds
        # ----------------------------------------------------------------
        QTimer.singleShot(2000, self.dialog.close)
