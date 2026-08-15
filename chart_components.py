import sys
from PyQt6.QtWidgets import QWidget, QToolTip
from PyQt6.QtGui import QPainter, QColor, QFont, QPen, QBrush
from PyQt6.QtCore import Qt, QRect

class BarChartWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMouseTracking(True)
        self.data = [] # list of dict: {"label": "월", "cost": 1.50, "details": "..."}
        self.max_cost = 0.0
        self.bars = [] # stores (QRect, dict) for mouse tracking

    def set_data(self, data):
        self.data = data
        self.max_cost = max([d["cost"] for d in data]) if data else 0.0
        # 여유 공간을 위해 max_cost 살짝 높임
        if self.max_cost > 0:
            self.max_cost *= 1.2
        else:
            self.max_cost = 1.0 # default scale if 0
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        width = self.width()
        height = self.height()
        
        # 배경 (다크테마)
        painter.fillRect(0, 0, width, height, QColor("#1e2129"))
        
        if not self.data:
            painter.setPen(QColor("#888c99"))
            font = QFont("Malgun Gothic", 16, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(self.rect(), Qt.AlignmentFlag.AlignCenter, "데이터가 없습니다.")
            return

        margin_left = 70
        margin_bottom = 40
        margin_top = 30
        margin_right = 20
        
        plot_width = width - margin_left - margin_right
        plot_height = height - margin_top - margin_bottom
        
        # y축 선 그리기
        painter.setPen(QPen(QColor("#3a3f4c"), 1))
        painter.drawLine(margin_left, margin_top, margin_left, height - margin_bottom)
        # x축 선 그리기
        painter.drawLine(margin_left, height - margin_bottom, width - margin_right, height - margin_bottom)
        
        # Y축 라벨 (최대 비용, 절반 비용)
        painter.setPen(QColor("#888c99"))
        font = QFont("Malgun Gothic", 12, QFont.Weight.Bold)
        painter.setFont(font)
        
        y_labels = [0, self.max_cost / 2, self.max_cost]
        for y_val in y_labels:
            y_pos = height - margin_bottom - int((y_val / self.max_cost) * plot_height)
            painter.drawText(0, y_pos - 15, margin_left - 10, 30, Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, f"${y_val:.2f}")
            # 가로 가이드라인
            painter.setPen(QPen(QColor("#3a3f4c"), 1, Qt.PenStyle.DashLine))
            painter.drawLine(margin_left, y_pos, width - margin_right, y_pos)
            painter.setPen(QColor("#888c99"))

        # 막대 그리기
        num_bars = len(self.data)
        bar_width = int(plot_width / num_bars * 0.6)
        spacing = int(plot_width / num_bars * 0.4)
        
        self.bars.clear()
        
        for i, item in enumerate(self.data):
            x = margin_left + i * (bar_width + spacing) + spacing // 2
            bar_h = int((item["cost"] / self.max_cost) * plot_height)
            y = height - margin_bottom - bar_h
            
            rect = QRect(x, y, bar_width, bar_h)
            
            # 그라데이션 대신 심플한 단색 바
            if item["cost"] > 0:
                painter.fillRect(rect, QColor("#2a64f6"))
            else:
                # 0원일 경우 아주 작은 바 (또는 안그림)
                pass
                
            self.bars.append((rect, item))
            
            # X축 라벨
            painter.setPen(QColor("#e0e0e0"))
            painter.drawText(x, height - margin_bottom + 10, bar_width, 25, Qt.AlignmentFlag.AlignCenter, item["label"])
            
            # 바 위쪽에 비용 텍스트
            if item["cost"] > 0:
                painter.setPen(QColor("#00e5ff"))
                painter.drawText(x - 20, y - 25, bar_width + 40, 20, Qt.AlignmentFlag.AlignCenter, f"${item['cost']:.2f}")

    def mouseMoveEvent(self, event):
        pos = event.pos()
        found = False
        for rect, item in self.bars:
            # y값 상관없이 해당 x 구간에 있으면 툴팁 띄워주기 (더 쉽게 hover 가능)
            if rect.x() <= pos.x() <= rect.x() + rect.width():
                if item["cost"] > 0:
                    QToolTip.showText(event.globalPosition().toPoint(), item["details"], self)
                else:
                    QToolTip.showText(event.globalPosition().toPoint(), f"{item['label']}: 내역 없음", self)
                found = True
                break
        if not found:
            QToolTip.hideText()
