# Knowledge Bot

`knowledge_bot/` содержит код локального конвейера:

1. импорт видео и транскрибация;
2. извлечение кадров и OCR;
3. построение файловой базы знаний;
4. keyword/RAG-поиск;
5. анализ графиков и торговых сценариев;
6. read-only проверки бэктест-контрактов;
7. web и desktop интерфейсы.

Исходный корпус, изображения, построенные индексы и экспериментальные
результаты являются локальными данными и в репозиторий не входят.

## Основные точки входа

| Файл | Назначение |
|---|---|
| `run_new_lecture_pipeline.py` | Полный импорт нового видеокорпуса |
| `build_knowledge_base.py` | Построение RAG-чанков из подготовленных транскриптов |
| `search_lectures.py` | Локальный keyword-поиск |
| `chat_lectures.py` | Prompt-only, OpenAI-compatible или Ollama чат |
| `web_app.py` | Локальный web UI |
| `desktop_app.py` | Desktop UI на PySide6 |
| `chart_vision.py` | Опциональный vision-анализ графиков |
| `shadow_breakout_tracker.py` | Shadow-наблюдение без ордеров |
| `backtest_harness/cli.py` | Синтетические fixture-проверки и safety status |

Остальные `build_*`, `validate_*` и analysis-скрипты решают отдельные этапы
подготовки знаний, калибровки и контроля. Перед запуском проверяйте `--help` и
явно задавайте входные и выходные каталоги.

## Локальная структура данных

Типичный рабочий каталог:

```text
_new_lecture_corpus/   # импортированные транскрипты и кадры
_knowledge_base/       # RAG-чанки, индексы и производные знания
_historical_data/      # локальные рыночные данные и манифесты
_scenario_archive/     # локальные сценарии и изображения
_exports/              # отчёты и результаты запусков
_shadow_forward/       # runtime ledger shadow-трекера
```

Все эти каталоги игнорируются Git. Не меняйте это правило для удобства:
транскрипты, изображения, логи, базы и ledger-файлы должны храниться отдельно
от исходного кода.

## Импорт нового корпуса

Нужны Python 3.12, зависимости из корневого `requirements.txt` и `ffmpeg` в
`PATH`.

```powershell
$sourceDir = "D:\path\to\your\videos"

.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\run_new_lecture_pipeline.py `
  --source-dir $sourceDir `
  --pipeline-root .\_new_lecture_corpus `
  --model base `
  --language ru
```

По умолчанию runner сохраняет подготовленный корпус в `_new_lecture_corpus/`,
базу для поиска и интерфейсов — в корневую `_knowledge_base/`, а логи и отчёт
запуска — в `_new_lecture_corpus/`. Другой каталог базы можно явно задать через
`--knowledge-dir`.

Runner поддерживает `--limit`, `--skip-frames`, `--skip-transcription`,
`--no-ocr` и другие параметры. Полный список:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\run_new_lecture_pipeline.py --help
```

Не используйте `--delete-source` без отдельной резервной копии: этот флаг
удаляет исходные видео после успешного импорта.

## Построение и поиск

Для уже подготовленного корпуса:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\build_knowledge_base.py `
  --root .\_new_lecture_corpus `
  --output .\_knowledge_base
```

Поиск:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\search_lectures.py `
  "ложный пробой после резкого подхода" `
  --root . `
  --top 5
```

Поиск работает без внешнего LLM. Если он не находит нужный фрагмент, RAG-чат
также не сможет надёжно обосновать ответ.

## RAG-чат

Prompt-only режим не делает внешних запросов:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\chat_lectures.py `
  --root . `
  --provider prompt
```

Для OpenAI-compatible API скопируйте корневой `.env.example` в `.env` и
заполните локальные значения. Для Ollama задайте `OLLAMA_BASE_URL` и
`OLLAMA_MODEL`. Никогда не вставляйте ключи в исходники, команды shell history,
issues или логи.

## Интерфейсы

Desktop UI:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\desktop_app.py
```

Web UI:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\web_app.py
```

Оба интерфейса используют локальную базу знаний; пустой clone без
`_knowledge_base/` не содержит пользовательских данных и не готов к поиску.

## Backtest harness

`backtest_harness/` содержит fixture-first проверки. Публикуемые fixtures
синтетические: они не загружают реальную историю, не создают ордера и не
разрешают paper/live execution.

```powershell
$commands = @(
  "validate-fixtures",
  "validate-manifest-inventory-fixtures",
  "validate-manifest-metadata-fixtures",
  "validate-manifest-metadata-execution-fixtures",
  "validate-public-seed-checksum-fixtures",
  "validate-scn002-fixtures",
  "scan-capabilities"
)

foreach ($command in $commands) {
  .\.venv\Scripts\python.exe -X utf8 -m knowledge_bot.backtest_harness.cli $command
  if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
}
```

Контракт и текущие ограничения описаны в
`backtest_harness/README.md`.

## Торговая безопасность

- `broker_adapter.py` по умолчанию не предоставляет реальный биржевой адаптер.
- Shadow-режим предназначен только для наблюдения и пишет локальный ledger.
- Наличие результатов бэктеста не означает воспроизводимую доходность.
- Перед любой реальной интеграцией нужны отдельные проверки исполнения,
  комиссий, проскальзывания, риска, хранения ключей и аварийного отключения.

## Проверка исходников

```powershell
.\.venv\Scripts\python.exe -m compileall -q knowledge_bot
```

CI выполняет компиляцию и синтетические fixture-проверки без секретов, без
доступа к приватному корпусу и без публикации артефактов.
