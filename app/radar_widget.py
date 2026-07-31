import numpy as np
from PyQt6.QtWidgets import QWidget
from PyQt6.QtGui import QPainter, QColor, QPen, QBrush, QPolygonF
from PyQt6.QtCore import pyqtSignal, Qt, QTimer, QPointF
from constants import (
    MAX_SAMPLES, SPEED_OF_SOUND, FS, MAX_RANGE,
    DEFAULT_MIN_STRENGTH, DEFAULT_MAX_STRENGTH,
    INTERPOLATION_INTERVAL_MS, DECAY_RATE, MIN_VISIBILITY_THRESHOLD,
    RADAR_BG_COLOR, RADAR_TEXT_GREEN, TARGET_COLOR_STOPS,
    RADAR_CONCENTRIC_RINGS, RADAR_GRID_ANGLES, RADAR_LABEL_ANGLES,
    RADAR_SECTOR_STEP_HALF, RADAR_SWEEP_TRAIL_SLICES,
    RADAR_SWEEP_TRAIL_SLICE_WIDTH, RADAR_SWEEP_MAX_ALPHA,
    CLUSTER_ANGLE_THRESHOLD_DEG, CLUSTER_RANGE_THRESHOLD_M,
    CLUSTER_MIN_ANGLE_SIZE_DEG, CLUSTER_MIN_RANGE_SIZE_M
)
from service.signal_processor import process_radar_intensities

class RadarWidget(QWidget):
    angle_requested = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.servo_enabled = False
        self.current_angle = 90
        self.sweep_direction = 1  # 1 for CCW (increasing), -1 for CW (decreasing)
        self.history = {}  # Maps angle (int) -> numpy array of normalized intensities
        self.max_samples = MAX_SAMPLES
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
        self.interpolation_timer.start(INTERPOLATION_INTERVAL_MS)
        self.min_detected_strength = DEFAULT_MIN_STRENGTH
        self.max_detected_strength = DEFAULT_MAX_STRENGTH
        self.zoom_start_pos = None
        self.zoom_current_pos = None
        self.is_selecting = False

    def interpolate_angle(self):
        diff = self.target_angle - self.current_angle
        
        # Decay older sweeps (simulating phosphor decay) at ~60 FPS
        has_history = False
        for d in list(self.history.keys()):
            if d != int(self.target_angle):
                self.history[d] = self.history[d] * DECAY_RATE
                if np.max(self.history[d]) < MIN_VISIBILITY_THRESHOLD:
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
        
        stops = TARGET_COLOR_STOPS
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
        normalized = process_radar_intensities(samples, None)
        if len(normalized) > 0:
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
                        if ang_diff <= CLUSTER_ANGLE_THRESHOLD_DEG and range_diff <= CLUSTER_RANGE_THRESHOLD_M:
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
            best_detection = max(cluster, key=lambda t: t[2])
            avg_v = best_detection[3]
            
            if max_a - min_a < CLUSTER_MIN_ANGLE_SIZE_DEG:
                center_a = (min_a + max_a) / 2.0
                min_a = center_a - CLUSTER_MIN_ANGLE_SIZE_DEG / 2.0
                max_a = center_a + CLUSTER_MIN_ANGLE_SIZE_DEG / 2.0
            if max_r - min_r < CLUSTER_MIN_RANGE_SIZE_M:
                center_r = (min_r + max_r) / 2.0
                min_r = max(0.0, center_r - CLUSTER_MIN_RANGE_SIZE_M / 2.0)
                max_r = center_r + CLUSTER_MIN_RANGE_SIZE_M / 2.0
                
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
        max_range = MAX_RANGE
        
        # Draw deep dark space background
        painter.fillRect(self.rect(), QColor(RADAR_BG_COLOR))

        # Draw current servo angle
        text_pen = QPen(QColor(RADAR_TEXT_GREEN))
        painter.setPen(text_pen)
        font = painter.font()
        font.setPointSize(12)
        font.setBold(True)
        painter.setFont(font)
        painter.drawText(20, 30, f"Servo Angle: {int(self.current_angle)}°")
        
        center_x = width // 2 + int(self.pan_x)
        center_y = int(height * 0.9) + int(self.pan_y)
        
        max_radius = int(min(width // 2 - 40, int(height * 0.8)) * self.zoom_factor)
        if max_radius < 50:
            return
            
        # Draw concentric arcs
        grid_pen = QPen(QColor(0, 255, 100, 40), 1, Qt.PenStyle.DashLine)
        painter.setPen(grid_pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)
        
        for i in range(1, RADAR_CONCENTRIC_RINGS + 1):
            r = int(max_radius * i / RADAR_CONCENTRIC_RINGS)
            painter.drawArc(center_x - r, center_y - r, r * 2, r * 2, 0, 180 * 16)
            
        # Draw radial angle reference lines
        for angle_deg in RADAR_GRID_ANGLES:
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
        
        for angle_deg in RADAR_LABEL_ANGLES:
            rad = np.radians(angle_deg)
            offset_x = -10 if angle_deg == 90 else (-25 if angle_deg > 90 else 5)
            offset_y = -10 if angle_deg in [0, 180] else -5
            x = center_x + (max_radius + 15) * np.cos(rad) + offset_x
            y = center_y - (max_radius + 15) * np.sin(rad) + offset_y
            painter.drawText(int(x), int(y), f"{angle_deg}°")
            
        # Draw range markers
        font.setPointSize(8)
        font.setBold(False)
        painter.setFont(font)
        for i in range(1, RADAR_CONCENTRIC_RINGS + 1):
            r = int(max_radius * i / RADAR_CONCENTRIC_RINGS)
            dist_m = max_range * i / RADAR_CONCENTRIC_RINGS
            label = f"{dist_m:.2f}m"
            painter.drawText(center_x + 5, center_y - r - 2, label)

        # 1. Draw Sonar echo history
        painter.setPen(Qt.PenStyle.NoPen)
        step_half = RADAR_SECTOR_STEP_HALF
        for deg, intensities in self.history.items():
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
                    
        # 2. Draw smooth fading sweep wedge
        painter.setPen(Qt.PenStyle.NoPen)
        trail_dir = -self.sweep_direction
        num_slices = RADAR_SWEEP_TRAIL_SLICES
        slice_width = RADAR_SWEEP_TRAIL_SLICE_WIDTH
        
        for i in range(num_slices):
            a1_deg = np.clip(self.current_angle + i * slice_width * trail_dir, 0.0, 180.0)
            a2_deg = np.clip(self.current_angle + (i + 1) * slice_width * trail_dir, 0.0, 180.0)
            
            a1 = np.radians(a1_deg)
            a2 = np.radians(a2_deg)
            
            p1_x = center_x + max_radius * np.cos(a1)
            p1_y = center_y - max_radius * np.sin(a1)
            p2_x = center_x + max_radius * np.cos(a2)
            p2_y = center_y - max_radius * np.sin(a2)
            
            factor = (1.0 - i / num_slices) ** 2.0
            alpha = int(RADAR_SWEEP_MAX_ALPHA * factor)
            
            painter.setBrush(QColor(0, 255, 100, alpha))
            painter.drawPolygon(QPolygonF([
                QPointF(center_x, center_y),
                QPointF(p1_x, p1_y),
                QPointF(p2_x, p2_y)
            ]))
                    
        # 3. Draw active scanning beam line
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
        clustered = self.get_clustered_targets()
        
        for t in clustered:
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
            
            for a in angles:
                rad = np.radians(a)
                px = center_x + max_r_pix * np.cos(rad)
                py = center_y - max_r_pix * np.sin(rad)
                polygon_points.append(QPointF(px, py))
                
            for a in reversed(angles):
                rad = np.radians(a)
                px = center_x + min_r_pix * np.cos(rad)
                py = center_y - min_r_pix * np.sin(rad)
                polygon_points.append(QPointF(px, py))
                
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
        
        painter.setPen(QPen(QColor(0, 255, 100, 80), 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawRect(cb_x - 1, cb_y - 1, cb_width + 2, cb_height + 2)
        
        for y_offset in range(cb_height):
            frac = 1.0 - (y_offset / cb_height)
            strength_val = self.min_detected_strength + frac * (self.max_detected_strength - self.min_detected_strength)
            color = self.get_target_color(strength_val)
            painter.setPen(QPen(color, 1))
            painter.drawLine(cb_x, cb_y + y_offset, cb_x + cb_width, cb_y + y_offset)
            
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

        if hasattr(self, 'is_selecting') and self.is_selecting and self.zoom_start_pos and self.zoom_current_pos:
            x1, y1 = self.zoom_start_pos.x(), self.zoom_start_pos.y()
            x2, y2 = self.zoom_current_pos.x(), self.zoom_current_pos.y()
            
            box_pen = QPen(QColor(0, 255, 100, 200), 1.5, Qt.PenStyle.DashLine)
            painter.setPen(box_pen)
            painter.setBrush(QBrush(QColor(0, 255, 100, 25)))
            painter.drawRect(int(min(x1, x2)), int(min(y1, y2)), int(abs(x1 - x2)), int(abs(y1 - y2)))
