import socket
import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal
from constants import (
    DEFAULT_HOST, DEFAULT_PORT, SOCKET_TIMEOUT, SOCKET_RECV_BUFFER,
    CHUNK_HEADER_SIZE, CHUNK_SAMPLES, CHUNKS_PER_FRAME, CHUNK_PACKET_SIZE
)

class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray, int, int)
    target_received = pyqtSignal(float, int, float, float, int)
    status_changed = pyqtSignal(str)

    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, initial_configs=None):
        super().__init__()
        self.host = host
        self.port = port
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(SOCKET_TIMEOUT)
        self.pulse_type = 'single'
        self.initial_configs = initial_configs
        self.current_angle = 0

    def run(self):
        try:
            self.running = True

            # Register client port by sending a ping
            self.sock.sendto(b"ping", (self.host, self.port))
            self.status_changed.emit(f"Connected to {self.host}")
            
            # Send initial configurations if provided, with a tiny delay to ensure ping is processed
            if self.initial_configs:
                self.msleep(50)
                for cmd in self.initial_configs:
                     self.sock.sendto(cmd.encode('utf-8'), (self.host, self.port))
                     self.msleep(15)

            current_frame_id = {0: None, 1: None, 2: None}
            chunks = {0: {}, 1: {}, 2: {}}

            while self.running:
                try:
                    data, _addr = self.sock.recvfrom(SOCKET_RECV_BUFFER)
                except socket.timeout:
                    continue

                if len(data) == CHUNK_PACKET_SIZE:
                    frame_id = data[0] | (data[1] << 8)
                    chunk_idx = data[2]
                    receiver_id = data[3]
                    payload = data[CHUNK_HEADER_SIZE:]

                    if receiver_id not in chunks:
                        chunks[receiver_id] = {}
                        current_frame_id[receiver_id] = None

                    if frame_id != current_frame_id[receiver_id]:
                        current_frame_id[receiver_id] = frame_id
                        chunks[receiver_id] = {}

                    chunks[receiver_id][chunk_idx] = payload

                    if len(chunks[receiver_id]) == CHUNKS_PER_FRAME:
                        full = b"".join(chunks[receiver_id][i] for i in range(CHUNKS_PER_FRAME))
                        chunks[receiver_id] = {}
                        current_frame_id[receiver_id] = None

                        samples = np.frombuffer(full, dtype=np.int16).astype(np.float32)
                        self.data_received.emit(samples, self.current_angle, receiver_id)

                elif data.startswith(b"ang:"):
                    try:
                        angle = int(data[4:])
                        self.current_angle = angle
                        self.data_received.emit(np.array([]), angle, 0)
                    except ValueError:
                        pass

                elif data.startswith(b"target:"):
                    try:
                        parts = data[7:].decode('utf-8').split(',')
                        if len(parts) >= 4:
                            t_range = float(parts[0])
                            t_angle = int(parts[1])
                            t_strength = float(parts[2])
                            t_velocity = float(parts[3])
                            receiver_id = int(parts[4]) if len(parts) >= 5 else 0
                            
                            self.target_received.emit(t_range, t_angle, t_strength, t_velocity, receiver_id)
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
                self.sock = None
            self.running = False
            self.status_changed.emit("Disconnected")

    def send_command(self, cmd):
        if self.sock and self.running:
            try:
                self.sock.sendto(cmd.encode('utf-8'), (self.host, self.port))
            except Exception as e:
                print(f"Error sending command: {e}")

    def stop(self):
        self.running = False
