import struct
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
    USB_TARGET_MAGIC,
    USB_TARGET_HEADER_SIZE,
    USB_TARGET_ENTRY_SIZE,
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
        targets = []
        text_logs = []

        while True:
            signal_start = self.buffer.find(b"FRX")
            log_start = self.buffer.find(USB_LOG_MAGIC)
            debug_start = self.buffer.find(USB_DEBUG_MAGIC)
            dsp_start = self.buffer.find(USB_DSP_MAGIC)
            target_start = self.buffer.find(USB_TARGET_MAGIC)
            starts = [value for value in (signal_start, log_start, debug_start, dsp_start, target_start) if value >= 0]
            if not starts:
                # Kiểm tra nếu trong buffer có chứa chuỗi text (ví dụ log panic hoặc log hệ thống)
                newline_pos = self.buffer.find(b"\n")
                if newline_pos >= 0:
                    line_bytes = self.buffer[:newline_pos + 1]
                    try:
                        text_str = line_bytes.decode("utf-8", errors="replace")
                        if any(c.isalnum() for c in text_str) or "=" in text_str or "-" in text_str:
                            text_logs.append(text_str)
                    except Exception:
                        pass
                    del self.buffer[:newline_pos + 1]
                    continue
                # Nếu không có newline nhưng buffer chứa text (ví dụ khi kết thúc truyền sau crash)
                if len(self.buffer) > 0 and (b"=" in self.buffer or b"Backtrace" in self.buffer or b"CFSR" in self.buffer):
                    try:
                        text_str = self.buffer.decode("utf-8", errors="replace")
                        text_logs.append(text_str)
                    except Exception:
                        pass
                    self.buffer.clear()
                    break

                self.buffer = self.buffer[-3:]
                break
            start = min(starts)
            if start:
                pre_data = self.buffer[:start]
                try:
                    pre_text = pre_data.decode("utf-8", errors="ignore")
                    if any(c.isalnum() for c in pre_text):
                        text_logs.append(pre_text)
                except Exception:
                    pass
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
                    "ds_us": int.from_bytes(self.buffer[32:36], "little"),
                    "detect_us": int.from_bytes(self.buffer[36:40], "little"),
                })
                del self.buffer[:USB_DSP_SIZE]
                continue
            if self.buffer.startswith(USB_TARGET_MAGIC):
                if len(self.buffer) < USB_TARGET_HEADER_SIZE:
                    break
                target_count = int.from_bytes(self.buffer[4:6], "little")
                total_tgt_size = USB_TARGET_HEADER_SIZE + target_count * USB_TARGET_ENTRY_SIZE
                if len(self.buffer) < total_tgt_size:
                    break
                offset = USB_TARGET_HEADER_SIZE
                for _ in range(target_count):
                    range_m, strength_dbv, angle_deg, reserved, velocity_mps = struct.unpack_from("<ffhhf", self.buffer, offset)
                    targets.append({
                        "range": range_m,
                        "strength": strength_dbv,
                        "angle": angle_deg,
                        "velocity": velocity_mps,
                    })
                    offset += USB_TARGET_ENTRY_SIZE
                del self.buffer[:total_tgt_size]
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
                # Kiểm tra nếu trong phần buffer còn lại xuất hiện chuỗi Panic / Backtrace
                # (nghĩa là frame FRX này bị đứt gánh giữa chừng do MCU crash)
                panic_pos = self.buffer.find(b"[SYSTEM PANIC / CRASH]")
                if panic_pos < 0:
                    panic_pos = self.buffer.find(b"SYSTEM PANIC")
                if panic_pos >= 0:
                    # Lùi lại tìm dấu '=' đầu tiên của chuỗi các dấu '=' liên tục
                    p = panic_pos
                    while p > 0 and self.buffer[p - 1] in (ord('='), ord(' '), ord('\r'), ord('\n')):
                        p -= 1
                    del self.buffer[:p]
                    continue
                break

            # Kiểm tra xem trong payload của frame có vô tình chứa đoạn text log Panic hay không
            payload_raw = self.buffer[USB_FRAME_HEADER_SIZE:frame_size]
            panic_in_payload = payload_raw.find(b"[SYSTEM PANIC / CRASH]")
            if panic_in_payload < 0:
                panic_in_payload = payload_raw.find(b"SYSTEM PANIC")
            if panic_in_payload >= 0:
                p = panic_in_payload
                while p > 0 and payload_raw[p - 1] in (ord('='), ord(' '), ord('\r'), ord('\n')):
                    p -= 1
                del self.buffer[:USB_FRAME_HEADER_SIZE + p]
                continue

            receiver_id = self.buffer[3] - 0x30
            payload = bytes(payload_raw)
            del self.buffer[:frame_size]
            # Dữ liệu mẫu truyền lên từ STM32 là uint16 (bao gồm bias 2048 hoặc giá trị lớn hơn 32767 khi nén xung)
            frames.append((np.frombuffer(payload, dtype="<u2").astype(np.float32), receiver_id))

        return frames, telemetry, debug, dsp, targets, text_logs


def find_stm32_port():
    ports = list(list_ports.comports())
    for port in ports:
        if port.vid == STM32_USB_VID and port.pid == STM32_USB_PID:
            return port.device
        if "cu.usb" in port.device or "ttyUSB" in port.device:
            return port.device
    return None


class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray, int, int)
    target_received = pyqtSignal(float, int, float, float)
    status_changed = pyqtSignal(str)
    port_connected = pyqtSignal()
    telemetry_received = pyqtSignal(int, int, int, int, int, int, int)
    debug_received = pyqtSignal(int, int, int, int, int, bool, list, list)
    dsp_received = pyqtSignal(int, int, int, int, int, int, int, int, int)
    text_log_received = pyqtSignal(str)
    bytes_received = pyqtSignal(int)

    def __init__(self, port="/dev/cu.usbmodem5AA90328801", baudrate=6000000, initial_configs=None):
        super().__init__()
        self.running = False
        self.serial_port = None
        self.port = port
        self.baudrate = baudrate
        self.initial_configs = initial_configs
        self.pending_commands = []
        self.current_angle = 90

    def run(self):
        self.running = True
        parser = UsbFrameParser()
        try:
            while self.running:
                if self.serial_port is None:
                    device = self.port if self.port else find_stm32_port()
                    if device is None:
                        self.status_changed.emit("Waiting for UART Port...")
                        self.msleep(500)
                        continue
                    try:
                        self.serial_port = serial.Serial(device, self.baudrate, timeout=0.1)
                        self.msleep(100)  # Chờ chip USB-UART và DTR/RTS ổn định
                        self.status_changed.emit(f"UART Connected: {device} @ {self.baudrate} bps")
                        for command in self.pending_commands:
                            self.serial_port.write((command + "\n").encode("ascii"))
                            self.serial_port.flush()
                            self.msleep(20)
                        self.pending_commands.clear()
                        self.port_connected.emit()
                    except serial.SerialException:
                        self.status_changed.emit("Waiting for UART Port...")
                        self.msleep(500)
                        continue

                try:
                    data = self.serial_port.read(8192)
                    if data:
                        self.bytes_received.emit(len(data))
                    signal_frames, telemetry_frames, debug_frames, dsp_frames, target_frames, text_logs = parser.feed(data)
                    for log_line in text_logs:
                        self.text_log_received.emit(log_line)
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
                            log["send_us"],
                            log["ds_us"],
                            log["detect_us"]
                        )
                    for tgt in target_frames:
                        self.target_received.emit(
                            tgt["range"],
                            tgt["angle"],
                            tgt["strength"],
                            tgt["velocity"]
                        )
                except (serial.SerialException, OSError):
                    self.status_changed.emit("UART Disconnected")
                    self._close_port()
                    parser.buffer.clear()
        finally:
            self._close_port()
            self.running = False
            self.status_changed.emit("UART Disconnected")

    def set_config(self, port, baudrate):
        if self.port != port or self.baudrate != baudrate:
            self.port = port
            self.baudrate = baudrate
            self._close_port()

    def send_command(self, command):
        if self.serial_port is None or not self.serial_port.is_open:
            self.pending_commands.append(command)
            return
        try:
            self.serial_port.write((command + "\n").encode("ascii"))
            self.serial_port.flush()
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


class UartReceiver(QThread):
    text_received = pyqtSignal(str)
    status_changed = pyqtSignal(str)

    def __init__(self, port="/dev/cu.usbmodem5AA90328801", baudrate=6000000):
        super().__init__()
        self.port = port
        self.baudrate = baudrate
        self.running = False
        self.serial_port = None

    def run(self):
        self.running = True
        while self.running:
            if self.serial_port is None:
                # Tìm port nếu chưa chỉ định hoặc cố kết nối port chỉ định
                target_port = self.port
                if not target_port:
                    # Auto find CH343 / USB modem ports
                    for p in list_ports.comports():
                        dev = p.device
                        desc = (p.description or "").lower()
                        if "usbmodem" in dev or "wch" in dev or "ch34" in desc:
                            # Tránh port trùng với STM32 USB CDC nếu đã biết
                            stm32_dev = find_stm32_port()
                            if stm32_dev and dev == stm32_dev:
                                continue
                            target_port = dev
                            break
                if not target_port:
                    self.status_changed.emit("UART: Waiting for CH343P port...")
                    self.msleep(500)
                    continue

                try:
                    self.serial_port = serial.Serial(target_port, self.baudrate, timeout=0.2)
                    self.status_changed.emit(f"UART Connected: {target_port} @ {self.baudrate} bps")
                except Exception as e:
                    self.status_changed.emit(f"UART Connect error: {e}")
                    self.msleep(500)
                    continue

            try:
                data = self.serial_port.read(1024)
                if data:
                    text = data.decode("utf-8", errors="replace")
                    self.text_received.emit(text)
            except Exception as e:
                self.status_changed.emit(f"UART Disconnected: {e}")
                self._close_port()
                self.msleep(500)

        self._close_port()
        self.status_changed.emit("UART Disconnected")

    def set_config(self, port, baudrate):
        if self.port != port or self.baudrate != baudrate:
            self.port = port
            self.baudrate = baudrate
            self._close_port()

    def send_data(self, data_str):
        if self.serial_port and self.serial_port.is_open:
            try:
                self.serial_port.write(data_str.encode("utf-8"))
            except Exception:
                pass

    def stop(self):
        self.running = False
        self._close_port()

    def _close_port(self):
        if self.serial_port is not None:
            try:
                self.serial_port.close()
            except Exception:
                pass
            self.serial_port = None
