import sys
import socket
import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget, QHBoxLayout, QLineEdit, QLabel, QCheckBox, QComboBox
from PyQt6.QtCore import QThread, pyqtSignal, Qt
import pyqtgraph as pg

class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray)
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

            # Khởi động stream (UDP: gửi lệnh "start" để ESP32 ghi nhận địa chỉ của ta)
            self.sock.sendto(b"start", (self.host, self.port))
            self.status_changed.emit(f"Streaming from {self.host}")

            # ESP32 chia mỗi khung 2048 mẫu thành các gói UDP NHỎ (dưới MTU) để tránh bị
            # phân mảnh IP - mỗi gói gồm: [frame_id: 2 byte][chunk_index: 1 byte][dữ liệu].
            CHUNK_HEADER_SIZE = 3
            CHUNK_SAMPLES = 512
            CHUNKS_PER_FRAME = 4  # 2048 / 512
            CHUNK_PACKET_SIZE = CHUNK_HEADER_SIZE + CHUNK_SAMPLES * 2  # 1027 bytes

            # Chỉ giữ các chunk của 1 khung (frame_id) đang ghép dở - khung cũ hơn bị huỷ
            current_frame_id = None
            chunks = {}

            while self.running:
                try:
                    data, _addr = self.sock.recvfrom(65536)
                except socket.timeout:
                    continue

                if len(data) != CHUNK_PACKET_SIZE:
                    # Không phải gói dữ liệu ADC (vd: bản tin text heartbeat) - bỏ qua
                    continue

                frame_id = data[0] | (data[1] << 8)
                chunk_idx = data[2]
                payload = data[CHUNK_HEADER_SIZE:]

                if frame_id != current_frame_id:
                    # Khung mới bắt đầu -> huỷ phần dở dang của khung trước (nếu có)
                    current_frame_id = frame_id
                    chunks = {}

                chunks[chunk_idx] = payload

                if len(chunks) == CHUNKS_PER_FRAME:
                    full = b"".join(chunks[i] for i in range(CHUNKS_PER_FRAME))
                    chunks = {}
                    current_frame_id = None

                    # Chuyển đổi dữ liệu sang numpy array uint16 (ESP32 gửi Little Endian)
                    samples = np.frombuffer(full, dtype=np.uint16)

                    # Chuyển đổi từ giá trị ADC (0-4095) sang Điện áp (0-3.3V)
                    voltages = (samples / 4095.0) * 3.3
                    self.data_received.emit(voltages)

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

    def stop(self):
        self.running = False



class SonarViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SonarViewer GUI")
        self.resize(1000, 600)

        # Layout chính
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        layout = QVBoxLayout(central_widget)

        # Thanh điều khiển
        ctrl_layout = QHBoxLayout()
        self.ip_input = QLineEdit("esp32.local")
        self.start_btn = QPushButton("Start Continuous")
        self.start_btn.clicked.connect(self.toggle_stream)
        
        self.single_btn = QPushButton("Single Shot")
        self.single_btn.clicked.connect(self.request_single)
        
        # (Removed Software Trigger checkbox)
        
        self.autorange_btn = QPushButton("Auto Range: OFF")
        self.autorange_btn.setCheckable(True)
        self.autorange_btn.clicked.connect(self.toggle_autorange)

        self.status_label = QLabel("Status: Disconnected")

        self.pulse_type_combo = QComboBox()
        self.pulse_type_combo.addItems(["Single", "Barker13"])
        self.pulse_type_combo.currentIndexChanged.connect(self.change_pulse_type)

        self.reset_zoom_btn = QPushButton("Reset Zoom")
        self.reset_zoom_btn.clicked.connect(self.reset_zoom)

        ctrl_layout.addWidget(QLabel("ESP32 IP:"))
        ctrl_layout.addWidget(self.ip_input)
        ctrl_layout.addWidget(self.start_btn)
        ctrl_layout.addWidget(self.single_btn)
        ctrl_layout.addWidget(self.autorange_btn)
        ctrl_layout.addWidget(self.reset_zoom_btn)
        ctrl_layout.addWidget(QLabel("Pulse Type:"))
        ctrl_layout.addWidget(self.pulse_type_combo)
        ctrl_layout.addStretch()
        ctrl_layout.addWidget(self.status_label)
        layout.addLayout(ctrl_layout)

        # Đồ thị
        self.plot_widget = pg.PlotWidget(title="Received Signal (2048 samples)")
        self.plot_widget.getViewBox().setMouseMode(pg.ViewBox.RectMode) # Kích hoạt chế độ quét chọn vùng để zoom
        self.plot_widget.setYRange(0, 3.3) # Đổi sang thang đo Volt (0-3.3V)
        self.plot_widget.setXRange(0, 2048)
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setLabel('bottom', 'Sample Index')
        self.plot_widget.showGrid(x=True, y=True)
        self.curve = self.plot_widget.plot(pen=pg.mkPen('y', width=1.5))
        layout.addWidget(self.plot_widget)

        self.receiver = None
        self.is_single_shot = False

    def reset_zoom(self):
        self.plot_widget.setYRange(0, 3.3)
        self.plot_widget.setXRange(0, 2048)

    def change_pulse_type(self):
        pulse_type = self.pulse_type_combo.currentText().lower() # "single" or "barker13"
        host = self.ip_input.text()
        try:
            sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            message = f"cfg:{pulse_type}".encode('utf-8')
            sock.sendto(message, (host, 8080))
            sock.close()
            self.status_label.setText(f"Config sent: {pulse_type}")
        except Exception as e:
            self.status_label.setText(f"Send config error: {e}")

    def toggle_autorange(self):
        if self.autorange_btn.isChecked():
            self.autorange_btn.setText("Auto Range: ON")
            self.plot_widget.enableAutoRange(axis='y', enable=True)
        else:
            self.autorange_btn.setText("Auto Range: OFF")
            self.plot_widget.setYRange(0, 3.3)

    def request_single(self):
        self.is_single_shot = True
        if not (self.receiver and self.receiver.isRunning()):
            self.start_stream()

    def toggle_stream(self):
        if self.receiver and self.receiver.isRunning():
            self.stop_stream()
        else:
            self.is_single_shot = False
            self.start_stream()

    def start_stream(self):
        host = self.ip_input.text()
        self.receiver = DataReceiver(host=host)
        self.receiver.data_received.connect(self.update_plot)
        self.receiver.status_changed.connect(self.status_label.setText)
        self.receiver.start()
        self.start_btn.setText("Stop")

    def stop_stream(self):
        if self.receiver:
            self.receiver.stop()
        self.start_btn.setText("Start Continuous")

    def update_plot(self, samples):
        # Tính toán thông số cơ bản (trên dữ liệu gốc)
        baseline = np.median(samples)
        noise_std = np.std(samples)
        deviation = samples - baseline

        peak_idx = int(np.argmax(np.abs(deviation)))
        peak_val = float(np.abs(deviation[peak_idx]))

        # Cập nhật đồ thị (hiển thị trực tiếp dữ liệu thô nhận được từ ESP32)
        self.curve.setData(samples)

        # Nếu là Single Shot, dừng sau 1 lần nhận dữ liệu
        if self.is_single_shot:
            self.stop_stream()
            self.is_single_shot = False

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
