import sys
import os
import json
import numpy as np
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QMainWindow, QVBoxLayout, QWidget, QHBoxLayout, QLabel,
    QTabWidget, QScrollArea, QPlainTextEdit, QCheckBox, QPushButton,
    QComboBox, QLineEdit
)
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QTextCursor

from constants import (
    MAX_SAMPLES, DISPLAY_SAMPLE_COUNT, FS,
    PLOT_Y_MIN, PLOT_Y_MAX_RX0, PLOT_Y_MAX_RX12_RAW_DEMOD, PLOT_Y_MAX_RX12_COMPRESSED,
    PLOT_DEFAULT_Y_MAX_RX0, PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD, PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED,
    ACTIVE_SIGNAL_START_IDX
)
from app.custom_zoom_viewbox import CustomZoomViewBox
from app.radar_widget import RadarWidget
from app.control_panel import ControlPanel
from app.toggle_switch import ToggleSwitch
from service.data_receiver import DataReceiver, UartReceiver
from service.signal_processor import convert_samples_to_voltages, calculate_snr, shift_voltages, compute_spectrum


def format_vietnamese(value, precision=0):
    """Định dạng số theo kiểu Việt Nam: dấu chấm ngăn cách phần nghìn, dấu phẩy thập phân."""
    if isinstance(value, float):
        formatted = f"{value:,.{precision}f}"
        parts = formatted.split('.')
        left = parts[0].replace(',', '.')
        if len(parts) > 1:
            return left + ',' + parts[1]
        return left
    else:
        return f"{value:,}".replace(',', '.')


class SonarViewer(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("SonarViewer GUI")
        self.resize(1280, 800)
        self.current_y_max = 0.01
        self.current_y_max0 = 0.01
        self.latest_voltages = None
        self.latest_snr_str = "SNR: -- dB"
        self.is_spectrum_mode = False
        self._last_sent_servo_angle = -1

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

        # Đồ thị tín hiệu bên phải
        right_layout = QVBoxLayout()
        right_layout.setSpacing(6)

        # Thanh chuyển đổi chế độ đồ thị (Time Domain / Spectrum FFT)
        plot_header_layout = QHBoxLayout()
        plot_header_layout.setContentsMargins(4, 0, 4, 0)
        
        spectrum_title = QLabel("Spectrum (FFT):")
        spectrum_title.setStyleSheet("color: #000000; font-size: 13px; font-weight: bold; margin-right: 4px;")
        
        self.spectrum_switch = ToggleSwitch()
        self.spectrum_switch.setToolTip("Bật/tắt hiển thị phổ tần số FFT của tín hiệu")
        self.spectrum_switch.clicked.connect(self.toggle_spectrum)
        
        plot_header_layout.addStretch(1)
        plot_header_layout.addWidget(spectrum_title)
        plot_header_layout.addWidget(self.spectrum_switch)
        
        right_layout.addLayout(plot_header_layout)

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

        signal_tab = QWidget()
        signal_tab.setLayout(right_layout)
        
        # 1. Telemetry Tab
        telemetry_tab = QWidget()
        telemetry_layout = QVBoxLayout(telemetry_tab)
        self.telemetry_label = QPlainTextEdit("No telemetry received")
        self.telemetry_label.setReadOnly(True)
        self.telemetry_label.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        self.telemetry_label.setMaximumBlockCount(2000)
        self.telemetry_label.setStyleSheet("font-family: Menlo, Monaco, monospace; font-size: 14px; padding: 12px;")
        self.telemetry_autoscroll = QCheckBox("Auto-scroll")
        self.telemetry_autoscroll.setChecked(True)
        telemetry_layout.addWidget(self.telemetry_autoscroll)
        telemetry_layout.addWidget(self.telemetry_label, stretch=1)
        self.signal_tabs = QTabWidget()
        self.signal_tabs.addTab(signal_tab, "Signal")
        self.signal_tabs.addTab(telemetry_tab, "Telemetry")
        top_layout.addWidget(self.signal_tabs, stretch=2)

        # SNR Label overlaying the plot widget
        self.snr_label = QLabel("SNR: -- dB", self.plot_widget)
        self.snr_label.setStyleSheet("color: #4CD964; background-color: rgba(9, 13, 22, 200); border: 1px solid rgba(0, 255, 100, 100); padding: 3px 6px; border-radius: 4px; font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 11px; font-weight: bold;")
        self.snr_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.snr_label.setFixedWidth(90)
        self.snr_label.setFixedHeight(22)
        self.snr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # 2. Thanh điều khiển phía dưới
        self.control_panel = ControlPanel()
        self.load_settings()
        main_layout.addWidget(self.control_panel, stretch=1)

        # Kết nối các tín hiệu từ ControlPanel
        self.control_panel.ip_changed.connect(self._on_ip_changed)
        self.control_panel.pause_toggled.connect(self.toggle_pause)
        self.control_panel.single_shot_clicked.connect(self.request_single)
        self.control_panel.pulse_type_changed.connect(self.change_pulse_type)
        self.control_panel.signal_type_changed.connect(self.change_signal_type)
        self.control_panel.tx_atten_changed.connect(self.change_tx_attenuation)
        self.control_panel.rx_channel_changed.connect(self.change_rx_channel)
        self.control_panel.autoscale_toggled.connect(self.toggle_autoscale_cb)
        self.control_panel.reset_zoom_clicked.connect(self.reset_zoom)
        self.control_panel.servo_toggled.connect(self.toggle_servo)
        self.control_panel.tx_toggled.connect(self.toggle_tx)
        self.control_panel.capture_clicked.connect(self.toggle_capture)
        self.control_panel.capture_prev_clicked.connect(self.prev_captured_pulse)
        self.control_panel.capture_next_clicked.connect(self.next_captured_pulse)

        self.receiver = None
        self.is_streaming = True
        self.is_paused = False
        self.is_single_shot = False
        
        # Capture state
        self.is_capturing = False
        self.in_capture_browse_mode = False
        self.captured_pulses = []
        self.capture_current_idx = -1

        # Buffer và Timer cho Telemetry để tránh giật lag UI và kẹt scrollbar
        self.telemetry_buffer = []
        self.telemetry_timer = QTimer(self)
        self.telemetry_timer.setInterval(100)  # Cập nhật UI mỗi 100ms
        self.telemetry_timer.timeout.connect(self.flush_telemetry)
        self.telemetry_timer.start()

        # Khởi động Receiver (DataReceiver qua UART 6 Mbps)
        self.get_receiver()

    def get_receiver(self):
        if not self.receiver or not self.receiver.isRunning():
            if self.receiver:
                self.receiver.stop()
                self.receiver.wait()
            self.radar_widget.servo_enabled = self.control_panel.servo_switch.isChecked()
            self.receiver = DataReceiver()
            self.receiver.data_received.connect(self.update_plot)
            self.receiver.telemetry_received.connect(self.update_telemetry)
            self.receiver.debug_received.connect(self.update_debug)
            self.receiver.dsp_received.connect(self.update_dsp_log)
            self.receiver.bytes_received.connect(self.update_bytes_received)
            self.receiver.target_received.connect(self.update_target)
            self.receiver.status_changed.connect(self._on_status_changed)
            self.receiver.start()
            self.send_all_configs()
        return self.receiver

    def _on_status_changed(self, status):
        self.control_panel.update_status(status)

    def _on_ip_changed(self, text):
        pass

    def update_telemetry(self, sequence, adc1_fs_hz, adc1_pri_us, adc2_fs_hz, adc2_pri_us, dac_fs_hz, dac_pri_us):
        self.telemetry_buffer.append(
            f"Sequence: {format_vietnamese(sequence)}\n"
            f"ADC1 sampling rate: {format_vietnamese(adc1_fs_hz)} Hz\n"
            f"ADC1 PRI: {format_vietnamese(adc1_pri_us)} us\n"
            f"ADC2 sampling rate: {format_vietnamese(adc2_fs_hz)} Hz\n"
            f"ADC2 PRI: {format_vietnamese(adc2_pri_us)} us\n"
            f"DAC sampling rate: {format_vietnamese(dac_fs_hz)} Hz\n"
            f"DAC PRI: {format_vietnamese(dac_pri_us)} us\n\n"
        )

    def update_dsp_log(self, sequence, total_us, read_us, bpf_us, demod_us, mfilt_us, send_us, accum_us, detect_us=0):
        self.telemetry_buffer.append(
            f"--- DSP Log #{format_vietnamese(sequence)} ---\n"
            f"Total DSP time: {format_vietnamese(total_us)} us\n"
            f"Read ADC: {format_vietnamese(read_us)} us\n"
            f"BPF Filter: {format_vietnamese(bpf_us)} us\n"
            f"Matched Filter: {format_vietnamese(mfilt_us)} us\n"
            f"Accumulate: {format_vietnamese(accum_us)} us\n"
            f"Target Detect: {format_vietnamese(detect_us)} us\n"
            f"Send Data: {format_vietnamese(send_us)} us\n\n"
        )

    def update_debug(self, counter, tick_ms, adc_count, dac_count, timer_counter, timer_enabled, registers, diagnostics):
        self.telemetry_buffer.append(
            f"USB heartbeat received\n"
            f"Debug counter: {format_vietnamese(counter)}\n"
            f"Firmware tick: {format_vietnamese(tick_ms)} ms\n\n"
            f"ADC DMA completed: {format_vietnamese(adc_count)}\n"
            f"DAC DMA completed: {format_vietnamese(dac_count)}\n\n"
            f"TIM6 CNT: {format_vietnamese(timer_counter)}\n"
            f"TIM6 enabled: {timer_enabled}\n"
            f"ADC CR/CFGR/ISR: {[hex(value) for value in registers[:3]]}\n"
            f"DAC CR: {hex(registers[3])}\n"
            f"DMA0 CCR/CSR: {[hex(value) for value in registers[4:6]]}\n"
            f"DMA1 CCR/CSR: {[hex(value) for value in registers[6:8]]}\n"
            f"DMA0 CLBAR/CLLR/CBR1: {[hex(value) for value in registers[8:11]]}\n"
            f"DMA1 CLBAR/CLLR/CBR1: {[hex(value) for value in registers[11:14]]}\n"
            f"ADC SQR1/SMPR1/DR: {[hex(value) for value in registers[14:17]]}\n"
            f"DMA0 CTR1/CTR2/CTR3/CDSR: {[hex(value) for value in registers[17:21]]}\n"
            f"ADC CFGR2: {hex(registers[21])}\n"
            f"TIM6 CR1/CR2/ARR/PSC: {[hex(value) for value in registers[22:26]]}\n"
            f"ADC overruns: {format_vietnamese(diagnostics[0])}\n"
            f"ADC DMA errors: {format_vietnamese(diagnostics[1])}\n"
            f"ADC restarts: {format_vietnamese(diagnostics[2])}\n"
            f"DMA0 CTR3: {hex(diagnostics[3])}\n"
            f"ADC IER: {hex(diagnostics[4])}\n"
            f"ADC frame min/max: {[hex(value) for value in diagnostics[5:7]]}\n"
            f"DMA0 CDAR/CLLR: {[hex(value) for value in diagnostics[7:8]]}\n\n"
        )

    def flush_telemetry(self):
        if not self.telemetry_buffer:
            return
        
        combined_text = "".join(self.telemetry_buffer)
        self.telemetry_buffer.clear()

        if self.telemetry_label.toPlainText() == "No telemetry received":
            self.telemetry_label.clear()

        self.telemetry_label.appendPlainText(combined_text)

        if self.telemetry_autoscroll.isChecked():
            self.telemetry_label.moveCursor(QTextCursor.MoveOperation.End)
            self.telemetry_label.ensureCursorVisible()

    def update_bytes_received(self, count):
        self._usb_bytes_received = getattr(self, "_usb_bytes_received", 0) + count
        if "No telemetry received" in self.telemetry_label.toPlainText():
            self.telemetry_label.setPlainText(
                f"USB bytes received: {format_vietnamese(self._usb_bytes_received)}\n"
                "No recognized telemetry frame yet"
            )

    def send_servo_angle(self, angle):
        if not self.control_panel.servo_switch.isChecked():
            if angle != self._last_sent_servo_angle:
                self._last_sent_servo_angle = angle
                self.get_receiver().send_command(f"servo:{angle}")

    def change_tx_attenuation(self, txt):
        atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
        cmd = f"tx_atten:{atten_val}"
        self.get_receiver().send_command(cmd)
        self.control_panel.info_label.setText(f"Tx attenuation command sent: {cmd}")

    def toggle_tx(self, state):
        cmd = "tx:on" if state else "tx:off"
        self.get_receiver().send_command(cmd)
        self.control_panel.info_label.setText(f"Tx switch command sent: {cmd}")

    def send_all_configs(self):
        # 1. Pulse Type
        pulse_type = self.control_panel.pulse_type_combo.currentText().lower()
        self.get_receiver().pulse_type = pulse_type
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        
        # 2. Signal Stream
        idx = self.control_panel.signal_type_combo.currentIndex()
        modes = ["raw", "bpf", "compressed"]
        mode = modes[idx] if idx < len(modes) else "raw"
        self.get_receiver().send_command(f"mode:{mode}")
        
        # 3. Servo State
        servo_cmd = "servo:on" if self.control_panel.servo_switch.isChecked() else "servo:off"
        self.get_receiver().send_command(servo_cmd)
        
        # 4. Tx Attenuation
        txt = self.control_panel.tx_atten_combo.currentText()
        atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
        self.get_receiver().send_command(f"tx_atten:{atten_val}")
        
        # 5. Tx Switch State
        tx_cmd = "tx:on" if self.control_panel.tx_switch.isChecked() else "tx:off"
        self.get_receiver().send_command(tx_cmd)
        
        # 6. Rx Select Channel
        rx_chan = self.control_panel.rx_select_combo.currentIndex()
        channel_map = {0: 0, 1: 3, 2: 1, 3: 2}
        actual_rx = channel_map.get(rx_chan, 0)
        self.get_receiver().send_command(f"rx_select:{actual_rx}")
        
        self.control_panel.info_label.setText(f"Initial configs sent: cfg:{pulse_type} | mode:{mode} | {servo_cmd} | tx_atten:{atten_val} | {tx_cmd} | rx_select:{rx_chan}")

    def update_plot_style(self):
        rx_chan = self.control_panel.rx_select_combo.currentIndex()
        actual_rx = {0: 0, 1: 3, 2: 1, 3: 2}.get(rx_chan, 0)
        idx = self.control_panel.signal_type_combo.currentIndex()

        if actual_rx == 0:
            base_title = "Rx Sum Received Signal"
            pen_color = 'c'
        elif actual_rx == 3:
            base_title = "Rx Diff Received Signal"
            pen_color = 'g'
        elif actual_rx == 1:
            base_title = "Rx 1 (GPIO 32) Received Signal"
            pen_color = 'y'
        else:
            base_title = "Rx 2 (GPIO 33) Received Signal"
            pen_color = 'm'

        self.curve.setPen(pg.mkPen(pen_color, width=1.5))

        if self.is_spectrum_mode:
            max_freq_khz = (FS / 2.0) / 1000.0
            self.plot_widget.setTitle(f"{base_title} - Frequency Spectrum (FFT)")
            self.plot_widget.setLabel('left', 'Magnitude')
            self.plot_widget.setLabel('bottom', 'Frequency', units='kHz')
            self.plot_widget.getViewBox().setLimits(xMin=0, xMax=max_freq_khz, yMin=0, yMax=50000.0, minXRange=0, minYRange=0)
            self._is_updating_plot = True
            self.plot_widget.setXRange(0, max_freq_khz, padding=0)
            self.current_y_max = 0.01
            self._is_updating_plot = False
        else:
            self.plot_widget.setTitle(base_title)
            self.plot_widget.setLabel('left', 'Voltage', units='V')
            self.plot_widget.setLabel('bottom', 'Sample Index')

            if actual_rx in (0, 3):
                y_lim = PLOT_Y_MAX_RX0
                default_y = PLOT_DEFAULT_Y_MAX_RX0
            else:
                y_lim = PLOT_Y_MAX_RX12_COMPRESSED if idx == 3 else PLOT_Y_MAX_RX12_RAW_DEMOD
                default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 3 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD

            self.plot_widget.getViewBox().setLimits(xMin=0, xMax=MAX_SAMPLES, yMin=PLOT_Y_MIN, yMax=y_lim, minXRange=0, minYRange=0)
            self.current_y_max = 0.01 if self.control_panel.autoscale_cb.isChecked() else default_y
            self._is_updating_plot = True
            self.plot_widget.setXRange(0, MAX_SAMPLES, padding=0)
            self.plot_widget.setYRange(0, self.current_y_max, padding=0)
            self._is_updating_plot = False

    def toggle_spectrum(self):
        self.is_spectrum_mode = self.spectrum_switch.isChecked()
        self.current_y_max = 0.01
        self.update_plot_style()
        if self.latest_voltages is not None and len(self.latest_voltages) > 0:
            if self.is_spectrum_mode:
                freqs, mags = compute_spectrum(self.latest_voltages)
                self.curve.setData(freqs, mags)
                if len(mags) > 0:
                    peak_idx = np.argmax(mags)
                    self.snr_label.setText(f"Peak: {freqs[peak_idx]:.1f} kHz")
                    if self.control_panel.autoscale_cb.isChecked():
                        target_y_max = max(float(mags[peak_idx]) * 1.25, 0.1)
                        self.current_y_max = target_y_max
                        self._is_updating_plot = True
                        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
                        self._is_updating_plot = False
            else:
                self.curve.setData(self.latest_voltages)
                self.snr_label.setText(self.latest_snr_str)

    def change_rx_channel(self, rx_chan):
        # Map combo index to actual channel ID
        channel_map = {0: 0, 1: 3, 2: 1, 3: 2}
        actual_rx = channel_map.get(rx_chan, 0)

        # Enforce Compressed stream mode for Rx Sum (0) and Rx Diff (3)
        if actual_rx in (0, 3):
            self.control_panel.signal_type_combo.setCurrentIndex(2) # Compressed
            self.control_panel.signal_type_combo.setEnabled(False)
            self.get_receiver().send_command("mode:compressed")
        else:
            self.control_panel.signal_type_combo.setEnabled(True)

        self.update_plot_style()
        self.get_receiver().send_command(f"rx_select:{actual_rx}")
        self.control_panel.info_label.setText(f"Rx channel select command sent: rx_select:{actual_rx}")

    def _on_plot_range_changed(self):
        if not self._is_updating_plot:
            if self.control_panel.autoscale_cb.isChecked():
                self.control_panel.autoscale_cb.blockSignals(True)
                self.control_panel.autoscale_cb.setChecked(False)
                self.control_panel.autoscale_cb.blockSignals(False)

    def reset_zoom(self):
        if self.is_spectrum_mode:
            self.current_y_max = 0.01
        self.update_plot_style()
        self.radar_widget.reset_zoom()

    def toggle_autoscale_cb(self, checked):
        if checked:
            self.current_y_max = 0.01
            if self.is_spectrum_mode and self.latest_voltages is not None and len(self.latest_voltages) > 0:
                _, mags = compute_spectrum(self.latest_voltages)
                if len(mags) > 0:
                    peak_idx = np.argmax(mags)
                    self.current_y_max = max(float(mags[peak_idx]) * 1.25, 0.1)
        else:
            if self.is_spectrum_mode:
                if self.latest_voltages is not None and len(self.latest_voltages) > 0:
                    _, mags = compute_spectrum(self.latest_voltages)
                    if len(mags) > 0:
                        self.current_y_max = max(float(np.max(mags)) * 1.25, 10.0)
                    else:
                        self.current_y_max = 10.0
                else:
                    self.current_y_max = 10.0
            else:
                idx = self.control_panel.signal_type_combo.currentIndex()
                rx_chan = self.control_panel.rx_select_combo.currentIndex()
                actual_rx = {0: 0, 1: 3, 2: 1, 3: 2}.get(rx_chan, 0)
                if actual_rx in (0, 3):
                    default_y = PLOT_DEFAULT_Y_MAX_RX0
                else:
                    default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 2 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
                self.current_y_max = default_y
        self._is_updating_plot = True
        self.plot_widget.setYRange(0, self.current_y_max, padding=0)
        self._is_updating_plot = False

    def change_pulse_type(self, pulse_type_text):
        pulse_type = pulse_type_text.lower()
        self.get_receiver().pulse_type = pulse_type
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        self.control_panel.info_label.setText(f"Config sent: {pulse_type}")
        self.update_plot_style()

    def change_signal_type(self, idx):
        modes = ["raw", "bpf", "compressed"]
        mode = modes[idx] if idx < len(modes) else "raw"
        self.update_plot_style()
        self.get_receiver().send_command(f"mode:{mode}")
        self.control_panel.info_label.setText(f"Mode command sent: mode:{mode}")

    def update_target(self, range_val, angle, strength, velocity):
        if self.is_paused:
            return
            
        angle_int = int(angle)
        clean_angle = angle_int & 0x7FFF
        disp_velocity = velocity

        self.radar_widget.add_target(range_val, clean_angle, strength, disp_velocity)
        self.control_panel.info_label.setText(f"Target: {range_val:4.2f} m | Angle: {clean_angle:3d}° | Strength: {strength:5.1f} dBV | Velocity: {disp_velocity:+5.2f} m/s")

    def toggle_servo(self, state):
        self.radar_widget.servo_enabled = state
        cmd = "servo:on" if state else "servo:off"
        self.get_receiver().send_command(cmd)
        self.control_panel.info_label.setText(f"Servo command sent: {cmd}")

    def request_single(self):
        self.is_single_shot = True
        self.get_receiver().send_command("start")
        self.is_streaming = True
        self.is_paused = False
        self.control_panel.set_paused_state(False)

    def toggle_pause(self, paused):
        receiver = self.get_receiver()
        self.is_paused = paused
        if paused:
            receiver.send_command("stop")
            self.control_panel.info_label.setText("Streaming paused.")
        else:
            receiver.send_command("start")
            self.control_panel.info_label.setText("Streaming resumed.")

    def toggle_capture(self):
        if self.is_capturing or self.in_capture_browse_mode:
            # Exit capture mode
            self.is_capturing = False
            self.in_capture_browse_mode = False
            self.captured_pulses = []
            self.capture_current_idx = -1
            self.control_panel.set_capture_idle()
            # Resume streaming if it was paused by capture finishing
            if self.is_paused:
                self.toggle_pause(False)
                self.control_panel.set_paused_state(False)
        else:
            # Start capture mode
            self.is_capturing = True
            self.in_capture_browse_mode = False
            self.captured_pulses = []
            self.capture_current_idx = -1
            self.control_panel.set_capturing_status(0, 10)
            # Resume streaming if paused so we can capture
            if self.is_paused:
                self.toggle_pause(False)
                self.control_panel.set_paused_state(False)

    def show_captured_pulse(self, idx):
        if not self.captured_pulses or idx < 0 or idx >= len(self.captured_pulses):
            return
        self.capture_current_idx = idx
        pulse = self.captured_pulses[idx]
        voltages = pulse['voltages']
        
        # Update curve and label
        if self.is_spectrum_mode:
            freqs, mags = compute_spectrum(voltages)
            self.curve.setData(freqs, mags)
            if len(mags) > 0:
                peak_idx = np.argmax(mags)
                self.snr_label.setText(f"Peak: {freqs[peak_idx]:.1f} kHz")
                if self.control_panel.autoscale_cb.isChecked():
                    target_y_max = max(float(mags[peak_idx]) * 1.25, 0.1)
                    self.current_y_max = target_y_max
                    self._is_updating_plot = True
                    self.plot_widget.setYRange(0, self.current_y_max, padding=0)
                    self._is_updating_plot = False
        else:
            self.curve.setData(voltages)
            self.snr_label.setText(pulse['snr_str'])
            # Peak Hold Auto Scale for captured pulse
            if self.control_panel.autoscale_cb.isChecked() and len(voltages) > ACTIVE_SIGNAL_START_IDX:
                active_voltages = voltages[ACTIVE_SIGNAL_START_IDX:]
                valid_samples = active_voltages[np.isfinite(active_voltages)]
                if len(valid_samples) > 0:
                    peak = np.max(valid_samples)
                    if np.isfinite(peak):
                        max_cap = PLOT_DEFAULT_Y_MAX_RX0 if (pulse['receiver_id'] in (0, 3) or pulse['stream_idx'] == 2) else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
                        target_y_max = min(max(peak * 1.15, 0.01), max_cap)
                        if target_y_max > self.current_y_max:
                            self.current_y_max = target_y_max
                            self._is_updating_plot = True
                            self.plot_widget.setYRange(0, self.current_y_max, padding=0)
                            self._is_updating_plot = False
        
        # Update radar widget
        receiver_id = pulse['receiver_id']
        angle = pulse['angle']
        shifted_voltages = pulse['shifted_voltages']
        if receiver_id in (0, 3):
            self.radar_widget.set_data(angle, shifted_voltages)
        else:
            self.radar_widget.set_angle(angle)
            
        # Update info text
        self.control_panel.info_label.setText(pulse['info_text'])
        
        # Update control panel browse label
        self.control_panel.set_capture_browse(idx + 1, len(self.captured_pulses))

    def prev_captured_pulse(self):
        if not self.in_capture_browse_mode or not self.captured_pulses:
            return
        new_idx = (self.capture_current_idx - 1) % len(self.captured_pulses)
        self.show_captured_pulse(new_idx)

    def next_captured_pulse(self):
        if not self.in_capture_browse_mode or not self.captured_pulses:
            return
        new_idx = (self.capture_current_idx + 1) % len(self.captured_pulses)
        self.show_captured_pulse(new_idx)

    def update_plot(self, samples, angle, receiver_id=0):
        if len(samples) == 0:
            self.radar_widget.set_angle(angle)
            return

        if self.is_paused:
            return

        # Check expected rx channel
        channel_map = {0: 0, 1: 3, 2: 1, 3: 2}
        expected_rx = channel_map.get(self.control_panel.rx_select_combo.currentIndex(), 0)
        if receiver_id != expected_rx:
            return
            
        if len(samples) > 0:
            stream_idx = self.control_panel.signal_type_combo.currentIndex()
            voltages = convert_samples_to_voltages(samples, receiver_id, stream_idx)
            self.latest_voltages = voltages
            display_voltages = voltages[:DISPLAY_SAMPLE_COUNT]
 
            # Calculate SNR
            pulse_type = self.control_panel.pulse_type_combo.currentText().lower()
            tx_on = self.control_panel.tx_switch.isChecked()
            calibrated_snr = calculate_snr(voltages, pulse_type, tx_on, receiver_id, stream_idx)
            
            if calibrated_snr is not None:
                snr_str = f"SNR: {calibrated_snr:.1f} dB"
            else:
                snr_str = "SNR: -- dB"
            self.latest_snr_str = snr_str
            
            # Shift voltages to align radar history
            shifted_voltages = shift_voltages(voltages, pulse_type)
            
            if self.is_capturing:
                pulse_data = {
                    'voltages': voltages,
                    'angle': angle,
                    'receiver_id': receiver_id,
                    'snr_str': snr_str,
                    'shifted_voltages': shifted_voltages,
                    'info_text': self.control_panel.info_label.text(),
                    'stream_idx': stream_idx
                }
                self.captured_pulses.append(pulse_data)
                self.control_panel.set_capturing_status(len(self.captured_pulses), 10)
                
                if len(self.captured_pulses) >= 10:
                    self.is_capturing = False
                    self.in_capture_browse_mode = True
                    self.capture_current_idx = 0
                    self.show_captured_pulse(0)
                    self.toggle_pause(True)
                    self.control_panel.set_paused_state(True)
                    return  # Stop processing further for this pulse update
            
            if receiver_id in (0, 3):
                self.radar_widget.set_data(angle, shifted_voltages)
            else:
                self.radar_widget.set_angle(angle)
            
            # Display either frequency spectrum or time domain
            if self.is_spectrum_mode:
                freqs, mags = compute_spectrum(voltages)
                self.curve.setData(freqs, mags)
                if len(mags) > 0:
                    peak_idx = np.argmax(mags)
                    self.snr_label.setText(f"Peak: {freqs[peak_idx]:.1f} kHz")
                    if self.control_panel.autoscale_cb.isChecked():
                        peak = float(mags[peak_idx])
                        if np.isfinite(peak) and peak > 0:
                            target_y_max = max(peak * 1.25, 0.1)
                            if abs(target_y_max - self.current_y_max) > 0.05 or self.current_y_max == 0.01:
                                self.current_y_max = target_y_max
                                self._is_updating_plot = True
                                self.plot_widget.setYRange(0, self.current_y_max, padding=0)
                                self._is_updating_plot = False
            else:
                self.curve.setData(display_voltages)
                self.snr_label.setText(snr_str)
                # Peak Hold Auto Scale
                if self.control_panel.autoscale_cb.isChecked() and len(display_voltages) > ACTIVE_SIGNAL_START_IDX:
                    active_voltages = display_voltages[ACTIVE_SIGNAL_START_IDX:]
                    valid_samples = active_voltages[np.isfinite(active_voltages)]
                    if len(valid_samples) > 0:
                        peak = np.max(valid_samples)
                        if np.isfinite(peak):
                            max_cap = PLOT_DEFAULT_Y_MAX_RX0 if (receiver_id in (0, 3) or stream_idx == 2) else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
                            target_y_max = min(max(peak * 1.15, 0.01), max_cap)
                            if receiver_id in (0, 3):
                                target_y_max = max(target_y_max, 0.05)
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
                self.control_panel.set_paused_state(True)
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
        self.save_settings()
        if self.receiver:
            self.receiver.stop()
            self.receiver.wait()
        event.accept()

    def get_settings_filepath(self):
        # Lưu file settings.json tại thư mục chứa main.py (SonarViewer)
        base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        return os.path.join(base_dir, "settings.json")

    def load_settings(self):
        filepath = self.get_settings_filepath()
        if os.path.exists(filepath):
            try:
                with open(filepath, "r", encoding="utf-8") as f:
                    settings = json.load(f)
                    self.control_panel.set_settings(settings)
            except Exception as e:
                print(f"Error loading settings: {e}")

    def save_settings(self):
        filepath = self.get_settings_filepath()
        try:
            settings = self.control_panel.get_settings()
            with open(filepath, "w", encoding="utf-8") as f:
                json.dump(settings, f, indent=4, ensure_ascii=False)
        except Exception as e:
            print(f"Error saving settings: {e}")

