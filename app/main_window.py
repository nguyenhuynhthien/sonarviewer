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
    MAX_SAMPLES, DISPLAY_SAMPLE_COUNT, FS, SAMPLE_COUNT, DOWNSAMPLED_BINS, MAX_RANGE,
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

        # Heatmap 2D PlotWidget cho Range-Doppler Map (Trục X: Doppler, Trục Y: Range, Colorbar: dB)
        self.rd_plot_widget = pg.PlotWidget(title="Range-Doppler Response")
        self.rd_plot_widget.setLabel('bottom', 'Doppler', units='kHz')
        self.rd_plot_widget.setLabel('left', 'Range', units='m')
        self.rd_plot_widget.showGrid(x=True, y=True, alpha=0.3)
        self.rd_plot_widget.getViewBox().setAspectLocked(False)
        self.rd_plot_widget.getViewBox().invertY(False)

        self.rd_image_item = pg.ImageItem()
        self.rd_plot_widget.addItem(self.rd_image_item)

        # Thanh màu ColorBarItem bên phải hiển thị thang đo 'Amplitude (dB)' [-45 dB -> 0 dB]
        try:
            cmap = pg.colormap.get('turbo')
        except Exception:
            cmap = pg.colormap.get('viridis')

        self.rd_colorbar = pg.ColorBarItem(
            values=(-45.0, 0.0),
            colorMap=cmap,
            label='Amplitude (dB)',
            limits=(-60.0, 10.0),
            rounding=1
        )
        self.rd_colorbar.setImageItem(self.rd_image_item, insert_in=self.rd_plot_widget.getPlotItem())

        self.rd_plot_widget.setVisible(False)
        right_layout.addWidget(self.rd_plot_widget)

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

        # SNR Label overlaying the Range-Doppler plot widget
        self.rd_snr_label = QLabel("SNR: -- dB", self.rd_plot_widget)
        self.rd_snr_label.setStyleSheet("color: #4CD964; background-color: rgba(9, 13, 22, 200); border: 1px solid rgba(0, 255, 100, 100); padding: 3px 6px; border-radius: 4px; font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 11px; font-weight: bold;")
        self.rd_snr_label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.rd_snr_label.setFixedWidth(90)
        self.rd_snr_label.setFixedHeight(22)
        self.rd_snr_label.setAlignment(Qt.AlignmentFlag.AlignCenter)

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
            self.receiver.text_log_received.connect(self.update_text_log)
            self.receiver.bytes_received.connect(self.update_bytes_received)
            self.receiver.target_received.connect(self.update_target)
            self.receiver.status_changed.connect(self._on_status_changed)
            self.receiver.port_connected.connect(self.send_all_configs)
            self.receiver.start()
        return self.receiver

    def _on_status_changed(self, status):
        self.control_panel.update_status(status)

    def _on_ip_changed(self, text):
        pass

    def update_text_log(self, text):
        # Đẩy trực tiếp log text (Panic, Backtrace, Boot log) vào buffer hiển thị
        if self.telemetry_label.toPlainText() == "No telemetry received" or "No recognized telemetry frame yet" in self.telemetry_label.toPlainText():
            self.telemetry_label.clear()
        self.telemetry_buffer.append(text)

    def update_telemetry(self, sequence, adc1_fs_hz, adc1_pri_us, adc2_fs_hz, adc2_pri_us, dac_fs_hz, dac_pri_us):
        # Phát hiện kit STM32 vừa bị Reset (sequence nhảy lùi về 0 hoặc nhỏ)
        last_seq = getattr(self, "_last_telemetry_sequence", 0)
        if sequence < last_seq and sequence <= 2:
            self.send_all_configs()
        self._last_telemetry_sequence = sequence

        self.telemetry_buffer.append(
            f"Sequence: {format_vietnamese(sequence)}\n"
            f"ADC1 sampling rate: {format_vietnamese(adc1_fs_hz)} Hz\n"
            f"ADC1 PRI: {format_vietnamese(adc1_pri_us)} us\n"
            f"ADC2 sampling rate: {format_vietnamese(adc2_fs_hz)} Hz\n"
            f"ADC2 PRI: {format_vietnamese(adc2_pri_us)} us\n"
            f"DAC sampling rate: {format_vietnamese(dac_fs_hz)} Hz\n"
            f"DAC PRI: {format_vietnamese(dac_pri_us)} us\n\n"
        )

    def update_dsp_log(self, sequence, total_us, read_us, bpf_us, demod_us, mfilt_us, send_us, ds_us, rd_matrix_us=0):
        self.telemetry_buffer.append(
            f"--- DSP Log #{format_vietnamese(sequence)} (Every 64 Pulses) ---\n"
            f"Total DSP time: {format_vietnamese(total_us)} us\n"
            f"Read ADC: {format_vietnamese(read_us)} us\n"
            f"BPF Filter: {format_vietnamese(bpf_us)} us\n"
            f"Demodulation: {format_vietnamese(demod_us)} us\n"
            f"DownSampling: {format_vietnamese(ds_us)} us\n"
            f"Matched Filter: {format_vietnamese(mfilt_us)} us\n"
            f"Range-Doppler Matrix: {format_vietnamese(rd_matrix_us)} us\n"
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

        if self.telemetry_label.toPlainText() == "No telemetry received" or "No recognized telemetry frame yet" in self.telemetry_label.toPlainText():
            self.telemetry_label.clear()

        self.telemetry_label.appendPlainText(combined_text)

        if self.telemetry_autoscroll.isChecked():
            self.telemetry_label.moveCursor(QTextCursor.MoveOperation.End)
            self.telemetry_label.ensureCursorVisible()

    def update_bytes_received(self, count):
        self._usb_bytes_received = getattr(self, "_usb_bytes_received", 0) + count
        # Không tự ý ghi đè nếu telemetry_buffer hoặc nội dung log đang có dữ liệu
        if self.telemetry_label.toPlainText() == "No telemetry received":
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
        receiver = self.get_receiver()
        
        # 1. Pulse Type
        pulse_type = self.control_panel.pulse_type_combo.currentText().lower()
        receiver.pulse_type = pulse_type
        
        # 2. Stream Mode
        idx = self.control_panel.signal_type_combo.currentIndex()
        modes = ["raw", "bpf", "demodulated", "downsampling", "compressed", "range_doppler"]
        mode = modes[idx] if idx < len(modes) else "compressed"
        
        # 3. Servo State
        servo_cmd = "servo:on" if self.control_panel.servo_switch.isChecked() else "servo:off"
        
        # 4. Tx Attenuation
        txt = self.control_panel.tx_atten_combo.currentText()
        atten_val = "mute" if txt == "Mute" else txt.replace(" dB", "").replace("-", "")
        
        # 5. Tx Switch State
        tx_cmd = "tx:on" if self.control_panel.tx_switch.isChecked() else "tx:off"
        
        # 6. Rx Select Channel
        rx_chan = self.control_panel.rx_select_combo.currentIndex()
        channel_map = {0: 0, 1: 3, 2: 1, 3: 2}
        actual_rx = channel_map.get(rx_chan, 0)
        
        commands = [
            f"cfg:{pulse_type}",
            f"mode:{mode}",
            servo_cmd,
            f"tx_atten:{atten_val}",
            tx_cmd,
            f"rx_select:{actual_rx}"
        ]
        
        for cmd in commands:
            receiver.send_command(cmd)
        
        self.control_panel.info_label.setText(f"Configs sent: cfg:{pulse_type} | mode:{mode} | rx_select:{actual_rx}")

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

        if idx == 5:  # Range-Doppler 2D Heatmap Mode
            self.plot_widget.setVisible(False)
            self.rd_plot_widget.setVisible(True)
            self.rd_snr_label.setText(self.latest_snr_str)
        elif self.is_spectrum_mode:
            self.rd_plot_widget.setVisible(False)
            self.plot_widget.setVisible(True)
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
            self.rd_plot_widget.setVisible(False)
            self.plot_widget.setVisible(True)
            self.plot_widget.setTitle(base_title)
            self.plot_widget.setLabel('left', 'Voltage', units='V')
            self.plot_widget.setLabel('bottom', 'Sample Index')

            if actual_rx in (0, 3):
                y_lim = PLOT_Y_MAX_RX0
                default_y = PLOT_DEFAULT_Y_MAX_RX0
            elif idx == 4:
                y_lim = PLOT_Y_MAX_RX12_COMPRESSED
                default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED
            else:
                y_lim = PLOT_Y_MAX_RX12_RAW_DEMOD
                default_y = PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD

            max_x = DOWNSAMPLED_BINS if idx in (3, 4) else MAX_SAMPLES
            self.plot_widget.getViewBox().setLimits(xMin=0, xMax=max_x, yMin=PLOT_Y_MIN, yMax=y_lim, minXRange=0, minYRange=0)
            self.current_y_max = default_y
            self._is_updating_plot = True
            self.plot_widget.setXRange(0, max_x, padding=0)
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

        # For Rx Sum (0) and Rx Diff (3), allow Compressed or Range-Doppler (for Rx Sum)
        current_stream = self.control_panel.signal_type_combo.currentIndex()
        if actual_rx in (0, 3):
            if current_stream not in (4, 5) or (actual_rx == 3 and current_stream == 5):
                self.control_panel.signal_type_combo.setCurrentIndex(4) # Compressed
                self.get_receiver().send_command("mode:compressed")
        
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
                    default_y = PLOT_DEFAULT_Y_MAX_RX12_COMPRESSED if idx == 4 else PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
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
        modes = ["raw", "bpf", "demodulated", "downsampling", "compressed", "range_doppler"]
        mode = modes[idx] if idx < len(modes) else "raw"
        
        if idx == 5:  # Range-Doppler
            self.spectrum_switch.setChecked(False)
            self.spectrum_switch.setEnabled(False)
            self.is_spectrum_mode = False
        else:
            self.spectrum_switch.setEnabled(True)
            
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
        if stream_idx == 5:
            if len(voltages) == 1024:
                num_doppler = 8
                rd_matrix = voltages.reshape(num_doppler, 128).astype(np.float32)
                peak_val = np.max(rd_matrix)
                if peak_val > 0:
                    rd_db = 20.0 * np.log10(np.clip(rd_matrix / peak_val, 1e-3, 1.0))
                else:
                    rd_db = np.zeros_like(rd_matrix) - 45.0
                prf_hz = (FS / SAMPLE_COUNT)
                d_span_khz = (prf_hz / 2.0) / 1000.0
                bin_w = (2.0 * d_span_khz) / float(num_doppler)
                self.rd_image_item.setImage(rd_db, autoLevels=False)
                self.rd_image_item.setLevels([-45.0, 0.0])
                from PyQt6.QtCore import QRectF
                x_left = -4.5 * bin_w
                total_w = float(num_doppler) * bin_w
                self.rd_image_item.setRect(QRectF(x_left, 0.0, total_w, MAX_RANGE))
                self.rd_plot_widget.setXRange(-d_span_khz, d_span_khz, padding=0.0)
                self.rd_plot_widget.setYRange(0.0, MAX_RANGE, padding=0.0)
                self.rd_snr_label.setText(pulse['snr_str'])
        elif self.is_spectrum_mode:
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
            scale_factor = (len(voltages) / SAMPLE_COUNT) if len(voltages) < SAMPLE_COUNT else 1.0
            active_start = max(1, int(ACTIVE_SIGNAL_START_IDX * scale_factor))
            if self.control_panel.autoscale_cb.isChecked() and len(voltages) > active_start:
                active_voltages = voltages[active_start:]
                valid_samples = active_voltages[np.isfinite(active_voltages)]
                if len(valid_samples) > 0:
                    peak = np.max(valid_samples)
                    if np.isfinite(peak):
                        if pulse.get('stream_idx', 0) == 4:
                            max_cap = PLOT_Y_MAX_RX12_COMPRESSED
                        elif pulse['receiver_id'] in (0, 3):
                            max_cap = PLOT_DEFAULT_Y_MAX_RX0
                        else:
                            max_cap = PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
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

        # Check expected rx channel (ngoại trừ chế độ Range-Doppler luôn cố định ở kênh 0 - Rx Sum)
        stream_idx = self.control_panel.signal_type_combo.currentIndex()
        channel_map = {0: 0, 1: 3, 2: 1, 3: 2}
        expected_rx = channel_map.get(self.control_panel.rx_select_combo.currentIndex(), 0)
        if stream_idx != 5 and receiver_id != expected_rx:
            # Phát hiện STM32 bị lệch cấu hình (ví dụ vừa Reset), gửi lại toàn bộ cấu hình (pulse_type, mode, tx_atten, rx_select, ...)
            now_ms = getattr(self, "_last_rx_sync_time", 0)
            import time
            current_time = time.time()
            if current_time - now_ms > 0.5:
                self._last_rx_sync_time = current_time
                self.send_all_configs()
            return
            
        if len(samples) > 0:
            voltages = convert_samples_to_voltages(samples, receiver_id, stream_idx)
            self.latest_voltages = voltages
            display_voltages = voltages if stream_idx == 5 else voltages[:DISPLAY_SAMPLE_COUNT]
 
            # Calculate SNR
            pulse_type = self.control_panel.pulse_type_combo.currentText().lower()
            tx_on = self.control_panel.tx_switch.isChecked()
            calibrated_snr = calculate_snr(voltages, pulse_type, tx_on, receiver_id, stream_idx)
            
            if calibrated_snr is not None:
                snr_str = f"SNR: {calibrated_snr:.1f} dB"
            elif stream_idx == 5:
                snr_str = "RD 8x128"
            else:
                snr_str = "SNR: -- dB"
            self.latest_snr_str = snr_str
            
            # Shift voltages to align radar history
            shifted_voltages = voltages if stream_idx == 5 else shift_voltages(voltages, pulse_type)
            
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
            
            if receiver_id in (0, 3) or stream_idx == 5:
                self.radar_widget.set_data(angle, shifted_voltages)
            else:
                self.radar_widget.set_angle(angle)
            
            # Display either Range-Doppler, frequency spectrum or time domain
            if stream_idx == 5:
                # Range-Doppler 2D Heatmap: chính xác 8 Doppler bins x 128 Range bins = 1024 mẫu
                if len(display_voltages) == 1024:
                    num_doppler = 8
                    # STM32 lưu: rd_sum_mag_matrix[d][r] -> shape (8, 128)
                    # Doppler bin d=0..7 (-4..+3), Range bin r=0..127
                    rd_matrix = display_voltages.reshape(num_doppler, 128).astype(np.float32)
                    
                    # Chuyển sang thang đo Logarit / Decibel (dB) tương đối (Đỉnh 0 dB, nền tối thiểu -45 dB)
                    peak_val = np.max(rd_matrix)
                    if peak_val > 0:
                        rd_db = 20.0 * np.log10(np.clip(rd_matrix / peak_val, 1e-3, 1.0))
                    else:
                        rd_db = np.zeros_like(rd_matrix) - 45.0

                    # Tần số Doppler từ -d_span_khz đến +d_span_khz (-23.4375 Hz đến +23.4375 Hz)
                    # PRI = SAMPLE_COUNT / FS = 2048 / 96000 = ~21.33 ms -> PRF = 46.875 Hz
                    prf_hz = (FS / SAMPLE_COUNT)
                    d_span_khz = (prf_hz / 2.0) / 1000.0 # 0.0234375 kHz
                    bin_w = (2.0 * d_span_khz) / float(num_doppler) # Độ rộng 1 bin = 5.859375 mkHz
                    
                    # ImageItem nhận dữ liệu 2D theo trục [X, Y] = [Doppler, Range] -> shape (8, 128)
                    self.rd_image_item.setImage(rd_db, autoLevels=False)
                    self.rd_image_item.setLevels([-45.0, 0.0])
                    
                    from PyQt6.QtCore import QRectF
                    # 8 Doppler bins: indices d=0..7 ứng với [-4, -3, -2, -1, 0, +1, +2, +3]
                    # Bin index d=4 là 0 Hz. Để tâm bin 4 nằm chính xác tại X = 0.0 mkHz:
                    # x_left = -(4 + 0.5) * bin_w = -4.5 * bin_w = -26.367 mkHz
                    # total_w = 8 * bin_w = 46.875 mkHz
                    x_left = -4.5 * bin_w
                    total_w = float(num_doppler) * bin_w
                    self.rd_image_item.setRect(QRectF(x_left, 0.0, total_w, MAX_RANGE))
                    self.rd_plot_widget.setXRange(-d_span_khz, d_span_khz, padding=0.0)
                    self.rd_plot_widget.setYRange(0.0, MAX_RANGE, padding=0.0)
                    self.rd_snr_label.setText(snr_str)
            elif self.is_spectrum_mode:
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
                scale_factor = (len(display_voltages) / SAMPLE_COUNT) if len(display_voltages) < SAMPLE_COUNT else 1.0
                active_start = max(1, int(ACTIVE_SIGNAL_START_IDX * scale_factor))
                if self.control_panel.autoscale_cb.isChecked() and len(display_voltages) > active_start:
                    active_voltages = display_voltages[active_start:]
                    valid_samples = active_voltages[np.isfinite(active_voltages)]
                    if len(valid_samples) > 0:
                        peak = np.max(valid_samples)
                        if np.isfinite(peak):
                            if stream_idx == 4:
                                max_cap = PLOT_Y_MAX_RX12_COMPRESSED
                            elif receiver_id in (0, 3):
                                max_cap = PLOT_DEFAULT_Y_MAX_RX0
                            else:
                                max_cap = PLOT_DEFAULT_Y_MAX_RX12_RAW_DEMOD
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
        if hasattr(self, 'snr_label') and self.plot_widget:
            self.snr_label.move(self.plot_widget.width() - 100, 10)
        if hasattr(self, 'rd_snr_label') and self.rd_plot_widget:
            self.rd_snr_label.move(max(10, self.rd_plot_widget.width() - 170), 10)

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

