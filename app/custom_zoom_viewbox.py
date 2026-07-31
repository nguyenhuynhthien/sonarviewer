from PyQt6.QtCore import Qt, QRectF
import pyqtgraph as pg

class CustomZoomViewBox(pg.ViewBox):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.setMouseMode(pg.ViewBox.RectMode)
        self.setDefaultPadding(0.0)
 
    def suggestPadding(self, axis):
        return 0.0
 
    def showAxRect(self, ax, **kwargs):
        """Override to force padding=0 when zooming with mouse drag (RectMode)"""
        kwargs['padding'] = 0
        super().showAxRect(ax, **kwargs)
 
    def mouseDragEvent(self, ev, axis=None):
        """Override to fix zoom: clip range to limits instead of allowing PyQtGraph to shift"""
        if self.state['mouseMode'] == pg.ViewBox.RectMode and ev.button() == Qt.MouseButton.LeftButton:
            if ev.isFinish():
                self.rbScaleBox.hide()
 
                p1 = self.mapSceneToView(ev.buttonDownScenePos())
                p2 = self.mapSceneToView(ev.scenePos())
 
                x_min = min(p1.x(), p2.x())
                x_max = max(p1.x(), p2.x())
                y_min = min(p1.y(), p2.y())
                y_max = max(p1.y(), p2.y())
 
                lim = self.state['limits']
                x_lo, x_hi = lim['xLimits']
                y_lo, y_hi = lim['yLimits']
                if x_lo is not None: x_min = max(x_min, x_lo)
                if x_hi is not None: x_max = min(x_max, x_hi)
                if y_lo is not None: y_min = max(y_min, y_lo)
                if y_hi is not None: y_max = min(y_max, y_hi)
 
                if abs(x_max - x_min) > 1e-3 and abs(y_max - y_min) > 1e-3:
                    self.setRange(xRange=(x_min, x_max), yRange=(y_min, y_max), padding=0)
                    self.axHistoryPointer += 1
                    self.axHistory = self.axHistory[:self.axHistoryPointer] + [QRectF(x_min, y_min, x_max - x_min, y_max - y_min)]
                ev.accept()
            else:
                self.updateScaleBox(ev.buttonDownPos(), ev.pos())
                ev.accept()
        else:
            super().mouseDragEvent(ev, axis=axis)
