"""Desktop shell for the trader-brain.

This is the visible app: pick a coin, pull its candles from the Binance public
feed, draw the chart *inside the window*, run the read-only KB analysis,
watch the continuous scanner's signal feed, and rehearse execution in paper or
shadow mode. Live trading is intentionally not wired into this window.

SAFETY INVARIANTS
-----------------
* Chart analysis stays read-only and uses only public market data.
* The Execution tab exposes only paper/shadow modes from broker_adapter.
* A persistent safety banner makes the mode explicit to the user.
* Network/analysis work runs on a worker thread so the UI never blocks and the
  app can never be tempted to do blocking work on the GUI thread.

Run with:  python knowledge_bot/desktop_app.py
Requires:  PySide6, pyqtgraph  (already in the venv).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Make sibling brain modules importable whether run as a script or frozen.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import binance_feed
import bybit_feed
import broker_adapter as exec_layer
import casebook_store as store
from build_level_feedback_statistics import build as build_level_feedback_statistics
from build_level_feedback_statistics import compare_inflection_feedback, read_records, _active_labels
from chart_review_packet import build_live_kb_chart_review_packet
from level_discovery import Bar, DiscoveryParams, discover_levels, level_report, apply_confirmed_inflections
from chart_level_modes import MODE_LABELS as REVIEW_MODE_LABELS, typed_review_levels, feedback_applies_to_mode

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


SCANNER_STATE_PATH = store.ROOT / "_knowledge_base" / "live_scanner" / "scanner_state.json"
EXECUTION_STATE_PATH = store.ROOT / "_knowledge_base" / "live_execution" / "execution_state.json"
EXECUTION_AUDIT_PATH = store.ROOT / "_knowledge_base" / "live_execution" / "execution_audit.jsonl"
USER_LEVEL_FEEDBACK_PATH = store.ROOT / "_knowledge_base" / "user_level_feedback.jsonl"

INTERVALS = ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w"]
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]
INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h"}

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"

MODE_LABELS = {"paper": "Бумажный", "shadow": "Теневой"}
SIDE_LABELS = {"buy": "Покупка", "sell": "Продажа"}
LEVEL_SIDE_LABELS = {"support": "поддержка", "resistance": "сопротивление"}
EXCHANGES = {"Bybit": "bybit", "Binance": "binance"}


def ru_value(value: Any) -> str:
    """Render common machine values in the Russian UI without changing data."""
    labels = {
        True: "да", False: "нет", None: "—",
        "ok": "готово", "pass": "пройдено", "reject": "отклонено",
        "warn": "внимание", "unknown": "неизвестно", "missing": "нет данных",
        "none": "нет", "fail": "ошибка", "support": "поддержка",
        "resistance": "сопротивление", "buy": "покупка", "sell": "продажа",
        "paper": "бумажный", "shadow": "теневой", "released": "выключен",
        "ENGAGED": "ВКЛЮЧЕН",
    }
    return labels.get(value, str(value))


def kb_context_interval(interval: str) -> str:
    return "1d" if interval in INTRADAY_INTERVALS else interval


def kb_higher_interval(context_interval: str) -> str:
    return "1w" if context_interval != "1w" else ""


def kb_limit(interval: str, requested: int) -> int:
    if interval == "1w":
        return min(260, binance_feed.MAX_LIMIT)
    if interval in {"1d", "3d"}:
        return min(max(requested, 365), binance_feed.MAX_LIMIT)
    return min(requested, binance_feed.MAX_LIMIT)


def feed_module(exchange: str):
    return bybit_feed if exchange == "bybit" else binance_feed


def feed_get_ohlc(exchange: str, symbol: str, interval: str, limit: int, market: str) -> dict[str, Any]:
    feed = feed_module(exchange)
    if exchange == "bybit":
        return feed.get_ohlc(symbol, interval=interval, limit=limit, category=market, write_cache=True)
    return feed.get_ohlc(symbol, interval=interval, limit=limit, market=market, write_cache=True)


def feed_cache_path(exchange: str, symbol: str, interval: str, market: str) -> Path:
    feed = feed_module(exchange)
    return feed.cache_path(symbol, interval, market)


def feed_list_symbols(exchange: str, market: str) -> list[str]:
    if exchange == "bybit":
        return bybit_feed.list_symbols(category=market, quote="USDT")
    return binance_feed.list_symbols(market=market, quote="USDT", trading_only=True)


# --------------------------------------------------------------------------- #
# OHLC bar drawing
# --------------------------------------------------------------------------- #
class OHLCBarItem(pg.GraphicsObject):
    """Minimal OHLC bar item for pyqtgraph.

    ``bars`` is a list of (index, open, high, low, close) tuples.
    """

    def __init__(self, bars: list[tuple[float, float, float, float, float]]):
        super().__init__()
        self._bars = bars
        self._picture = QtGui.QPicture()
        self._generate()

    def _generate(self) -> None:
        painter = QtGui.QPainter(self._picture)
        tick_width = 0.32
        for index, open_, high, low, close in self._bars:
            rising = close >= open_
            color = QtGui.QColor(UP_COLOR if rising else DOWN_COLOR)
            painter.setPen(pg.mkPen(color))
            # Vertical high-low range, with open tick to the left and close
            # tick to the right: the conventional OHLC bar representation.
            painter.drawLine(QtCore.QPointF(index, low), QtCore.QPointF(index, high))
            painter.drawLine(QtCore.QPointF(index - tick_width, open_), QtCore.QPointF(index, open_))
            painter.drawLine(QtCore.QPointF(index, close), QtCore.QPointF(index + tick_width, close))
        painter.end()

    def paint(self, painter: QtGui.QPainter, *_args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._picture.boundingRect())


# --------------------------------------------------------------------------- #
# Worker thread: fetch + analyze off the GUI thread
# --------------------------------------------------------------------------- #
def inflection_review_levels(rows: list[dict[str, Any]], confirmations=None) -> list[dict[str, Any]]:
    """Historical review on the displayed timeframe, including distant candidates."""
    bars = [Bar(int(row["open_time_ms"]), *[
        float(row[key]) for key in ("open", "high", "low", "close", "volume")
    ]) for row in rows]
    levels = discover_levels(bars, DiscoveryParams(nearest_window_atr=float("inf")))
    levels = [level for level in levels if "inflection" in level.basis_tags
              or level.inflection_check.get("retained_secondary")]
    levels = apply_confirmed_inflections(bars, levels, confirmations or [])
    return [level_report(level) for level in levels]


class AnalyzeWorker(QtCore.QThread):
    """Fetch OHLC and run the read-only KB analysis on a background thread."""

    finished_ok = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, symbol: str, interval: str, limit: int,
                 exchange: str = "bybit", market: str = "linear", inflection_only: bool = False,
                 review_mode: str | None = None):
        super().__init__()
        self._symbol = symbol
        self._interval = interval
        self._limit = limit
        self._exchange = exchange
        self._market = market
        self._inflection_only = inflection_only
        self._review_mode = review_mode or ('inflection' if inflection_only else None)

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            feed = feed_module(self._exchange)
            symbol = feed.normalize_symbol(self._symbol)
            payload = feed_get_ohlc(
                self._exchange,
                symbol,
                interval=self._interval,
                limit=self._limit,
                market=self._market,
            )
            if self._review_mode in ('mirror_limit', 'paranormal'):
                self.finished_ok.emit({
                    'symbol': symbol, 'exchange': self._exchange, 'market': self._market,
                    'interval': self._interval, 'bars': payload.get('bars') or [],
                    'inflection_levels': typed_review_levels(payload.get('bars') or [], self._review_mode),
                    'review_mode': self._review_mode, 'packet': {},
                })
                return
            if self._review_mode == 'inflection':
                records = read_records(USER_LEVEL_FEEDBACK_PATH)
                inflection_records = [record for record in records
                    if record.get('action') not in ('hide_robot_level', 'restore_robot_level')
                    or feedback_applies_to_mode(record, 'inflection')]
                active, _ = _active_labels(inflection_records)
                confirmations = [record for key, record in active.items()
                    if key[:3] == (self._exchange, symbol, self._interval)
                    and record.get('label') == 'human_accepted'
                    and 'излом_тренда' in record.get('reason_codes', [])]
                automatic = inflection_review_levels(payload.get("bars") or [])
                levels = inflection_review_levels(payload.get("bars") or [], confirmations)
                comparison = compare_inflection_feedback(
                    automatic, records, self._exchange, symbol, self._interval)
                self.finished_ok.emit({
                    "symbol": symbol, "exchange": self._exchange, "market": self._market,
                    "interval": self._interval, "bars": payload.get("bars") or [],
                    "inflection_levels": levels,
                    "human_review_comparison": comparison,
                    "inflection_only": True, "packet": {},
                    "review_mode": "inflection",
                })
                return
            ohlc_path = feed_cache_path(self._exchange, symbol, self._interval, self._market)
            context_interval = kb_context_interval(self._interval)
            context_path = ohlc_path
            if context_interval != self._interval:
                feed_get_ohlc(
                    self._exchange,
                    symbol,
                    interval=context_interval,
                    limit=kb_limit(context_interval, self._limit),
                    market=self._market,
                )
                context_path = feed_cache_path(self._exchange, symbol, context_interval, self._market)
            higher_interval = kb_higher_interval(context_interval)
            higher_path: Path | None = None
            if higher_interval:
                feed_get_ohlc(
                    self._exchange,
                    symbol,
                    interval=higher_interval,
                    limit=kb_limit(higher_interval, self._limit),
                    market=self._market,
                )
                higher_path = feed_cache_path(self._exchange, symbol, higher_interval, self._market)
            packet = build_live_kb_chart_review_packet(
                _packet_args(
                    symbol,
                    ohlc_path,
                    self._interval,
                    f"{self._exchange}_{self._market}",
                    context_ohlc_path=context_path,
                    context_interval=context_interval,
                    higher_ohlc_path=higher_path,
                    higher_interval=higher_interval,
                    feedback_exchange=self._exchange,
                )
            )
            self.finished_ok.emit({
                "symbol": symbol,
                "exchange": self._exchange,
                "market": self._market,
                "interval": self._interval,
                "context_interval": context_interval,
                "higher_interval": higher_interval,
                "bars": payload.get("bars") or [],
                "inflection_levels": inflection_review_levels(payload.get("bars") or []),
                "packet": packet,
            })
        except Exception as exc:  # noqa: BLE001 - surface error to UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SymbolsWorker(QtCore.QThread):
    """Load the tradable symbol list from the public feed."""

    finished_ok = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, exchange: str = "bybit", market: str = "linear", quote: str = "USDT"):
        super().__init__()
        self._exchange = exchange
        self._market = market
        self._quote = quote

    def run(self) -> None:
        try:
            symbols = feed_list_symbols(self._exchange, self._market)
            self.finished_ok.emit(sorted(symbols))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def _packet_args(symbol: str, ohlc_path: Path, interval: str, market: str,
                 *, context_ohlc_path: Path | None = None,
                 context_interval: str | None = None,
                 higher_ohlc_path: Path | None = None,
                 higher_interval: str = "",
                 feedback_exchange: str = "") -> argparse.Namespace:
    """Build the Namespace the read-only packet builder expects."""
    return argparse.Namespace(
        ohlc_file=ohlc_path,
        context_ohlc_file=context_ohlc_path,
        higher_ohlc_file=higher_ohlc_path,
        symbol=symbol,
        instrument=symbol,
        venue=market,
        feedback_exchange=feedback_exchange,
        timeframe=interval,
        context_timeframe=context_interval,
        higher_timeframe=higher_interval,
        direction=None,
        date_session=None,
        level=None,
        entry=None,
        stop=None,
        target=None,
        trigger=None,
        atr_period=14,
        top_k=3,
        no_describe_outcome=True,
    )


# --------------------------------------------------------------------------- #
# Main window
# --------------------------------------------------------------------------- #
class MainWindow(QtWidgets.QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("Trader Brain — анализ графика (только чтение)")
        self.resize(1180, 760)
        self._worker: AnalyzeWorker | None = None
        self._symbols_worker: SymbolsWorker | None = None
        self._execution_engines: dict[str, exec_layer.ExecutionEngine] = {}
        self._last_close: float | None = None
        self._chart_price_bounds: tuple[float, float] | None = None
        self._manual_level_prices: list[float] = []
        self._manual_level_details: dict[float, dict[str, Any]] = {}
        self._manual_level_items: list[pg.GraphicsObject] = []
        self._robot_level_items: list[tuple[float, pg.GraphicsObject]] = []
        self._visible_robot_levels: list[dict[str, Any]] = []
        self._hidden_robot_levels: dict[tuple[str, str], list[float]] = {}
        self._active_chart_key: tuple[str, str, str] | None = None
        self._last_chart_packet: dict[str, Any] | None = None
        self._inflection_levels: list[dict[str, Any]] = []
        self._review_mode = 'inflection'

        self._build_ui()
        self._refresh_scanner_feed()
        self._refresh_execution_state()

        # Poll the scanner heartbeat so the signals feed stays live.
        self._timer = QtCore.QTimer(self)
        self._timer.setInterval(10_000)
        self._timer.timeout.connect(self._refresh_scanner_feed)
        self._timer.start()

    # -- UI construction --------------------------------------------------- #
    def _build_ui(self) -> None:
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)

        outer.addWidget(self._build_banner())
        outer.addLayout(self._build_controls())

        tabs = QtWidgets.QTabWidget()
        tabs.addTab(self._build_review_tab(), "Анализ графика")
        tabs.addTab(self._build_execution_tab(), "Симуляция сделок")
        outer.addWidget(tabs, stretch=1)

        self._cursor_price_label = QtWidgets.QLabel("Цена курсора: —")
        self.statusBar().addPermanentWidget(self._cursor_price_label)
        self.statusBar().showMessage("Готово. Анализ только читает данные; сделки доступны лишь в режимах симуляции.")

    def _build_review_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        review_row = QtWidgets.QHBoxLayout()
        self.show_inflection_btn = QtWidgets.QPushButton("Показать уровень излома тренда")
        self.show_inflection_btn.clicked.connect(self._on_fetch)
        review_row.addWidget(self.show_inflection_btn)
        self.show_mirror_limit_btn = QtWidgets.QPushButton("Показать зеркальные и лимитные уровни")
        self.show_mirror_limit_btn.clicked.connect(lambda: self._on_fetch('mirror_limit'))
        review_row.addWidget(self.show_mirror_limit_btn)
        self.show_paranormal_btn = QtWidgets.QPushButton("Показать уровни, образованные паранормальным баром")
        self.show_paranormal_btn.clicked.connect(lambda: self._on_fetch('paranormal'))
        review_row.addWidget(self.show_paranormal_btn)
        self.inflection_info = QtWidgets.QLabel("Выберите тип уровней: каждая кнопка загружает график самостоятельно.")
        self.inflection_info.setWordWrap(True)
        layout.addLayout(review_row)
        layout.addWidget(self.inflection_info)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._build_chart())
        splitter.addWidget(self._build_side_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return tab

    def _build_banner(self) -> QtWidgets.QWidget:
        banner = QtWidgets.QLabel(
            "  БЕЗОПАСНЫЙ РЕЖИМ — анализ только читает данные. Реальная торговля в этом окне отключена.  "
        )
        banner.setAlignment(QtCore.Qt.AlignCenter)
        banner.setStyleSheet(
            "background-color:#33691e;color:white;font-weight:bold;padding:6px;border-radius:4px;"
        )
        return banner

    def _build_controls(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()

        row.addWidget(QtWidgets.QLabel("Биржа:"))
        self.exchange_combo = QtWidgets.QComboBox()
        for label, value in EXCHANGES.items():
            self.exchange_combo.addItem(label, value)
        self.exchange_combo.setCurrentIndex(self.exchange_combo.findData("bybit"))
        row.addWidget(self.exchange_combo)

        row.addWidget(QtWidgets.QLabel("Монета:"))
        self.symbol_combo = QtWidgets.QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.addItems(DEFAULT_SYMBOLS)
        self.symbol_combo.setMinimumWidth(140)
        row.addWidget(self.symbol_combo)

        self.load_symbols_btn = QtWidgets.QPushButton("Загрузить список")
        self.load_symbols_btn.clicked.connect(self._on_load_symbols)
        row.addWidget(self.load_symbols_btn)

        row.addWidget(QtWidgets.QLabel("Таймфрейм:"))
        self.interval_combo = QtWidgets.QComboBox()
        self.interval_combo.addItems(INTERVALS)
        self.interval_combo.setCurrentText("1d")
        row.addWidget(self.interval_combo)

        row.addWidget(QtWidgets.QLabel("Баров:"))
        self.limit_spin = QtWidgets.QSpinBox()
        self.limit_spin.setRange(20, 1000)
        self.limit_spin.setValue(200)
        self.limit_spin.setSingleStep(20)
        row.addWidget(self.limit_spin)

        self.fetch_btn = QtWidgets.QPushButton("Загрузить и проанализировать")
        self.fetch_btn.clicked.connect(self._on_fetch)
        row.addWidget(self.fetch_btn)

        self.add_manual_level_btn = QtWidgets.QPushButton("Добавить свой уровень")
        self.add_manual_level_btn.setCheckable(True)
        self.add_manual_level_btn.toggled.connect(self._on_manual_level_mode_changed)
        row.addWidget(self.add_manual_level_btn)
        self.classify_manual_level_btn = QtWidgets.QPushButton("Указать тип моего уровня")
        self.classify_manual_level_btn.setCheckable(True)
        self.classify_manual_level_btn.toggled.connect(self._on_classify_manual_level_mode)
        row.addWidget(self.classify_manual_level_btn)

        self.remove_selected_manual_level_btn = QtWidgets.QPushButton("Удалить мой выбранный уровень")
        self.remove_selected_manual_level_btn.setCheckable(True)
        self.remove_selected_manual_level_btn.toggled.connect(self._on_remove_selected_manual_mode)
        row.addWidget(self.remove_selected_manual_level_btn)

        self.clear_manual_levels_btn = QtWidgets.QPushButton("Очистить свои уровни")
        self.clear_manual_levels_btn.clicked.connect(self._clear_manual_levels)
        row.addWidget(self.clear_manual_levels_btn)

        self.remove_robot_level_btn = QtWidgets.QPushButton("Удалить уровень робота")
        self.remove_robot_level_btn.setCheckable(True)
        self.remove_robot_level_btn.toggled.connect(self._on_remove_robot_level_mode_changed)
        row.addWidget(self.remove_robot_level_btn)

        self.restore_robot_levels_btn = QtWidgets.QPushButton("Вернуть последний уровень")
        self.restore_robot_levels_btn.clicked.connect(self._restore_last_robot_level)
        row.addWidget(self.restore_robot_levels_btn)

        row.addStretch(1)
        return row

    def _build_chart(self) -> QtWidgets.QWidget:
        pg.setConfigOptions(antialias=True, background="#101418", foreground="#cccccc")
        self.chart = pg.PlotWidget()
        self.chart.showGrid(x=True, y=True, alpha=0.2)
        self.chart.setLabel("left", "Цена")
        self.chart.setLabel("bottom", "Номер бара")
        self.chart.scene().sigMouseMoved.connect(self._on_chart_mouse_moved)
        self.chart.scene().sigMouseClicked.connect(self._on_chart_clicked)
        self._bar_item: OHLCBarItem | None = None
        return self.chart

    def _build_side_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        layout.addWidget(self._section_label("Анализ"))
        self.analysis_view = QtWidgets.QTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setMinimumHeight(220)
        layout.addWidget(self.analysis_view)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(self._section_label("Сигналы сканера (ожидают подтверждения)"))
        header.addStretch(1)
        self.refresh_feed_btn = QtWidgets.QPushButton("Обновить")
        self.refresh_feed_btn.clicked.connect(self._refresh_scanner_feed)
        header.addWidget(self.refresh_feed_btn)
        layout.addLayout(header)

        self.signals_table = QtWidgets.QTableWidget(0, 5)
        self.signals_table.setHorizontalHeaderLabels(["Инструмент", "Статус проверки", "Вердикт", "База знаний", "Результат"])
        self.signals_table.horizontalHeader().setStretchLastSection(True)
        self.signals_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.signals_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.signals_table.cellDoubleClicked.connect(self._on_signal_double_click)
        layout.addWidget(self.signals_table)

        self.scanner_status_label = QtWidgets.QLabel("Сканер: данных пока нет")
        self.scanner_status_label.setStyleSheet("color:#888;")
        layout.addWidget(self.scanner_status_label)

        return panel

    def _build_execution_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Режим симуляции:"))
        self.exec_mode_combo = QtWidgets.QComboBox()
        self.exec_mode_combo.addItem("Бумажный", "paper")
        self.exec_mode_combo.addItem("Теневой", "shadow")
        self.exec_mode_combo.currentTextChanged.connect(lambda _text: self._refresh_execution_state())
        mode_row.addWidget(self.exec_mode_combo)
        self.sync_execution_btn = QtWidgets.QPushButton("Использовать инструмент и цену с графика")
        self.sync_execution_btn.clicked.connect(lambda _checked=False: self._on_sync_execution_from_chart())
        mode_row.addWidget(self.sync_execution_btn)
        self.refresh_execution_btn = QtWidgets.QPushButton("Обновить состояние")
        self.refresh_execution_btn.clicked.connect(lambda _checked=False: self._refresh_execution_state())
        mode_row.addWidget(self.refresh_execution_btn)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        top = QtWidgets.QHBoxLayout()
        order_group = QtWidgets.QGroupBox("Ручная симулированная заявка")
        order_form = QtWidgets.QFormLayout(order_group)
        self.exec_symbol_edit = QtWidgets.QLineEdit()
        self.exec_symbol_edit.setText(self.symbol_combo.currentText().strip().upper())
        order_form.addRow("Инструмент:", self.exec_symbol_edit)
        self.exec_side_combo = QtWidgets.QComboBox()
        self.exec_side_combo.addItem("Покупка", "buy")
        self.exec_side_combo.addItem("Продажа", "sell")
        order_form.addRow("Сторона:", self.exec_side_combo)
        self.exec_qty_spin = QtWidgets.QDoubleSpinBox()
        self.exec_qty_spin.setDecimals(8)
        self.exec_qty_spin.setRange(0.0, 1_000_000.0)
        self.exec_qty_spin.setValue(0.001)
        self.exec_qty_spin.setSingleStep(0.001)
        order_form.addRow("Количество:", self.exec_qty_spin)
        self.exec_price_spin = QtWidgets.QDoubleSpinBox()
        self.exec_price_spin.setDecimals(8)
        self.exec_price_spin.setRange(0.0, 100_000_000.0)
        self.exec_price_spin.setSingleStep(10.0)
        order_form.addRow("Расчётная цена:", self.exec_price_spin)
        self.exec_reason_edit = QtWidgets.QLineEdit()
        self.exec_reason_edit.setPlaceholderText("case:<id> или заметка к симуляции")
        order_form.addRow("Причина / кейс:", self.exec_reason_edit)
        self.exec_submit_btn = QtWidgets.QPushButton("Отправить на проверку риска")
        self.exec_submit_btn.clicked.connect(self._on_submit_execution)
        order_form.addRow(self.exec_submit_btn)
        top.addWidget(order_group, stretch=2)

        risk_group = QtWidgets.QGroupBox("Ограничения риска")
        risk_form = QtWidgets.QFormLayout(risk_group)
        self.max_notional_spin = QtWidgets.QDoubleSpinBox()
        self.max_notional_spin.setRange(1.0, 1_000_000.0)
        self.max_notional_spin.setValue(100.0)
        self.max_notional_spin.setSingleStep(10.0)
        risk_form.addRow("Макс. сумма заявки:", self.max_notional_spin)
        self.max_positions_spin = QtWidgets.QSpinBox()
        self.max_positions_spin.setRange(1, 100)
        self.max_positions_spin.setValue(3)
        risk_form.addRow("Макс. открытых позиций:", self.max_positions_spin)
        self.max_daily_loss_spin = QtWidgets.QDoubleSpinBox()
        self.max_daily_loss_spin.setRange(1.0, 1_000_000.0)
        self.max_daily_loss_spin.setValue(50.0)
        self.max_daily_loss_spin.setSingleStep(10.0)
        risk_form.addRow("Макс. дневной убыток:", self.max_daily_loss_spin)
        self.max_qty_spin = QtWidgets.QDoubleSpinBox()
        self.max_qty_spin.setDecimals(8)
        self.max_qty_spin.setRange(0.00000001, 1_000_000.0)
        self.max_qty_spin.setValue(1.0)
        self.max_qty_spin.setSingleStep(0.1)
        risk_form.addRow("Макс. количество на инструмент:", self.max_qty_spin)
        top.addWidget(risk_group, stretch=2)

        kill_group = QtWidgets.QGroupBox("Аварийная блокировка")
        kill_layout = QtWidgets.QVBoxLayout(kill_group)
        self.kill_switch_label = QtWidgets.QLabel("Аварийная блокировка: неизвестно")
        kill_layout.addWidget(self.kill_switch_label)
        self.engage_kill_btn = QtWidgets.QPushButton("Включить блокировку")
        self.engage_kill_btn.clicked.connect(self._on_engage_kill_switch)
        kill_layout.addWidget(self.engage_kill_btn)
        self.release_kill_btn = QtWidgets.QPushButton("Снять блокировку")
        self.release_kill_btn.clicked.connect(self._on_release_kill_switch)
        kill_layout.addWidget(self.release_kill_btn)
        kill_layout.addStretch(1)
        top.addWidget(kill_group, stretch=1)
        layout.addLayout(top)

        layout.addWidget(self._section_label("Состояние симуляции"))
        self.execution_state_view = QtWidgets.QTextEdit()
        self.execution_state_view.setReadOnly(True)
        self.execution_state_view.setMaximumHeight(120)
        layout.addWidget(self.execution_state_view)

        layout.addWidget(self._section_label("Журнал действий (последние)"))
        self.audit_table = QtWidgets.QTableWidget(0, 6)
        self.audit_table.setHorizontalHeaderLabels(["Время", "Событие", "Режим", "Инструмент", "Статус", "Причина"])
        self.audit_table.horizontalHeader().setStretchLastSection(True)
        self.audit_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.audit_table, stretch=1)

        return tab

    @staticmethod
    def _section_label(text: str) -> QtWidgets.QLabel:
        label = QtWidgets.QLabel(text)
        label.setStyleSheet("font-weight:bold;margin-top:4px;")
        return label

    # -- Actions ----------------------------------------------------------- #
    def _on_load_symbols(self) -> None:
        self.load_symbols_btn.setEnabled(False)
        self.statusBar().showMessage("Загрузка списка инструментов из публичного источника...")
        self._symbols_worker = SymbolsWorker(
            exchange=str(self.exchange_combo.currentData()),
            market="linear" if self.exchange_combo.currentData() == "bybit" else "spot",
        )
        self._symbols_worker.finished_ok.connect(self._on_symbols_loaded)
        self._symbols_worker.failed.connect(self._on_symbols_failed)
        self._symbols_worker.start()

    def _on_symbols_loaded(self, symbols: list[str]) -> None:
        current = self.symbol_combo.currentText()
        self.symbol_combo.clear()
        self.symbol_combo.addItems(symbols)
        if current:
            self.symbol_combo.setCurrentText(current)
        self.load_symbols_btn.setEnabled(True)
        self.statusBar().showMessage(f"Загружено инструментов к USDT: {len(symbols)}.")

    def _on_symbols_failed(self, message: str) -> None:
        self.load_symbols_btn.setEnabled(True)
        self.statusBar().showMessage(f"Не удалось загрузить инструменты: {message}")

    def _set_analysis_buttons_enabled(self, enabled: bool) -> None:
        for button in (self.fetch_btn, self.show_inflection_btn, self.show_mirror_limit_btn, self.show_paranormal_btn):
            button.setEnabled(enabled)

    def _on_fetch(self, review_mode='inflection') -> None:
        # QPushButton.clicked may pass a checked boolean.
        if not isinstance(review_mode, str):
            review_mode = 'inflection'
        if self._worker is not None and self._worker.isRunning():
            return
        symbol = self.symbol_combo.currentText().strip().upper()
        if not symbol:
            self.statusBar().showMessage("Сначала укажите инструмент.")
            return
        interval = self.interval_combo.currentText()
        limit = self.limit_spin.value()
        exchange = str(self.exchange_combo.currentData())
        market = "linear" if exchange == "bybit" else "spot"
        self._set_analysis_buttons_enabled(False)
        self.statusBar().showMessage(f"Загрузка {symbol} {interval} с {self.exchange_combo.currentText()}...")
        self._worker = AnalyzeWorker(symbol, interval, limit, exchange=exchange, market=market,
                                     review_mode=review_mode)
        self._worker.finished_ok.connect(self._on_analysis_done)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.start()

    def _on_analysis_done(self, result: dict[str, Any]) -> None:
        self._set_analysis_buttons_enabled(True)
        self._review_mode = result.get('review_mode', 'inflection')
        for button in (self.add_manual_level_btn, self.classify_manual_level_btn,
                       self.remove_selected_manual_level_btn, self.clear_manual_levels_btn):
            if self._review_mode != 'inflection':
                button.setChecked(False)
            button.setEnabled(self._review_mode == 'inflection')
        self._active_chart_key = (
            str(result["exchange"]),
            str(result["symbol"]),
            str(result["interval"]),
        )
        self._load_persisted_level_state(self._active_chart_key)
        self._inflection_levels = result.get("inflection_levels") or []
        self.show_inflection_btn.setEnabled(True)
        self._draw_candles(result.get("bars") or [])
        if self._review_mode in ('mirror_limit', 'paranormal'):
            self._last_chart_packet = {}
            self.analysis_view.setPlainText(
                f"{result['symbol']} · {result['interval']}\n{REVIEW_MODE_LABELS[self._review_mode]}\n\n"
                + ("Кружки отмечают БСУ и все касания хвостами в пределах допуска. "
                   "Проход тела свечи через линию не считается касанием.\n"
                   if self._review_mode == 'mirror_limit' else
                   "Кружок отмечает экстремум паранормального бара, образовавшего уровень.\n")
                + "Наведите курсор на кружок, чтобы увидеть дату UTC и цену.\n"
                  "Удаление в этом режиме относится только к выбранному типу уровней.")
            self._draw_kb_levels({})
            self._draw_manual_levels()
            self.statusBar().showMessage(f"{result['symbol']} {result['interval']}: {REVIEW_MODE_LABELS[self._review_mode]}.")
            return
        if result.get("inflection_only"):
            self._last_chart_packet = {}
            comparison = result.get("human_review_comparison") or {}
            expected = comparison.get("expected_inflections", [])
            missing = [r for r in expected if r["status"] == "missing"]
            self.analysis_view.setPlainText(
                f"{result['symbol']} · {result['interval']}\n"
                "Показаны изломы и подтверждённые лимитные уровни, уступившие более сильному излому.\n"
                "Второстепенный лимитный уровень подписан отдельно и не считается изломом.\n"
                "Круг отмечает БСУ. Для проверки используются последующие бары.\n"
                "Удалите неверную линию и добавьте свой уровень с пояснением.\n\n"
                f"Ваших явно отмеченных изломов: {len(expected)}. Не найдено: {len(missing)}.\n"
                f"Пропущенные цены: {', '.join(str(r['price']) for r in missing) or 'нет'}.\n"
                f"Повторно найдены отклонённые уровни: {len(comparison.get('repeated_rejections', []))}.\n"
                "Остальные линии требуют привязки к вашей проверке; отсутствие удаления не считается автоматическим одобрением.")
            self._draw_kb_levels({})
            self._draw_manual_levels()
            self.statusBar().showMessage(
                f"{result['exchange']} {result['symbol']} {result['interval']}: "
                f"загружено {len(result.get('bars') or [])} баров; изломы и их второстепенные подтверждения.")
            return
        self._render_analysis(result["symbol"], result["interval"], result["packet"])
        context_interval = result.get("context_interval") or result["interval"]
        higher_interval = result.get("higher_interval") or "нет"
        self.statusBar().showMessage(
            f"{result['exchange']} {result['symbol']} {result['interval']}: проанализировано баров: {len(result.get('bars') or [])} "
            f"(контекст БЗ={context_interval}, старший ТФ={higher_interval}, только чтение)."
        )

    def _on_analysis_failed(self, message: str) -> None:
        self._set_analysis_buttons_enabled(True)
        self.statusBar().showMessage(f"Ошибка анализа: {message}")
        QtWidgets.QMessageBox.warning(self, "Ошибка анализа", message)

    # -- Rendering --------------------------------------------------------- #
    def _draw_candles(self, bars: list[dict[str, Any]]) -> None:
        self.chart.clear()
        self._manual_level_items.clear()
        self._robot_level_items.clear()
        self._visible_robot_levels.clear()
        self._last_close = None
        self._chart_price_bounds = None
        if not bars:
            return
        tuples: list[tuple[float, float, float, float, float]] = []
        for index, bar in enumerate(bars):
            try:
                tuples.append((
                    float(index),
                    float(bar["open"]),
                    float(bar["high"]),
                    float(bar["low"]),
                    float(bar["close"]),
                ))
            except (KeyError, TypeError, ValueError):
                continue
        if not tuples:
            return
        self._last_close = tuples[-1][4]
        lows = [item[3] for item in tuples]
        highs = [item[2] for item in tuples]
        self._chart_price_bounds = (min(lows), max(highs))
        self._bar_item = OHLCBarItem(tuples)
        self.chart.addItem(self._bar_item)
        self.chart.enableAutoRange()
        self._on_sync_execution_from_chart(update_reason=False)

    def _draw_kb_levels(self, packet: dict[str, Any]) -> None:
        bounds = self._chart_price_bounds
        if bounds is None:
            return
        low, high = bounds
        padding = max((high - low) * 0.15, 1e-9)
        visible_low = low - padding
        visible_high = high + padding
        kb = packet.get("kb_analysis") or {}
        level_report = ((kb.get("layer_reports") or {}).get("levels") or {})
        reviewing = True
        levels = list(self._inflection_levels if reviewing else level_report.get("levels") or [])
        for _price, item in self._robot_level_items:
            self.chart.removeItem(item)
        self._robot_level_items.clear()
        self._visible_robot_levels.clear()
        hidden_prices = self._hidden_robot_levels.get(self._active_chart_key or ("", "", ""), [])
        # Show every discovered candidate, not only the levels that passed the
        # automated KB filter.  This lets the trader compare their own chart
        # reading with both uncertain and rejected candidates.
        levels.sort(key=lambda item: float(item.get("distance_atr") or 9999))
        for level in (levels if reviewing else levels[:24]):
            try:
                price = float(level.get("price"))
            except (TypeError, ValueError):
                continue
            if any(abs(price - hidden) <= 1e-9 for hidden in hidden_prices):
                continue
            if price < visible_low or price > visible_high:
                continue
            side = str(level.get("side") or "level")
            status = str(level.get("kb_status") or "warn")
            if status == "reject":
                color = "#78909c"
                line_style = QtCore.Qt.DotLine
            elif status == "warn":
                color = "#ffca28"
                line_style = QtCore.Qt.DashLine
            elif side == "support":
                color = "#26a69a"
                line_style = QtCore.Qt.SolidLine
            elif side == "resistance":
                color = "#ef5350"
                line_style = QtCore.Qt.SolidLine
            else:
                color = "#ffca28"
                line_style = QtCore.Qt.DashLine
            pen = pg.mkPen(color, width=1.6, style=line_style)
            if reviewing:
                color = "#ffd166" if status != "reject" else "#aaa0cb"
                pen = pg.mkPen(color, width=2.2, style=line_style)
            secondary = self._review_mode == 'inflection' and bool((level.get("inflection_check") or {}).get("retained_secondary"))
            if secondary:
                color = "#8fa3b8"
                pen = pg.mkPen(color, width=1.4, style=QtCore.Qt.DashLine)
            title = ("Лимитный · второстепенный · " if secondary else
                     "Излом (БСУ подтверждён вами) · " if level.get('source') == 'human_confirmed_inflection' else
                     "Излом · " if reviewing else "")
            if self._review_mode != 'inflection':
                title = level.get('chart_title', REVIEW_MODE_LABELS[self._review_mode]) + ' · '
            line = pg.InfiniteLine(pos=price, angle=0, pen=pen, movable=False)
            line.setZValue(10)
            self.chart.addItem(line)
            label = pg.TextItem(
                f"{title}{LEVEL_SIDE_LABELS.get(side, 'уровень')} {price:g}; "
                f"{ru_value(status)}; оценка={level.get('kb_score', '-')}",
                color=color,
                anchor=(0, 1),
            )
            label.setZValue(11)
            label.setPos(0, price)
            self.chart.addItem(label)
            self._robot_level_items.extend([(price, line), (price, label)])
            if reviewing:
                bsu = level.get("bsu") or {}
                points = level.get('chart_markers')
                if points is None:
                    points = [{'index': int(bsu['index']), 'price': price,
                               'time': bsu.get('time', ''), 'roles': ['БСУ']}]
                marker = pg.ScatterPlotItem(
                    x=[point['index'] for point in points], y=[point['price'] for point in points],
                    data=points, symbol="o", size=12, hoverable=True,
                    tip=lambda x, y, data: f"{'; '.join(data['roles'])}\n{data['time']}\nЦена: {data['price']:g}",
                    pen=pg.mkPen(color, width=2), brush=pg.mkBrush(0, 0, 0, 0))
                marker.setZValue(14)
                self.chart.addItem(marker)
                self._robot_level_items.append((price, marker))
            self._visible_robot_levels.append({
                "price": price,
                "side": side,
                "kb_status": status,
                "kb_score": level.get("kb_score"),
                "basis_tags": level.get("basis_tags", []),
                "inflection_check": level.get("inflection_check", {}),
                "bsu": level.get("bsu"),
                "chart_markers": level.get('chart_markers', []),
                "review_mode": self._review_mode,
            })
        if self._review_mode != 'inflection':
            self.inflection_info.setText(
                f"{REVIEW_MODE_LABELS[self._review_mode]}: {len(self._visible_robot_levels)}. "
                "Кружки отмечают бары-основания и касания; дата и цена — при наведении.")
        elif reviewing:
            inflections = sum('inflection' in level.get('basis_tags', []) for level in self._visible_robot_levels)
            secondary_count = sum(bool(level['inflection_check'].get('retained_secondary')) for level in self._visible_robot_levels)
            self.inflection_info.setText(
                f"Изломов: {inflections}; второстепенных лимитных: {secondary_count}. "
                "Круг — БСУ; пунктир — кандидат с замечаниями. Историческая разметка, не сигнал. "
                "Для исправления: удалите линию робота и добавьте свой уровень с причиной.")
        else:
            self.inflection_info.setText("Показаны обычные уровни. Кнопка включает проверку изломов на таймфрейме графика.")

    def _on_manual_level_mode_changed(self, enabled: bool) -> None:
        if enabled:
            self.remove_selected_manual_level_btn.setChecked(False)
            self.classify_manual_level_btn.setChecked(False)
            self.remove_robot_level_btn.setChecked(False)
            self.add_manual_level_btn.setText("Щёлкните по цене на графике")
            self.statusBar().showMessage("Выберите цену: щёлкните левой кнопкой внутри графика.")
        else:
            self.add_manual_level_btn.setText("Добавить свой уровень")

    def _on_chart_mouse_moved(self, scene_pos: QtCore.QPointF) -> None:
        view_box = self.chart.getViewBox()
        if not view_box.sceneBoundingRect().contains(scene_pos):
            self._cursor_price_label.setText("Цена курсора: —")
            return
        price = float(view_box.mapSceneToView(scene_pos).y())
        self._cursor_price_label.setText(f"Цена курсора: {price:.8g}")

    def _on_chart_clicked(self, event: Any) -> None:
        """Add one manual level at the price selected in the chart."""
        if event.button() != QtCore.Qt.LeftButton:
            return
        view_box = self.chart.getViewBox()
        if not view_box.sceneBoundingRect().contains(event.scenePos()):
            return
        price = float(view_box.mapSceneToView(event.scenePos()).y())
        if self.remove_selected_manual_level_btn.isChecked():
            self._remove_selected_manual_level_at(price)
            return
        if self.classify_manual_level_btn.isChecked():
            self._classify_manual_level_at(price)
            return
        if self.remove_robot_level_btn.isChecked():
            self._remove_robot_level_at(price)
            self.remove_robot_level_btn.setChecked(False)
            return
        if not self.add_manual_level_btn.isChecked():
            return
        if price <= 0:
            return
        span = (self._chart_price_bounds[1] - self._chart_price_bounds[0]) if self._chart_price_bounds else 0.0
        tolerance = max(span * 0.001, 1e-9)
        if any(abs(price - existing) <= tolerance for existing in self._manual_level_prices):
            self.statusBar().showMessage("Такой ручной уровень уже есть на графике.")
        else:
            feedback = self._ask_manual_level_reason(price)
            if feedback is None:
                self.add_manual_level_btn.setChecked(False)
                self.statusBar().showMessage("Добавление уровня отменено.")
                return
            try:
                self._append_level_feedback({
                    "action": "add_manual_level",
                    "exchange": self._active_chart_key[0] if self._active_chart_key else self.exchange_combo.currentData(),
                    "symbol": self._active_chart_key[1] if self._active_chart_key else self.symbol_combo.currentText().strip(),
                    "interval": self._active_chart_key[2] if self._active_chart_key else self.interval_combo.currentText(),
                    "price": price,
                    **feedback,
                })
            except OSError as exc:
                self.add_manual_level_btn.setChecked(False)
                self.statusBar().showMessage(f"Не удалось сохранить причину добавления: {exc}")
                return
            self._manual_level_prices.append(price)
            self._manual_level_details[price] = feedback
            self._draw_manual_levels()
            self.statusBar().showMessage(f"Добавлен ручной уровень: {price:g}.")
        self.add_manual_level_btn.setChecked(False)

    def _on_remove_robot_level_mode_changed(self, enabled: bool) -> None:
        if enabled:
            self.remove_selected_manual_level_btn.setChecked(False)
            self.classify_manual_level_btn.setChecked(False)
            self.add_manual_level_btn.setChecked(False)
            self.remove_robot_level_btn.setText("Щёлкните по уровню робота")
            self.statusBar().showMessage("Щёлкните левой кнопкой по линии уровня, которую хотите скрыть.")
        else:
            self.remove_robot_level_btn.setText("Удалить уровень робота")

    def _remove_robot_level_at(self, clicked_price: float) -> None:
        if not self._visible_robot_levels or self._active_chart_key is None:
            self.statusBar().showMessage("На этом графике нет уровней робота для удаления.")
            return
        level = min(self._visible_robot_levels, key=lambda candidate: abs(float(candidate["price"]) - clicked_price))
        price = float(level["price"])
        low, high = self._chart_price_bounds or (price, price)
        click_tolerance = max((high - low) * 0.02, 1e-9)
        if abs(price - clicked_price) > click_tolerance:
            self.statusBar().showMessage("Щёлкните ближе к линии уровня робота.")
            return
        feedback = self._ask_level_removal_reason(level)
        if feedback is None:
            self.statusBar().showMessage("Удаление уровня отменено.")
            return
        try:
            self._append_level_feedback({
                "action": "hide_robot_level",
                    "label": "human_rejected",
                    "training_signal": "negative",
                "exchange": self._active_chart_key[0],
                "symbol": self._active_chart_key[1],
                "interval": self._active_chart_key[2],
                **level,
                **feedback,
            })
        except OSError as exc:
            self.statusBar().showMessage(f"Не удалось сохранить причину удаления: {exc}")
            return
        hidden = self._hidden_robot_levels.setdefault(self._active_chart_key, [])
        hidden.append(price)
        for item_price, item in self._robot_level_items[:]:
            if item_price == price:
                self.chart.removeItem(item)
                self._robot_level_items.remove((item_price, item))
        self._visible_robot_levels = [item for item in self._visible_robot_levels if float(item["price"]) != price]
        self.statusBar().showMessage(f"Уровень робота {price:g} скрыт, причина сохранена.")

    def _ask_level_removal_reason(self, level: dict[str, Any]) -> dict[str, str] | None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Почему удалить этот уровень?")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(
            f"Уровень {float(level['price']):g}. Выберите причину:"
        ))
        reasons = [
            ("мало_касаний", "Мало касаний"),
            ("запиленный", "Уровень запилен ценой"),
            ("внутри_канала", "Внутри канала или рыночного шума"),
            ("нет_реакции", "Нет сильной реакции от уровня"),
            ("не_подтверждён_старшим_тф", "Не подтверждён старшим таймфреймом"),
            ("слишком_далеко", "Слишком далеко от текущей цены"),
            ("другое", "Другая причина"),
        ]
        reason_list = QtWidgets.QListWidget()
        reason_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for _code, label in reasons:
            reason_list.addItem(label)
        layout.addWidget(reason_list)
        layout.addWidget(QtWidgets.QLabel("Ваш комментарий (необязательно):"))
        note_edit = QtWidgets.QTextEdit()
        note_edit.setPlaceholderText("Например: уровень есть, но для входа сейчас не важен")
        note_edit.setMaximumHeight(90)
        layout.addWidget(note_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Сохранить и скрыть")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while dialog.exec() == QtWidgets.QDialog.Accepted:
            selected_indexes = reason_list.selectedIndexes()
            if selected_indexes:
                break
            QtWidgets.QMessageBox.warning(dialog, "Причина не выбрана", "Выберите хотя бы одну причину удаления.")
        else:
            return None
        selected_positions = {index.row() for index in selected_indexes}
        selected_reasons = [reason for index, reason in enumerate(reasons) if index in selected_positions]
        return {
            "reason_codes": [code for code, _label in selected_reasons],
            "reason_labels": [label for _code, label in selected_reasons],
            "reason_code": selected_reasons[0][0],
            "reason_label": "; ".join(label for _code, label in selected_reasons),
            "note": note_edit.toPlainText().strip(),
        }

    def _on_classify_manual_level_mode(self, enabled: bool) -> None:
        if enabled:
            self.remove_selected_manual_level_btn.setChecked(False)
            self.add_manual_level_btn.setChecked(False)
            self.remove_robot_level_btn.setChecked(False)
            self.statusBar().showMessage("Щёлкните по своему уровню, чтобы указать его тип и причину.")

    def _classify_manual_level_at(self, clicked_price: float) -> None:
        if not self._active_chart_key or not self._manual_level_prices:
            self.statusBar().showMessage("На графике нет ваших уровней.")
            self.classify_manual_level_btn.setChecked(False)
            return
        price = min(self._manual_level_prices, key=lambda value: abs(value - clicked_price))
        tolerance = max(abs(self.chart.getViewBox().viewPixelSize()[1]) * 10, 1e-9)
        if abs(price - clicked_price) > tolerance:
            self.statusBar().showMessage("Щёлкните ближе к линии своего уровня.")
            return
        feedback = self._ask_manual_level_reason(price, self._manual_level_details.get(price))
        self.classify_manual_level_btn.setChecked(False)
        if feedback is None:
            return
        try:
            self._append_level_feedback({
                "action": "update_manual_level", "exchange": self._active_chart_key[0],
                "symbol": self._active_chart_key[1], "interval": self._active_chart_key[2],
                "price": price, **feedback,
            })
        except OSError as exc:
            self.statusBar().showMessage(f"Не удалось сохранить тип уровня: {exc}")
            return
        self._manual_level_details[price] = feedback
        self._draw_manual_levels()
        self.statusBar().showMessage(f"Тип и причина уровня {price:g} сохранены.")

    def _ask_manual_level_reason(self, price: float, initial: dict[str, Any] | None = None) -> dict[str, Any] | None:
        dialog = QtWidgets.QDialog(self)
        dialog.setWindowTitle("Тип и причина ручного уровня")
        layout = QtWidgets.QVBoxLayout(dialog)
        layout.addWidget(QtWidgets.QLabel(
            f"Уровень {price:g}. Выберите одну или несколько причин:"
        ))
        reasons = [
            ("сильная_реакция", "Есть сильная реакция от уровня"),
            ("излом_тренда", "Уровень излома тренда"),
            ("паранормальный_бар", "Уровень паранормального бара"),
            ("лимитный", "Лимитный уровень"),
            ("проторговка", "Уровень проторговки"),
            ("ложный_пробой", "Уровень, образованный ложным пробоем"),
            ("ранее_встречавшийся", "Ранее встречавшийся уровень"),
            ("несколько_касаний", "Есть несколько касаний"),
            ("подтверждён_старшим_тф", "Подтверждён старшим таймфреймом"),
            ("зеркальный", "Зеркальный уровень"),
            ("граница_канала", "Граница канала или диапазона"),
            ("другое", "Другая причина"),
        ]
        reason_list = QtWidgets.QListWidget()
        reason_list.setSelectionMode(QtWidgets.QAbstractItemView.MultiSelection)
        for _code, label in reasons:
            reason_list.addItem(label)
            if _code in (initial or {}).get("reason_codes", []):
                reason_list.item(reason_list.count() - 1).setSelected(True)
        layout.addWidget(reason_list)
        layout.addWidget(QtWidgets.QLabel("Ваш комментарий (необязательно):"))
        note_edit = QtWidgets.QTextEdit()
        note_edit.setPlainText(str((initial or {}).get("note", "")))
        note_edit.setPlaceholderText("Например: уровень виден на 4H и дал реакцию на дневном графике")
        note_edit.setMaximumHeight(90)
        layout.addWidget(note_edit)
        buttons = QtWidgets.QDialogButtonBox(
            QtWidgets.QDialogButtonBox.Ok | QtWidgets.QDialogButtonBox.Cancel
        )
        buttons.button(QtWidgets.QDialogButtonBox.Ok).setText("Сохранить")
        buttons.button(QtWidgets.QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        while dialog.exec() == QtWidgets.QDialog.Accepted:
            selected_indexes = reason_list.selectedIndexes()
            if selected_indexes:
                break
            QtWidgets.QMessageBox.warning(dialog, "Причина не выбрана", "Выберите хотя бы одну причину добавления.")
        else:
            return None
        selected_positions = {index.row() for index in selected_indexes}
        selected_reasons = [reason for index, reason in enumerate(reasons) if index in selected_positions]
        return {
            "reason_codes": [code for code, _label in selected_reasons],
            "reason_labels": [label for _code, label in selected_reasons],
            "reason_code": selected_reasons[0][0],
            "reason_label": "; ".join(label for _code, label in selected_reasons),
            "note": note_edit.toPlainText().strip(),
        }

    @staticmethod
    def _append_level_feedback(record: dict[str, Any]) -> None:
        USER_LEVEL_FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "recorded_at": datetime.now(timezone.utc).isoformat(),
            **record,
        }
        with USER_LEVEL_FEEDBACK_PATH.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, ensure_ascii=False) + "\n")
        try:
            build_level_feedback_statistics()
        except Exception:
            pass

    def _load_persisted_level_state(self, chart_key: tuple[str, str, str]) -> None:
        hidden: list[float] = []
        manual: list[float] = []
        details: dict[float, dict[str, Any]] = {}
        if USER_LEVEL_FEEDBACK_PATH.exists():
            try:
                records = USER_LEVEL_FEEDBACK_PATH.read_text(encoding="utf-8").splitlines()
            except OSError:
                records = []
            for raw_record in records:
                try:
                    record = json.loads(raw_record)
                except json.JSONDecodeError:
                    continue
                record_key = (
                    str(record.get("exchange") or ""),
                    str(record.get("symbol") or ""),
                    str(record.get("interval") or ""),
                )
                if record_key != chart_key:
                    continue
                action = record.get("action")
                if action in ('hide_robot_level', 'restore_robot_level') and not feedback_applies_to_mode(record, self._review_mode):
                    continue
                if action == "clear_manual_levels":
                    manual.clear()
                    details.clear()
                    continue
                try:
                    price = float(record["price"])
                except (KeyError, TypeError, ValueError):
                    continue
                if action == "hide_robot_level" and price not in hidden:
                    hidden.append(price)
                elif action == "restore_robot_level":
                    hidden = [item for item in hidden if item != price]
                elif action == "add_manual_level" and price not in manual:
                    manual.append(price)
                    details[price] = record
                elif action == "update_manual_level" and price in manual:
                    details[price] = record
                elif action == "remove_manual_level":
                    manual = [value for value in manual if value != price]
                    details.pop(price, None)
        if hidden:
            self._hidden_robot_levels[chart_key] = hidden
        else:
            self._hidden_robot_levels.pop(chart_key, None)
        self._manual_level_prices = manual
        self._manual_level_details = details

    def _restore_last_robot_level(self) -> None:
        if self._active_chart_key is None or not self._hidden_robot_levels.get(self._active_chart_key):
            self.statusBar().showMessage("Нет удалённого уровня, который можно вернуть.")
            return
        restored_price = self._hidden_robot_levels[self._active_chart_key][-1]
        try:
            self._append_level_feedback({
                "action": "restore_robot_level",
                "review_mode": self._review_mode,
                "exchange": self._active_chart_key[0],
                "symbol": self._active_chart_key[1],
                "interval": self._active_chart_key[2],
                "price": restored_price,
            })
        except OSError as exc:
            self.statusBar().showMessage(f"Не удалось сохранить возврат уровня: {exc}")
            return
        self._hidden_robot_levels[self._active_chart_key].pop()
        if not self._hidden_robot_levels[self._active_chart_key]:
            self._hidden_robot_levels.pop(self._active_chart_key)
        if self._last_chart_packet is not None:
            self._draw_kb_levels(self._last_chart_packet)
            self._draw_manual_levels()
        self.statusBar().showMessage(f"Возвращён последний удалённый уровень робота: {restored_price:g}.")

    def _draw_manual_levels(self) -> None:
        for item in self._manual_level_items:
            self.chart.removeItem(item)
        self._manual_level_items.clear()
        if self._review_mode != 'inflection':
            return
        for price in self._manual_level_prices:
            line = pg.InfiniteLine(pos=price, angle=0, pen=pg.mkPen("#42a5f5", width=2.0), movable=False)
            line.setZValue(12)
            description = "; ".join(self._manual_level_details.get(price, {}).get("reason_labels", []))
            label = pg.TextItem(f"свой уровень {price:g}" + (f" · {description}" if description else ""), color="#42a5f5", anchor=(0, 1))
            label.setZValue(13)
            label.setPos(0, price)
            self.chart.addItem(line)
            self.chart.addItem(label)
            self._manual_level_items.extend([line, label])

    def _on_remove_selected_manual_mode(self, enabled: bool) -> None:
        if enabled:
            self.add_manual_level_btn.setChecked(False)
            self.classify_manual_level_btn.setChecked(False)
            self.remove_robot_level_btn.setChecked(False)
            self.statusBar().showMessage("Щёлкните по своей линии, которую хотите удалить.")

    def _remove_selected_manual_level_at(self, clicked_price: float) -> None:
        if not self._active_chart_key or not self._manual_level_prices:
            self.statusBar().showMessage("На этом графике нет ручных уровней для удаления.")
            self.remove_selected_manual_level_btn.setChecked(False)
            return
        price = min(self._manual_level_prices, key=lambda value: abs(value - clicked_price))
        tolerance = max(abs(self.chart.getViewBox().viewPixelSize()[1]) * 10, 1e-9)
        if abs(price - clicked_price) > tolerance:
            self.statusBar().showMessage("Щёлкните ближе к линии своего уровня.")
            return
        try:
            self._append_level_feedback({
                "action": "remove_manual_level",
                "exchange": self._active_chart_key[0],
                "symbol": self._active_chart_key[1],
                "interval": self._active_chart_key[2],
                "price": price,
            })
        except OSError as exc:
            self.statusBar().showMessage(f"Не удалось сохранить удаление уровня: {exc}")
            return
        self._manual_level_prices.remove(price)
        self._manual_level_details.pop(price, None)
        self._draw_manual_levels()
        self.remove_selected_manual_level_btn.setChecked(False)
        self.statusBar().showMessage(f"Удалён выбранный ручной уровень: {price:g}.")

    def _clear_manual_levels(self) -> None:
        if self._active_chart_key and self._manual_level_prices:
            try:
                self._append_level_feedback({
                    "action": "clear_manual_levels",
                    "exchange": self._active_chart_key[0],
                    "symbol": self._active_chart_key[1],
                    "interval": self._active_chart_key[2],
                })
            except OSError as exc:
                self.statusBar().showMessage(f"Не удалось сохранить очистку уровней: {exc}")
                return
        self._manual_level_prices.clear()
        self._manual_level_details.clear()
        self._draw_manual_levels()
        self.statusBar().showMessage("Все ручные уровни очищены.")

    def _render_analysis(self, symbol: str, interval: str, packet: dict[str, Any]) -> None:
        self._last_chart_packet = packet
        review = packet.get("review_state") or {}
        analyzer = packet.get("analyzer_summary") or {}
        verdict = analyzer.get("verdict") or {}
        completeness = analyzer.get("trade_contract_completeness") or {}
        context = packet.get("context") or {}
        ohlc = context.get("ohlc") or {}
        kb = packet.get("kb_analysis") or {}
        level_summary = kb.get("level_summary") or {}
        permission_summary = kb.get("permission_summary") or {}
        layer_reports = kb.get("layer_reports") or {}
        entry_report = layer_reports.get("entry") or {}
        scenario = entry_report.get("scenario") or {}
        nearest_level = entry_report.get("nearest_level") or {}
        hard_rejects = ", ".join(permission_summary.get("hard_rejects") or []) or "нет"
        flags_ok = all(packet.get(flag) is False for flag in (
            "execution_allowed", "runtime_signal_allowed", "order_generation_allowed",
            "pnl_computation_allowed", "aggregate_winrate_allowed", "paper_trading_allowed",
            "live_trading_allowed", "backtest_harness_allowed",
        ))
        missing = ", ".join(completeness.get("missing_fields") or []) or "нет"
        lines = [
            f"<h3>{symbol} &middot; {interval}</h3>",
            f"<b>Статус проверки:</b> {ru_value(review.get('state', 'unknown'))}<br>",
            f"<i>{review.get('reason', '')}</i><br><br>",
            f"<b>Вердикт анализатора:</b> {ru_value(verdict.get('state', 'unknown'))}<br>",
            f"<i>{verdict.get('reason', '')}</i><br><br>",
            f"<b>Критерии допуска выполнены:</b> {ru_value(completeness.get('hard_gate_satisfied'))}<br>",
            f"<b>Незаполненные поля сделки:</b> {missing}<br><br>",
            f"<b>Баров:</b> {ohlc.get('bar_count')} "
            f"({ohlc.get('start_time')} &rarr; {ohlc.get('end_time')})<br>",
            f"<b>ATR{ohlc.get('atr_period')}:</b> {ohlc.get('atr')}<br>",
            f"<b>Закрытие относительно уровня:</b> {ru_value(ohlc.get('close_position_vs_level'))}<br><br>",
            f"<b>База знаний:</b> {ru_value(kb.get('status', 'missing'))}<br>",
        ]
        if kb.get("status") == "ok":
            timeframes = kb.get("timeframes") or {}
            lines.extend([
                f"<b>Проверка БЗ:</b> {ru_value(kb.get('review_status', 'unknown'))}<br>",
                f"<b>Таймфреймы БЗ:</b> контекст={timeframes.get('context', '-')} / "
                f"исполнение={timeframes.get('execution', '-')} / старший={timeframes.get('higher') or 'нет'}<br>",
                f"<b>Уровни:</b> рабочих: {level_summary.get('working_level_count', 0)} / "
                f"кандидатов: {level_summary.get('candidate_count', 0)} "
                f"(отклонено: {level_summary.get('rejected_count', 0)})<br>",
                f"<b>Ближайший уровень:</b> {nearest_level.get('price', '-')} "
                f"{ru_value(nearest_level.get('side', ''))}; оценка={nearest_level.get('kb_score', '-')}<br>",
                f"<b>Сценарий:</b> {scenario.get('family', '-')} "
                f"направление={ru_value(scenario.get('direction', '-'))} верен={ru_value(scenario.get('valid', '-'))}<br>",
                f"<b>Рекомендация:</b> {ru_value(permission_summary.get('advisor_status', 'unknown'))} / "
                f"допуск={ru_value(permission_summary.get('hard_gate_status', 'unknown'))}<br>",
                f"<b>Лучший вход:</b> {permission_summary.get('best_entry_model', '-')} "
                f"статус={ru_value(permission_summary.get('best_entry_status', '-'))}<br>",
                f"<b>Критические причины отказа:</b> {hard_rejects}<br>",
                f"<b>Ручная проверка / блокеры:</b> {len(kb.get('manual_review_queue') or [])} / "
                f"{len(kb.get('blockers') or [])}<br><br>",
            ])
        else:
            lines.extend([
                f"<b>БЗ недоступна:</b> {kb.get('error', 'анализ БЗ недоступен')}<br><br>",
            ])
        lines.extend([
            f"<b>Все операции только для чтения:</b> "
            f"<span style='color:{'#26a69a' if flags_ok else '#ef5350'}'>{ru_value(flags_ok)}</span><br>",
        ])
        references = packet.get("knowledge_references") or {}
        lines.append("<br><b>Материалы из подключённой базы знаний:</b><br>")
        if references.get("status") == "ok":
            for hit in references.get("results", []):
                lines.append(
                    f"<b>{html.escape(str(hit.get('title', '')))}</b><br>"
                    f"{html.escape(str(hit.get('excerpt', '')))}<br>"
                    f"Источник: <code>{html.escape(str(hit.get('target_id', '')))}</code><br><br>"
                )
            if not references.get("results"):
                lines.append("По запросу нет совпадений.<br>")
            lines.append(html.escape(str(references.get("notice", ""))) + "<br>")
        else:
            lines.append(html.escape(str(references.get("error", "База не подключена"))) + "<br>")
        self.analysis_view.setHtml("".join(lines))
        self._draw_kb_levels(packet)
        self._draw_manual_levels()

    # -- Scanner feed ------------------------------------------------------ #
    def _refresh_scanner_feed(self) -> None:
        if not SCANNER_STATE_PATH.exists():
            self.scanner_status_label.setText("Сканер: файла состояния пока нет (сканер не запущен).")
            self.signals_table.setRowCount(0)
            return
        try:
            state = json.loads(SCANNER_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.scanner_status_label.setText(f"Сканер: не удалось прочитать состояние ({exc}).")
            return
        latest = state.get("latest") or []
        self.signals_table.setRowCount(len(latest))
        for row, item in enumerate(latest):
            values = [
                str(item.get("symbol", "")),
                str(item.get("review_state") or "-"),
                str(item.get("analyzer_verdict") or "-"),
                str(item.get("kb_review_status") or item.get("kb_status") or "-"),
                "готово" if item.get("ok") else (item.get("error") or "ошибка"),
            ]
            for col, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if col == 0:
                    cell.setData(QtCore.Qt.UserRole, item.get("case_id"))
                self.signals_table.setItem(row, col, cell)
        self.scanner_status_label.setText(
            f"Цикл сканера {state.get('last_cycle')} @ {state.get('last_finished_at')} — "
            f"готово: {state.get('symbols_ok')}/{state.get('symbols_scanned')}; "
            f"новых кейсов на проверке: {state.get('new_signal_cases')}."
        )

    def _on_signal_double_click(self, row: int, _col: int) -> None:
        symbol_item = self.signals_table.item(row, 0)
        if symbol_item is None:
            return
        symbol = symbol_item.text()
        case_id = symbol_item.data(QtCore.Qt.UserRole)
        self.symbol_combo.setCurrentText(symbol)
        if case_id:
            self.statusBar().showMessage(
                f"{symbol}: кейс {case_id} ожидает подтверждения человеком."
            )
            if hasattr(self, "exec_reason_edit"):
                self.exec_reason_edit.setText(f"case:{case_id}")
        self._on_fetch()

    # -- Paper/shadow execution ------------------------------------------ #
    def _on_sync_execution_from_chart(self, update_reason: bool = True) -> None:
        if not hasattr(self, "exec_symbol_edit"):
            return
        symbol = self.symbol_combo.currentText().strip().upper()
        if symbol:
            self.exec_symbol_edit.setText(symbol)
        if self._last_close is not None:
            self.exec_price_spin.setValue(float(self._last_close))
        if update_reason and not self.exec_reason_edit.text().strip():
            self.exec_reason_edit.setText("ручная симуляция")

    def _execution_limits(self) -> exec_layer.RiskLimits:
        return exec_layer.RiskLimits(
            max_notional_per_order=float(self.max_notional_spin.value()),
            max_open_positions=int(self.max_positions_spin.value()),
            max_daily_loss=float(self.max_daily_loss_spin.value()),
            max_position_qty_per_symbol=float(self.max_qty_spin.value()),
        )

    def _execution_engine(self) -> exec_layer.ExecutionEngine:
        mode_text = str(self.exec_mode_combo.currentData())
        engine = self._execution_engines.get(mode_text)
        limits = self._execution_limits()
        if engine is None:
            engine = exec_layer.ExecutionEngine(mode=exec_layer.ExecutionMode(mode_text), limits=limits)
            self._execution_engines[mode_text] = engine
        else:
            engine.limits = limits
            engine.gate.limits = limits
        return engine

    def _order_request_from_form(self) -> exec_layer.OrderRequest:
        symbol = binance_feed.normalize_symbol(self.exec_symbol_edit.text().strip())
        mode = exec_layer.ExecutionMode(str(self.exec_mode_combo.currentData()))
        side = exec_layer.Side(str(self.exec_side_combo.currentData()))
        quantity = float(self.exec_qty_spin.value())
        price = float(self.exec_price_spin.value())
        reason = self.exec_reason_edit.text().strip() or "ручная симуляция"
        return exec_layer.OrderRequest(
            symbol=symbol,
            side=side,
            quantity=quantity,
            price=price,
            mode=mode,
            reason=reason,
        )

    def _on_submit_execution(self) -> None:
        try:
            order = self._order_request_from_form()
            result = self._execution_engine().submit(order)
        except Exception as exc:  # noqa: BLE001 - surface validation errors to UI
            QtWidgets.QMessageBox.warning(self, "Заявка отклонена", f"{type(exc).__name__}: {exc}")
            return
        self._refresh_execution_state()
        color = "#26a69a" if result.accepted else "#ef5350"
        self.statusBar().showMessage(
            f"{MODE_LABELS[result.mode.value]}: {ru_value(result.status)} — {result.symbol} {SIDE_LABELS[result.side.value]} "
            f"{result.quantity} @ {result.price} ({result.reason})"
        )
        self.execution_state_view.append(
            f"<span style='color:{color}'>{ru_value(result.status)}</span> "
            f"{result.symbol} {SIDE_LABELS[result.side.value]}; количество={result.quantity}; цена={result.price}; "
            f"причина={result.reason}"
        )

    def _on_engage_kill_switch(self) -> None:
        reason = self.exec_reason_edit.text().strip() or "ручной интерфейс"
        self._execution_engine().engage_kill_switch(reason=reason)
        self._refresh_execution_state()
        self.statusBar().showMessage("Аварийная блокировка включена. Новые симулированные заявки заблокированы.")

    def _on_release_kill_switch(self) -> None:
        self._execution_engine().release_kill_switch()
        self._refresh_execution_state()
        self.statusBar().showMessage("Аварийная блокировка снята.")

    def _refresh_execution_state(self) -> None:
        if not hasattr(self, "execution_state_view"):
            return
        kill_on = exec_layer.KILL_SWITCH_PATH.exists()
        self.kill_switch_label.setText("Аварийная блокировка: ВКЛЮЧЕНА" if kill_on else "Аварийная блокировка: выключена")
        self.kill_switch_label.setStyleSheet("color:#ef5350;font-weight:bold;" if kill_on else "color:#26a69a;")

        mode_text = str(self.exec_mode_combo.currentData())
        engine = self._execution_engines.get(mode_text)
        open_positions = engine.account.open_positions if engine else {}
        state_bits = [
            f"<b>Режим:</b> {MODE_LABELS[mode_text]}",
            f"<b>Реальная торговля включена:</b> нет (доступна только симуляция)",
            f"<b>Аварийная блокировка:</b> {'ВКЛЮЧЕНА' if kill_on else 'выключена'}",
            f"<b>Открытые позиции:</b> {json.dumps(open_positions, ensure_ascii=False)}",
        ]
        if EXECUTION_STATE_PATH.exists():
            try:
                state = json.loads(EXECUTION_STATE_PATH.read_text(encoding="utf-8"))
                state_bits.append(f"<b>Последнее состояние:</b> {state.get('updated_at')} ({ru_value(state.get('mode'))})")
            except (OSError, json.JSONDecodeError):
                state_bits.append("<b>Последнее состояние:</b> не удалось прочитать")
        else:
            state_bits.append("<b>Последнее состояние:</b> пока нет")
        self.execution_state_view.setHtml("<br>".join(state_bits))
        self._refresh_audit_table()

    def _refresh_audit_table(self) -> None:
        rows: list[dict[str, Any]] = []
        if EXECUTION_AUDIT_PATH.exists():
            try:
                lines = [line for line in EXECUTION_AUDIT_PATH.read_text(encoding="utf-8").splitlines() if line.strip()]
                rows = [json.loads(line) for line in lines[-30:]]
            except (OSError, json.JSONDecodeError):
                rows = []
        rows = list(reversed(rows))
        self.audit_table.setRowCount(len(rows))
        for row, item in enumerate(rows):
            values = [
                str(item.get("at", "")),
                ru_value(item.get("event", "")),
                ru_value(item.get("mode", "")),
                str(item.get("symbol", "")),
                ru_value(item.get("status", "")),
                str(item.get("reason", "")),
            ]
            for col, value in enumerate(values):
                self.audit_table.setItem(row, col, QtWidgets.QTableWidgetItem(value))


def _run_smoke(argv: list[str]) -> int:
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    app = QtWidgets.QApplication(argv)
    window = MainWindow()
    window.exec_symbol_edit.setText("BTCUSDT")
    window.exec_side_combo.setCurrentText("buy")
    window.exec_qty_spin.setValue(0.001)
    window.exec_price_spin.setValue(50_000)
    window.exec_reason_edit.setText("desktop-smoke")
    window._on_submit_execution()
    engine = window._execution_engines.get("paper")
    payload = {
        "ok": True,
        "window_constructed": True,
        "audit_rows": window.audit_table.rowCount(),
        "paper_position": None if engine is None else engine.account.open_positions.get("BTCUSDT"),
        "kill_switch_exists": exec_layer.KILL_SWITCH_PATH.exists(),
    }
    print(json.dumps(payload, ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    argv = list(argv if argv is not None else sys.argv)
    if "--smoke" in argv:
        return _run_smoke(argv)
    app = QtWidgets.QApplication(argv)
    app.setApplicationName("Trader Brain")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
