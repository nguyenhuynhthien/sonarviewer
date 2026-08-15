import numpy as np
import serial
from serial.tools import list_ports
from PyQt6.QtCore import QThread, pyqtSignal

from constants import (
    STM32_USB_PID,
    STM32_USB_VID,
    USB_BAUDRATE,
    USB_FRAME_HEADER_SIZE,
    USB_FRAME_MAGIC,
    USB_MAX_SAMPLES,
    USB_LOG_MAGIC,
    USB_LOG_SIZE,
    USB_DEBUG_MAGIC,
    USB_DEBUG_SIZE,
    USB_DSP_MAGIC,
    USB_DSP_SIZE,
)


class UsbFrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []
        telemetry = []
        debug = []
        dsp = []

        while True:
            signal_start = self.buffer.find(b"FRX")
            log_start = self.buffer.find(USB_LOG_MAGIC)
            debug_start = self.buffer.find(USB_DEBUG_MAGIC)
            dsp_start = self.buffer.find(USB_DSP_MAGIC)
            starts = [value for value in (signal_start, log_start, debug_start, dsp_start) if value >= 0]
            if not starts:
                self.buffer = self.buffer[-3:]
                break
            start = min(starts)
            if start:
                del self.buffer[:start]
            if self.buffer.startswith(USB_LOG_MAGIC):
                if len(self.buffer) < USB_LOG_SIZE:
                    break
                telemetry.append({
                    "sequence": int.from_bytes(self.buffer[8:12], "little"),
                    "adc1_fs_hz": int.from_bytes(self.buffer[12:16], "little"),
                    "adc1_pri_us": int.from_bytes(self.buffer[16:20], "little"),
                    "adc2_fs_hz": int.from_bytes(self.buffer[20:24], "little"),
                    "adc2_pri_us": int.from_bytes(self.buffer[24:28], "little"),
                    "dac_fs_hz": int.from_bytes(self.buffer[28:32], "little"),
                    "dac_pri_us": int.from_bytes(self.buffer[32:36], "little"),
                })
                del self.buffer[:USB_LOG_SIZE]
                continue
            if self.buffer.startswith(USB_DEBUG_MAGIC):
                if len(self.buffer) < USB_DEBUG_SIZE:
                    break
                debug.append({
                    "counter": int.from_bytes(self.buffer[8:12], "little"),
                    "tick_ms": int.from_bytes(self.buffer[12:16], "little"),
                    "adc_count": int.from_bytes(self.buffer[16:20], "little"),
                    "dac_count": int.from_bytes(self.buffer[20:24], "little"),
                    "timer_counter": int.from_bytes(self.buffer[24:28], "little"),
                    "timer_enabled": bool(self.buffer[28]),
                    "registers": [int.from_bytes(self.buffer[offset:offset + 4], "little") for offset in range(32, 132, 4)],
                    "diagnostics": [int.from_bytes(self.buffer[offset:offset + 4], "little") for offset in range(132, 164, 4)],
                })
                del self.buffer[:USB_DEBUG_SIZE]
                continue
            if self.buffer.startswith(USB_DSP_MAGIC):
                if len(self.buffer) < USB_DSP_SIZE:
                    break
                dsp.append({
                    "sequence": int.from_bytes(self.buffer[4:8], "little"),
                    "total_us": int.from_bytes(self.buffer[8:12], "little"),
                    "read_us": int.from_bytes(self.buffer[12:16], "little"),
                    "bpf_us": int.from_bytes(self.buffer[16:20], "little"),
                    "demod_us": int.from_bytes(self.buffer[20:24], "little"),
                    "mfilt_us": int.from_bytes(self.buffer[24:28], "little"),
                    "send_us": int.from_bytes(self.buffer[28:32], "little"),
                })
                del self.buffer[:USB_DSP_SIZE]
                continue
            if len(self.buffer) < USB_FRAME_HEADER_SIZE:
                break

            # Verify it's followed by '0', '1', '2' or '3'
            if self.buffer[3] not in (0x30, 0x31, 0x32, 0x33):  # ASCII '0', '1', '2', '3'
                del self.buffer[:3]
                continue

            sample_count = int.from_bytes(self.buffer[4:6], "little")
            if sample_count == 0 or sample_count > USB_MAX_SAMPLES:
                del self.buffer[:4]
                continue

            frame_size = USB_FRAME_HEADER_SIZE + sample_count * 2
            if len(self.buffer) < frame_size:
                break

            receiver_id = self.buffer[3] - 0x30
            payload = bytes(self.buffer[USB_FRAME_HEADER_SIZE:frame_size])
            del self.buffer[:frame_size]
            frames.append((np.frombuffer(payload, dtype="<i2").astype(np.float32), receiver_id))

        return frames, telemetry, debug, dsp


def find_stm32_port():
    ports = list_ports.comports()
    for port in ports:
        if port.vid == STM32_USB_VID and port.pid == STM32_USB_PID:
            return port.device
    for port in ports:
        description = (port.description or "").lower()
        if "stm32" in description or "weact" in description:
            return port.device
    return None


class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray, int, int)
    target_received = pyqtSignal(float, int, float, float, int)
    status_changed = pyqtSignal(str)
    telemetry_received = pyqtSignal(int, int, int, int, int, int, int)
    debug_received = pyqtSignal(int, int, int, int, int, bool, list, list)
    dsp_received = pyqtSignal(int, int, int, int, int, int, int)
    bytes_received = pyqtSignal(int)

    def __init__(self, initial_configs=None):
        super().__init__()
        self.running = False
        self.serial_port = None
        self.initial_configs = initial_configs
        self.pending_commands = []
        self.current_angle = 90

    def run(self):
        self.running = True
        parser = UsbFrameParser()
        try:
            while self.running:
                if self.serial_port is None:
                    device = find_stm32_port()
                    if device is None:
                        self.status_changed.emit("Waiting for STM32 USB")
                        self.msleep(500)
                        continue
                    try:
                        self.serial_port = serial.Serial(device, USB_BAUDRATE, timeout=0.1)
                        self.status_changed.emit(f"Connected to {device}")
                        for command in self.pending_commands:
                            self.serial_port.write((command + "\n").encode("ascii"))
                            self.msleep(50)
                        self.pending_commands.clear()
                    except serial.SerialException as error:
                        self.status_changed.emit(f"USB error: {error}")
                        self.msleep(500)
                        continue

                try:
                    data = self.serial_port.read(8192)
                    if data:
                        self.bytes_received.emit(len(data))
                    signal_frames, telemetry_frames, debug_frames, dsp_frames = parser.feed(data)
                    for samples, rx_id in signal_frames:
                        self.data_received.emit(samples, self.current_angle, rx_id)
                    for log in telemetry_frames:
                        self.telemetry_received.emit(
                            log["sequence"],
                            log["adc1_fs_hz"],
                            log["adc1_pri_us"],
                            log["adc2_fs_hz"],
                            log["adc2_pri_us"],
                            log["dac_fs_hz"],
                            log["dac_pri_us"]
                        )
                    for log in debug_frames:
                        self.debug_received.emit(log["counter"], log["tick_ms"], log["adc_count"], log["dac_count"], log["timer_counter"], log["timer_enabled"], log["registers"], log["diagnostics"])
                    for log in dsp_frames:
                        self.dsp_received.emit(
                            log["sequence"],
                            log["total_us"],
                            log["read_us"],
                            log["bpf_us"],
                            log["demod_us"],
                            log["mfilt_us"],
                            log["send_us"]
                        )
                except (serial.SerialException, OSError) as error:
                    self.status_changed.emit(f"USB disconnected: {error}")
                    self._close_port()
                    parser.buffer.clear()
        finally:
            self._close_port()
            self.running = False
            self.status_changed.emit("Disconnected")

    def send_command(self, command):
        if self.serial_port is None or not self.serial_port.is_open:
            self.pending_commands.append(command)
            return
        try:
            self.serial_port.write((command + "\n").encode("ascii"))
        except (serial.SerialException, OSError):
            self.pending_commands.append(command)

    def stop(self):
        self.running = False
        self._close_port()

    def _close_port(self):
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except OSError:
                pass
            self.serial_port = None
