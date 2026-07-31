import sys
import os

# Ensure current directory is in python search path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from PyQt6.QtWidgets import QApplication
from app.main_window import SonarViewer

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SonarViewer()
    window.show()
    sys.exit(app.exec())
