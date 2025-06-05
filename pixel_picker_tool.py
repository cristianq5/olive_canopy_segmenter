# -*- coding: utf-8 -*-
from qgis.gui import QgsMapToolEmitPoint, QgsRubberBand
from qgis.core import QgsWkbTypes, QgsGeometry, QgsPointXY
from qgis.PyQt.QtGui import QColor

class PixelPickerTool(QgsMapToolEmitPoint):
    """
    Tool that inherits from QgsMapToolEmitPoint to:
      - Draw a small marker at each click.
      - Call a callback method with coordinates (x_geo, y_geo).
    """
    def __init__(self, canvas, callback):
        """
        :param canvas: QgsMapCanvas where clicks are captured
        :param callback: function that receives (x_geo, y_geo) per click
        """
        super().__init__(canvas)
        self.canvas = canvas
        self.onPointClicked = callback

        # Create a rubberBand to draw points (POINT geometry type)
        self.rubberBand = QgsRubberBand(self.canvas, QgsWkbTypes.PointGeometry)
        self.rubberBand.setColor(QColor(0, 255, 0, 200))  # semi-transparent green
        self.rubberBand.setIconSize(6)
        self.rubberBand.setWidth(2)

    def canvasPressEvent(self, event):
        # Get geographic coordinates from the click
        qgs_point = self.toMapCoordinates(event.pos())
        x_geo = qgs_point.x()
        y_geo = qgs_point.y()

        # Call the callback so the dialog can process the point
        self.onPointClicked(x_geo, y_geo)

        # Draw the point (add to rubberBand)
        geo_pt = QgsGeometry.fromPointXY(QgsPointXY(x_geo, y_geo))
        self.rubberBand.addPoint(QgsPointXY(x_geo, y_geo))

    def resetRubberBand(self):
        """
        Clear all drawn points.
        Called when starting a new sample collection.
        """
        self.rubberBand.reset(QgsWkbTypes.PointGeometry)
