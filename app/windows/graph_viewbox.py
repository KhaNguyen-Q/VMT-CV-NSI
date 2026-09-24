"""Shared pyqtgraph ViewBox with axis-selective zoom/pan (Ctrl=X, Shift=Y)."""

from PyQt5.QtCore import Qt
import pyqtgraph as pg


class GraphViewBox(pg.ViewBox):
    def _axis_from_modifiers(self, modifiers):
        if modifiers & Qt.ControlModifier:
            return 0
        if modifiers & Qt.ShiftModifier:
            return 1
        return None

    def wheelEvent(self, event, axis=None):
        if axis is None:
            axis = self._axis_from_modifiers(event.modifiers())
        super().wheelEvent(event, axis=axis)

    def mouseDragEvent(self, event, axis=None):
        if axis is None and event.button() == Qt.RightButton:
            axis = self._axis_from_modifiers(event.modifiers())
        super().mouseDragEvent(event, axis=axis)
