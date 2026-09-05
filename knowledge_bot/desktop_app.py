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
import json
import os
import sys
from pathlib import Path
from typing import Any

# Make sibling brain modules importable whether run as a script or frozen.
_HERE = Path(__file__).resolve().parent
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import binance_feed
import broker_adapter as exec_layer
import casebook_store as store
from chart_review_packet import build_live_kb_chart_review_packet

import pyqtgraph as pg
from PySide6 import QtCore, QtGui, QtWidgets


SCANNER_STATE_PATH = store.ROOT / "_knowledge_base" / "live_scanner" / "scanner_state.json"
EXECUTION_STATE_PATH = store.ROOT / "_knowledge_base" / "live_execution" / "execution_state.json"
EXECUTION_AUDIT_PATH = store.ROOT / "_knowledge_base" / "live_execution" / "execution_audit.jsonl"

INTERVALS = ["15m", "30m", "1h", "2h", "4h", "6h", "12h", "1d", "3d", "1w"]
DEFAULT_SYMBOLS = [
    "BTCUSDT", "ETHUSDT", "BNBUSDT", "SOLUSDT", "XRPUSDT",
    "ADAUSDT", "DOGEUSDT", "AVAXUSDT", "LINKUSDT", "DOTUSDT",
]
INTRADAY_INTERVALS = {"1m", "3m", "5m", "15m", "30m", "1h", "2h", "4h", "6h", "8h", "12h"}

UP_COLOR = "#26a69a"
DOWN_COLOR = "#ef5350"


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


# --------------------------------------------------------------------------- #
# Candlestick drawing
# --------------------------------------------------------------------------- #
class CandlestickItem(pg.GraphicsObject):
    """Minimal OHLC candlestick item for pyqtgraph.

    ``bars`` is a list of (index, open, high, low, close) tuples.
    """

    def __init__(self, bars: list[tuple[float, float, float, float, float]]):
        super().__init__()
        self._bars = bars
        self._picture = QtGui.QPicture()
        self._generate()

    def _generate(self) -> None:
        painter = QtGui.QPainter(self._picture)
        width = 0.6
        for index, open_, high, low, close in self._bars:
            rising = close >= open_
            color = QtGui.QColor(UP_COLOR if rising else DOWN_COLOR)
            painter.setPen(pg.mkPen(color))
            # high-low wick
            painter.drawLine(QtCore.QPointF(index, low), QtCore.QPointF(index, high))
            # body
            painter.setBrush(pg.mkBrush(color))
            top = max(open_, close)
            bottom = min(open_, close)
            height = top - bottom or (high - low) * 0.001 or 1e-9
            painter.drawRect(QtCore.QRectF(index - width / 2, bottom, width, height))
        painter.end()

    def paint(self, painter: QtGui.QPainter, *_args: Any) -> None:
        painter.drawPicture(0, 0, self._picture)

    def boundingRect(self) -> QtCore.QRectF:
        return QtCore.QRectF(self._picture.boundingRect())


# --------------------------------------------------------------------------- #
# Worker thread: fetch + analyze off the GUI thread
# --------------------------------------------------------------------------- #
class AnalyzeWorker(QtCore.QThread):
    """Fetch OHLC and run the read-only KB analysis on a background thread."""

    finished_ok = QtCore.Signal(dict)
    failed = QtCore.Signal(str)

    def __init__(self, symbol: str, interval: str, limit: int, market: str = "spot"):
        super().__init__()
        self._symbol = symbol
        self._interval = interval
        self._limit = limit
        self._market = market

    def run(self) -> None:  # noqa: D401 - QThread entry point
        try:
            symbol = binance_feed.normalize_symbol(self._symbol)
            payload = binance_feed.get_ohlc(
                symbol,
                interval=self._interval,
                limit=self._limit,
                market=self._market,
                write_cache=True,
            )
            ohlc_path = binance_feed.cache_path(symbol, self._interval, self._market)
            context_interval = kb_context_interval(self._interval)
            context_path = ohlc_path
            if context_interval != self._interval:
                binance_feed.get_ohlc(
                    symbol,
                    interval=context_interval,
                    limit=kb_limit(context_interval, self._limit),
                    market=self._market,
                    write_cache=True,
                )
                context_path = binance_feed.cache_path(symbol, context_interval, self._market)
            higher_interval = kb_higher_interval(context_interval)
            higher_path: Path | None = None
            if higher_interval:
                binance_feed.get_ohlc(
                    symbol,
                    interval=higher_interval,
                    limit=kb_limit(higher_interval, self._limit),
                    market=self._market,
                    write_cache=True,
                )
                higher_path = binance_feed.cache_path(symbol, higher_interval, self._market)
            packet = build_live_kb_chart_review_packet(
                _packet_args(
                    symbol,
                    ohlc_path,
                    self._interval,
                    self._market,
                    context_ohlc_path=context_path,
                    context_interval=context_interval,
                    higher_ohlc_path=higher_path,
                    higher_interval=higher_interval,
                )
            )
            self.finished_ok.emit({
                "symbol": symbol,
                "interval": self._interval,
                "context_interval": context_interval,
                "higher_interval": higher_interval,
                "bars": payload.get("bars") or [],
                "packet": packet,
            })
        except Exception as exc:  # noqa: BLE001 - surface error to UI
            self.failed.emit(f"{type(exc).__name__}: {exc}")


class SymbolsWorker(QtCore.QThread):
    """Load the tradable symbol list from the public feed."""

    finished_ok = QtCore.Signal(list)
    failed = QtCore.Signal(str)

    def __init__(self, market: str = "spot", quote: str = "USDT"):
        super().__init__()
        self._market = market
        self._quote = quote

    def run(self) -> None:
        try:
            symbols = binance_feed.list_symbols(market=self._market, quote=self._quote, trading_only=True)
            self.finished_ok.emit(sorted(symbols))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{type(exc).__name__}: {exc}")


def _packet_args(symbol: str, ohlc_path: Path, interval: str, market: str,
                 *, context_ohlc_path: Path | None = None,
                 context_interval: str | None = None,
                 higher_ohlc_path: Path | None = None,
                 higher_interval: str = "") -> argparse.Namespace:
    """Build the Namespace the read-only packet builder expects."""
    return argparse.Namespace(
        ohlc_file=ohlc_path,
        context_ohlc_file=context_ohlc_path,
        higher_ohlc_file=higher_ohlc_path,
        symbol=symbol,
        instrument=symbol,
        venue=f"binance_{market}",
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
        self.setWindowTitle("Trader Brain - Read-only Chart Review")
        self.resize(1180, 760)
        self._worker: AnalyzeWorker | None = None
        self._symbols_worker: SymbolsWorker | None = None
        self._execution_engines: dict[str, exec_layer.ExecutionEngine] = {}
        self._last_close: float | None = None
        self._chart_price_bounds: tuple[float, float] | None = None

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
        tabs.addTab(self._build_review_tab(), "Chart review")
        tabs.addTab(self._build_execution_tab(), "Execution")
        outer.addWidget(tabs, stretch=1)

        self.statusBar().showMessage("Ready. Chart review is read-only; execution tab is paper/shadow only.")

    def _build_review_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)
        splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        splitter.addWidget(self._build_chart())
        splitter.addWidget(self._build_side_panel())
        splitter.setStretchFactor(0, 3)
        splitter.setStretchFactor(1, 2)
        layout.addWidget(splitter)
        return tab

    def _build_banner(self) -> QtWidgets.QWidget:
        banner = QtWidgets.QLabel(
            "  SAFE MODE - chart analysis is read-only; execution tab is paper/shadow only. Live trading is not armed here.  "
        )
        banner.setAlignment(QtCore.Qt.AlignCenter)
        banner.setStyleSheet(
            "background-color:#33691e;color:white;font-weight:bold;padding:6px;border-radius:4px;"
        )
        return banner

    def _build_controls(self) -> QtWidgets.QHBoxLayout:
        row = QtWidgets.QHBoxLayout()

        row.addWidget(QtWidgets.QLabel("Coin:"))
        self.symbol_combo = QtWidgets.QComboBox()
        self.symbol_combo.setEditable(True)
        self.symbol_combo.addItems(DEFAULT_SYMBOLS)
        self.symbol_combo.setMinimumWidth(140)
        row.addWidget(self.symbol_combo)

        self.load_symbols_btn = QtWidgets.QPushButton("Load full list")
        self.load_symbols_btn.clicked.connect(self._on_load_symbols)
        row.addWidget(self.load_symbols_btn)

        row.addWidget(QtWidgets.QLabel("Interval:"))
        self.interval_combo = QtWidgets.QComboBox()
        self.interval_combo.addItems(INTERVALS)
        self.interval_combo.setCurrentText("1d")
        row.addWidget(self.interval_combo)

        row.addWidget(QtWidgets.QLabel("Bars:"))
        self.limit_spin = QtWidgets.QSpinBox()
        self.limit_spin.setRange(20, 1000)
        self.limit_spin.setValue(200)
        self.limit_spin.setSingleStep(20)
        row.addWidget(self.limit_spin)

        self.fetch_btn = QtWidgets.QPushButton("Fetch & Analyze")
        self.fetch_btn.clicked.connect(self._on_fetch)
        row.addWidget(self.fetch_btn)

        row.addStretch(1)
        return row

    def _build_chart(self) -> QtWidgets.QWidget:
        pg.setConfigOptions(antialias=True, background="#101418", foreground="#cccccc")
        self.chart = pg.PlotWidget()
        self.chart.showGrid(x=True, y=True, alpha=0.2)
        self.chart.setLabel("left", "Price")
        self.chart.setLabel("bottom", "Bar index")
        self._candle_item: CandlestickItem | None = None
        return self.chart

    def _build_side_panel(self) -> QtWidgets.QWidget:
        panel = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(panel)

        layout.addWidget(self._section_label("Analysis"))
        self.analysis_view = QtWidgets.QTextEdit()
        self.analysis_view.setReadOnly(True)
        self.analysis_view.setMinimumHeight(220)
        layout.addWidget(self.analysis_view)

        header = QtWidgets.QHBoxLayout()
        header.addWidget(self._section_label("Scanner signals (pending confirmation)"))
        header.addStretch(1)
        self.refresh_feed_btn = QtWidgets.QPushButton("Refresh")
        self.refresh_feed_btn.clicked.connect(self._refresh_scanner_feed)
        header.addWidget(self.refresh_feed_btn)
        layout.addLayout(header)

        self.signals_table = QtWidgets.QTableWidget(0, 5)
        self.signals_table.setHorizontalHeaderLabels(["Symbol", "Review state", "Verdict", "KB", "OK"])
        self.signals_table.horizontalHeader().setStretchLastSection(True)
        self.signals_table.setEditTriggers(QtWidgets.QAbstractItemView.NoEditTriggers)
        self.signals_table.setSelectionBehavior(QtWidgets.QAbstractItemView.SelectRows)
        self.signals_table.cellDoubleClicked.connect(self._on_signal_double_click)
        layout.addWidget(self.signals_table)

        self.scanner_status_label = QtWidgets.QLabel("Scanner: no state yet")
        self.scanner_status_label.setStyleSheet("color:#888;")
        layout.addWidget(self.scanner_status_label)

        return panel

    def _build_execution_tab(self) -> QtWidgets.QWidget:
        tab = QtWidgets.QWidget()
        layout = QtWidgets.QVBoxLayout(tab)

        mode_row = QtWidgets.QHBoxLayout()
        mode_row.addWidget(QtWidgets.QLabel("Execution mode:"))
        self.exec_mode_combo = QtWidgets.QComboBox()
        self.exec_mode_combo.addItems(["paper", "shadow"])
        self.exec_mode_combo.currentTextChanged.connect(lambda _text: self._refresh_execution_state())
        mode_row.addWidget(self.exec_mode_combo)
        self.sync_execution_btn = QtWidgets.QPushButton("Use chart symbol / last close")
        self.sync_execution_btn.clicked.connect(lambda _checked=False: self._on_sync_execution_from_chart())
        mode_row.addWidget(self.sync_execution_btn)
        self.refresh_execution_btn = QtWidgets.QPushButton("Refresh execution")
        self.refresh_execution_btn.clicked.connect(lambda _checked=False: self._refresh_execution_state())
        mode_row.addWidget(self.refresh_execution_btn)
        mode_row.addStretch(1)
        layout.addLayout(mode_row)

        top = QtWidgets.QHBoxLayout()
        order_group = QtWidgets.QGroupBox("Manual paper/shadow order")
        order_form = QtWidgets.QFormLayout(order_group)
        self.exec_symbol_edit = QtWidgets.QLineEdit()
        self.exec_symbol_edit.setText(self.symbol_combo.currentText().strip().upper())
        order_form.addRow("Symbol:", self.exec_symbol_edit)
        self.exec_side_combo = QtWidgets.QComboBox()
        self.exec_side_combo.addItems(["buy", "sell"])
        order_form.addRow("Side:", self.exec_side_combo)
        self.exec_qty_spin = QtWidgets.QDoubleSpinBox()
        self.exec_qty_spin.setDecimals(8)
        self.exec_qty_spin.setRange(0.0, 1_000_000.0)
        self.exec_qty_spin.setValue(0.001)
        self.exec_qty_spin.setSingleStep(0.001)
        order_form.addRow("Quantity:", self.exec_qty_spin)
        self.exec_price_spin = QtWidgets.QDoubleSpinBox()
        self.exec_price_spin.setDecimals(8)
        self.exec_price_spin.setRange(0.0, 100_000_000.0)
        self.exec_price_spin.setSingleStep(10.0)
        order_form.addRow("Reference price:", self.exec_price_spin)
        self.exec_reason_edit = QtWidgets.QLineEdit()
        self.exec_reason_edit.setPlaceholderText("case:<id> or manual rehearsal note")
        order_form.addRow("Reason / case:", self.exec_reason_edit)
        self.exec_submit_btn = QtWidgets.QPushButton("Submit through RiskGate")
        self.exec_submit_btn.clicked.connect(self._on_submit_execution)
        order_form.addRow(self.exec_submit_btn)
        top.addWidget(order_group, stretch=2)

        risk_group = QtWidgets.QGroupBox("Risk limits")
        risk_form = QtWidgets.QFormLayout(risk_group)
        self.max_notional_spin = QtWidgets.QDoubleSpinBox()
        self.max_notional_spin.setRange(1.0, 1_000_000.0)
        self.max_notional_spin.setValue(100.0)
        self.max_notional_spin.setSingleStep(10.0)
        risk_form.addRow("Max notional / order:", self.max_notional_spin)
        self.max_positions_spin = QtWidgets.QSpinBox()
        self.max_positions_spin.setRange(1, 100)
        self.max_positions_spin.setValue(3)
        risk_form.addRow("Max open positions:", self.max_positions_spin)
        self.max_daily_loss_spin = QtWidgets.QDoubleSpinBox()
        self.max_daily_loss_spin.setRange(1.0, 1_000_000.0)
        self.max_daily_loss_spin.setValue(50.0)
        self.max_daily_loss_spin.setSingleStep(10.0)
        risk_form.addRow("Max daily loss:", self.max_daily_loss_spin)
        self.max_qty_spin = QtWidgets.QDoubleSpinBox()
        self.max_qty_spin.setDecimals(8)
        self.max_qty_spin.setRange(0.00000001, 1_000_000.0)
        self.max_qty_spin.setValue(1.0)
        self.max_qty_spin.setSingleStep(0.1)
        risk_form.addRow("Max qty / symbol:", self.max_qty_spin)
        top.addWidget(risk_group, stretch=2)

        kill_group = QtWidgets.QGroupBox("Kill-switch")
        kill_layout = QtWidgets.QVBoxLayout(kill_group)
        self.kill_switch_label = QtWidgets.QLabel("Kill-switch: unknown")
        kill_layout.addWidget(self.kill_switch_label)
        self.engage_kill_btn = QtWidgets.QPushButton("Engage kill-switch")
        self.engage_kill_btn.clicked.connect(self._on_engage_kill_switch)
        kill_layout.addWidget(self.engage_kill_btn)
        self.release_kill_btn = QtWidgets.QPushButton("Release kill-switch")
        self.release_kill_btn.clicked.connect(self._on_release_kill_switch)
        kill_layout.addWidget(self.release_kill_btn)
        kill_layout.addStretch(1)
        top.addWidget(kill_group, stretch=1)
        layout.addLayout(top)

        layout.addWidget(self._section_label("Execution state"))
        self.execution_state_view = QtWidgets.QTextEdit()
        self.execution_state_view.setReadOnly(True)
        self.execution_state_view.setMaximumHeight(120)
        layout.addWidget(self.execution_state_view)

        layout.addWidget(self._section_label("Audit log (latest)"))
        self.audit_table = QtWidgets.QTableWidget(0, 6)
        self.audit_table.setHorizontalHeaderLabels(["At", "Event", "Mode", "Symbol", "Status", "Reason"])
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
        self.statusBar().showMessage("Loading symbol list from the public feed...")
        self._symbols_worker = SymbolsWorker()
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
        self.statusBar().showMessage(f"Loaded {len(symbols)} USDT symbols.")

    def _on_symbols_failed(self, message: str) -> None:
        self.load_symbols_btn.setEnabled(True)
        self.statusBar().showMessage(f"Symbol load failed: {message}")

    def _on_fetch(self) -> None:
        symbol = self.symbol_combo.currentText().strip().upper()
        if not symbol:
            self.statusBar().showMessage("Enter a coin symbol first.")
            return
        interval = self.interval_combo.currentText()
        limit = self.limit_spin.value()
        self.fetch_btn.setEnabled(False)
        self.statusBar().showMessage(f"Fetching {symbol} {interval}...")
        self._worker = AnalyzeWorker(symbol, interval, limit)
        self._worker.finished_ok.connect(self._on_analysis_done)
        self._worker.failed.connect(self._on_analysis_failed)
        self._worker.start()

    def _on_analysis_done(self, result: dict[str, Any]) -> None:
        self.fetch_btn.setEnabled(True)
        self._draw_candles(result.get("bars") or [])
        self._render_analysis(result["symbol"], result["interval"], result["packet"])
        context_interval = result.get("context_interval") or result["interval"]
        higher_interval = result.get("higher_interval") or "none"
        self.statusBar().showMessage(
            f"{result['symbol']} {result['interval']}: {len(result.get('bars') or [])} bars analyzed "
            f"(KB context={context_interval}, higher={higher_interval}, read-only)."
        )

    def _on_analysis_failed(self, message: str) -> None:
        self.fetch_btn.setEnabled(True)
        self.statusBar().showMessage(f"Analysis failed: {message}")
        QtWidgets.QMessageBox.warning(self, "Analysis failed", message)

    # -- Rendering --------------------------------------------------------- #
    def _draw_candles(self, bars: list[dict[str, Any]]) -> None:
        self.chart.clear()
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
        self._candle_item = CandlestickItem(tuples)
        self.chart.addItem(self._candle_item)
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
        levels = level_report.get("levels") or []
        working_levels = [level for level in levels if level.get("kb_status") == "pass"]
        working_levels.sort(key=lambda item: float(item.get("distance_atr") or 9999))
        for level in working_levels[:12]:
            try:
                price = float(level.get("price"))
            except (TypeError, ValueError):
                continue
            if price < visible_low or price > visible_high:
                continue
            side = str(level.get("side") or "level")
            if side == "support":
                color = "#26a69a"
            elif side == "resistance":
                color = "#ef5350"
            else:
                color = "#ffca28"
            pen = pg.mkPen(color, width=1.6, style=QtCore.Qt.DashLine)
            line = pg.InfiniteLine(pos=price, angle=0, pen=pen, movable=False)
            line.setZValue(10)
            self.chart.addItem(line)
            label = pg.TextItem(
                f"{side} {price:g} score={level.get('kb_score', '-')}",
                color=color,
                anchor=(0, 1),
            )
            label.setZValue(11)
            label.setPos(0, price)
            self.chart.addItem(label)

    def _render_analysis(self, symbol: str, interval: str, packet: dict[str, Any]) -> None:
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
        hard_rejects = ", ".join(permission_summary.get("hard_rejects") or []) or "none"
        flags_ok = all(packet.get(flag) is False for flag in (
            "execution_allowed", "runtime_signal_allowed", "order_generation_allowed",
            "pnl_computation_allowed", "aggregate_winrate_allowed", "paper_trading_allowed",
            "live_trading_allowed", "backtest_harness_allowed",
        ))
        missing = ", ".join(completeness.get("missing_fields") or []) or "none"
        lines = [
            f"<h3>{symbol} &middot; {interval}</h3>",
            f"<b>Review state:</b> {review.get('state', 'unknown')}<br>",
            f"<i>{review.get('reason', '')}</i><br><br>",
            f"<b>Analyzer verdict:</b> {verdict.get('state', 'unknown')}<br>",
            f"<i>{verdict.get('reason', '')}</i><br><br>",
            f"<b>Hard gate satisfied:</b> {completeness.get('hard_gate_satisfied')}<br>",
            f"<b>Missing trade-contract fields:</b> {missing}<br><br>",
            f"<b>Bars:</b> {ohlc.get('bar_count')} "
            f"({ohlc.get('start_time')} &rarr; {ohlc.get('end_time')})<br>",
            f"<b>ATR{ohlc.get('atr_period')}:</b> {ohlc.get('atr')}<br>",
            f"<b>Close vs level:</b> {ohlc.get('close_position_vs_level')}<br><br>",
            f"<b>KB engine:</b> {kb.get('status', 'missing')}<br>",
        ]
        if kb.get("status") == "ok":
            timeframes = kb.get("timeframes") or {}
            lines.extend([
                f"<b>KB review:</b> {kb.get('review_status', 'unknown')}<br>",
                f"<b>KB timeframes:</b> context={timeframes.get('context', '-')} / "
                f"execution={timeframes.get('execution', '-')} / higher={timeframes.get('higher') or 'none'}<br>",
                f"<b>Levels:</b> {level_summary.get('working_level_count', 0)} working / "
                f"{level_summary.get('candidate_count', 0)} candidates "
                f"({level_summary.get('rejected_count', 0)} rejected)<br>",
                f"<b>Nearest level:</b> {nearest_level.get('price', '-')} "
                f"{nearest_level.get('side', '')} score={nearest_level.get('kb_score', '-')}<br>",
                f"<b>Scenario:</b> {scenario.get('family', '-')} "
                f"direction={scenario.get('direction', '-')} valid={scenario.get('valid', '-')}<br>",
                f"<b>Advisor:</b> {permission_summary.get('advisor_status', 'unknown')} / "
                f"hard_gate={permission_summary.get('hard_gate_status', 'unknown')}<br>",
                f"<b>Best entry:</b> {permission_summary.get('best_entry_model', '-')} "
                f"status={permission_summary.get('best_entry_status', '-')}<br>",
                f"<b>Hard rejects:</b> {hard_rejects}<br>",
                f"<b>Manual review / blockers:</b> {len(kb.get('manual_review_queue') or [])} / "
                f"{len(kb.get('blockers') or [])}<br><br>",
            ])
        else:
            lines.extend([
                f"<b>KB fallback:</b> {kb.get('error', 'no KB analysis available')}<br><br>",
            ])
        lines.extend([
            f"<b>Safety flags all read-only:</b> "
            f"<span style='color:{'#26a69a' if flags_ok else '#ef5350'}'>{flags_ok}</span><br>",
        ])
        self.analysis_view.setHtml("".join(lines))
        self._draw_kb_levels(packet)

    # -- Scanner feed ------------------------------------------------------ #
    def _refresh_scanner_feed(self) -> None:
        if not SCANNER_STATE_PATH.exists():
            self.scanner_status_label.setText("Scanner: no state file yet (run scanner_daemon.py).")
            self.signals_table.setRowCount(0)
            return
        try:
            state = json.loads(SCANNER_STATE_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            self.scanner_status_label.setText(f"Scanner: cannot read state ({exc}).")
            return
        latest = state.get("latest") or []
        self.signals_table.setRowCount(len(latest))
        for row, item in enumerate(latest):
            values = [
                str(item.get("symbol", "")),
                str(item.get("review_state") or "-"),
                str(item.get("analyzer_verdict") or "-"),
                str(item.get("kb_review_status") or item.get("kb_status") or "-"),
                "ok" if item.get("ok") else (item.get("error") or "fail"),
            ]
            for col, value in enumerate(values):
                cell = QtWidgets.QTableWidgetItem(value)
                if col == 0:
                    cell.setData(QtCore.Qt.UserRole, item.get("case_id"))
                self.signals_table.setItem(row, col, cell)
        self.scanner_status_label.setText(
            f"Scanner cycle {state.get('last_cycle')} @ {state.get('last_finished_at')} - "
            f"{state.get('symbols_ok')}/{state.get('symbols_scanned')} ok, "
            f"{state.get('new_signal_cases')} new pending case(s)."
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
                f"{symbol}: pending case {case_id}. Confirm it via casebook.py confirm (human-in-the-loop)."
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
            self.exec_reason_edit.setText("manual-paper-rehearsal")

    def _execution_limits(self) -> exec_layer.RiskLimits:
        return exec_layer.RiskLimits(
            max_notional_per_order=float(self.max_notional_spin.value()),
            max_open_positions=int(self.max_positions_spin.value()),
            max_daily_loss=float(self.max_daily_loss_spin.value()),
            max_position_qty_per_symbol=float(self.max_qty_spin.value()),
        )

    def _execution_engine(self) -> exec_layer.ExecutionEngine:
        mode_text = self.exec_mode_combo.currentText()
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
        mode = exec_layer.ExecutionMode(self.exec_mode_combo.currentText())
        side = exec_layer.Side(self.exec_side_combo.currentText())
        quantity = float(self.exec_qty_spin.value())
        price = float(self.exec_price_spin.value())
        reason = self.exec_reason_edit.text().strip() or "manual-paper-rehearsal"
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
            QtWidgets.QMessageBox.warning(self, "Execution rejected", f"{type(exc).__name__}: {exc}")
            return
        self._refresh_execution_state()
        color = "#26a69a" if result.accepted else "#ef5350"
        self.statusBar().showMessage(
            f"{result.mode.value} {result.status}: {result.symbol} {result.side.value} "
            f"{result.quantity} @ {result.price} ({result.reason})"
        )
        self.execution_state_view.append(
            f"<span style='color:{color}'>{result.status}</span> "
            f"{result.symbol} {result.side.value} qty={result.quantity} price={result.price} "
            f"reason={result.reason}"
        )

    def _on_engage_kill_switch(self) -> None:
        reason = self.exec_reason_edit.text().strip() or "manual-ui"
        self._execution_engine().engage_kill_switch(reason=reason)
        self._refresh_execution_state()
        self.statusBar().showMessage("Kill-switch engaged. Paper/shadow orders are blocked until released.")

    def _on_release_kill_switch(self) -> None:
        self._execution_engine().release_kill_switch()
        self._refresh_execution_state()
        self.statusBar().showMessage("Kill-switch released.")

    def _refresh_execution_state(self) -> None:
        if not hasattr(self, "execution_state_view"):
            return
        kill_on = exec_layer.KILL_SWITCH_PATH.exists()
        self.kill_switch_label.setText("Kill-switch: ENGAGED" if kill_on else "Kill-switch: released")
        self.kill_switch_label.setStyleSheet("color:#ef5350;font-weight:bold;" if kill_on else "color:#26a69a;")

        mode_text = self.exec_mode_combo.currentText()
        engine = self._execution_engines.get(mode_text)
        open_positions = engine.account.open_positions if engine else {}
        state_bits = [
            f"<b>Mode:</b> {mode_text}",
            f"<b>Live armed:</b> False (GUI exposes paper/shadow only)",
            f"<b>Kill-switch:</b> {'ENGAGED' if kill_on else 'released'}",
            f"<b>Open positions:</b> {json.dumps(open_positions, ensure_ascii=False)}",
        ]
        if EXECUTION_STATE_PATH.exists():
            try:
                state = json.loads(EXECUTION_STATE_PATH.read_text(encoding="utf-8"))
                state_bits.append(f"<b>Last state file:</b> {state.get('updated_at')} ({state.get('mode')})")
            except (OSError, json.JSONDecodeError):
                state_bits.append("<b>Last state file:</b> unreadable")
        else:
            state_bits.append("<b>Last state file:</b> none yet")
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
                str(item.get("event", "")),
                str(item.get("mode", "")),
                str(item.get("symbol", "")),
                str(item.get("status", "")),
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
