"""Read-only pixel measurements of eight manually reviewed original OHLC charts.

Run with botArtjahan/.venv/Scripts/python.exe. Original JPEG files are only read.
These are body/prior mean high-low range proxies, never exact price ATR.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
from PIL import Image

HERE = Path(__file__).resolve().parent
IMAGE_DIR = HERE.parent / "images"

# Manually chosen from the ORIGINAL images: signal stem under arrow/dashed line,
# regular OHLC-bar pitch, available complete prior bars, author-label scope.
CASES = [
    dict(id=109, x=472, pitch=28.46, n=14, author_pn="Предшествующий растущий бар около x=386 назван ПН; измеряется более поздний сигнальный бар x=472.", note="Стрелка над сигнальным дневным баром; отдельная стрелка ПН стоит раньше."),
    dict(id=113, x=374, pitch=20.55, n=14, author_pn="ПН на дневной картинке не подписан; контрольный пример.", note="Стрелка на закрытии у уровня после окончания распила."),
    dict(id=117, x=477, pitch=39.15, n=12, author_pn="Автор: подошли к уровню ПН баром; стрелка стоит на этом растущем баре.", note="До сигнала видны только 12 полных баров; не выдавать среднее 12 за prior14."),
    dict(id=132, x=455, pitch=25.1, n=14, author_pn="Автор называет ПН предшествующий подходящий бар около x=430; измеряется сигнальный ЛП x=455.", note="Дневная стрелка и пунктир совпадают с сигнальным баром."),
    dict(id=160, x=305, pitch=17.82, n=14, author_pn="В дневном тексте ПН нет; часовой текст называет предшествующие нисходящие бары ПН.", note="Дневная подпись про сильный рост противоречит видимому снижению; это отдельно отмечено в основном разборе."),
    dict(id=172, x=433, pitch=29.35, n=14, author_pn="Автор: подход к уровню ПН барами; точный набор названных баров не выделен.", note="Измеряется растущий бар ЛП под дневной стрелкой."),
    dict(id=182, x=377, pitch=16.4, n=14, author_pn="Автор: после ПН бара нет отката; ПН относится к предыдущему бару около x=360.", note="Измеряется небольшой сигнальный красный бар после крупного падения."),
    dict(id=200, x=419, pitch=18.35, n=14, author_pn="Автор: после ПН бара нет отката; подразумевается более ранний большой растущий бар около x=383.", note="Измеряется сигнальный бар у уровня, а не более ранний импульс."),
]


def masks(rgb: np.ndarray) -> dict[str, np.ndarray]:
    a = rgb.astype(np.int16)
    r, g, b = a[..., 0], a[..., 1], a[..., 2]
    return {
        "red": (r > 160) & (r > g + 60) & (r > b + 35),
        "green": (g > 85) & (g > r + 45) & (b > r + 25) & (g > b + 8),
    }


def row_bands(ys: np.ndarray, gap: int = 3) -> list[list[int]]:
    if not len(ys):
        return []
    split = np.where(np.diff(ys) > gap)[0] + 1
    return [[int(z[0]), int(z[-1])] for z in np.split(ys, split)]


def decode_bar(ms: dict[str, np.ndarray], expected_x: float, pitch: float) -> dict:
    h, w = next(iter(ms.values())).shape
    xx = int(round(expected_x))
    search = range(max(0, xx - 3), min(w, xx + 4))
    candidates = [(int(m[:, x].sum()), c, x) for c, m in ms.items() for x in search]
    score, color, best_x = max(candidates)
    m = ms[color]
    # Recenter on the flat peak of the stem; JPEG edges can vary by one pixel.
    peak_xs = [x for x in search if m[:, x].sum() >= max(score - 2, score * .96)]
    x = int(round(float(np.mean(peak_xs)))) if peak_xs else best_x
    ys = np.flatnonzero(m[:, x])
    if not len(ys):
        raise ValueError(f"No colored stem near x={expected_x}")
    top, bottom = int(ys[0]), int(ys[-1])
    offset = max(3, int(round(pitch * .32)))
    sides = {}
    for side, sx in [("open", x - offset), ("close", x + offset)]:
        # Sample the horizontal open/close tick inside this bar's half-slot.
        if sx < 0 or sx + 1 >= w:
            sides[side] = []
            continue
        yy = np.flatnonzero(m[:, max(0, sx):min(w, sx + 2)].any(axis=1))
        yy = yy[(yy >= top - 2) & (yy <= bottom + 2)]
        bands = row_bands(yy)
        sides[side] = bands
    body = None
    open_y = close_y = None
    if len(sides["open"]) == 1 and len(sides["close"]) == 1:
        open_y = sum(sides["open"][0]) / 2
        close_y = sum(sides["close"][0]) / 2
        body = abs(close_y - open_y)
    return dict(
        expected_x=round(expected_x, 2), x=x, color=color,
        high_y=top, low_y=bottom, range_px=bottom-top,
        open_y=open_y, close_y=close_y, body_px=body,
        open_tick_bands=sides["open"], close_tick_bands=sides["close"],
        frame_clipped=top <= 1 or bottom >= h-2,
    )


def measure(case: dict) -> dict:
    path = IMAGE_DIR / f"1D_{case['id']}.jpg"
    original_bytes = path.read_bytes()
    rgb = np.asarray(Image.open(path).convert("RGB"))
    ms = masks(rgb)
    signal = decode_bar(ms, case["x"], case["pitch"])
    prior = [decode_bar(ms, case["x"] - k*case["pitch"], case["pitch"])
             for k in range(case["n"], 0, -1)]
    avg_range = float(np.mean([p["range_px"] for p in prior]))
    body = signal["body_px"]
    ratio = body/avg_range if body is not None else None
    result = dict(
        scenario_id=case["id"], image_file=f"images/{path.name}",
        sha256=hashlib.sha256(original_bytes).hexdigest(),
        image_size_px=[rgb.shape[1], rgb.shape[0]],
        author_pn=case["author_pn"], visual_note=case["note"],
        chosen_signal_x=signal["x"], manually_selected_x=case["x"],
        bar_pitch_px=case["pitch"], n_prior=case["n"], signal_excluded=True,
        body_px=body, average_range_px=round(avg_range, 4),
        average_TR_px=None, ratio_body_to_prior_mean_range=round(ratio, 4) if ratio is not None else None,
        approximate_px_estimate="body / mean(high-low) of preceding complete bars; not true ATR",
        signal_measurement=signal, prior_measurements=prior,
    )
    if body is None or signal["frame_clipped"] or any(p["frame_clipped"] for p in prior):
        raise ValueError(f"Incomplete signal/prior range in scenario {case['id']}")
    if len({p['x'] for p in prior}) != case['n'] or max(p['x'] for p in prior) >= signal['x']:
        raise ValueError("Prior-bar selection overlaps/duplicates signal")
    # Fixed pixel perturbation only, not a statistical confidence interval.
    result["sensitivity_ratio_assuming_plus_minus_4px_body_and_mean_range"] = [
        round(max(0, body-4)/(avg_range+4), 3),
        round((body+4)/max(1, avg_range-4), 3),
    ]
    result["reliability"] = (
        "moderate; visually checked body and 14 complete prior ranges; linear scale assumed"
        if case["n"] == 14 else
        "limited; visually checked body and only 12 prior ranges; linear scale assumed"
    )
    result["limitations"] = [
        "No price axis or OHLC data; linear vertical scale is assumed, not verified.",
        "Mean high-low range proxy omits gaps and is not true range or Wilder/RMA ATR.",
        "JPEG edges, drawing thickness and blue-line overlays introduce pixel error.",
        "The 1.6 threshold cannot be treated as a confirmed PN classification from this proxy.",
    ]
    if case["n"] != 14:
        result["limitations"].append("Only 12 full prior stems are present in this crop; not a prior14 estimate.")
    result["visual_verification"] = {
        "original_image_viewed": True,
        "signal_identified_by": "author arrow and vertical dashed line; x manually selected",
        "body_identified_by": "left opening tick and right closing tick, not total stem height",
        "prior_stems_checked_for_frame_clipping": True,
        "source_image_not_modified": True,
    }
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--diagnostic", action="store_true")
    args = parser.parse_args()
    results = [measure(case) for case in CASES]
    if args.diagnostic:
        for r in results:
            print(json.dumps(r, ensure_ascii=False))
        return
    limitations = [
        "Верифицированная выборка восьми дневных оригиналов; не автоматический разбор всех 409.",
        "Числитель — расстояние между центрами левого open-тика и правого close-тика OHLC-бара.",
        "Знаменатель — среднее high-low 14 предыдущих баров; в 117 доступны только 12.",
        "Сигнальный бар исключён. Значения прошлого закрытия не используются, TR и точный ATR не рассчитываются.",
        "Линейная вертикальная шкала предполагается; отсутствие ценовой шкалы не позволяет её проверить.",
        "Нет преобразования в цену, проценты или PnL. Авторское ПН не равно автоматической метке из оценки.",
    ]
    payload = {
        "method": "approximate_px_estimate: body_px / prior_mean_high_low_range_px",
        "scope": "8 manually visually verified samples from 409 daily originals",
        "reviewed_daily_originals_in_this_estimation_pass": 8,
        "source_images_modified": False,
        "limitations": limitations,
        "reproduce": "botArtjahan/.venv/Scripts/python.exe visual_analysis/estimate_pixel_atr.py",
        "examples": results,
    }
    (HERE / "atr_pixel_examples.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    lines = [
        "# Пиксельная оценка тела относительно прежнего диапазона", "",
        "Проверены восемь дневных оригиналов. Это воспроизводимая выборка, а не оценка всех 409 сценариев.", "",
        "**Результат — прокси ATR: body / mean(high−low), не истинный ATR.** Сигнальный бар исключён из среднего. Все координаты относятся к исходному JPEG, начало в левом верхнем углу.", "",
        "На рисунках используются OHLC-бары. Тело измеряется между центрами горизонтальных отметок открытия слева и закрытия справа; длина всей вертикальной линии включает хвосты.", "",
        "| Сценарий | x сигнала | Предыдущих баров | Тело, px | Средний high−low, px | Отношение |", "|---|---:|---:|---:|---:|---:|",
    ]
    for r in results:
        lines.append(f"| {r['scenario_id']} | {r['chosen_signal_x']} | {r['n_prior']} | {r['body_px']:.1f} | {r['average_range_px']:.2f} | {r['ratio_body_to_prior_mean_range']:.3f} |")
    lines += ["", "## Разделение подписи автора и измерения", ""]
    for r in results:
        s = r["signal_measurement"]
        lo, hi = r["sensitivity_ratio_assuming_plus_minus_4px_body_and_mean_range"]
        lines += [f"- **{r['scenario_id']}**: {r['author_pn']} {r['visual_note']} Проверка тела: open y={s['open_y']}, close y={s['close_y']}; весь high−low сигнала {s['range_px']} px. Условный тест погрешности ±4 px даёт отношение {lo}–{hi}; это не доверительный интервал."]
    lines += ["", "## Метод и ограничения", ""]
    lines += [f"- {v}" for v in limitations]
    lines += [
        "- Скрипт выделяет красные/зелёные пиксели, ищет вертикальную стойку в пределах ±3 px от вручную выбранной сетки баров и измеряет её крайние y. Синие уровни и чёрные подписи не включаются в маску.",
        "- Положение сигнала, порядок предыдущих баров и отсутствие обрезанных high/low проверены по оригиналам. Для 200 исправлен шаг сетки по реально видимым стойкам, чтобы не принять горизонтальный тик за целый бар.",
        "- У сценария 117 только 12 предыдущих баров; левый open-тик первого частично вне кадра. Его high−low виден, поэтому диапазон включён; закрытия не требуются для выбранного прокси.",
        "- Из-за пропуска гэпов средний диапазон может быть меньше среднего TR; отношение к диапазону может завышать отношение к ATR. Сглаженный Wilder/RMA ATR также не воспроизводится простым средним.",
        "- В 109 оценка сигнального бара заметно выше 1.6; в остальных выбранных сигнальных барах ниже. Это сравнение с прокси, а не подтверждение или опровержение ПН по правилу body ≥ 1.6 × priorATR.",
        "- В 117 автор называет подход ПН, но сигнальное тело даёт около 1.23 среднего диапазона 12 прошлых баров. Эту разницу следует сохранить для сверки исходных данных; не подгонять измерение под подпись.",
        "- В 132, 182 и 200 ПН в тексте относится к предыдущему бару, поэтому маленькое отношение сигнального бара не противоречит подписи автоматически.",
        "- JSON содержит все координаты, диапазоны предыдущих баров, hash SHA-256 исходных JPEG и параметры сетки для воспроизведения.",
        "", "Запуск из любой папки: `.venv/Scripts/python.exe <полный путь>/visual_analysis/estimate_pixel_atr.py`. Файлы оригиналов открываются только для чтения; записываются только JSON и этот Markdown.", "",
    ]
    (HERE / "atr_pixel_examples.md").write_text("\n".join(lines), encoding="utf-8")
    for r in results:
        print(f"{r['scenario_id']}: x={r['chosen_signal_x']} n={r['n_prior']} body={r['body_px']} mean_range={r['average_range_px']} ratio={r['ratio_body_to_prior_mean_range']}")


if __name__ == "__main__":
    main()
