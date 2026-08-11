from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLineEdit, QPushButton, QLabel, QComboBox, QCheckBox
from PyQt6.QtCore import pyqtSignal, Qt
from app.toggle_switch import ToggleSwitch

class ControlPanel(QWidget):
    pause_toggled = pyqtSignal(bool)
    single_shot_clicked = pyqtSignal()
    pulse_type_changed = pyqtSignal(str)
    signal_type_changed = pyqtSignal(int)
    tx_atten_changed = pyqtSignal(str)
    autoscale_toggled = pyqtSignal(bool)
    reset_zoom_clicked = pyqtSignal()
    servo_toggled = pyqtSignal(bool)
    tx_toggled = pyqtSignal(bool)
    rx_channel_changed = pyqtSignal(int)
    ip_changed = pyqtSignal(str)
    capture_clicked = pyqtSignal()
    capture_prev_clicked = pyqtSignal()
    capture_next_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._is_paused = False

        ctrl_layout = QVBoxLayout(self)
        ctrl_layout.setContentsMargins(10, 5, 10, 5)
        ctrl_layout.setSpacing(6)

        row1_layout = QHBoxLayout()
        row1_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        row2_layout = QHBoxLayout()
        row2_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # USB device status/input placeholder
        self.ip_input = QLineEdit("USB auto-detect")
        self.ip_input.setFixedWidth(120)
        self.ip_input.editingFinished.connect(self._on_ip_changed)

        # Pause Button
        self.pause_btn = QPushButton("Pause")
        self.pause_btn.clicked.connect(self._on_pause_clicked)
        
        # Single Shot Button
        self.single_btn = QPushButton("Single Shot")
        self.single_btn.clicked.connect(self.single_shot_clicked.emit)
        
        # Status Label
        self.status_label = QLabel()
        self.update_status("Disconnected")

        # ComboBoxes
        self.pulse_type_combo = QComboBox()
        self.pulse_type_combo.addItems(["Single", "Barker13"])
        self.pulse_type_combo.activated.connect(self._on_pulse_type_activated)

        self.signal_type_combo = QComboBox()
        self.signal_type_combo.addItems(["Raw", "Demodulated", "Compressed"])
        self.signal_type_combo.activated.connect(self.signal_type_changed.emit)

        self.tx_atten_combo = QComboBox()
        self.tx_atten_combo.addItems(["0 dB", "-1 dB", "-1.5 dB", "-2 dB", "-2.5 dB", "-3 dB", "-4 dB", "-6 dB", "-12 dB", "-18 dB", "-24 dB", "Mute"])
        self.tx_atten_combo.activated.connect(self._on_tx_atten_activated)

        self.rx_select_combo = QComboBox()
        self.rx_select_combo.addItems(["Rx 0 (Sum Channel)", "Rx 1 (GPIO 32)", "Rx 2 (GPIO 33)"])
        self.rx_select_combo.activated.connect(self.rx_channel_changed.emit)

        # Autoscale Checkbox
        self.autoscale_cb = QCheckBox("Auto Scale")
        self.autoscale_cb.setChecked(True)
        self.autoscale_cb.stateChanged.connect(self._on_autoscale_changed)

        # Reset Zoom Button
        self.reset_zoom_btn = QPushButton("Reset Zoom")
        self.reset_zoom_btn.clicked.connect(self.reset_zoom_clicked.emit)

        # Capture Button & Navigation
        self.capture_btn = QPushButton("Capture")
        self.capture_btn.clicked.connect(self.capture_clicked.emit)

        self.capture_prev_btn = QPushButton("◀")
        self.capture_prev_btn.setFixedWidth(30)
        self.capture_prev_btn.clicked.connect(self.capture_prev_clicked.emit)
        self.capture_prev_btn.setVisible(False)

        self.capture_label = QLabel("")
        self.capture_label.setStyleSheet("color: #FF9500; font-weight: bold; margin-left: 5px; margin-right: 5px;")
        self.capture_label.setVisible(False)

        self.capture_next_btn = QPushButton("▶")
        self.capture_next_btn.setFixedWidth(30)
        self.capture_next_btn.clicked.connect(self.capture_next_clicked.emit)
        self.capture_next_btn.setVisible(False)

        # Switches
        self.servo_switch = ToggleSwitch()
        self.servo_switch.clicked.connect(self._on_servo_clicked)

        self.tx_switch = ToggleSwitch()
        self.tx_switch.clicked.connect(self._on_tx_clicked)

        # Group Tx On label and switch
        tx_widget = QWidget()
        tx_layout = QHBoxLayout(tx_widget)
        tx_layout.setSpacing(5)
        tx_layout.setContentsMargins(0, 0, 0, 0)
        tx_layout.addWidget(QLabel("Tx On:"))
        tx_layout.addWidget(self.tx_switch)
        tx_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Group Servo label and switch
        servo_widget = QWidget()
        servo_layout = QHBoxLayout(servo_widget)
        servo_layout.setSpacing(5)
        servo_layout.setContentsMargins(0, 0, 0, 0)
        servo_layout.addWidget(QLabel("Run Servo:"))
        servo_layout.addWidget(self.servo_switch)
        servo_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)

        # Row 1 layout
        row1_layout.addWidget(QLabel("STM32 USB:"))
        row1_layout.addWidget(self.ip_input)
        row1_layout.addWidget(self.pause_btn)
        row1_layout.addWidget(self.single_btn)
        row1_layout.addWidget(self.autoscale_cb)
        row1_layout.addWidget(self.reset_zoom_btn)
        row1_layout.addWidget(self.capture_btn)
        row1_layout.addWidget(self.capture_prev_btn)
        row1_layout.addWidget(self.capture_label)
        row1_layout.addWidget(self.capture_next_btn)
        row1_layout.addSpacing(15)
        row1_layout.addWidget(tx_widget)
        row1_layout.addSpacing(15)
        row1_layout.addWidget(servo_widget)
        
        # Info Label
        self.info_label = QLabel("")
        self.info_label.setStyleSheet("color: #8E8E93; font-style: italic; margin-right: 15px;")

        # Row 2 layout
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

    def set_paused_state(self, paused):
        self._is_paused = paused
        if paused:
            self.pause_btn.setText("Resume")
        else:
            self.pause_btn.setText("Pause")

    def _on_pause_clicked(self):
        self._is_paused = not self._is_paused
        self.set_paused_state(self._is_paused)
        self.pause_toggled.emit(self._is_paused)

    def _on_ip_changed(self):
        self.ip_changed.emit(self.ip_input.text())

    def _on_pulse_type_activated(self, index):
        self.pulse_type_changed.emit(self.pulse_type_combo.currentText())

    def _on_tx_atten_activated(self, index):
        self.tx_atten_changed.emit(self.tx_atten_combo.currentText())

    def _on_autoscale_changed(self, state):
        self.autoscale_toggled.emit(self.autoscale_cb.isChecked())

    def _on_servo_clicked(self):
        self.servo_toggled.emit(self.servo_switch.isChecked())

    def _on_tx_clicked(self):
        self.tx_toggled.emit(self.tx_switch.isChecked())

    def get_settings(self):
        return {
            "pulse_type": self.pulse_type_combo.currentText(),
            "signal_type": self.signal_type_combo.currentIndex(),
            "tx_atten": self.tx_atten_combo.currentText(),
            "rx_channel": self.rx_select_combo.currentIndex()
        }

    def set_settings(self, settings):
        if "pulse_type" in settings:
            idx = self.pulse_type_combo.findText(settings["pulse_type"])
            if idx >= 0:
                self.pulse_type_combo.setCurrentIndex(idx)
        if "signal_type" in settings:
            self.signal_type_combo.setCurrentIndex(settings["signal_type"])
        if "tx_atten" in settings:
            idx = self.tx_atten_combo.findText(settings["tx_atten"])
            if idx >= 0:
                self.tx_atten_combo.setCurrentIndex(idx)
        if "rx_channel" in settings:
            self.rx_select_combo.setCurrentIndex(settings["rx_channel"])

    def set_capture_idle(self):
        self.capture_btn.setText("Capture")
        self.capture_prev_btn.setVisible(False)
        self.capture_next_btn.setVisible(False)
        self.capture_label.setVisible(False)

    def set_capturing_status(self, current, total=10):
        self.capture_btn.setText("Exit Capture")
        self.capture_prev_btn.setVisible(False)
        self.capture_next_btn.setVisible(False)
        self.capture_label.setText(f"Capturing ({current}/{total})")
        self.capture_label.setVisible(True)

    def set_capture_browse(self, current, total=10):
        self.capture_btn.setText("Exit Capture")
        self.capture_prev_btn.setVisible(True)
        self.capture_next_btn.setVisible(True)
        self.capture_label.setText(f"Pulse {current}/{total}")
        self.capture_label.setVisible(True)


