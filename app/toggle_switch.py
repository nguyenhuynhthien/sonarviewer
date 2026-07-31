from PyQt6.QtWidgets import QAbstractButton
from PyQt6.QtGui import QPainter, QColor
from PyQt6.QtCore import Qt, QPropertyAnimation, pyqtProperty

class ToggleSwitch(QAbstractButton):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setCheckable(True)
        self.setFixedSize(50, 26)
        self._pos = 3
        self._anim = QPropertyAnimation(self, b"pos")
        self._anim.setDuration(120)
 
    @pyqtProperty(int)
    def pos(self):
        return self._pos
 
    @pos.setter
    def pos(self, p):
        self._pos = p
        self.update()
 
    def mouseReleaseEvent(self, event):
        super().mouseReleaseEvent(event)
        self.animate(self.isChecked())
 
    def nextCheckState(self):
        super().nextCheckState()
        self.animate(self.isChecked())
 
    def animate(self, checked):
        self._anim.stop()
        if checked:
            self._anim.setEndValue(27)
        else:
            self._anim.setEndValue(3)
        self._anim.start()
 
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        # Draw background
        bg_color = QColor("#4CD964") if self.isChecked() else QColor("#D1D1D6")
        painter.setBrush(bg_color)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(self.rect(), 13, 13)
        
        # Draw handle
        painter.setBrush(QColor("#FFFFFF"))
        painter.drawEllipse(self._pos, 3, 20, 20)
