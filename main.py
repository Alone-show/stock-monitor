import sys
import time
import json
import os
import yfinance as yf
from datetime import datetime  # 新增: 用于获取时间
from PyQt6.QtWidgets import (QApplication, QWidget, QLabel, QGridLayout, 
                             QMenu, QInputDialog, QSystemTrayIcon, QStyle)
from PyQt6.QtCore import Qt, QThread, pyqtSignal, QTimer
from PyQt6.QtGui import QFont, QColor, QPainter, QPainterPath, QPen, QAction

# ================= 环境设置 =================
os.environ["QT_LOGGING_RULES"] = "*.debug=false;qt.qpa.qgnomeplatform=false"
os.environ["QT_QPA_PLATFORM"] = "xcb"
# ===========================================

# ================= 默认配置 =================
CONFIG_FILE = "stock_config.json"
DEFAULT_CONFIG = {
    "stocks": {
        '000880.SZ': '潍柴动力',
        'NVDA':      '英伟达',
    },
    "x": 100,
    "y": 100,
    "locked": False
}

COLOR_UP = "#FF5555"    # 红
COLOR_DOWN = "#50FA7B"  # 绿
COLOR_FLAT = "#F8F8F2"  # 白
COLOR_TIME = "#6272A4"  # 时间显示颜色 (灰蓝)
FONT_NAME = "Noto Sans CJK SC" 
FONT_SIZE = 11
REFRESH_INTERVAL = 15 
# ===========================================

class ConfigManager:
    @staticmethod
    def load():
        if not os.path.exists(CONFIG_FILE): return DEFAULT_CONFIG.copy()
        try:
            with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
                cfg = json.load(f)
                if "stocks" not in cfg: return DEFAULT_CONFIG.copy()
                return cfg
        except: return DEFAULT_CONFIG.copy()

    @staticmethod
    def save(config):
        try:
            with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, indent=4, ensure_ascii=False)
        except: pass

class FetchThread(QThread):
    data_signal = pyqtSignal(dict)
    
    def __init__(self, stock_map):
        super().__init__()
        self.stock_map = stock_map 
        self.running = True

    def update_map(self, new_map):
        self.stock_map = new_map

    def run(self):
        while self.running:
            try:
                codes = list(self.stock_map.keys())
                if not codes: time.sleep(2); continue
                
                print(f"--- 刷新 {time.strftime('%H:%M:%S')} ---")
                
                result = {}
                # 逐个获取更稳定
                for symbol in codes:
                    try:
                        print(f"Fetch: {symbol}...", end=" ", flush=True)
                        ticker = yf.Ticker(symbol)
                        df = ticker.history(period="5d", interval="5m") 
                        
                        if df is None or df.empty or 'Close' not in df.columns:
                            print("空数据")
                            continue
                            
                        current_price = df['Close'].iloc[-1]
                        
                        # 计算涨跌
                        try:
                            info = ticker.fast_info
                            prev_close = info.previous_close
                            if prev_close and prev_close > 0:
                                base_price = prev_close
                            else:
                                base_price = df['Close'].iloc[0]
                        except:
                            base_price = df['Close'].iloc[0]

                        pct = ((current_price - base_price) / base_price) * 100
                        hist_list = df['Close'].tail(30).tolist()
                        
                        result[symbol] = {
                            'price': current_price,
                            'pct': pct,
                            'history': hist_list
                        }
                        print("OK")
                    except Exception as e:
                        print(f"Err: {e}")

                if result:
                    self.data_signal.emit(result)

            except Exception as e:
                print(f"Network Err: {e}")
            
            time.sleep(REFRESH_INTERVAL)

    def stop(self): self.running = False

class ShadowLabel(QLabel):
    def __init__(self, text="", color="#FFFFFF", parent=None, size=FONT_SIZE):
        super().__init__(text, parent)
        self.text_color = QColor(color)
        self.shadow_color = QColor(0, 0, 0, 180) 
        self.setFont(QFont(FONT_NAME, size, QFont.Weight.Bold))
    
    def set_color(self, color_hex):
        self.text_color = QColor(color_hex)
        self.update() 
    
    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        rect = self.contentsRect()
        text = self.text()
        font = self.font()
        painter.setPen(self.shadow_color)
        painter.setFont(font)
        painter.drawText(rect.translated(1, 1), self.alignment(), text)
        painter.setPen(self.text_color)
        painter.drawText(rect, self.alignment(), text)

class Sparkline(QWidget):
    def __init__(self):
        super().__init__()
        self.setFixedSize(60, 25)
        self.prices = []
        self.color = QColor(COLOR_FLAT)
    def update_data(self, prices, color_hex):
        self.prices = prices
        self.color = QColor(color_hex)
        self.update()
    def paintEvent(self, event):
        if len(self.prices) < 2: return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        path = QPainterPath()
        w, h = self.width(), self.height()
        min_p, max_p = min(self.prices), max(self.prices)
        if max_p == min_p:
            path.moveTo(0, h/2); path.lineTo(w, h/2)
        else:
            step_x = w / (len(self.prices) - 1)
            for i, p in enumerate(self.prices):
                x = i * step_x
                norm = (p - min_p) / (max_p - min_p)
                y = h - (norm * (h - 6)) - 3
                if i == 0: path.moveTo(x, y)
                else: path.lineTo(x, y)
        pen = QPen(self.color, 2)
        shadow_pen = QPen(QColor(0,0,0,100), 2)
        painter.setPen(shadow_pen)
        painter.drawPath(path.translated(1, 1))
        painter.setPen(pen)
        painter.drawPath(path)

class DesktopWidget(QWidget):
    def __init__(self):
        super().__init__()
        self.config = ConfigManager.load()
        self.locked = self.config.get("locked", False)
        self.boss_hidden = False
        self.lbl_time = None # 时间标签引用
        
        self.init_base_properties()
        self.init_tray()
        
        self.layout = QGridLayout()
        self.layout.setSpacing(10)
        self.layout.setContentsMargins(10, 10, 10, 10)
        self.setLayout(self.layout)
        
        self.rows = {} 
        self.rebuild_ui()
        
        self.thread = FetchThread(self.config['stocks'])
        self.thread.data_signal.connect(self.update_ui_data)
        self.thread.start()
        
        self.apply_lock_state()
        self.z_timer = QTimer(self)
        self.z_timer.timeout.connect(self.enforce_top_level)
        self.z_timer.start(2000)

    def init_base_properties(self):
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.move(self.config.get('x', 100), self.config.get('y', 100))
    def init_tray(self):
        self.tray_icon = QSystemTrayIcon(self)
        icon = self.style().standardIcon(QStyle.StandardPixmap.SP_ComputerIcon)
        self.tray_icon.setIcon(icon)
        self.tray_icon.setToolTip("股票行情")
        self.tray_icon.activated.connect(self.on_tray_click)
        self.tray_menu = QMenu()
        self.update_tray_menu()
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()
    def on_tray_click(self, reason):
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            if self.isVisible():
                self.boss_hidden = True; self.hide()
            else:
                self.boss_hidden = False; self.apply_lock_state()
    def update_tray_menu(self):
        self.tray_menu.clear()
        self.tray_menu.addAction("👁 老板键", lambda: self.on_tray_click(QSystemTrayIcon.ActivationReason.Trigger))
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("🔒 锁定/解锁", self.toggle_lock)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("➕ 添加", self.add_stock_dialog)
        del_menu = self.tray_menu.addMenu("➖ 删除")
        if not self.config['stocks']: del_menu.setDisabled(True)
        else:
            for s, n in self.config['stocks'].items():
                del_menu.addAction(n, lambda sym=s: self.delete_stock(sym))
        self.tray_menu.addSeparator()
        self.tray_menu.addAction("❌ 退出", QApplication.quit)
    def enforce_top_level(self):
        if not self.boss_hidden and self.isVisible() and self.locked: self.raise_()
    def apply_lock_state(self):
        self.hide(); 
        if self.boss_hidden: return
        flags = Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool
        if self.locked:
            flags |= Qt.WindowType.X11BypassWindowManagerHint | Qt.WindowType.WindowTransparentForInput
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        else:
            self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, False)
        self.setWindowFlags(flags)
        self.show()
        if self.locked: QTimer.singleShot(100, self.raise_)
        self.update_tray_menu()
    def toggle_lock(self):
        self.locked = not self.locked
        self.config['locked'] = self.locked
        ConfigManager.save(self.config)
        self.apply_lock_state()
    def mouseDoubleClickEvent(self, e):
        if not self.locked and e.button() == Qt.MouseButton.LeftButton:
            self.boss_hidden = True; self.hide()
    def mousePressEvent(self, e):
        if not self.locked and e.button() == Qt.MouseButton.LeftButton:
            self.drag_pos = e.globalPosition().toPoint() - self.frameGeometry().topLeft(); e.accept()
        elif not self.locked and e.button() == Qt.MouseButton.RightButton:
            self.tray_menu.exec(e.globalPosition().toPoint())
    def mouseMoveEvent(self, e):
        if not self.locked and e.buttons() & Qt.MouseButton.LeftButton:
            self.move(e.globalPosition().toPoint() - self.drag_pos); e.accept()
    def mouseReleaseEvent(self, e):
        if not self.locked and e.button() == Qt.MouseButton.LeftButton:
            self.config['x'], self.config['y'] = self.x(), self.y(); ConfigManager.save(self.config)
    
    # === UI 重建逻辑，包含新增的时间行 ===
    def rebuild_ui(self):
        for i in reversed(range(self.layout.count())): 
            if w := self.layout.itemAt(i).widget(): w.setParent(None)
        self.rows.clear()
        
        idx = 0
        for idx, (sym, name) in enumerate(self.config['stocks'].items()):
            w_p = ShadowLabel("...", COLOR_FLAT); w_p.setAlignment(Qt.AlignmentFlag.AlignRight)
            w_c = ShadowLabel("--%", COLOR_FLAT); w_c.setAlignment(Qt.AlignmentFlag.AlignRight)
            chart = Sparkline()
            self.layout.addWidget(ShadowLabel(name, "#BD93F9"), idx, 0)
            self.layout.addWidget(w_p, idx, 1)
            self.layout.addWidget(w_c, idx, 2)
            self.layout.addWidget(chart, idx, 3)
            self.rows[sym] = {'price': w_p, 'pct': w_c, 'chart': chart}
        
        # === 新增：时间显示行 (占用最后一行，跨4列) ===
        last_row = idx + 1 if self.config['stocks'] else 0
        # 字体稍微小一点 (9号)，颜色淡一点
        self.lbl_time = ShadowLabel("等待更新...", COLOR_TIME, size=11)
        self.lbl_time.setAlignment(Qt.AlignmentFlag.AlignCenter) # 居中显示
        self.layout.addWidget(self.lbl_time, last_row, 0, 1, 4)
        # ==========================================

        self.adjustSize(); self.update_tray_menu()

    def add_stock_dialog(self):
        c, ok = QInputDialog.getText(self, "添加", "代码:"); c=c.strip().upper()
        if ok and c:
            n, ok2 = QInputDialog.getText(self, "添加", "名称:");
            if ok2: self.config['stocks'][c]=n; self.save_and_refresh()
    def delete_stock(self, sym):
        if sym in self.config['stocks']: del self.config['stocks'][sym]; self.save_and_refresh()
    def save_and_refresh(self):
        ConfigManager.save(self.config); self.thread.update_map(self.config['stocks']); self.rebuild_ui()
    
    def update_ui_data(self, data):
        # 1. 更新股票数据
        for s, info in data.items():
            if s not in self.rows: continue
            widgets = self.rows[s]
            pct = info['pct']
            col = COLOR_UP if pct > 0 else (COLOR_DOWN if pct < 0 else COLOR_FLAT)
            sig = "+" if pct > 0 else ""
            widgets['price'].setText(f"{info['price']:.2f}"); widgets['price'].set_color(col)
            widgets['pct'].setText(f"{sig}{pct:.2f}%"); widgets['pct'].set_color(col)
            widgets['chart'].update_data(info['history'], col)
        
        # 2. 更新底部时间
        if self.lbl_time:
            now_str = datetime.now().strftime("%H:%M:%S")
            self.lbl_time.setText(f"更新于: {now_str}")

if __name__ == "__main__":
    app = QApplication(sys.argv); app.setQuitOnLastWindowClosed(False)
    w = DesktopWidget(); 
    if not w.boss_hidden: w.show()
    sys.exit(app.exec())