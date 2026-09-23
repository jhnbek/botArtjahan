from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path


@dataclass(frozen=True)
class ChecklistQuestion:
    text: str
    source_card: str
    missing_risk: str


@dataclass(frozen=True)
class ChecklistSection:
    key: str
    title: str
    source_cards: tuple[str, ...]
    questions: tuple[ChecklistQuestion, ...]
    contradictions: tuple[str, ...]


def configure_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")


def default_root() -> Path:
    return Path(__file__).resolve().parents[1]


MANDATORY_QUESTIONS: tuple[ChecklistQuestion, ...] = (
    ChecklistQuestion(
        "Сценарий написан по закрытому бару, до касания/пробоя уровня?",
        "scenario_homework_process.md",
        "Если сценарий написан после движения, вывод легко подогнать под результат.",
    ),
    ChecklistQuestion(
        "Есть конкретный уровень и объяснение, почему он рабочий/сильный?",
        "level_selection_strength.md",
        "Без уровня нельзя определить локальный тренд, стоп и точку отмены.",
    ),
    ChecklistQuestion(
        "Направление сделки отделено от точки входа?",
        "core_trade_framework.md",
        "Красивая ТВХ без дневного сценария не имеет смысла.",
    ),
    ChecklistQuestion(
        "Определены глобальный тренд и локальный тренд относительно ближайшего уровня?",
        "trend_context.md",
        "Пробой против тренда и вход против локальной зоны требуют разных правил.",
    ),
    ChecklistQuestion(
        "Есть минимум несколько факторов за сценарий и явно записаны факторы против?",
        "scenario_homework_process.md",
        "Один фактор не делает сделку; противоречия должны быть видны до входа.",
    ),
    ChecklistQuestion(
        "Есть понятная ТВХ на H1/M15/M5, которая не противоречит дневке?",
        "timeframe_workflow.md",
        "Вход на младшем ТФ против дневного сценария запрещает сделку, а не меняет направление.",
    ),
    ChecklistQuestion(
        "До входа понятно, куда поставить стоп и почему именно туда?",
        "risk_stop_take.md",
        "Если стоп не показан структурой рынка, сделки нет.",
    ),
    ChecklistQuestion(
        "До ближайшей цели есть минимум 3R, лучше 4R+?",
        "risk_stop_take.md",
        "Даже правильный сценарий без запаса хода не имеет нормального R/R.",
    ),
    ChecklistQuestion(
        "Записана отмена сценария: что должно случиться, чтобы сделку не брать?",
        "core_trade_framework.md",
        "Без отмены сценарий превращается в попытку угадать рынок.",
    ),
)


SECTIONS: dict[str, ChecklistSection] = {
    "scenario": ChecklistSection(
        key="scenario",
        title="Сценарий и домашка",
        source_cards=("scenario_homework_process.md", "core_trade_framework.md", "timeframe_workflow.md"),
        questions=(
            ChecklistQuestion(
                "Указаны тикер, дата, уровень, направление, модель, цена/зона входа?",
                "scenario_homework_process.md",
                "Неполный сценарий нельзя проверять на дистанции.",
            ),
            ChecklistQuestion(
                "Сохранён скриншот до реализации сценария?",
                "scenario_homework_process.md",
                "Без скриншота легко забыть, что реально было видно до движения.",
            ),
            ChecklistQuestion(
                "После сценария запрещён вход в противоположную сторону?",
                "scenario_homework_process.md",
                "Так защищаемся от красивых, но чужих ТВХ на младшем ТФ.",
            ),
            ChecklistQuestion(
                "Результат будет записан отдельно как результат сценария и как результат сделки?",
                "trade_reviews_examples.md",
                "Правильный сценарий может дать стоп, а плохой сценарий может случайно дать прибыль.",
            ),
        ),
        contradictions=(
            "Сценарий написан уже после движения.",
            "Последний незакрытый бар влияет на вывод.",
            "Есть только ощущение направления, но нет списка факторов.",
        ),
    ),
    "trend": ChecklistSection(
        key="trend",
        title="Глобальный и локальный тренд",
        source_cards=("trend_context.md",),
        questions=(
            ChecklistQuestion(
                "Глобальный тренд описан через структуру хаи/лои, а не через наклонную линию для входа?",
                "trend_context.md",
                "Наклонная линия может помогать визуально, но не является торговым уровнем.",
            ),
            ChecklistQuestion(
                "Цена находится в правильной локальной зоне для планируемой сделки?",
                "trend_context.md",
                "По лекциям против локального тренда не торгуем.",
            ),
            ChecklistQuestion(
                "Если пробой против глобального тренда, есть накопление/слом структуры?",
                "trend_context.md",
                "Пробой против тренда без базы обычно слабый.",
            ),
            ChecklistQuestion(
                "Если идея контртрендовая, это ЛП/реакция от уровня, а не голый пробой?",
                "trend_context.md",
                "ЛП лучше переносит контртренд, чем пробой.",
            ),
        ),
        contradictions=(
            "Пробой против глобального тренда одним баром.",
            "Шорт пока цена выше ближайшего уровня, или лонг пока цена ниже ближайшего уровня.",
            "Ближайший уровень подменён более удобным дальним уровнем.",
        ),
    ),
    "breakout": ChecklistSection(
        key="breakout",
        title="Пробой уровня",
        source_cards=("breakout_preconditions.md", "near_far_retest.md", "trend_context.md"),
        questions=(
            ChecklistQuestion(
                "Есть ближний ретест: до 10 баров/дней, идеально 3-5?",
                "near_far_retest.md",
                "Без ближнего ретеста пробой теряет один из сильнейших факторов.",
            ),
            ChecklistQuestion(
                "Цена закрылась вблизи уровня или под хай/над лоу в сторону пробоя?",
                "breakout_preconditions.md",
                "Далёкое закрытие требует пройти слишком много до самого пробоя.",
            ),
            ChecklistQuestion(
                "Есть поджатие/накопление/затухание волатильности перед уровнем?",
                "breakout_preconditions.md",
                "Пробой без подготовки чаще превращается в ЛП или шум.",
            ),
            ChecklistQuestion(
                "Нет реакции на предыдущий ЛП или есть локальный ЛП в сторону пробоя?",
                "breakout_preconditions.md",
                "Это усиливает идею, что сторону ЛП не поддержали.",
            ),
            ChecklistQuestion(
                "Подход не был резким паранормальным баром с уже пройденным ATR?",
                "false_breakout_reversal.md",
                "Резкий подход к уровню обычно ведёт к ЛП/развороту.",
            ),
        ),
        contradictions=(
            "Дальний ретест первым касанием.",
            "Пройдено 100% ATR до уровня.",
            "Большие бары прямо в уровень без отстоя.",
            "Уровень распилен закреплениями с обеих сторон.",
        ),
    ),
    "false_breakout": ChecklistSection(
        key="false_breakout",
        title="Ложный пробой / разворот",
        source_cards=("false_breakout_reversal.md", "near_far_retest.md", "trend_context.md"),
        questions=(
            ChecklistQuestion(
                "Подход к уровню был резким, большими/паранормальными барами?",
                "false_breakout_reversal.md",
                "Без чрезмерного подхода ЛП слабее.",
            ),
            ChecklistQuestion(
                "Пройдено много ATR до уровня или движение было безоткатным?",
                "false_breakout_reversal.md",
                "Большая пройденная дистанция повышает шанс реакции.",
            ),
            ChecklistQuestion(
                "Это дальний ретест: 30+ баров/дней до возврата к уровню?",
                "near_far_retest.md",
                "Первое касание после дальнего отсутствия чаще даёт ЛП.",
            ),
            ChecklistQuestion(
                "После прокола цена вернулась за уровень и показала место для стопа?",
                "false_breakout_reversal.md",
                "Вход без возврата и хвоста ЛП не показывает, где модель ломается.",
            ),
            ChecklistQuestion(
                "На младшем ТФ нет закрепления в сторону пробоя после ЛП?",
                "fixation_return_entry.md",
                "Закрепление отменяет идею ЛП и переводит сценарий в пробой.",
            ),
        ),
        contradictions=(
            "Поджатие маленькими барами прямо к уровню.",
            "Ближний ретест после дальнего отстоя у уровня.",
            "Цена закрепилась за уровнем вместо возврата.",
            "U-формация или накопление после дальнего ретеста.",
        ),
    ),
    "retest": ChecklistSection(
        key="retest",
        title="Ближний / дальний ретест",
        source_cards=("near_far_retest.md",),
        questions=(
            ChecklistQuestion(
                "Посчитано число баров между прошлым контактом уровня и текущим возвратом?",
                "near_far_retest.md",
                "Без числа баров один и тот же ретест можно прочитать в противоположную сторону.",
            ),
            ChecklistQuestion(
                "Если ретест ближний, сценарий трактуется как пробойный?",
                "near_far_retest.md",
                "Ближний ретест обычно работает в сторону пробоя.",
            ),
            ChecklistQuestion(
                "Если ретест дальний, первый подход трактуется как риск ЛП?",
                "near_far_retest.md",
                "Дальний ретест первым касанием часто даёт ЛП/разворот.",
            ),
            ChecklistQuestion(
                "Если дальний ретест превратился в ближний после отстоя, знак пересмотрен к пробою?",
                "near_far_retest.md",
                "Дальний -> ближний у уровня является важным исключением.",
            ),
        ),
        contradictions=(
            "Серая зона 11-29 баров без других сильных факторов.",
            "Уровень распилен между первым касанием и возвратом.",
            "Ретест используется как вход, а не как контекст.",
        ),
    ),
    "fixation": ChecklistSection(
        key="fixation",
        title="Закрепление и возврат к уровню",
        source_cards=("fixation_return_entry.md", "tbx_entry_models.md"),
        questions=(
            ChecklistQuestion(
                "Есть бар целиком за уровнем после пробоя?",
                "fixation_return_entry.md",
                "Хвост, цепляющий уровень, не считается полноценным закреплением.",
            ),
            ChecklistQuestion(
                "Была попытка возврата к/за уровень?",
                "fixation_return_entry.md",
                "Без попытки возврата не показано, где защитили уровень.",
            ),
            ChecklistQuestion(
                "После попытки возврата снова появился бар целиком за уровнем?",
                "fixation_return_entry.md",
                "Именно второй бар подтверждает, что возврат не пустили.",
            ),
            ChecklistQuestion(
                "Волатильность перед входом маленькая?",
                "fixation_return_entry.md",
                "После больших баров вход без затухания опаснее.",
            ),
            ChecklistQuestion(
                "Стоп за попытку возврата укладывается в ATR-правило?",
                "risk_stop_take.md",
                "Слишком глубокий возврат ломает R/R.",
            ),
        ),
        contradictions=(
            "Нет бара целиком за уровнем.",
            "Возврат глубокий и закрывается обратно за уровень.",
            "Уровень пилится несколькими возвратами без подтверждения.",
        ),
    ),
    "bsu_bpu": ChecklistSection(
        key="bsu_bpu",
        title="БСУ/БПУ: вход от лимитного игрока",
        source_cards=("bsu_bpu_entry.md",),
        questions=(
            ChecklistQuestion(
                "Определён БСУ: бар, по которому проведён уровень?",
                "bsu_bpu_entry.md",
                "Без БСУ непонятно, какой уровень подтверждают БПУ.",
            ),
            ChecklistQuestion(
                "БПУ1 и БПУ2 идут подряд?",
                "bsu_bpu_entry.md",
                "Если между ними пробой уровня, старый БПУ1 обнуляется.",
            ),
            ChecklistQuestion(
                "БПУ1/БПУ2 находятся в одной плоскости относительно уровня?",
                "bsu_bpu_entry.md",
                "Бары по разные стороны уровня не дают модель входа от лимита.",
            ),
            ChecklistQuestion(
                "Дневной сценарий совпадает с направлением входа от лимита?",
                "bsu_bpu_entry.md",
                "Лимит против дневки не торгуется только потому, что он красивый.",
            ),
            ChecklistQuestion(
                "Лимитный ордер выставляется с люфтом и коротким стопом?",
                "bsu_bpu_entry.md",
                "Главная ценность модели — короткий понятный стоп.",
            ),
        ),
        contradictions=(
            "После БПУ1 бар пробил уровень.",
            "Цена ушла от уровня на 2 средних стопа и вернулась позже.",
            "Есть поджатие против входа, которое требует выравнивания.",
        ),
    ),
    "tbx": ChecklistSection(
        key="tbx",
        title="ТВХ/TBX: выбор модели входа",
        source_cards=("tbx_entry_models.md", "risk_stop_take.md"),
        questions=(
            ChecklistQuestion(
                "Выбрана одна конкретная ТВХ: первичный импульс, закрепление, лимит или ЛП?",
                "tbx_entry_models.md",
                "Торговать все ТВХ подряд значит потерять алгоритм.",
            ),
            ChecklistQuestion(
                "Почему эта ТВХ подходит именно к текущей дневной картинке?",
                "tbx_entry_models.md",
                "Выбор входа зависит от того, что было до пробоя.",
            ),
            ChecklistQuestion(
                "Если вход первичным импульсом, дневка и M5 достаточно сильные?",
                "tbx_entry_models.md",
                "Первичный импульс требует сильной дневки и понятного стопа заранее.",
            ),
            ChecklistQuestion(
                "Если вход на возврат, после пробоя пройдено не более 30% ATR?",
                "tbx_entry_models.md",
                "Дальний возврат после большого прохода уже не стандартная ТВХ.",
            ),
            ChecklistQuestion(
                "Стоп не висит в воздухе?",
                "tbx_entry_models.md",
                "Во всех ТВХ главный вопрос: куда поставить стоп.",
            ),
        ),
        contradictions=(
            "Дневка не в пользу входа.",
            "Большая волатильность прямо перед входом.",
            "Стоп большой, но вход всё равно берётся из страха пропустить движение.",
        ),
    ),
    "v_u": ChecklistSection(
        key="v_u",
        title="V-формация и U-формация",
        source_cards=("v_u_formations.md",),
        questions=(
            ChecklistQuestion(
                "Формация действительно резкая V, а не поступательный тренд?",
                "v_u_formations.md",
                "Обычный тренд нельзя читать как V-формацию.",
            ),
            ChecklistQuestion(
                "Если V без накопления, первый возврат к исходной точке трактуется как риск ЛП?",
                "v_u_formations.md",
                "Классическая V ведёт к ЛП/развороту при первом взаимодействии.",
            ),
            ChecklistQuestion(
                "Если V с накоплением у уровня, знак пересмотрен к пробою?",
                "v_u_formations.md",
                "Накопление меняет знак V-формации.",
            ),
            ChecklistQuestion(
                "Если U-формация, есть ровное дно/округление и возврат улыбкой?",
                "v_u_formations.md",
                "Без округления это может быть обычный ретест.",
            ),
            ChecklistQuestion(
                "U-формация не используется как повод входить первым касанием?",
                "v_u_formations.md",
                "В лекциях U чаще фильтр против ЛП и усилитель продолжения после подтверждения.",
            ),
        ),
        contradictions=(
            "V с накоплением читается как обычная V к ЛП.",
            "U после дальнего ретеста игнорируется при плане шорта в ЛП.",
            "Формация используется без уровня или точки излома.",
        ),
    ),
    "tail": ChecklistSection(
        key="tail",
        title="Хвостатые бары и лимит с двух сторон",
        source_cards=("tail_bars_two_sided_limit.md", "level_selection_strength.md"),
        questions=(
            ChecklistQuestion(
                "Понятно, откуда цена пришла в хвостатое накопление?",
                "tail_bars_two_sided_limit.md",
                "Без направления прихода хвосты в обе стороны не дают знака.",
            ),
            ChecklistQuestion(
                "Есть несколько хвостатых баров с маленькими телами, а не один случайный бар?",
                "tail_bars_two_sided_limit.md",
                "Один хвостатый бар без контекста не является моделью.",
            ),
            ChecklistQuestion(
                "Хвостатый/паранормальный бар подтверждён касаниями или ЛП, если по нему берётся уровень?",
                "level_selection_strength.md",
                "Хвост может быть сильным уровнем только с контекстом/подтверждением.",
            ),
            ChecklistQuestion(
                "Вход не планируется в середине заражённой зоны?",
                "tail_bars_two_sided_limit.md",
                "Середина зоны мелких баров и хвостов часто пилит обе стороны.",
            ),
        ),
        contradictions=(
            "Хвосты далеко от сильных уровней.",
            "Уровень проведён через середину накопления.",
            "Закрытие без хвоста используется как единственный фактор при слабом уровне.",
        ),
    ),
    "risk": ChecklistSection(
        key="risk",
        title="Стоп, ATR, тейк и сопровождение",
        source_cards=("risk_stop_take.md",),
        questions=(
            ChecklistQuestion(
                "Расчётный стоп примерно 10% ATR известен?",
                "risk_stop_take.md",
                "Без ATR нельзя понять, большой или нормальный технический стоп.",
            ),
            ChecklistQuestion(
                "Технический стоп за структурой не уходит в 2-3x расчётного ориентира?",
                "risk_stop_take.md",
                "Слишком большой стоп ломает математику сделки.",
            ),
            ChecklistQuestion(
                "Тейк минимум 3R, рабочий ориентир 4R+, и он достижим до ближайшего уровня?",
                "risk_stop_take.md",
                "Если 3R некуда взять, сделка статистически слабая.",
            ),
            ChecklistQuestion(
                "Перенос стопа в плюс планируется только после новой базы/ретеста?",
                "risk_stop_take.md",
                "Ранний безубыток может выбить правильную сделку шумом.",
            ),
        ),
        contradictions=(
            "Стоп придуман после входа.",
            "Стоп слишком далеко за уровнем 'на всякий случай'.",
            "Тейк выбран психологически, а не по R или уровню.",
        ),
    ),
    "review": ChecklistSection(
        key="review",
        title="Разбор сделки после исполнения",
        source_cards=("trade_reviews_examples.md",),
        questions=(
            ChecklistQuestion(
                "Разбор начинается с дневки, затем H1, затем M5/M15?",
                "trade_reviews_examples.md",
                "Если начать с входа, можно пропустить ошибку сценария.",
            ),
            ChecklistQuestion(
                "Записано, кто должен был терять при реализации сценария?",
                "trade_reviews_examples.md",
                "Без топлива движения непонятно, почему цена должна идти.",
            ),
            ChecklistQuestion(
                "Отдельно отмечено: ошибка сценария или ошибка входа?",
                "trade_reviews_examples.md",
                "Правильная дневка и плохая ТВХ требуют разных выводов.",
            ),
            ChecklistQuestion(
                "Результат записан в R, а не только в деньгах?",
                "trade_reviews_examples.md",
                "Без R нельзя сравнивать сделки между собой.",
            ),
        ),
        contradictions=(
            "Прибыльная сделка записана как правильная без проверки сценария.",
            "Стоп объясняется задним числом.",
            "Не сохранён скриншот входа/выхода.",
        ),
    ),
}


ALIASES: dict[str, str] = {
    "lp": "false_breakout",
    "false": "false_breakout",
    "false-breakout": "false_breakout",
    "ложный": "false_breakout",
    "пробой": "breakout",
    "break": "breakout",
    "retest": "retest",
    "ретест": "retest",
    "закрепление": "fixation",
    "fix": "fixation",
    "bsu": "bsu_bpu",
    "bpu": "bsu_bpu",
    "бсу": "bsu_bpu",
    "бпу": "bsu_bpu",
    "tvx": "tbx",
    "tbx": "tbx",
    "твх": "tbx",
    "v": "v_u",
    "u": "v_u",
    "vu": "v_u",
    "tail": "tail",
    "хвост": "tail",
    "trend": "trend",
    "тренд": "trend",
    "risk": "risk",
    "риск": "risk",
    "homework": "scenario",
    "scenario": "scenario",
    "сценарий": "scenario",
    "review": "review",
    "разбор": "review",
}


FULL_MODEL_ORDER: tuple[str, ...] = (
    "scenario",
    "trend",
    "breakout",
    "false_breakout",
    "retest",
    "fixation",
    "bsu_bpu",
    "tbx",
    "v_u",
    "tail",
    "risk",
    "review",
)


KEYWORD_TO_SECTION: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"\b(ложн|лп|lp|false)\b", re.IGNORECASE), "false_breakout"),
    (re.compile(r"\b(пробой|пробива|breakout)\b", re.IGNORECASE), "breakout"),
    (re.compile(r"\b(ретест|ритест|retest)\b", re.IGNORECASE), "retest"),
    (re.compile(r"\b(бсу|бпу|bsu|bpu)\b", re.IGNORECASE), "bsu_bpu"),
    (re.compile(r"\b(закреп|возврат|bar целиком|бар целиком)\b", re.IGNORECASE), "fixation"),
    (re.compile(r"\b(твх|tbx|точк[аи] входа)\b", re.IGNORECASE), "tbx"),
    (re.compile(r"\b(v[- ]?формац|u[- ]?формац|ви формац|юформац)\b", re.IGNORECASE), "v_u"),
    (re.compile(r"\b(хвост|хвостат|без хвоста)\b", re.IGNORECASE), "tail"),
    (re.compile(r"\b(тренд|gt|lt|локальн|глобальн)\b", re.IGNORECASE), "trend"),
)


def normalize_model_name(model: str) -> str:
    normalized = model.strip().lower().replace("-", "_")
    if normalized == "full":
        return "full"
    return ALIASES.get(normalized, normalized)


def resolve_sections(models: list[str], scenario_text: str) -> list[str]:
    resolved: list[str] = []
    for model in models:
        name = normalize_model_name(model)
        if name == "full":
            for item in FULL_MODEL_ORDER:
                if item not in resolved:
                    resolved.append(item)
            continue
        if name not in SECTIONS:
            valid = ", ".join(sorted(SECTIONS))
            raise SystemExit(f"Unknown model '{model}'. Use --list-models. Valid keys: {valid}")
        if name not in resolved:
            resolved.append(name)

    for pattern, section_key in KEYWORD_TO_SECTION:
        if scenario_text and pattern.search(scenario_text) and section_key not in resolved:
            resolved.append(section_key)

    return resolved or ["scenario", "trend", "risk"]


def read_rulebook_statuses(root: Path) -> dict[str, str]:
    rulebook_dir = root / "_knowledge_base" / "rulebook"
    statuses: dict[str, str] = {}
    if not rulebook_dir.exists():
        return statuses
    for path in sorted(rulebook_dir.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        status = "unknown"
        for line in text.splitlines()[:8]:
            if line.startswith("Status:"):
                status = line
                break
        statuses[path.name] = status
    return statuses


def collect_source_cards(section_keys: list[str]) -> list[str]:
    cards: list[str] = []
    for question in MANDATORY_QUESTIONS:
        if question.source_card not in cards:
            cards.append(question.source_card)
    for section_key in section_keys:
        for card in SECTIONS[section_key].source_cards:
            if card not in cards:
                cards.append(card)
        for question in SECTIONS[section_key].questions:
            if question.source_card not in cards:
                cards.append(question.source_card)
    return cards


def format_question(question: ChecklistQuestion) -> list[str]:
    return [
        f"- [ ] {question.text}",
        f"  - Источник: `{question.source_card}`",
        f"  - Если нет: {question.missing_risk}",
    ]


def build_document(
    root: Path,
    section_keys: list[str],
    ticker: str | None,
    direction: str | None,
    level: str | None,
    entry: str | None,
    scenario_text: str,
) -> str:
    generated_at = datetime.now(timezone.utc).isoformat(timespec="seconds")
    statuses = read_rulebook_statuses(root)
    source_cards = collect_source_cards(section_keys)

    lines: list[str] = [
        "# Чеклист Сценария",
        "",
        f"Сформировано: `{generated_at}`",
        "",
        "Учебный чеклист по лекциям. Он не даёт персональную торговую рекомендацию и не заменяет самостоятельный сценарий.",
        "",
        "## Данные Сценария",
        "",
        f"- Ticker: {ticker or 'TODO'}",
        f"- Направление: {direction or 'TODO'}",
        f"- Уровень: {level or 'TODO'}",
        f"- План входа: {entry or 'TODO'}",
        f"- Секции чеклиста: {', '.join(section_keys)}",
        "",
    ]

    if scenario_text:
        lines.extend(["## Текст Сценария", "", scenario_text.strip(), ""])

    lines.extend([
        "## Жёсткие Стоп-Факторы",
        "",
        "- [ ] Нет сильного/понятного уровня.",
        "- [ ] Нет заранее написанного направления.",
        "- [ ] Вход против локального тренда.",
        "- [ ] Нет логичного стопа до входа.",
        "- [ ] До цели меньше 3R.",
        "- [ ] Сценарий написан после движения или по незакрытому бару.",
        "",
        "## Обязательный Чеклист",
        "",
    ])
    for question in MANDATORY_QUESTIONS:
        lines.extend(format_question(question))

    for section_key in section_keys:
        section = SECTIONS[section_key]
        lines.extend(["", f"## {section.title}", ""])
        for question in section.questions:
            lines.extend(format_question(question))
        if section.contradictions:
            lines.extend(["", "Противоречия, которые нужно явно отклонить:"])
            for contradiction in section.contradictions:
                lines.append(f"- [ ] {contradiction}")

    lines.extend(["", "## Карточки-Источники", ""])
    for card in source_cards:
        status = statuses.get(card, "missing")
        lines.append(f"- `_knowledge_base/rulebook/{card}` — {status}")

    distilled_count = sum(1 for status in statuses.values() if "distilled v1" in status)
    if statuses:
        lines.extend([
            "",
            "## Статус Rulebook",
            "",
            f"- Найдено distilled cards: {distilled_count}/{len(statuses)}",
        ])

    lines.extend([
        "",
        "## Разбор После Результата",
        "",
        "- [ ] Что было правильно в дневном сценарии?",
        "- [ ] Что было ошибкой: сценарий, уровень, ТВХ, стоп, тейк или дисциплина?",
        "- [ ] Кто должен был терять в этой сделке, и действительно ли они начали выходить?",
        "- [ ] Результат записан в R.",
        "- [ ] Скриншот до/после сохранён.",
        "",
    ])
    return "\n".join(lines)


def write_or_print(document: str, out_path: Path | None) -> None:
    if out_path:
        out_path.parent.mkdir(parents=True, exist_ok=True)
        out_path.write_text(document, encoding="utf-8")
        print(f"Wrote checklist: {out_path}")
        return
    print(document)


def main() -> None:
    configure_stdio()
    parser = argparse.ArgumentParser(description="Сформировать чеклист сценария по rulebook лекций.")
    parser.add_argument("--root", type=Path, default=default_root())
    parser.add_argument("--models", nargs="*", default=["scenario", "trend", "risk"], help="Checklist sections, e.g. breakout false_breakout bsu_bpu. Use full for all.")
    parser.add_argument("--ticker", default=None)
    parser.add_argument("--direction", choices=["long", "short", "лонг", "шорт"], default=None)
    parser.add_argument("--level", default=None)
    parser.add_argument("--entry", default=None)
    parser.add_argument("--scenario", nargs="*", default=[], help="Текст сценария. Ключевые слова автоматически добавят релевантные секции.")
    parser.add_argument("--scenario-file", type=Path, default=None, help="Прочитать текст сценария из markdown/text файла.")
    parser.add_argument("--out", type=Path, default=None)
    parser.add_argument("--list-models", action="store_true")
    args = parser.parse_args()

    if args.list_models:
        print("Available models:")
        for key, section in SECTIONS.items():
            print(f"- {key}: {section.title}")
        print("- full: all sections")
        return

    scenario_text = " ".join(args.scenario).strip()
    if args.scenario_file:
        scenario_text = args.scenario_file.read_text(encoding="utf-8").strip()

    root = args.root.resolve()
    section_keys = resolve_sections(args.models, scenario_text)
    document = build_document(
        root=root,
        section_keys=section_keys,
        ticker=args.ticker,
        direction=args.direction,
        level=args.level,
        entry=args.entry,
        scenario_text=scenario_text,
    )
    write_or_print(document, args.out)


if __name__ == "__main__":
    main()
