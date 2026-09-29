# Продолжение работы на другом ПК

## Текущая точка переноса — 29 сентября 2026

Базовая версия до текущего этапа — `b73b40d7840e30b32ee3482e929972d8a2471832`. Получите последнюю ветку `main`, содержащую эту инструкцию: в ней должны быть новые рыночные модели, правило всего безоткатного подхода, биржевая история и отчёты. Скачивание только старого `b73b40d…` не восстановит текущую работу.

После `git clone` либо `git pull --ff-only origin main` проверьте наличие всей коллекции `_knowledge_base/manual_reviews/scenarios_dzhahan_20260925/`, включая `images/` и `training/` с биржевой историей, разметкой, моделями и пояснениями автора. Одного файла продолжения недостаточно. Виртуальное окружение переносить не нужно — создайте его заново по инструкции ниже.

На новом ПК сначала прочитайте [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md), `training/TRAINING_STATE.json` и `training/NEXT_MODEL_TASKS.json` внутри коллекции. В handoff есть приоритеты доработок, точные номера ошибок и готовый текст для нового чата. До переобучения сохраните перенесённые модели как исходное сравнение.

Для текущего ПК 27 сентября 2026 создано отдельное окружение **`.venv312`**
на Python 3.12. Старое `.venv` на Python 3.14 сохранено. Здесь для запуска
используйте `.\.venv312\Scripts\python.exe -m knowledge_bot.desktop_app`, а для
тестов — `.\.venv312\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"`.
Команды ниже с `.venv` относятся к установке в новую папку на следующем ПК.
Git также установлен, текущая папка снова связана с `origin/main` на GitHub.
Если старый терминал не видит команду `git`, перезапустите редактор и терминал,
чтобы они получили обновлённый PATH.

В Git сохранены код робота, тесты, база знаний `knowledge/`, журнал
`_knowledge_base/user_level_feedback.jsonl` и разборы `_knowledge_base/manual_reviews/`.
Журнал содержит ваши ручные уровни, причины, даты БСУ и удаления по режимам.
Виртуальное окружение, распакованный кэш базы и состояние симуляции сделок
создаются отдельно и не переносятся через Git.

## Первый запуск на Windows

Установите Git и Python 3.12, затем в терминале VS Code:

```powershell
git clone https://github.com/jhnbek/botArtjahan.git
cd botArtjahan
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt -r requirements-training.txt
.\.venv\Scripts\python.exe -m knowledge_bot.desktop_app
```

Для следующих запусков из папки `botArtjahan` достаточно последней команды.
Кнопки над графиком отдельно показывают изломы, зеркальные/лимитные уровни
и уровни паранормального бара. Выберите Bybit, BTCUSDT и 1d для продолжения
сохранённой разметки. Свечи загружаются с выбранной биржи; исторические снимки
проверок находятся в `manual_reviews`.

Для OCR и расшифровки лекций есть полный `requirements.txt`; для графика он
не требуется. API-ключи для просмотра публичных свечей не нужны.

## Подключение базы знаний без второй копии репозитория

Исходная база уже находится в `knowledge/`. В текущем терминале выполните:

```powershell
$env:BOT_KNOWLEDGE_SOURCE = (Resolve-Path .\knowledge).Path
.\.venv\Scripts\python.exe -m knowledge_bot.knowledge_base connect
.\.venv\Scripts\python.exe -m knowledge_bot.knowledge_base status
.\.venv\Scripts\python.exe -m knowledge_bot.knowledge_base search "уровень" --limit 5
```

`connect` нужен один раз после переноса либо обновления базы; создаётся проверенный
локальный снимок в соседней папке `.bot_knowledge`. Для нового терминала снова
задайте `BOT_KNOWLEDGE_SOURCE` перед поиском или запуском приложения с базой.
Также можно передавать `--source .\knowledge` перед командой `status`/`search`.
Вторая папка `botArtjahan-main` для такого запуска не нужна.

## Перенос последующих правок между ПК

Перед сменой компьютера закройте окно робота, чтобы оно закончило запись журнала,
сохраните правки и отправьте их:

```powershell
git status
git add .
git commit -m "Save chart review and robot changes"
git push origin main
```

На другом ПК, перед запуском робота:

```powershell
git pull --ff-only origin main
```

Если Git сообщает о локальных изменениях или расхождении веток, сохраните их
и разрешите конфликт; не заменяйте журнал разметки принудительным сбросом.

Проверки из корня проекта:

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py"
```

Текущая методика описана в `knowledge_bot/KNOWLEDGE_CONNECTION.md`.
`handoff_archive/` хранит первоначальный аудит и раннюю HTML-демонстрацию
из родительской рабочей папки; они являются историческими материалами.

## Модели направления и ТВХ

Исходная коллекция содержит 409 сценариев и 817 JPG. После авторских исключений
№23, 54, 59, 85, 109 подготовлены 404 пары; в текущих моделях 329 сценариев
использованы для обучения и 75 для проверки. Сохранённые рыночные модели:
`training/market_context_direction/model.json` и `training/market_v2/scenario_model.json`
внутри коллекции. Эти модели и все источники должны присутствовать в полученной версии репозитория.

Перед продолжением прочитайте [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md) и
[актуальные результаты](TRAINING_MARKET_RESULTS.md). `TRAINING_RESULTS.md` содержит
исторический этап по изображениям; не подменяйте им текущие рыночные отчёты.

После создания нового окружения установите числовые зависимости:

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-training.txt
```

Сначала проверьте перенос файлов и контрольные суммы из `TRAINING_STATE.json`.
Новое правило целого подхода уже проверено отдельно, но ещё не подключено
к моделям: это первая задача из `NEXT_MODEL_TASKS.json`.
