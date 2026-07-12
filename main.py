import sys
import socket
import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget, QHBoxLayout, QLineEdit, QLabel, QCheckBox, QComboBox, QAbstractButton
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QPropertyAnimation, pyqtProperty, QPointF
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF
import pyqtgraph as pg

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

class RadarWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_angle = 90
        self.sweep_direction = 1  # 1 for CCW (increasing), -1 for CW (decreasing)
        self.history = {}  # Maps angle (int) -> numpy array of normalized intensities
        self.max_samples = 2048
        self.downsample_factor = 16  # 2048 / 16 = 128 bins
        self.targets = []  # List of (range, angle, strength)

    def add_target(self, range_val, angle, strength):
        self.targets.append((range_val, angle, strength))

    def set_data(self, angle, samples):
        if angle != self.current_angle:
            new_dir = 1 if angle > self.current_angle else -1
            if new_dir != self.sweep_direction:
                self.targets = []  # Clear targets on new scan sweep direction change
            self.sweep_direction = new_dir
        self.current_angle = angle

        # Calculate intensities: absolute deviation from median (baseline)
        baseline = np.median(samples)
        deviation = np.abs(samples - baseline)
        
        # Downsample to 128 bins for fast rendering
        downsampled = deviation.reshape(-1, self.downsample_factor).mean(axis=1)
        max_val = np.max(downsampled) if np.max(downsampled) > 0 else 1.0
        normalized = downsampled / max_val
        
        self.history[int(angle)] = normalized

        # Decay older sweeps (simulating phosphor decay)
        for d in list(self.history.keys()):
            if d != int(angle):
                self.history[d] = self.history[d] * 0.92
                if np.max(self.history[d]) < 0.02:
                    del self.history[d]

        self.update()

    def set_angle(self, angle):
        if angle != self.current_angle:
            new_dir = 1 if angle > self.current_angle else -1
            if new_dir != self.sweep_direction:
                self.targets = []
            self.sweep_direction = new_dir
        self.current_angle = angle

        # Decay older sweeps slowly even when idle
        for d in list(self.history.keys()):
            if d != int(angle):
                self.history[d] = self.history[d] * 0.95
                if np.max(self.history[d]) < 0.02:
                    del self.history[d]
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Draw deep dark space background
        painter.fillRect(self.rect(), QColor("#090d16"))
        
        # Center of the semi-circle is at the bottom center of the widget
        center_x = width // 2
        center_y = int(height * 0.9)
        
        # Maximum radius for the radar arcs
        max_radius = min(width // 2 - 40, int(height * 0.8))
        if max_radius < 50:
            return
            
        # Draw concentric arcs (representing range rings)
        grid_pen = QPen(QColor(0, 255, 100, 40), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        for i in range(1, 5):
            r = int(max_radius * i / 4)
            painter.drawArc(center_x - r, center_y - r, r * 2, r * 2, 0, 180 * 16)
            
        # Draw radial angle reference lines (every 30 degrees)
        for angle_deg in [30, 60, 90, 120, 150]:
            rad = np.radians(angle_deg)
            x = center_x + max_radius * np.cos(rad)
            y = center_y - max_radius * np.sin(rad)
            painter.drawLine(center_x, center_y, int(x), int(y))
            
        # Draw angle labels
        text_pen = QPen(QColor(0, 255, 100, 160))
        painter.setPen(text_pen)
        font = painter.font()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        
        for angle_deg in [0, 30, 60, 90, 120, 150, 180]:
            rad = np.radians(angle_deg)
            offset_x = -10 if angle_deg == 90 else (-25 if angle_deg > 90 else 5)
            offset_y = -10 if angle_deg in [0, 180] else -5
            x = center_x + (max_radius + 15) * np.cos(rad) + offset_x
            y = center_y - (max_radius + 15) * np.sin(rad) + offset_y
            painter.drawText(int(x), int(y), f"{angle_deg}°")
            
        # Draw range markers along the 90 degree axis
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        for i in range(1, 5):
            r = int(max_radius * i / 4)
            label = f"{int(2048 * i / 4)}"
            painter.drawText(center_x + 5, center_y - r - 2, label)

        # 1. Draw Sonar echo history using filled quadrilaterals to avoid gaps
        painter.setPen(Qt.PenStyle.NoPen)
        step_half = 1.5  # half of the 3-degree step size
        for deg, intensities in self.history.items():
            rad1 = np.radians(deg - step_half)
            rad2 = np.radians(deg + step_half)
            cos1, sin1 = np.cos(rad1), np.sin(rad1)
            cos2, sin2 = np.cos(rad2), np.sin(rad2)
            
            num_bins = len(intensities)
            for bin_idx in range(num_bins):
                intensity = intensities[bin_idx]
                if intensity > 0.02:
                    r_start = max_radius * bin_idx / num_bins
                    r_end = max_radius * (bin_idx + 1) / num_bins
                    
                    # Compute 4 vertices of the sector bin
                    p1_x = center_x + r_start * cos1
                    p1_y = center_y - r_start * sin1
                    p2_x = center_x + r_start * cos2
                    p2_y = center_y - r_start * sin2
                    p3_x = center_x + r_end * cos2
                    p3_y = center_y - r_end * sin2
                    p4_x = center_x + r_end * cos1
                    p4_y = center_y - r_end * sin1
                    
                    g = int(80 + 175 * intensity)
                    r_val = int(220 * (intensity ** 1.8))
                    alpha = int(140 * intensity)
                    
                    painter.setBrush(QColor(r_val, g, 40, alpha))
                    painter.drawPolygon(QPolygonF([
                        QPointF(p1_x, p1_y),
                        QPointF(p2_x, p2_y),
                        QPointF(p3_x, p3_y),
                        QPointF(p4_x, p4_y)
                    ]))
                    
        # 2. Draw smooth fading sweep wedge (30 degrees trail, 60 slices of 0.5 degrees)
        painter.setPen(Qt.PenStyle.NoPen)
        trail_dir = -self.sweep_direction
        num_slices = 60
        slice_width = 0.5  # degrees
        
        for i in range(num_slices):
            a1_deg = np.clip(self.current_angle + i * slice_width * trail_dir, 0.0, 180.0)
            a2_deg = np.clip(self.current_angle + (i + 1) * slice_width * trail_dir, 0.0, 180.0)
            
            a1 = np.radians(a1_deg)
            a2 = np.radians(a2_deg)
            
            p1_x = center_x + max_radius * np.cos(a1)
            p1_y = center_y - max_radius * np.sin(a1)
            p2_x = center_x + max_radius * np.cos(a2)
            p2_y = center_y - max_radius * np.sin(a2)
            
            # Smooth exponential alpha decay
            factor = (1.0 - i / num_slices) ** 2.0
            alpha = int(130 * factor)
            
            painter.setBrush(QColor(0, 255, 100, alpha))
            painter.drawPolygon(QPolygonF([
                QPointF(center_x, center_y),
                QPointF(p1_x, p1_y),
                QPointF(p2_x, p2_y)
            ]))
                    
        # 3. Draw active scanning beam line (front-most)
        sweep_rad = np.radians(self.current_angle)
        sweep_x = center_x + max_radius * np.cos(sweep_rad)
        sweep_y = center_y - max_radius * np.sin(sweep_rad)
        
        sweep_pen = QPen(QColor(0, 255, 100, 255), 2.5)
        painter.setPen(sweep_pen)
        painter.drawLine(center_x, center_y, int(sweep_x), int(sweep_y))
        
        # Draw origin center point
        painter.setBrush(QColor(0, 255, 100))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(center_x - 6, center_y - 6, 12, 12)

        # 4. Draw detected targets
        max_range = (2048 * 343.0) / (2.0 * 160000.0)  # ~2.1952 meters
        for r_val, a_val, s_val in self.targets:
            rad = np.radians(a_val)
            target_r = max_radius * (r_val / max_range)
            if target_r > max_radius:
                target_r = max_radius
            
            tx = center_x + target_r * np.cos(rad)
            ty = center_y - target_r * np.sin(rad)
            
            # Draw target as red circle
            painter.setPen(QPen(QColor(255, 38, 38, 255), 2))
            painter.setBrush(QColor(255, 69, 58, 200))
            painter.drawEllipse(QPointF(tx, ty), 6, 6)
            
            # Draw range text next to it
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 69, 58, 220)))
            painter.drawText(int(tx) + 8, int(ty) + 4, f"{r_val:.2f}m")

class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray, int)
    target_received = pyqtSignal(float, int, float)
    status_changed = pyqtSignal(str)

    def __init__(self, host='esp32.local', port=8080):
        super().__init__()
        self.host = host
        self.port = port
        self.running = False
        self.sock = None

    def run(self):
        try:
            self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            self.sock.settimeout(2.0)
            self.running = True

            # Register client port by sending a ping
            self.sock.sendto(b"ping", (self.host, self.port))
            self.status_changed.emit(f"Connected to {self.host}")

            CHUNK_HEADER_SIZE = 5
            CHUNK_SAMPLES = 512
            CHUNKS_PER_FRAME = 4
            CHUNK_PACKET_SIZE = CHUNK_HEADER_SIZE + CHUNK_SAMPLES * 2

            current_frame_id = None
            current_frame_angle = 90
            chunks = {}

            while self.running:
                try:
                    data, _addr = self.sock.recvfrom(65536)
                except socket.timeout:
                    continue

                if len(data) == CHUNK_PACKET_SIZE:
                    frame_id = data[0] | (data[1] << 8)
                    chunk_idx = data[2]
                    angle = data[3] | (data[4] << 8)
                    payload = data[CHUNK_HEADER_SIZE:]

                    if frame_id != current_frame_id:
                        current_frame_id = frame_id
                        chunks = {}

                    chunks[chunk_idx] = payload
                    current_frame_angle = angle

                    if len(chunks) == CHUNKS_PER_FRAME:
                        full = b"".join(chunks[i] for i in range(CHUNKS_PER_FRAME))
                        chunks = {}
                        current_frame_id = None

                        samples = np.frombuffer(full, dtype=np.uint16)
                        voltages = (samples / 4095.0) * 3.3
                        self.data_received.emit(voltages, current_frame_angle)

                elif data.startswith(b"ang:"):
                    try:
                        angle = int(data[4:])
                        self.data_received.emit(np.array([]), angle)
                    except ValueError:
                        pass

                elif data.startswith(b"target:"):
                    try:
                        parts = data[7:].decode('utf-8').split(',')
                        if len(parts) == 3:
                            t_range = float(parts[0])
                            t_angle = int(parts[1])
                            t_strength = float(parts[2])
                            self.target_received.emit(t_range, t_angle, t_strength)
                    except ValueError:
                        pass

        except Exception as e:
            self.status_changed.emit(f"Error: {e}")
        finally:
            if self.sock:
                try:
                    self.sock.sendto(b"stop", (self.host, self.port))
                    self.sock.close()
                except Exception:
                    pass
            self.running = False
            self.status_changed.emit("Disconnected")

    def send_command(self, cmd):
        if self.sock:
            try:
                self.sock.sendto(cmd.encode('utf-8'), (self.host, self.port))
            except Exception as e:
                print(f"Error sending command: {e}")

    def stop(self):
        self.running = False

class SonarViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SonarViewer GUI")
        self.showMaximized()

        # Layout chính dạng dọc
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(10, 10, 10, 10)
        main_layout.setSpacing(15)

        # 1. Phần hiển thị phía trên (Radar bên trái 2/3, Đồ thị bên phải 1/3)
        top_layout = QHBoxLayout()
        top_layout.setSpacing(15)
        main_layout.addLayout(top_layout, stretch=3)

        self.radar_widget = RadarWidget()
        top_layout.addWidget(self.radar_widget, stretch=2)

        # Đồ thị tín hiệu miền thời gian bên phải
        self.plot_widget = pg.PlotWidget(title="Received Signal (2048 samples)")
        self.plot_widget.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.plot_widget.setYRange(0, 3.3)
        self.plot_widget.setXRange(0, 2048)
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setLabel('bottom', 'Sample Index')
        self.plot_widget.showGrid(x=True, y=True)
        self.curve = self.plot_widget.plot(pen=pg.mkPen('y', width=1.5))
        top_layout.addWidget(self.plot_widget, stretch=1)

        # 2. Thanh điều khiển phía dưới (chia làm 2 dòng để vừa với màn hình MacBook)
        ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(10, 5, 10, 5)
        ctrl_layout.setSpacing(6)

        row1_layout = QHBoxLayout()
        row1_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row2_layout = QHBoxLayout()
        row2_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.ip_input = QLineEdit("esp32.local")
        self.ip_input.setFixedWidth(120)
        self.start_btn = QPushButton("Start Continuous")
        self.start_btn.clicked.connect(self.toggle_stream)
        
        self.single_btn = QPushButton("Single Shot")
        self.single_btn.clicked.connect(self.request_single)
        
        self.autorange_btn = QPushButton("Auto Range: OFF")
        self.autorange_btn.setCheckable(True)
        self.autorange_btn.clicked.connect(self.toggle_autorange)

        self.status_label = QLabel()
        self.update_status("Disconnected")

        self.pulse_type_combo = QComboBox()
        self.pulse_type_combo.addItems(["Single", "Barker13"])
        self.pulse_type_combo.currentIndexChanged.connect(self.change_pulse_type)

        self.signal_type_combo = QComboBox()
        self.signal_type_combo.addItems(["Raw Signal", "Demodulated", "Pulse Compressed"])
        self.signal_type_combo.currentIndexChanged.connect(self.change_signal_type)

        self.reset_zoom_btn = QPushButton("Reset Zoom")
        self.reset_zoom_btn.clicked.connect(self.reset_zoom)

        self.servo_switch = ToggleSwitch()
        self.servo_switch.clicked.connect(self.toggle_servo)

        # Dòng 1: Cấu hình kết nối và điều khiển
        row1_layout.addWidget(QLabel("ESP32 IP:"))
        row1_layout.addWidget(self.ip_input)
        row1_layout.addWidget(self.start_btn)
        row1_layout.addWidget(self.single_btn)
        row1_layout.addWidget(self.autorange_btn)
        row1_layout.addWidget(self.reset_zoom_btn)
        row1_layout.addWidget(QLabel("Run Servo:"))
        row1_layout.addWidget(self.servo_switch)
        
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #8E8E93; font-style: italic; margin-right: 15px;")

        # Dòng 2: Cấu hình tín hiệu và trạng thái hiển thị
        row2_layout.addWidget(QLabel("Pulse Type:"))
        row2_layout.addWidget(self.pulse_type_combo)
        row2_layout.addWidget(QLabel("Signal Stream:"))
        row2_layout.addWidget(self.signal_type_combo)
        row2_layout.addStretch()
        row2_layout.addWidget(self.info_label)
        row2_layout.addWidget(self.status_label)

        ctrl_layout.addLayout(row1_layout)
        ctrl_layout.addLayout(row2_layout)
        
        main_layout.addWidget(ctrl_widget, stretch=1)

        self.receiver = None
        self.is_streaming = False
        self.is_single_shot = False

        # Start continuous receiver thread on startup
        self.get_receiver()

    def update_status(self, status):
        if status.startswith("Connected"):
            self.status_label.setText("<span style='color: #4CD964; font-size: 16px;'>●</span> Connected")
            self.status_label.setToolTip(status)
        elif status.startswith("Disconnected"):
            self.status_label.setText("<span style='color: #FF3B30; font-size: 16px;'>●</span> Disconnected")
            self.status_label.setToolTip("")
        else:
            self.status_label.setText(f"<span style='color: #FF9500; font-size: 16px;'>●</span> {status}")
            self.status_label.setToolTip(status)

    def get_receiver(self):
        host = self.ip_input.text()
        if not self.receiver or self.receiver.host != host:
            if self.receiver:
                self.receiver.stop()
                self.receiver.wait()
            self.receiver = DataReceiver(host=host)
            self.receiver.data_received.connect(self.update_plot)
            self.receiver.target_received.connect(self.update_target)
            self.receiver.status_changed.connect(self.update_status)
            self.receiver.start()
        return self.receiver

    def reset_zoom(self):
        self.plot_widget.setYRange(0, 3.3)
        self.plot_widget.setXRange(0, 2048)

    def change_pulse_type(self):
        pulse_type = self.pulse_type_combo.currentText().lower()
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        self.info_label.setText(f"Config sent: {pulse_type}")

    def change_signal_type(self):
        idx = self.signal_type_combo.currentIndex()
        if idx == 0:
            mode = "raw"
        elif idx == 1:
            mode = "demod"
        else:
            mode = "compressed"
        self.get_receiver().send_command(f"mode:{mode}")
        self.info_label.setText(f"Mode command sent: mode:{mode}")

    def update_target(self, range_val, angle, strength):
        self.radar_widget.add_target(range_val, angle, strength)
        self.info_label.setText(f"Target: {range_val:.2f} m | Angle: {angle}° | Strength: {strength:.1f}")

    def toggle_servo(self, checked=None):
        state = self.servo_switch.isChecked()
        cmd = "servo:on" if state else "servo:off"
        self.get_receiver().send_command(cmd)
        self.info_label.setText(f"Servo command sent: {cmd}")

    def toggle_autorange(self):
        if self.autorange_btn.isChecked():
            self.autorange_btn.setText("Auto Range: ON")
            self.plot_widget.enableAutoRange(axis='y', enable=True)
        else:
            self.autorange_btn.setText("Auto Range: OFF")
            self.plot_widget.setYRange(0, 3.3)

    def request_single(self):
        self.is_single_shot = True
        self.get_receiver().send_command("start")
        self.is_streaming = True
        self.start_btn.setText("Stop")

    def toggle_stream(self):
        receiver = self.get_receiver()
        if self.is_streaming:
            receiver.send_command("stop")
            self.is_streaming = False
            self.start_btn.setText("Start Continuous")
        else:
            self.is_single_shot = False
            receiver.send_command("start")
            self.is_streaming = True
            self.start_btn.setText("Stop")

    def update_plot(self, samples, angle):
        if len(samples) > 0:
            self.radar_widget.set_data(angle, samples)
            self.curve.setData(samples)
            
            if self.is_single_shot:
                self.get_receiver().send_command("stop")
                self.is_streaming = False
                self.is_single_shot = False
                self.start_btn.setText("Start Continuous")
        else:
            self.radar_widget.set_angle(angle)

    def closeEvent(self, event):
        if self.receiver:
            self.receiver.stop()
            self.receiver.wait()
        event.accept()

if __name__ == "__main__":
    app = QApplication(sys.argv)
    window = SonarViewer()
    window.show()
    sys.exit(app.exec())
