import sys
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import QMainWindow, QVBoxLayout, QPushButton, QWidget, QHBoxLayout, QLineEdit, QLabel, QCheckBox, QComboBox
from PyQt6.QtCore import Qt

from constants import (
    DEFAULT_HOST, DEFAULT_PORT, MAX_SAMPLES,
    PLOT_Y_MIN, PLOT_Y_MAX_RX0, PLOT_Y_MAX_RX12_RAW_DEMOD, PLOT_Y_MAX_RX12_COMPRESSED,
    PLOT_DEFAULT_Y_MAX_RX0, PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD, PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED,
    ACTIVE_SIGNAL_START_IDX
)
from app.custom_zoom_viewbox import CustomZoomViewBox
from app.toggle_switch import ToggleSwitch
from app.radar_widget import RadarWidget
from service.data_receiver import DataReceiver
from service.signal_processor import convert_samples_to_voltages, calculate_snr, shift_voltages

class SonarViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SonarViewer GUI")
        self.showMaximized()
        self.current_y_max = 0.01
        self.current_y_max0 = 0.01
        self.latest_voltages = None

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
        self.radar_widget.angle_requested.connect(self.send_servo_angle)
        top_layout.addWidget(self.radar_widget, stretch=2)

        # Đồ thị tín hiệu miền thời gian bên phải
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        self.plot_widget = pg.PlotWidget(title="Rx 0 (Sum Channel) Received Signal", viewBox=CustomZoomViewBox())
        self.plot_widget.getViewBox().setMouseEnabled(x=True, y=True)
        self.plot_widget.getViewBox().setAspectLocked(False)
        self.plot_widget.getViewBox().setDefaultPadding(0)
        self.plot_widget.getViewBox().setLimits(xMin=0, xMax=MAX_SAMPLES, yMin=PLOT_Y_MIN, yMax=PLOT_Y_MAX_RX0, minXRange=0, minYRange=0)
        self.plot_widget.setYRange(0, PLOT_DEFAULT_Y_MAX_RX0, padding=0)
        self.plot_widget.setXRange(0, MAX_SAMPLES, padding=0)
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setLabel('bottom', 'Sample Index')
        self.plot_widget.showGrid(x=True, y=True)
        self.curve = self.plot_widget.plot(pen=pg.mkPen('c', width=1.5))
        right_layout.addWidget(self.plot_widget)

        # Lắng nghe sự kiện thay đổi vùng nhìn của biểu đồ
        self.plot_widget.getViewBox().sigRangeChanged.connect(self._on_plot_range_changed)
        self._is_updating_plot = False

        top_layout.addLayout(right_layout, stretch=1)

        # SNR Label overlaying the plot widget
        self.snr_label = QLabel("SNR: -- dB", self.plot_widget)
        self.snr_label.setStyleSheet("color: #4CD964; background-color: rgba(9, 13, 22, 200); border: 1px solid rgba(0, 255, 100, 100); padding: 3px 6px; border-radius: 4px; font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 11px; font-weight: bold;")
        self.snr_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.snr_label.setFixedWidth(90)
        self.snr_label.setFixedHeight(22)
        self.snr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 2. Thanh điều khiển phía dưới
        ctrl_widget = QWidget()
        ctrl_layout = QVBoxLayout(ctrl_widget)
        ctrl_layout.setContentsMargins(10, 5, 10, 5)
        ctrl_layout.setSpacing(6)

        row1_layout = QHBoxLayout()
        row1_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row2_layout = QHBoxLayout()
        row2_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        self.ip_input = QLineEdit(DEFAULT_HOST)
        self.ip_input.setFixedWidth(120)
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self.toggle_pause)
        
        self.single_btn = QPushButton("Single Shot")
        self.single_btn.clicked.connect(self.request_single)
        
        self.status_label = QLabel()
        self.update_status("Disconnected")

        self.pulse_type_combo = QComboBox()
        self.pulse_type_combo.addItems(["Single", "Barker13"])
        self.pulse_type_combo.activated.connect(self.change_pulse_type)

        self.signal_type_combo = QComboBox()
        self.signal_type_combo.addItems(["Raw", "Demodulated", "Compressed"])
        self.signal_type_combo.activated.connect(self.change_signal_type)

        self.tx_atten_combo = QComboBox()
        self.tx_atten_combo.addItems(["0 dB", "-6 dB", "-12 dB", "-18 dB", "-24 dB", "Mute"])
        self.tx_atten_combo.activated.connect(self.change_tx_attenuation)

        self.autoscale_cb = QCheckBox("Auto Scale")
        self.autoscale_cb.setChecked(True)
        self.autoscale_cb.stateChanged.connect(self.toggle_autoscale_cb)

        self.reset_zoom_btn = QPushButton("Reset Zoom")
        self.reset_zoom_btn.clicked.connect(self.reset_zoom)

        self.servo_switch = ToggleSwitch()
        self.servo_switch.clicked.connect(self.toggle_servo)

        self.tx_switch = ToggleSwitch()
        self.tx_switch.clicked.connect(self.toggle_tx)

        # Group Tx On label and switch closer in a QWidget
        tx_widget = QWidget()
        tx_layout = QHBoxLayout(tx_widget)
        tx_layout.setSpacing(5)
        tx_layout.setContentsMargins(0, 0, 0, 0)
        tx_layout.addWidget(QLabel("Tx On:"))
        tx_layout.addWidget(self.tx_switch)
        tx_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Group Servo label and switch closer in a QWidget
        servo_widget = QWidget()
        servo_layout = QHBoxLayout(servo_widget)
        servo_layout.setSpacing(5)
        servo_layout.setContentsMargins(0, 0, 0, 0)
        servo_layout.addWidget(QLabel("Run Servo:"))
        servo_layout.addWidget(self.servo_switch)
        servo_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Dòng 1: Cấu hình kết nối và điều khiển
        row1_layout.addWidget(QLabel("ESP32 IP:"))
        row1_layout.addWidget(self.ip_input)
        row1_layout.addWidget(self.pause_btn)
        row1_layout.addWidget(self.single_btn)
        row1_layout.addWidget(self.autoscale_cb)
        row1_layout.addWidget(self.reset_zoom_btn)
        row1_layout.addSpacing(15)
        row1_layout.addWidget(tx_widget)
        row1_layout.addSpacing(15)
        row1_layout.addWidget(servo_widget)
        
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #8E8E93; font-style: italic; margin-right: 15px;")

        self.rx_select_combo = QComboBox()
        self.rx_select_combo.addItems(["Rx 0 (Sum Channel)", "Rx 1 (GPIO 32)", "Rx 2 (GPIO 33)"])
        self.rx_select_combo.activated.connect(self.change_rx_channel)

        # Dòng 2: Cấu hình tín hiệu và trạng thái hiển thị
        row2_layout.addWidget(QLabel("Rx Select:"))
        row2_layout.addWidget(self.rx_select_combo)
        row2_layout.addWidget(QLabel("Pulse Type:"))
        row2_layout.addWidget(self.pulse_type_combo)
        row2_layout.addWidget(QLabel("Signal Stream:"))
        row2_layout.addWidget(self.signal_type_combo)
        row2_layout.addWidget(QLabel("Tx Attenuation:"))
        row2_layout.addWidget(self.tx_atten_combo)
        row2_layout.addStretch()
        row2_layout.addWidget(self.info_label)
        row2_layout.addWidget(self.status_label)

        ctrl_layout.addLayout(row1_layout)
        ctrl_layout.addLayout(row2_layout)
        
        main_layout.addWidget(ctrl_widget, stretch=1)

        self.receiver = None
        self.is_streaming = True
        self.is_paused = False
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
        if not self.receiver or self.receiver.host != host or not self.receiver.isRunning():
            if self.receiver:
                self.receiver.stop()
                self.receiver.wait()
            
            # Gather current UI configs to send immediately on connection
            pulse_type = self.pulse_type_combo.currentText().lower()
            idx = self.signal_type_combo.currentIndex()
            mode = "raw" if idx == 0 else ("demod" if idx == 1 else "compressed")
            servo_cmd = "servo:on" if self.servo_switch.isChecked() else "servo:off"
            self.radar_widget.servo_enabled = self.servo_switch.isChecked()
            
            txt = self.tx_atten_combo.currentText()
            atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
            atten_cmd = f"tx_atten:{atten_val}"
            tx_cmd = "tx:on" if self.tx_switch.isChecked() else "tx:off"

            rx_chan = self.rx_select_combo.currentIndex()
            rx_select_cmd = f"rx_select:{rx_chan}"

            initial_configs = [f"cfg:{pulse_type}", f"mode:{mode}", servo_cmd, atten_cmd, tx_cmd, rx_select_cmd, "servo:90", "start"]

            self.receiver = DataReceiver(host=host, port=DEFAULT_PORT, initial_configs=initial_configs)
            self.receiver.pulse_type = pulse_type
            self.receiver.data_received.connect(self.update_plot)
            self.receiver.target_received.connect(self.update_target)
            self.receiver.status_changed.connect(self.update_status)
            self.receiver.start()
        return self.receiver

    def send_servo_angle(self, angle):
        if not self.servo_switch.isChecked():
            self.get_receiver().send_command(f"servo:{angle}")

    def change_tx_attenuation(self):
        txt = self.tx_atten_combo.currentText()
        atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
        cmd = f"tx_atten:{atten_val}"
        self.get_receiver().send_command(cmd)
        self.info_label.setText(f"Tx attenuation command sent: {cmd}")

    def toggle_tx(self):
        state = self.tx_switch.isChecked()
        cmd = "tx:on" if state else "tx:off"
        self.get_receiver().send_command(cmd)
        self.info_label.setText(f"Tx switch command sent: {cmd}")

    def send_all_configs(self):
        # 1. Pulse Type
        pulse_type = self.pulse_type_combo.currentText().lower()
        self.get_receiver().pulse_type = pulse_type
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        
        # 2. Signal Stream
        idx = self.signal_type_combo.currentIndex()
        mode = "raw" if idx == 0 else ("demod" if idx == 1 else "compressed")
        self.get_receiver().send_command(f"mode:{mode}")
        
        # 3. Servo State
        servo_cmd = "servo:on" if self.servo_switch.isChecked() else "servo:off"
        self.get_receiver().send_command(servo_cmd)
        
        # 4. Tx Attenuation
        txt = self.tx_atten_combo.currentText()
        atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
        self.get_receiver().send_command(f"tx_atten:{atten_val}")
        
        # 5. Tx Switch State
        tx_cmd = "tx:on" if self.tx_switch.isChecked() else "tx:off"
        self.get_receiver().send_command(tx_cmd)
        
        self.info_label.setText(f"Initial configs sent: cfg:{pulse_type} | mode:{mode} | {servo_cmd} | tx_atten:{atten_val} | {tx_cmd}")

    def change_rx_channel(self):
        rx_chan = self.rx_select_combo.currentIndex()
        if rx_chan == 0:
            title = "Rx 0 (Sum Channel) Received Signal"
            pen_color = 'c'
        elif rx_chan == 1:
            title = "Rx 1 (GPIO 32) Received Signal"
            pen_color = 'y'
        else:
            title = "Rx 2 (GPIO 33) Received Signal"
            pen_color = 'm'
            
        self.plot_widget.setTitle(title)
        self.curve.setPen(pg.mkPen(pen_color, width=1.5))
        
        idx = self.signal_type_combo.currentIndex()
        if rx_chan == 0:
            y_lim = PLOT_Y_MAX_RX0
            default_y = PLOT_DEFAULT_Y_MAX_RX0
        else:
            y_lim = PLOT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_Y_MAX_RX12_RAW_DEMOD
            default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
            
        self.plot_widget.getViewBox().setLimits(xMin=0, xMax=MAX_SAMPLES, yMin=PLOT_Y_MIN, yMax=y_lim, minXRange=0, minYRange=0)
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else default_y
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self.plot_widget.setXRange(0, MAX_SAMPLES, padding=0)
        self._is_updating_plot = False

        self.get_receiver().send_command(f"rx_select:{rx_chan}")
        self.info_label.setText(f"Rx channel select command sent: rx_select:{rx_chan}")

    def _on_plot_range_changed(self):
        if not self._is_updating_plot:
            if self.autoscale_cb.isChecked():
                self.autoscale_cb.blockSignals(True)
                self.autoscale_cb.setChecked(False)
                self.autoscale_cb.blockSignals(False)

    def reset_zoom(self):
        idx = self.signal_type_combo.currentIndex()
        rx_chan = self.rx_select_combo.currentIndex()
        if rx_chan == 0:
            default_y = PLOT_DEFAULT_Y_MAX_RX0
        else:
            default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else default_y
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self.plot_widget.setXRange(0, MAX_SAMPLES, padding=0)
        self._is_updating_plot = False
        self.radar_widget.reset_zoom()

    def toggle_autoscale_cb(self, state):
        if self.autoscale_cb.isChecked():
            self.current_y_max = 0.01
        else:
            idx = self.signal_type_combo.currentIndex()
            rx_chan = self.rx_select_combo.currentIndex()
            if rx_chan == 0:
                default_y = PLOT_DEFAULT_Y_MAX_RX0
            else:
                default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
            self.current_y_max = default_y
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self._is_updating_plot = False

    def change_pulse_type(self):
        pulse_type = self.pulse_type_combo.currentText().lower()
        self.get_receiver().pulse_type = pulse_type
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        self.info_label.setText(f"Config sent: {pulse_type}")
        idx = self.signal_type_combo.currentIndex()
        rx_chan = self.rx_select_combo.currentIndex()
        if rx_chan == 0:
            default_y = PLOT_DEFAULT_Y_MAX_RX0
        else:
            default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else default_y
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self._is_updating_plot = False

    def change_signal_type(self):
        idx = self.signal_type_combo.currentIndex()
        rx_chan = self.rx_select_combo.currentIndex()
        if rx_chan == 0:
            mode = "raw" if idx == 0 else ("demod" if idx == 1 else "compressed")
            y_lim = PLOT_Y_MAX_RX0
            default_y = PLOT_DEFAULT_Y_MAX_RX0
        else:
            mode = "raw" if idx == 0 else ("demod" if idx == 1 else "compressed")
            y_lim = PLOT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_Y_MAX_RX12_RAW_DEMOD
            default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
        
        self.plot_widget.getViewBox().setLimits(xMin=0, xMax=MAX_SAMPLES, yMin=PLOT_Y_MIN, yMax=y_lim, minXRange=0, minYRange=0)
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else default_y
        
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self._is_updating_plot = False
        self.get_receiver().send_command(f"mode:{mode}")
        self.info_label.setText(f"Mode command sent: mode:{mode}")

    def update_target(self, range_val, angle, strength, velocity, receiver_id=0):
        if self.is_paused:
            return
        if receiver_id != self.rx_select_combo.currentIndex():
            return
            
        angle_int = int(angle)
        clean_angle = angle_int & 0x7FFF

        if not hasattr(self, '_smooth_velocity'):
            self._smooth_velocity = {}
        if receiver_id not in self._smooth_velocity:
            self._smooth_velocity[receiver_id] = velocity
        else:
            self._smooth_velocity[receiver_id] = velocity
            
        disp_velocity = self._smooth_velocity[receiver_id]

        self.radar_widget.add_target(range_val, clean_angle, strength, disp_velocity)
        if receiver_id == 0:
            self.info_label.setText(f"Sum Channel Target: {range_val:.2f} m | Angle: {clean_angle}° | Strength: {strength:.1f} dBV | Velocity: {disp_velocity:+.2f} m/s")
        else:
            self.info_label.setText(f"Rx {receiver_id} Target: {range_val:.2f} m | Angle: {clean_angle}° | Strength: {strength:.1f} dBV | Velocity: {disp_velocity:+.2f} m/s")

    def toggle_servo(self, checked=None):
        state = self.servo_switch.isChecked()
        self.radar_widget.servo_enabled = state
        cmd = "servo:on" if state else "servo:off"
        self.get_receiver().send_command(cmd)
        self.info_label.setText(f"Servo command sent: {cmd}")

    def request_single(self):
        self.is_single_shot = True
        self.get_receiver().send_command("start")
        self.is_streaming = True
        self.is_paused = False
        self.pause_btn.setText("Pause")

    def toggle_pause(self):
        receiver = self.get_receiver()
        if not self.is_paused:
            receiver.send_command("stop")
            self.is_paused = True
            self.pause_btn.setText("Resume")
            self.info_label.setText("Streaming paused.")
        else:
            self.is_paused = False
            receiver.send_command("start")
            self.pause_btn.setText("Pause")
            self.info_label.setText("Streaming resumed.")

    def update_plot(self, samples, angle, receiver_id=0):
        if self.is_paused:
            return
            
        # If this is Rx0, we update radar history sweep from Sum channel even if not displaying plot
        if receiver_id != self.rx_select_combo.currentIndex():
            if receiver_id == 0 and len(samples) > 0:
                voltages = convert_samples_to_voltages(samples, receiver_id, self.signal_type_combo.currentIndex())
                pulse_type = self.pulse_type_combo.currentText().lower()
                shifted_voltages = shift_voltages(voltages, pulse_type)
                self.radar_widget.set_data(angle, shifted_voltages)
            return

        if len(samples) > 0:
            stream_idx = self.signal_type_combo.currentIndex()
            voltages = convert_samples_to_voltages(samples, receiver_id, stream_idx)
            self.latest_voltages = voltages

            # Calculate SNR
            pulse_type = self.pulse_type_combo.currentText().lower()
            tx_on = self.tx_switch.isChecked()
            calibrated_snr = calculate_snr(voltages, pulse_type, tx_on, receiver_id, stream_idx)
            
            if calibrated_snr is not None and calibrated_snr > 1.0:
                snr_str = f"SNR: {calibrated_snr:.1f} dB"
            else:
                snr_str = "SNR: -- dB"

            self.snr_label.setText(snr_str)
            
            # Shift voltages to align radar history
            shifted_voltages = shift_voltages(voltages, pulse_type)
            
            if receiver_id == 0:
                self.radar_widget.set_data(angle, shifted_voltages)
                self.curve.setData(voltages)
            elif receiver_id == 1:
                self.radar_widget.set_angle(angle)
                self.curve.setData(voltages)
            elif receiver_id == 2:
                self.curve.setData(voltages)
            
            # Peak Hold Auto Scale
            if self.autoscale_cb.isChecked() and len(voltages) > ACTIVE_SIGNAL_START_IDX:
                active_voltages = voltages[ACTIVE_SIGNAL_START_IDX:]
                valid_samples = active_voltages[np.isfinite(active_voltages)]
                if len(valid_samples) > 0:
                    peak = np.max(valid_samples)
                    if np.isfinite(peak):
                        max_cap = PLOT_DEFAULT_Y_MAX_RX0 if (receiver_id == 0 or stream_idx == 2) else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
                        target_y_max = min(max(peak * 1.15, 0.01), max_cap)
                        if target_y_max > self.current_y_max:
                            self.current_y_max = target_y_max
                            self._is_updating_plot = True
                            self.plot_widget.setYRange(0, self.current_y_max, padding=0)
                            self._is_updating_plot = False
            
            if self.is_single_shot:
                self.get_receiver().send_command("stop")
                self.is_streaming = False
                self.is_single_shot = False
                self.is_paused = True
                self.pause_btn.setText("Resume")
        else:
            self.radar_widget.set_angle(angle)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.reposition_snr_labels()

    def reposition_snr_labels(self):
        if not hasattr(self, 'snr_label') or not self.plot_widget:
            return
        self.snr_label.move(self.plot_widget.width() - 100, 10)

    def closeEvent(self, event):
        if self.receiver:
            self.receiver.stop()
            self.receiver.wait()
        event.accept()
