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
)


class UsbFrameParser:
    def __init__(self):
        self.buffer = bytearray()

    def feed(self, data):
        self.buffer.extend(data)
        frames = []

        while True:
            start = self.buffer.find(USB_FRAME_MAGIC)
            if start < 0:
                self.buffer = self.buffer[-(len(USB_FRAME_MAGIC) - 1):]
                break
            if start:
                del self.buffer[:start]
            if len(self.buffer) < USB_FRAME_HEADER_SIZE:
                break

            sample_count = int.from_bytes(self.buffer[4:6], "little")
            if sample_count == 0 or sample_count > USB_MAX_SAMPLES:
                del self.buffer[:len(USB_FRAME_MAGIC)]
                continue

            frame_size = USB_FRAME_HEADER_SIZE + sample_count * 2
            if len(self.buffer) < frame_size:
                break

            payload = bytes(self.buffer[USB_FRAME_HEADER_SIZE:frame_size])
            del self.buffer[:frame_size]
            frames.append(np.frombuffer(payload, dtype="<i2").astype(np.float32))

        return frames


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

    def __init__(self, initial_configs=None):
        super().__init__()
        self.running = False
        self.serial_port = None
        self.initial_configs = initial_configs
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
                    except serial.SerialException as error:
                        self.status_changed.emit(f"USB error: {error}")
                        self.msleep(500)
                        continue

                try:
                    data = self.serial_port.read(8192)
                    for samples in parser.feed(data):
                        self.data_received.emit(samples, self.current_angle, 0)
                except (serial.SerialException, OSError) as error:
                    self.status_changed.emit(f"USB disconnected: {error}")
                    self._close_port()
                    parser.buffer.clear()
        finally:
            self._close_port()
            self.running = False
            self.status_changed.emit("Disconnected")

    def send_command(self, _cmd):
        return

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
