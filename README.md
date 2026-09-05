# botArtjahan / Trader Brain

Локальный исследовательский проект на Python: импорт видеолекций, построение
поисковой базы знаний, RAG-интерфейсы, детекторы торговых сценариев,
fixture-first проверка бэктест-контрактов и desktop UI.

> Проект предназначен для исследований и обучения. Это не финансовый совет и
> не готовая торговая система. Автоматическое исполнение сделок в опубликованном
> составе отсутствует.

## Что находится в репозитории

- `knowledge_bot/` — исходный код пайплайна, поиска, интерфейсов и детекторов.
- `knowledge_bot/backtest_harness/` — read-only harness и синтетические fixtures.
- `BACKTEST_RESULTS.md` — историческая сводка локальных экспериментов с
  явно указанными ограничениями.
- `RUN_TRADER_BRAIN.bat` — Windows launcher: packaged build при его наличии,
  иначе запуск GUI из исходников.
- `RUN_SHADOW_TRACKER.bat` — локальный shadow-трекер без выставления ордеров.

Репозиторий намеренно не содержит секреты, транскрипты, изображения лекций,
собранную базу знаний, исторические рыночные данные, локальные базы, логи,
экспериментальные ledger-файлы и сборки приложения.

## Требования

- Python 3.12.
- Windows и PowerShell — основной проверенный локальный сценарий.
- `ffmpeg` в `PATH` нужен только для импорта видео.
- Для OCR, vision и GUI нужны соответствующие зависимости из
  `requirements.txt`.

`requirements.txt` содержит ограниченные диапазоны зависимостей времени выполнения, но не
является платформенным lock-файлом. Для GPU установите совместимую сборку
PyTorch отдельно до установки остальных пакетов.

## Установка

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

Опциональная конфигурация LLM:

```powershell
Copy-Item .env.example .env
# Заполните .env локально. Не добавляйте его в Git.
```

Без API-ключа доступен prompt-only режим. Также поддерживается локальный
OpenAI-compatible endpoint или Ollama.

## Данные

Команды поиска и интерфейсы ожидают локально собранную корневую
`_knowledge_base/`. Данные создаются из вашего собственного корпуса и остаются
вне Git. Runner ниже хранит корпус в `_new_lecture_corpus/`, а базу знаний по
умолчанию создаёт в `_knowledge_base/`.

Пример импорта нового корпуса:

```powershell
$sourceDir = "D:\path\to\your\videos"

.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\run_new_lecture_pipeline.py `
  --source-dir $sourceDir `
  --pipeline-root .\_new_lecture_corpus `
  --model base `
  --language ru
```

Подробности о структуре данных и командах находятся в
`knowledge_bot/README.md`.

## Запуск

Поиск по уже собранной базе:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\search_lectures.py `
  "БСУ БПУ уровень" --top 5
```

Prompt-only чат:

```powershell
.\.venv\Scripts\python.exe -X utf8 .\knowledge_bot\chat_lectures.py `
  --provider prompt
```

Desktop UI из исходников:

```powershell
.\RUN_TRADER_BRAIN.bat
```

## Проверки

Быстрая синтаксическая проверка:

```powershell
.\.venv\Scripts\python.exe -m compileall -q knowledge_bot
```

Регрессионные unit-тесты:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -v
```

Синтетические fixture-наборы не читают приватный корпус или реальные
исторические данные:

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

Эти же проверки выполняются в GitHub Actions без секретов и без публикации
артефактов.

## Ограничения и безопасность

- `_knowledge_base/` и исходный корпус не поставляются, поэтому поиск и UI не
  работают до локальной сборки данных.
- Сводка бэктестов не является обещанием доходности; исходные локальные CSV не
  публикуются.
- Базовый broker adapter не реализует реальную биржевую интеграцию.
- Не включайте live-флаги и не добавляйте ключи бирж без отдельного аудита
  исполнения, риска и хранения секретов.

О найденной уязвимости сообщайте по инструкции в `SECURITY.md`.

## Лицензия

Исходный код опубликован без open-source лицензии. Все права сохранены; см.
`LICENSE`.
