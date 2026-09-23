"""Vision extraction layer for chart screenshots.

Sends a chart screenshot to OpenAI's vision endpoint and gets back **only
metadata** that we then plug into the existing numerical pipeline. The
model is *not* asked to predict price direction, calculate ATR, or invent
levels — it just reads what is drawn on the screen.

Returned JSON shape::

    {
        "ticker": "MGCM26",            # exact ticker as drawn (or null)
        "timeframe": "1d",             # 5m/15m/30m/1h/2h/4h/1d/1w (or null)
        "drawn_levels": [4506.0, ...], # numeric prices of horizontal lines
        "trend_lines": "ascending|descending|none",
        "notes": "short free-text",
        "confidence": 0.0..1.0
    }

The orchestrator (analyze_chart) stores ticker/timeframe/drawn_levels as
review metadata and runs the deterministic Layer 6 packet against real OHLC
data. Vision output never becomes a signal or an automatic checklist pass.
"""

from __future__ import annotations

import base64
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any

try:
    from dotenv import load_dotenv
    import sys as _sys

    _candidates = []
    if getattr(_sys, "frozen", False):
        # Running as PyInstaller exe — look next to the .exe
        _candidates.append(Path(_sys.executable).resolve().parent / ".env")
    # Look next to the source file (repo root)
    _candidates.append(Path(__file__).resolve().parent.parent / ".env")
    # And CWD fallback
    _candidates.append(Path.cwd() / ".env")
    for _p in _candidates:
        if _p.exists():
            load_dotenv(_p)
            break
except Exception:
    pass


DEFAULT_MODEL = os.environ.get("OPENAI_VISION_MODEL", "gpt-4o")

SYSTEM_PROMPT = """You are a precise chart reader. Look at the trading chart screenshot and extract ONLY visible metadata.

Rules:
- Do NOT predict price direction.
- Do NOT compute ATR, RSI, or any indicator the user did not draw.
- Do NOT invent levels that aren't drawn as horizontal lines.
- Read the ticker symbol exactly as displayed (e.g. MGCM26, ESM2026, BTCUSDT).
- Read the timeframe exactly (5m, 15m, 30m, 1h, 2h, 4h, 1d, 1w).
- For drawn horizontal lines, return their numeric price values rounded to the nearest tick.
- If something is unreadable, return null for that field. Do not guess.

Return STRICT JSON only, no markdown fences, with this schema:
{
  "ticker": string|null,
  "timeframe": string|null,
  "drawn_levels": [number, ...],
  "trend_lines": "ascending"|"descending"|"both"|"none",
  "notes": string,
  "confidence": number between 0 and 1
}
"""


@dataclass
class VisionResult:
    ticker: str | None
    timeframe: str | None
    drawn_levels: list[float]
    trend_lines: str
    notes: str
    confidence: float
    raw: dict

    def to_dict(self) -> dict:
        return {
            "ticker": self.ticker,
            "timeframe": self.timeframe,
            "drawn_levels": self.drawn_levels,
            "trend_lines": self.trend_lines,
            "notes": self.notes,
            "confidence": self.confidence,
        }


class VisionUnavailable(RuntimeError):
    """Raised when the OpenAI client cannot be initialised."""


def _encode_image(path: Path) -> tuple[str, str]:
    """Return (data_uri, mime_type)."""

    suffix = path.suffix.lower()
    mime = {
        ".png": "image/png",
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".gif": "image/gif",
        ".webp": "image/webp",
    }.get(suffix, "image/png")
    data = base64.b64encode(path.read_bytes()).decode("ascii")
    return f"data:{mime};base64,{data}", mime


def _client():
    try:
        from openai import OpenAI
    except ImportError as exc:  # pragma: no cover
        raise VisionUnavailable("openai package not installed") from exc

    key = os.environ.get("OPENAI_API_KEY")
    if not key:
        raise VisionUnavailable(
            "OPENAI_API_KEY not set. Put it in .env next to the repo or set the env var."
        )
    return OpenAI(api_key=key)


def _normalise_timeframe(value: str | None) -> str | None:
    if not value:
        return None
    cleaned = value.strip().lower().replace(" ", "")
    aliases = {
        "5m": "5m", "5min": "5m",
        "15m": "15m", "15min": "15m",
        "30m": "30m", "30min": "30m",
        "1h": "1h", "h1": "1h", "60m": "1h", "1hour": "1h",
        "2h": "2h", "h2": "2h",
        "4h": "4h", "h4": "4h", "240m": "4h",
        "1d": "1d", "d1": "1d", "1day": "1d", "daily": "1d",
        "1w": "1w", "w1": "1w", "weekly": "1w",
    }
    return aliases.get(cleaned, cleaned if cleaned in {"5m", "15m", "30m", "1h", "2h", "4h", "1d", "1w"} else None)


def extract_metadata(image_path: str | Path, *, model: str | None = None) -> VisionResult:
    """Send screenshot to the vision model and return parsed metadata."""

    path = Path(image_path)
    if not path.exists():
        raise FileNotFoundError(f"Image not found: {path}")

    client = _client()
    data_uri, _mime = _encode_image(path)

    response = client.chat.completions.create(
        model=model or DEFAULT_MODEL,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Extract the metadata from this chart screenshot."},
                    {"type": "image_url", "image_url": {"url": data_uri}},
                ],
            },
        ],
    )

    content = response.choices[0].message.content or "{}"
    try:
        raw = json.loads(content)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Model returned non-JSON: {content[:200]}") from exc

    levels_raw = raw.get("drawn_levels") or []
    levels: list[float] = []
    for v in levels_raw:
        try:
            levels.append(float(v))
        except (TypeError, ValueError):
            continue

    return VisionResult(
        ticker=(raw.get("ticker") or None) and str(raw["ticker"]).strip().upper(),
        timeframe=_normalise_timeframe(raw.get("timeframe")),
        drawn_levels=levels,
        trend_lines=str(raw.get("trend_lines") or "none"),
        notes=str(raw.get("notes") or ""),
        confidence=float(raw.get("confidence") or 0.0),
        raw=raw,
    )


def analyze_chart(image_path: str | Path,
                  *,
                  symbol: str | None = None,
                  start: str,
                  end: str,
                  context_interval: str = "1d",
                  execution_interval: str = "15m",
                  higher_interval: str = "1w",
                  breakout_direction: str = "auto",
                  execution_lookback_bars: int | None = None,
                  manual_context: dict[str, Any] | None = None,
                  model: str | None = None,
                  skip_vision: bool = False) -> dict[str, Any]:
    from chart_review_packet import (
        ChartReviewParams,
        build_chart_review_packet_from_data_source,
        enrich_manual_context_with_vision,
    )

    enriched_context = enrich_manual_context_with_vision(
        manual_context,
        image_path,
        model=model,
        expected_symbol=symbol,
        expected_timeframes=[context_interval, execution_interval, higher_interval],
        run_vision=not skip_vision,
    )
    resolved_symbol = (symbol or "").strip().upper()
    if not resolved_symbol:
        metadata = enriched_context.get("vision_metadata") or {}
        if isinstance(metadata, dict):
            resolved_symbol = str(metadata.get("ticker") or "").strip().upper()
            if resolved_symbol:
                metadata["symbol_source"] = "vision_metadata"
    if not resolved_symbol:
        raise ValueError("symbol is required unless vision metadata contains a readable ticker")

    params = ChartReviewParams() if execution_lookback_bars is None else ChartReviewParams(execution_lookback_bars=execution_lookback_bars)
    return build_chart_review_packet_from_data_source(
        resolved_symbol,
        context_interval,
        execution_interval,
        start,
        end,
        higher_interval,
        breakout_direction,
        enriched_context,
        params,
    )


def main(argv: list[str] | None = None) -> int:
    import argparse
    import sys

    parser = argparse.ArgumentParser(description="Extract chart metadata or build a read-only KB chart review packet.")
    parser.add_argument("image", help="Path to chart screenshot (png/jpg)")
    parser.add_argument("--model", default=None)
    parser.add_argument("--review-packet", "--analyze", action="store_true", help="Run Layer 6 review packet after metadata extraction")
    parser.add_argument("--symbol", help="Explicit OHLC symbol; used when vision cannot read ticker")
    parser.add_argument("--context-interval", default="1d")
    parser.add_argument("--execution-interval", default="15m")
    parser.add_argument("--higher-interval", default="1w")
    parser.add_argument("--start", help="Start month in YYYY-MM format for review packet")
    parser.add_argument("--end", help="End month in YYYY-MM format for review packet")
    parser.add_argument("--breakout-direction", choices=["auto", "long", "short"], default="auto")
    parser.add_argument("--execution-lookback-bars", type=int, default=None)
    parser.add_argument("--manual-context-json", help="Optional JSON object with chart/manual context")
    parser.add_argument("--skip-vision", action="store_true", help="Build review packet with screenshot ref but without calling the vision API")
    parser.add_argument("--output-format", choices=["text", "json"], default="json")
    args = parser.parse_args(argv)

    if args.review_packet:
        if not args.start or not args.end:
            print("error: --start and --end are required with --review-packet", file=sys.stderr)
            return 2
        from permission_context import load_manual_context
        from chart_review_packet import print_report

        manual_context = load_manual_context(args.manual_context_json)
        try:
            packet = analyze_chart(
                args.image,
                symbol=args.symbol,
                start=args.start,
                end=args.end,
                context_interval=args.context_interval,
                execution_interval=args.execution_interval,
                higher_interval=args.higher_interval,
                breakout_direction=args.breakout_direction,
                execution_lookback_bars=args.execution_lookback_bars,
                manual_context=manual_context,
                model=args.model,
                skip_vision=args.skip_vision,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        if args.output_format == "text":
            print_report(packet)
        else:
            print(json.dumps(packet, indent=2, ensure_ascii=False))
        return 0

    try:
        result = extract_metadata(args.image, model=args.model)
    except (VisionUnavailable, FileNotFoundError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    print(json.dumps(result.to_dict(), indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
