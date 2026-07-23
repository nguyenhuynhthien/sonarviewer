import sys
import socket
import numpy as np
from PyQt6.QtWidgets import QApplication, QMainWindow, QVBoxLayout, QPushButton, QWidget, QHBoxLayout, QLineEdit, QLabel, QCheckBox, QComboBox, QAbstractButton
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush
from PyQt6.QtCore import QThread, pyqtSignal, Qt, QPropertyAnimation, pyqtProperty, QPointF, QTimer
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
    angle_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.servo_enabled = False
        self.current_angle = 90
        self.sweep_direction = 1  # 1 for CCW (increasing), -1 for CW (decreasing)
        self.history = {}  # Maps angle (int) -> numpy array of normalized intensities
        self.max_samples = 2048
        self.downsample_factor = 16  # 2048 / 16 = 128 bins
        self.targets = []  # List of (range, angle, strength)
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.drag_start = None
        self.is_dragging = False
        self.target_angle = 90.0
        self.current_angle = 90.0
        self.interpolation_timer = QTimer(self)
        self.interpolation_timer.timeout.connect(self.interpolate_angle)
        self.interpolation_timer.start(16)  # ~60 FPS
        self.min_detected_strength = -50.0
        self.max_detected_strength = 10.0
        self.zoom_start_pos = None
        self.zoom_current_pos = None
        self.is_selecting = False

    def interpolate_angle(self):
        diff = self.target_angle - self.current_angle
        
        # Decay older sweeps (simulating phosphor decay) at ~60 FPS
        has_history = False
        for d in list(self.history.keys()):
            if d != int(self.target_angle):
                self.history[d] = self.history[d] * 0.96
                if np.max(self.history[d]) < 0.02:
                    del self.history[d]
                else:
                    has_history = True
            else:
                has_history = True
                
        if abs(diff) > 0.05:
            self.current_angle += diff * 0.30
            self.update()
        else:
            self.current_angle = self.target_angle
            if has_history or len(self.targets) > 0:
                self.update()

    def get_target_color(self, strength):
        s_min = self.min_detected_strength
        s_max = self.max_detected_strength
        if abs(s_max - s_min) < 1.0:
            val = 0.5
        else:
            val = (strength - s_min) / (s_max - s_min)
        val = max(0.0, min(1.0, val))
        
        # Multi-color gradient (Jet/Rainbow style): Blue -> Cyan -> Green -> Yellow -> Red
        stops = [
            (0.0, (10, 30, 180)),   # Blue (Weak)
            (0.25, (0, 200, 200)),  # Cyan
            (0.5, (0, 220, 50)),    # Green
            (0.75, (255, 200, 0)),  # Yellow
            (1.0, (255, 30, 30))    # Red (Strong)
        ]
        
        for i in range(len(stops) - 1):
            s1_val, s1_col = stops[i]
            s2_val, s2_col = stops[i+1]
            if s1_val <= val <= s2_val:
                t = (val - s1_val) / (s2_val - s1_val)
                r = int(s1_col[0] + (s2_col[0] - s1_col[0]) * t)
                g = int(s1_col[1] + (s2_col[1] - s1_col[1]) * t)
                b = int(s1_col[2] + (s2_col[2] - s1_col[2]) * t)
                return QColor(r, g, b, 170)
        return QColor(255, 30, 30, 170)

    def add_target(self, range_val, angle, strength, velocity=0.0):
        self.targets.append((range_val, angle, strength, velocity))
        if strength > self.max_detected_strength:
            self.max_detected_strength = strength
        if strength < self.min_detected_strength:
            self.min_detected_strength = strength

    def _update_sweep_direction(self, angle):
        angle_int = int(angle)
        direction = -1 if (angle_int & 0x8000) else 1
        clean_angle = angle_int & 0x7FFF
        
        self.sweep_direction = direction
        self.target_angle = float(clean_angle)
        
        if not hasattr(self, '_last_sweep_direction'):
            self._last_sweep_direction = self.sweep_direction
            
        if self.sweep_direction != self._last_sweep_direction:
            self.targets = []
            self._last_sweep_direction = self.sweep_direction
            
        return clean_angle

    def set_data(self, angle, samples):
        clean_angle = self._update_sweep_direction(angle)

        # Calculate intensities: absolute deviation from median (baseline)
        baseline = np.median(samples)
        deviation = np.abs(samples - baseline)
        
        # Downsample to 128 bins for fast rendering
        downsampled = deviation.reshape(-1, self.downsample_factor).mean(axis=1)
        max_val = np.max(downsampled) if np.max(downsampled) > 0 else 1.0
        normalized = downsampled / max_val
        
        self.history[int(clean_angle)] = normalized

    def set_angle(self, angle):
        self._update_sweep_direction(angle)

    def wheelEvent(self, event):
        angle = event.angleDelta().y()
        old_zoom = self.zoom_factor
        if angle > 0:
            self.zoom_factor = min(self.zoom_factor * 1.15, 15.0)
        else:
            self.zoom_factor = max(self.zoom_factor / 1.15, 1.0)
        
        if self.zoom_factor == 1.0:
            self.pan_x = 0.0
            self.pan_y = 0.0
        else:
            # Zoom centered on mouse cursor
            mouse_pos = event.position()
            mx, my = mouse_pos.x(), mouse_pos.y()
            
            cx = self.width() // 2
            cy = int(self.height() * 0.9)
            
            dx = mx - cx - self.pan_x
            dy = my - cy - self.pan_y
            
            ratio = self.zoom_factor / old_zoom
            self.pan_x = mx - cx - dx * ratio
            self.pan_y = my - cy - dy * ratio
            
        self.update()

    def handle_angle_select(self, event):
        pos = event.position()
        mx, my = pos.x(), pos.y()
        width = self.width()
        height = self.height()
        center_x = width // 2 + int(self.pan_x)
        center_y = int(height * 0.9) + int(self.pan_y)
        dx = mx - center_x
        dy = center_y - my
        angle_rad = np.arctan2(dy, dx)
        angle_deg = int(np.degrees(angle_rad))
        if angle_deg < 0:
            if dx >= 0:
                angle_deg = 0
            else:
                angle_deg = 180
        angle_deg = max(0, min(180, angle_deg))
        self.angle_requested.emit(angle_deg)
        self.target_angle = float(angle_deg)
        self.update()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if not self.servo_enabled:
                self.handle_angle_select(event)
            else:
                # Start zoom box selection
                self.zoom_start_pos = event.position()
                self.zoom_current_pos = event.position()
                self.is_selecting = True
        elif event.button() == Qt.MouseButton.RightButton:
            # Start panning
            self.drag_start = event.position()
            self.is_dragging = True

    def mouseMoveEvent(self, event):
        if not self.servo_enabled and event.buttons() & Qt.MouseButton.LeftButton:
            self.handle_angle_select(event)
        elif self.is_selecting and self.zoom_start_pos is not None:
            self.zoom_current_pos = event.position()
            self.update()
        elif self.is_dragging and self.drag_start is not None and self.zoom_factor > 1.0:
            curr_pos = event.position()
            delta = curr_pos - self.drag_start
            self.pan_x += delta.x()
            self.pan_y += delta.y()
            self.drag_start = curr_pos
            self.update()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton and self.is_selecting:
            self.is_selecting = False
            if self.zoom_start_pos and self.zoom_current_pos:
                x1, y1 = self.zoom_start_pos.x(), self.zoom_start_pos.y()
                x2, y2 = self.zoom_current_pos.x(), self.zoom_current_pos.y()
                
                # Check if drag rectangle is large enough
                bw = abs(x1 - x2)
                bh = abs(y1 - y2)
                if bw > 15 and bh > 15:
                    bx = (x1 + x2) / 2.0
                    by = (y1 + y2) / 2.0
                    
                    zoom_inc = min(self.width() / bw, self.height() / bh)
                    old_zoom = self.zoom_factor
                    self.zoom_factor = min(self.zoom_factor * zoom_inc, 15.0)
                    
                    cx = self.width() // 2
                    cy = int(self.height() * 0.9)
                    
                    rx = (bx - cx - self.pan_x) / old_zoom
                    ry = (by - cy - self.pan_y) / old_zoom
                    
                    widget_cx = self.width() // 2
                    widget_cy = self.height() // 2
                    
                    self.pan_x = widget_cx - cx - rx * self.zoom_factor
                    self.pan_y = widget_cy - cy - ry * self.zoom_factor
                    
            self.zoom_start_pos = None
            self.zoom_current_pos = None
            self.update()
        elif event.button() == Qt.MouseButton.RightButton:
            self.is_dragging = False
            self.drag_start = None

    def mouseDoubleClickEvent(self, event):
        # Double click to reset zoom
        if event.button() == Qt.MouseButton.LeftButton:
            self.reset_zoom()

    def reset_zoom(self):
        self.zoom_factor = 1.0
        self.pan_x = 0.0
        self.pan_y = 0.0
        self.update()

    def get_clustered_targets(self):
        if not self.targets:
            return []
        
        clusters = []
        visited = [False] * len(self.targets)
        for i in range(len(self.targets)):
            if visited[i]:
                continue
            cluster = [self.targets[i]]
            visited[i] = True
            queue = [self.targets[i]]
            while queue:
                curr = queue.pop(0)
                curr_r, curr_a, curr_s, curr_v = curr
                for j in range(len(self.targets)):
                    if not visited[j]:
                        r_j, a_j, s_j, v_j = self.targets[j]
                        ang_diff = abs(curr_a - a_j)
                        if ang_diff > 180:
                            ang_diff = 360 - ang_diff
                        range_diff = abs(curr_r - r_j)
                        if ang_diff <= 15.0 and range_diff <= 0.25:
                            visited[j] = True
                            cluster.append(self.targets[j])
                            queue.append(self.targets[j])
            clusters.append(cluster)
        
        results = []
        for cluster in clusters:
            min_r = min(t[0] for t in cluster)
            max_r = max(t[0] for t in cluster)
            min_a = min(t[1] for t in cluster)
            max_a = max(t[1] for t in cluster)
            avg_s = sum(t[2] for t in cluster) / len(cluster)
            # Use velocity of the peak strength detection to avoid low-SNR edge fluctuations
            best_detection = max(cluster, key=lambda t: t[2])
            avg_v = best_detection[3]
            
            # Pad/expand to a minimum visual size for rendering clarity
            if max_a - min_a < 4.0:
                center_a = (min_a + max_a) / 2.0
                min_a = center_a - 2.0
                max_a = center_a + 2.0
            if max_r - min_r < 0.05:
                center_r = (min_r + max_r) / 2.0
                min_r = max(0.0, center_r - 0.025)
                max_r = center_r + 0.025
                
            results.append({
                'min_r': min_r,
                'max_r': max_r,
                'min_a': min_a,
                'max_a': max_a,
                'avg_r': sum(t[0] for t in cluster) / len(cluster),
                'avg_a': sum(t[1] for t in cluster) / len(cluster),
                'strength': avg_s,
                'velocity': avg_v,
                'count': len(cluster)
            })
        return results

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # Max range in meters
        max_range = (self.max_samples * 343.0) / (2.0 * 160000.0)  # ~1.0976 meters
        
        # Draw deep dark space background
        painter.fillRect(self.rect(), QColor("#090d16"))

        # Draw current servo angle on top-left corner
        text_pen = QPen(QColor(0, 255, 100, 220))
        painter.setPen(text_pen)
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(20, 30, f"Servo Angle: {int(self.current_angle)}°")
        
        # Center of the semi-circle is at the bottom center of the widget, plus pan offsets
        center_x = width // 2 + int(self.pan_x)
        center_y = int(height * 0.9) + int(self.pan_y)
        
        # Maximum radius for the radar arcs
        max_radius = int(min(width // 2 - 40, int(height * 0.8)) * self.zoom_factor)
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
            
        # Draw range markers along the 90 degree axis in meters
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        for i in range(1, 5):
            r = int(max_radius * i / 4)
            dist_m = max_range * i / 4
            label = f"{dist_m:.2f}m"
            painter.drawText(center_x + 5, center_y - r - 2, label)

        # 1. Draw Sonar echo history using filled quadrilaterals to avoid gaps
        painter.setPen(Qt.PenStyle.NoPen)
        step_half = 1.5  # half of the 3-degree step size
        for deg, intensities in self.history.items():
            # Filter out history that is ahead of the current visual beam sweep position
            if self.sweep_direction == 1 and deg > self.current_angle:
                continue
            if self.sweep_direction == -1 and deg < self.current_angle:
                continue
                
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
                    
        # 2. Draw smooth fading sweep wedge (30 degrees trail, 120 slices of 0.25 degrees)
        painter.setPen(Qt.PenStyle.NoPen)
        trail_dir = -self.sweep_direction
        num_slices = 120
        slice_width = 0.25  # degrees
        
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

        # Draw detected targets
        max_range = (self.max_samples * 343.0) / (2.0 * 160000.0)  # ~1.0976 meters
        clustered = self.get_clustered_targets()
        
        for t in clustered:
            # Skip targets that are ahead of the current visual sweep ray
            if self.sweep_direction == 1 and t['avg_a'] > self.current_angle:
                continue
            if self.sweep_direction == -1 and t['avg_a'] < self.current_angle:
                continue
                
            min_r_pix = max_radius * (t['min_r'] / max_range)
            max_r_pix = max_radius * (t['max_r'] / max_range)
            
            if min_r_pix > max_radius:
                min_r_pix = max_radius
            if max_r_pix > max_radius:
                max_r_pix = max_radius
                
            polygon_points = []
            angles = np.linspace(t['min_a'], t['max_a'], 6)
            
            # Outer arc
            for a in angles:
                rad = np.radians(a)
                px = center_x + max_r_pix * np.cos(rad)
                py = center_y - max_r_pix * np.sin(rad)
                polygon_points.append(QPointF(px, py))
                
            # Inner arc
            for a in reversed(angles):
                rad = np.radians(a)
                px = center_x + min_r_pix * np.cos(rad)
                py = center_y - min_r_pix * np.sin(rad)
                polygon_points.append(QPointF(px, py))
                
            # Semi-transparent body colored by strength, with no border
            color = self.get_target_color(t['strength'])
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawPolygon(QPolygonF(polygon_points))
            
            # Text/Range indicator
            avg_rad = np.radians(t['avg_a'])
            avg_r_pix = max_radius * (t['avg_r'] / max_range)
            tx = center_x + avg_r_pix * np.cos(avg_rad)
            ty = center_y - avg_r_pix * np.sin(avg_rad)
            
            font = painter.font()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.setPen(QPen(QColor(255, 69, 58, 220)))
            v_val = t['velocity']
            v_str = f" {v_val:+.2f} m/s"
            painter.drawText(int(tx) + 10, int(ty) + 4, f"{t['avg_r']:.2f} m{v_str}")

        # 5. Draw target strength colorbar on the right
        cb_width = 12
        cb_height = 120
        cb_x = width - 40
        cb_y = 50
        
        # Background/border
        painter.setPen(QPen(QColor(0, 255, 100, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cb_x - 1, cb_y - 1, cb_width + 2, cb_height + 2)
        
        # Gradient fill
        for y_offset in range(cb_height):
            frac = 1.0 - (y_offset / cb_height)
            strength_val = self.min_detected_strength + frac * (self.max_detected_strength - self.min_detected_strength)
            color = self.get_target_color(strength_val)
            painter.setPen(QPen(color, 1))
            painter.drawLine(cb_x, cb_y + y_offset, cb_x + cb_width, cb_y + y_offset)
            
        # Labels
        font = painter.font()
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        painter.setPen(QPen(QColor(0, 255, 100, 160)))
        painter.drawText(cb_x - 55, cb_y + 10, f"Max ({self.max_detected_strength:.1f})")
        painter.drawText(cb_x - 55, cb_y + cb_height, f"Min ({self.min_detected_strength:.1f})")
        
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(cb_x - 45, cb_y - 10, "Strength")

        # Draw selection rectangle if active
        if hasattr(self, 'is_selecting') and self.is_selecting and self.zoom_start_pos and self.zoom_current_pos:
            x1, y1 = self.zoom_start_pos.x(), self.zoom_start_pos.y()
            x2, y2 = self.zoom_current_pos.x(), self.zoom_current_pos.y()
            
            box_pen = QPen(QColor(0, 255, 100, 200), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(box_pen)
            painter.setBrush(QBrush(QColor(0, 255, 100, 25)))
            painter.drawRect(int(min(x1, x2)), int(min(y1, y2)), int(abs(x1 - x2)), int(abs(y1 - y2)))

class DataReceiver(QThread):
    data_received = pyqtSignal(np.ndarray, int, int)
    target_received = pyqtSignal(float, int, float, float, int)
    status_changed = pyqtSignal(str)

    def __init__(self, host='esp32.local', port=8080, initial_configs=None):
        super().__init__()
        self.host = host
        self.port = port
        self.running = False
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.sock.settimeout(2.0)
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

            CHUNK_HEADER_SIZE = 4
            CHUNK_SAMPLES = 512
            CHUNKS_PER_FRAME = 4
            CHUNK_PACKET_SIZE = CHUNK_HEADER_SIZE + CHUNK_SAMPLES * 2

            current_frame_id = {0: None, 1: None, 2: None}
            chunks = {0: {}, 1: {}, 2: {}}

            while self.running:
                try:
                    data, _addr = self.sock.recvfrom(65536)
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

        # Đồ thị tín hiệu miền thời gian bên phải (gồm 3 đồ thị riêng biệt)
        right_layout = QVBoxLayout()
        right_layout.setSpacing(10)

        self.plot_widget0 = pg.PlotWidget(title="Rx 0 (Sum Channel) Received Signal")
        self.plot_widget0.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.plot_widget0.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=15.0)
        self.plot_widget0.setYRange(0, 13.5)
        self.plot_widget0.setXRange(0, 2048)
        self.plot_widget0.setLabel('left', 'Voltage', units='V')
        self.plot_widget0.setLabel('bottom', 'Sample Index')
        self.plot_widget0.showGrid(x=True, y=True)
        self.curve0 = self.plot_widget0.plot(pen=pg.mkPen('c', width=1.5))
        right_layout.addWidget(self.plot_widget0)

        self.plot_widget = pg.PlotWidget(title="Rx 1 (GPIO 32) Received Signal")
        self.plot_widget.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.plot_widget.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=3.5)
        self.plot_widget.setYRange(0, 3.3)
        self.plot_widget.setXRange(0, 2048)
        self.plot_widget.setLabel('left', 'Voltage', units='V')
        self.plot_widget.setLabel('bottom', 'Sample Index')
        self.plot_widget.showGrid(x=True, y=True)
        self.curve = self.plot_widget.plot(pen=pg.mkPen('y', width=1.5))
        right_layout.addWidget(self.plot_widget)

        self.plot_widget2 = pg.PlotWidget(title="Rx 2 (GPIO 33) Received Signal")
        self.plot_widget2.getViewBox().setMouseMode(pg.ViewBox.RectMode)
        self.plot_widget2.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=3.5)
        self.plot_widget2.setYRange(0, 3.3)
        self.plot_widget2.setXRange(0, 2048)
        self.plot_widget2.setLabel('left', 'Voltage', units='V')
        self.plot_widget2.setLabel('bottom', 'Sample Index')
        self.plot_widget2.showGrid(x=True, y=True)
        self.curve2 = self.plot_widget2.plot(pen=pg.mkPen('m', width=1.5))
        right_layout.addWidget(self.plot_widget2)

        top_layout.addLayout(right_layout, stretch=1)

        # SNR Labels overlaying the plot widgets
        self.snr_label0 = QLabel("SNR: -- dB", self.plot_widget0)
        self.snr_label = QLabel("SNR: -- dB", self.plot_widget)
        self.snr_label2 = QLabel("SNR: -- dB", self.plot_widget2)
        
        for label in [self.snr_label0, self.snr_label, self.snr_label2]:
            label.setStyleSheet("color: #4CD964; background-color: rgba(9, 13, 22, 200); border: 1px solid rgba(0, 255, 100, 100); padding: 3px 6px; border-radius: 4px; font-family: Menlo, Monaco, 'Courier New', monospace; font-size: 11px; font-weight: bold;")
            label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            label.setFixedWidth(90)
            label.setFixedHeight(22)
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)

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

        # Dòng 2: Cấu hình tín hiệu và trạng thái hiển thị
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

            initial_configs = [f"cfg:{pulse_type}", f"mode:{mode}", servo_cmd, atten_cmd, tx_cmd, "servo:90", "start"]

            self.receiver = DataReceiver(host=host, initial_configs=initial_configs)
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

    def reset_zoom(self):
        idx = self.signal_type_combo.currentIndex()
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else (13.5 if idx == 2 else 3.3)
        self.current_y_max0 = 0.01 if self.autoscale_cb.isChecked() else 13.5
        self.plot_widget0.setYRange(0, self.current_y_max0)
        self.plot_widget0.setXRange(0, 2048)
        self.plot_widget.setYRange(0, self.current_y_max)
        self.plot_widget.setXRange(0, 2048)
        self.plot_widget2.setYRange(0, self.current_y_max)
        self.plot_widget2.setXRange(0, 2048)
        self.radar_widget.reset_zoom()

    def toggle_autoscale_cb(self, state):
        if self.autoscale_cb.isChecked():
            self.current_y_max = 0.01  # Set to tiny value so next frame auto-scales to current peak
            self.current_y_max0 = 0.01
        else:
            idx = self.signal_type_combo.currentIndex()
            self.current_y_max = 13.5 if idx == 2 else 3.3
            self.current_y_max0 = 13.5
        self.plot_widget0.setYRange(0, self.current_y_max0)
        self.plot_widget.setYRange(0, self.current_y_max)
        self.plot_widget2.setYRange(0, self.current_y_max)

    def change_pulse_type(self):
        pulse_type = self.pulse_type_combo.currentText().lower()
        self.get_receiver().pulse_type = pulse_type
        self.get_receiver().send_command(f"cfg:{pulse_type}")
        self.info_label.setText(f"Config sent: {pulse_type}")
        idx = self.signal_type_combo.currentIndex()
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else (13.5 if idx == 2 else 3.3)
        self.current_y_max0 = 0.01 if self.autoscale_cb.isChecked() else 13.5
        self.plot_widget0.setYRange(0, self.current_y_max0)
        self.plot_widget.setYRange(0, self.current_y_max)
        self.plot_widget2.setYRange(0, self.current_y_max)

    def change_signal_type(self):
        idx = self.signal_type_combo.currentIndex()
        if idx == 0:
            mode = "raw"
            y_lim = 3.5
            default_y = 3.3
        elif idx == 1:
            mode = "demod"
            y_lim = 3.5
            default_y = 3.3
        else:
            mode = "compressed"
            y_lim = 15.0
            default_y = 13.5
        
        self.plot_widget0.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=15.0)
        self.plot_widget.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=y_lim)
        self.plot_widget2.getViewBox().setLimits(xMin=0, xMax=2048, yMin=-0.2, yMax=y_lim)
        
        self.current_y_max = 0.01 if self.autoscale_cb.isChecked() else default_y
        self.current_y_max0 = 0.01 if self.autoscale_cb.isChecked() else 13.5
        
        self.plot_widget0.setYRange(0, self.current_y_max0)
        self.plot_widget.setYRange(0, self.current_y_max)
        self.plot_widget2.setYRange(0, self.current_y_max)
        self.get_receiver().send_command(f"mode:{mode}")
        self.info_label.setText(f"Mode command sent: mode:{mode}")

    def update_target(self, range_val, angle, strength, velocity, receiver_id=0):
        if self.is_paused:
            return
        # Extract clean angle (MSB is sweep direction)
        angle_int = int(angle)
        clean_angle = angle_int & 0x7FFF

        # Apply exponential moving average (IIR filter) to smooth velocity display
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
        if len(samples) > 0:
            # Convert raw Q15 samples to voltages in the main GUI thread
            if receiver_id == 0:
                # Rx0 is 8-pulse accumulated Sum (Q15 max 32767 -> 13.2V max scale)
                voltages = (samples / 32767.0) * 13.2

            else:
                stream_idx = self.signal_type_combo.currentIndex()
                if stream_idx == 0:  # Raw
                    voltages = (samples / 32768.0) * 1.65 + 1.65
                elif stream_idx == 2:  # Compressed
                    voltages = np.clip((samples / 8192.0) * 3.3, 0.0, 13.2)
                else:  # Demodulated
                    voltages = (samples / 32768.0) * 3.3
            
            self.latest_voltages = voltages

            # Calculate SNR using windowed Signal RMS to Noise RMS
            if len(voltages) > 120 and self.tx_switch.isChecked():
                active_voltages = voltages[120:]
                baseline = np.median(active_voltages)
                deviation = np.abs(active_voltages - baseline)
                
                # Find peak index using a smoothed deviation to target coherent pulses and reject single-sample noise spikes
                smoothing_win = 5
                smoothed_dev = np.convolve(deviation, np.ones(smoothing_win)/smoothing_win, mode='same')
                peak_idx_active = np.argmax(smoothed_dev)
                peak_idx = 120 + peak_idx_active
                
                # Define CFAR-like window parameters (CUT, Guard, and Reference Cells)
                pulse_type = self.pulse_type_combo.currentText().lower()
                if pulse_type == 'barker13':
                    cut_size = 7       # Cell Under Test: compressed mainlobe peak
                    guard_size = 52    # Guard cells on each side to cover the full 104-sample Barker 13 response extent
                else:
                    cut_size = 7       # Cell Under Test: peak core
                    guard_size = 16    # Guard cells on each side to cover the rest of the 32-sample pulse

                
                # Define CUT (Signal) region
                cut_start = max(120, peak_idx - cut_size // 2)
                cut_end = min(len(voltages), peak_idx + cut_size // 2 + 1)
                signal_samples = voltages[cut_start:cut_end]
                
                # Define Guard region boundaries (to be excluded from Noise Reference Cells)
                guard_start = max(120, peak_idx - cut_size // 2 - guard_size)
                guard_end = min(len(voltages), peak_idx + cut_size // 2 + guard_size + 1)
                
                # Reference Cells (Noise region): active region excluding the Guard zone
                noise_samples = np.concatenate([voltages[120:guard_start], voltages[guard_end:]])
                
                # True Radar Peak Signal Amplitude (AC peak of compressed pulse above baseline)
                signal_peak = np.max(signal_samples) - baseline
                
                # Noise RMS using robust MAD on isolated Reference Cells
                noise_baseline = np.median(noise_samples)
                noise_deviation = np.abs(noise_samples - noise_baseline)
                mad = np.median(noise_deviation)
                noise_rms = mad / 0.6745 if mad > 1e-6 else np.std(noise_samples)
                
                if noise_rms > 1e-6 and signal_peak > 1e-6:
                    raw_snr = 20 * np.log10(signal_peak / noise_rms)

                    
                    # Calibrate out the peak selection bias (noise floor peaks)
                    is_compressed = (receiver_id == 0) or (self.signal_type_combo.currentIndex() == 2)
                    bias = 8.0 if is_compressed else 6.2
                    
                    calibrated_snr = raw_snr - bias
                    
                    # Display targets down to 1.0 dB of calibrated SNR
                    if calibrated_snr > 1.0:
                        snr_str = f"SNR: {calibrated_snr:.1f} dB"
                    else:
                        snr_str = "SNR: -- dB"
                else:
                    snr_str = "SNR: -- dB"
            else:
                snr_str = "SNR: -- dB"

            # Update corresponding SNR label
            if receiver_id == 0:
                self.snr_label0.setText(snr_str)
            elif receiver_id == 1:
                self.snr_label.setText(snr_str)
            elif receiver_id == 2:
                self.snr_label2.setText(snr_str)
            
            # Shift voltages to align radar history with the target distance (correcting for filter delay)
            pulse_type = self.pulse_type_combo.currentText().lower()
            filter_len = 104 if pulse_type == 'barker13' else 8
            
            shifted_voltages = np.zeros_like(voltages)
            shifted_voltages[:-filter_len] = voltages[filter_len:]
            shifted_voltages[-filter_len:] = np.median(voltages)
            
            if receiver_id == 0:
                self.radar_widget.set_data(angle, shifted_voltages)
                self.curve0.setData(voltages)
            elif receiver_id == 1:
                self.radar_widget.set_angle(angle)
                self.curve.setData(voltages)
            elif receiver_id == 2:
                self.curve2.setData(voltages)
            
            # Peak Hold Auto Scale (keeps Y-axis fit to the maximum peak value)
            if self.autoscale_cb.isChecked() and len(voltages) > 120:
                active_voltages = voltages[120:]
                valid_samples = active_voltages[np.isfinite(active_voltages)]
                if len(valid_samples) > 0:
                    peak = np.max(valid_samples)
                    if np.isfinite(peak):
                        if receiver_id == 0:
                            # Rx0 is always Compressed Sum (capped at 13.5)
                            target_y_max0 = min(max(peak * 1.15, 0.01), 13.5)
                            if target_y_max0 > self.current_y_max0:
                                self.current_y_max0 = target_y_max0
                                self.plot_widget0.setYRange(0, self.current_y_max0)
                        else:
                            max_cap = 13.5 if self.signal_type_combo.currentIndex() == 2 else 3.3
                            target_y_max = min(max(peak * 1.15, 0.01), max_cap)
                            if target_y_max > self.current_y_max:
                                self.current_y_max = target_y_max
                                self.plot_widget.setYRange(0, self.current_y_max)
                                self.plot_widget2.setYRange(0, self.current_y_max)
            
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
        if not hasattr(self, 'snr_label0') or not hasattr(self, 'snr_label') or not hasattr(self, 'snr_label2'):
            return
        for label, plot in [(self.snr_label0, self.plot_widget0), 
                             (self.snr_label, self.plot_widget), 
                             (self.snr_label2, self.plot_widget2)]:
            if label and plot:
                # Position in top right corner of the plot widget, offset from the right boundary to avoid scrollbar/axes
                label.move(plot.width() - 100, 10)

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
