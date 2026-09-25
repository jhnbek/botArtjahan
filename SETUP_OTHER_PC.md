# Продолжение работы на другом ПК

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
.\.venv\Scripts\python.exe -m pip install -r requirements-desktop.txt
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

## Незавершённое обучение ТВХ

Все 817 изображений из «Джахан» включены в репозиторий вместе с разметкой. Отдельно копировать Downloads не требуется. Перед продолжением обучения прочитайте [TRAINING_HANDOFF.md](TRAINING_HANDOFF.md): там зафиксированы последняя задача, сохранённые файлы и незавершённые проверки. Дополнительные зависимости: `python -m pip install -r requirements-training.txt`.
