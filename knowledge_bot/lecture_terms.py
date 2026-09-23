from __future__ import annotations

import re
from collections import Counter


TERM_ALIASES: dict[str, list[str]] = {
    "level": ["уровень", "уровня", "уровни", "уровней", "уровню", "level"],
    "energy": ["энергия", "энергии", "накопление", "накопления", "боковик"],
    "direction": ["направление", "направления", "сценарий", "план сделки"],
    "entry": ["точка входа", "твх", "вход", "войти", "заходить", "entry"],
    "exit": ["выход", "тейк", "take profit", "takeprofit", "tp", "тейк профит"],
    "stop_loss": ["стоп", "стоплосс", "стоплос", "stop loss", "sl"],
    "risk_reward": ["3 к 1", "4 к 1", "5 к 1", "соотношение", "risk reward", "rr"],
    "atr": ["atr", "атр", "10% atr", "10 процентов atr", "10 процентов отр"],
    "breakout": ["пробой", "пробоя", "пробое", "пробили", "пробивает", "breakout"],
    "false_breakout": ["ложный пробой", "лп", "lp", "false breakout"],
    "rebound": ["отбой", "отбоя", "разворот", "реверс"],
    "retest": ["ретест", "ритест", "ретеста", "retest"],
    "near_retest": ["ближний ретест", "ближний ритест"],
    "far_retest": ["дальний ретест", "дальний ритест"],
    "bsu": ["бсу", "bsu"],
    "bpu": ["бпу", "бпу1", "бпу2", "bpu", "bpu1", "bpu2"],
    "timeframe": ["таймфрейм", "тайм фрейм", "тф", "дневка", "дневной", "часовик", "5 минут", "м5", "м15"],
    "daily_context": ["дневка", "дневной график", "дневной таймфрейм", "1d", "1д"],
    "m5_execution": ["5 минут", "пять минут", "пятиминутка", "5-минутный", "м5", "m5"],
    "trend": ["тренд", "тренда", "глобальный тренд", "локальный тренд"],
    "long": ["лонг", "лонговый", "long"],
    "short": ["шорт", "шортовый", "short"],
    "limit_player": ["лимитный игрок", "лимитного игрока", "лимитник"],
    "dynamic_player": ["динамичный игрок", "динамический игрок"],
    "compression": ["поджатие", "поджимается", "сжатие", "пружина", "затухание волатильности"],
    "volatility": ["волатильность", "волатильности", "паранормальный бар", "паранормальные бары"],
    "close": ["закрытие", "закрылся", "закрылась", "закрылись", "close"],
    "tail": ["хвост", "хвостатый бар", "хвостатые бары", "без хвоста"],
    "gap": ["гэп", "gap"],
    "v_formation": ["v формация", "v-формация", "v форма", "ви формация"],
    "u_formation": ["u формация", "u-формация", "u форма"],
    "homework": ["домашка", "домашнее задание", "сценарии"],
    "trade_review": ["обзор сделок", "разбор сделок", "сделка", "сделки"],
}


NORMALIZATION_REPLACEMENTS: tuple[tuple[str, str], ...] = (
    ("шарт", "шорт"),
    ("шартовый", "шортовый"),
    ("шортовый", "шортовый"),
    ("ланг", "лонг"),
    ("лунг", "лонг"),
    ("ломг", "лонг"),
    ("ритест", "ретест"),
    ("бритвест", "ретест"),
    ("тайфрейм", "таймфрейм"),
    ("тайм фрэйм", "таймфрейм"),
    ("тайм фрейм", "таймфрейм"),
    ("стоплоз", "стоплосс"),
    ("стоп лос", "стоплосс"),
    ("стоплос", "стоплосс"),
    ("тейк профит", "take profit"),
    ("бепу", "бпу"),
    ("бпу 1", "бпу1"),
    ("бпу 2", "бпу2"),
)


STOPWORDS = {
    "а", "без", "бы", "был", "была", "были", "было", "в", "вам", "вас", "вот",
    "все", "где", "да", "для", "до", "его", "если", "есть", "же", "за", "здесь",
    "и", "из", "или", "именно", "как", "к", "когда", "которые", "который", "мы",
    "на", "над", "нам", "не", "него", "нет", "но", "ну", "о", "об", "он", "она",
    "они", "от", "очень", "по", "под", "пока", "потом", "почему", "просто", "с",
    "сам", "сейчас", "сюда", "так", "там", "то", "тоже", "тут", "у", "уже", "что",
    "чтобы", "это", "этого", "этой", "этот", "эту", "я",
}


TOKEN_RE = re.compile(r"[a-zа-яё0-9%]+", re.IGNORECASE)


def normalize_text(text: str) -> str:
    value = text.lower().replace("ё", "е")
    for old, new in NORMALIZATION_REPLACEMENTS:
        value = value.replace(old, new)
    value = re.sub(r"\s+", " ", value)
    return value.strip()


def tokenize(text: str) -> list[str]:
    normalized = normalize_text(text)
    tokens = TOKEN_RE.findall(normalized)
    return [token for token in tokens if len(token) > 1 and token not in STOPWORDS]


def detect_terms(text: str) -> list[str]:
    normalized = normalize_text(text)
    found: list[str] = []
    for term, aliases in TERM_ALIASES.items():
        for alias in aliases:
            alias_normalized = normalize_text(alias)
            if " " in alias_normalized:
                if alias_normalized in normalized:
                    found.append(term)
                    break
            elif re.search(rf"(?<![a-zа-я0-9]){re.escape(alias_normalized)}(?![a-zа-я0-9])", normalized):
                found.append(term)
                break
    return found


def expand_query_terms(query: str) -> set[str]:
    normalized = normalize_text(query)
    expanded = set(tokenize(normalized))
    for aliases in TERM_ALIASES.values():
        alias_hit = False
        for alias in aliases:
            alias_normalized = normalize_text(alias)
            if alias_normalized and alias_normalized in normalized:
                alias_hit = True
                break
        if alias_hit:
            for alias in aliases:
                expanded.update(tokenize(alias))
    return expanded


def top_terms(text: str, limit: int = 12) -> list[tuple[str, int]]:
    counts = Counter(tokenize(text))
    return counts.most_common(limit)