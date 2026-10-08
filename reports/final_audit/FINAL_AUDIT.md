# Финальный аудит проекта

Дата аудита: 6 октября 2026 года. Проверяемый baseline:
`20261004T095716Z_s42_2334c932`.

Аудит был read-only для benchmark: не изменялись PDF, Ground Truth, manifest,
кеши или оценки; не выполнялись новые benchmark acquisitions и не запускался
`--force-api`.

## Итог

Исследовательская часть и отчёт готовы к сдаче. Зафиксированный эксперимент
целостен и воспроизводим из совместимых сохранённых raw-ответов: 10 tools,
50 пар tool×PDF, 400 object rows, 0 structural errors и 13 сохранённых
статистических предупреждений. Control Prompt 12 воспроизводит оценки и
прошёл 625/625 проверок.

Web-приложение пригодно для локальной демонстрации и уже подтверждено
пользовательским запуском через Docker Desktop. Оно не готово к открытому
интернет-развёртыванию без авторизации, устойчивого job storage и отдельной
проверки каждого ML/cloud adapter в целевом GPU-образе.

## Проверенные факты

| Область | Результат | Доказательство |
|---|---|---|
| Документы | Одни и те же D01–D05 для всех tools | PDF hashes и 5/5 пар каждого tool проверены Prompt 12 |
| Ground Truth | 40 объектов, JSON и JSONL идентичны, manifest валиден | Prompt 12: `ground_truth_json_jsonl_equal`, `ground_truth_manifest_valid` |
| Правила оценки | Matching, object scores, aggregates и CI независимо воспроизведены | Prompt 12: 625/625 |
| Data leakage | Не обнаружен | enrichment Chemistry/Math имеет `gt_independent=true`; cached re-standardization не читает GT при извлечении |
| Benchmark grid | Полный | 10 tools × 5 PDF, 400 rows; 40 объектов на tool |
| Категории | Все 6 присутствуют в corpus | text, table, math, chemistry, image, diagram; матрица coverage сохранена |
| Анализ | Полный | Prompt 13: 24 таблицы, 17 фигур, 50/50 artifact checks |
| Error analysis | Полный | 60 случаев: каждый tool × каждая категория; 25/25 checks |
| Полный отчёт | Полный | 20 разделов, 50 примеров — ровно 5 на tool, 17 фигур; 29/29 checks |
| Код | Safe suite пройден | `322 passed, 1 warning` (устаревший Starlette TestClient) |
| API | Работает в Docker Desktop | `/health`: `ok`, storage/executor `ok`, 10/10 converters available |

## Методологическая оценка

Правила сопоставления и scoring одинаковы для всех инструментов. Пропущенный
GT-объект даёт ноль, а сравнение идёт на общей нормализованной схеме данных.
Кеширование не смешивает результаты разных этапов: fingerprint включает
standardization stage, а контроль проверяет versioned downstream cache и
неизменность 2 284 legacy-cache files.

Ground Truth согласован с manifest и проверен контрольным скриптом. Были
документированные, явно разрешённые точечные исправления payload/bbox/единиц
формул; идентификаторы, страницы и категории не изменялись. Это не data leakage:
поздние enrichment для Chemistry и Math используют только provider evidence или
layout, а не GT.

Результаты воспроизводимы **из сохранённых raw outputs**. Это не означает, что
повторный запрос к облачному provider сегодня вернёт тот же ответ: API и модели
поставщиков меняются. Поэтому baseline фиксирует версии, кеш, hashes и условия
измерений, а не обещает неизменность внешних сервисов.

## Найденные проблемы и исправления

| ID | Проблема | Критичность | Конкретное исправление |
|---|---|---|---|
| A20-01 | `ConverterRegistry` проверяет наличие Python executable, но не импорт конкретного пакета. CPU image не устанавливает Docling, однако `.venv` существует; UI может показать Docling доступным и job завершится ошибкой импорта. Это наблюдалось в Docker Desktop. | Высокая для web UI | Для каждого local converter добавить лёгкий subprocess probe `python -c "import …"` при старте или build-time availability manifest. Отображать доступность только при успешном probe; добавить CPU Docker regression test. |
| A20-02 | Полный runtime smoke-test всех 10 adapters в GPU/cloud Docker не завершился в ограниченное время: последовательный audit job был остановлен после нескольких минут ожидания на ML/GPU этапе. `10/10 available` проверяет конфигурацию, не успешную обработку. | Высокая перед демонстрацией всех tools | Отдельно выполнить по одному одностраничному smoke-test для каждого adapter с лимитом времени и сохранить результат. Для Docling/MinerU предварительно загрузить модели в образ/cache, записать RAM/VRAM и сделать timeout видимым в UI. |
| A20-03 | Backend не имеет authentication, ownership документов или rate limiting. UUID не заменяет контроль доступа. | Критическая для публичного сервера; не блокирует закрытую локальную демонстрацию | До внешнего публичного доступа добавить auth (минимум session/API token), ownership/job isolation, upload rate limit и audit log. Оставить порт API только за reverse proxy. |
| A20-04 | Runtime — single-node: ThreadPoolExecutor и JSON-файлы. Несколько replicas или общий volume могут дать гонки и потерю состояния jobs. | Высокая для production | Реализовать предложенную в Prompt 16 замену: PostgreSQL для состояния, Redis/Celery/RQ для jobs, S3/MinIO для объектов; запустить только один API replica до миграции. |
| A20-05 | Docker dependency pinning частичный: `requirements-web-pinned.txt` фиксирует прямые зависимости, но не полный hash-locked transitive graph. | Средняя | Создать платформенный lock (например, `uv.lock`/`pip-tools` с hashes) отдельно для CPU и GPU, фиксировать image digest и проверять SBOM/vulnerability scan в CI. |
| A20-06 | Web tests покрывают API и реальный PyMuPDF subprocess, но не запускают browser E2E. Недавняя ошибка `state.workspace` в frontend была найдена только ручным запуском в браузере. | Средняя | Добавить Playwright browser E2E: upload → status changes → result → TXT/JSON download; включить в non-heavy CI. |
| A20-07 | Документация CPU-режима утверждает, что Docling недоступен, но текущий registry может показывать его доступным из-за A20-01. | Средняя | После исправления A20-01 синхронизировать `Dockerfile`, `compose`, Prompt 19 и UI availability contract; проверить CPU/GPU matrix в CI. |
| A20-08 | Обобщаемость исследования ограничена пятью целенаправленно выбранными PDF и 40 GT-объектами. Coverage неравномерен: Chemistry есть только в D02, Math — в D01/D04/D05, визуальные категории выборочные. | Средняя методологическая | В выводах сохранить это ограничение. Для расширенного исследования добавить независимый test set, больше документов каждого типа и отдельную inter-annotator GT validation. Не менять текущий baseline. |
| A20-09 | Reading order не имеет отдельной GT-метрики; TEDS-like table metric — собственный аналог; visual protocol измеряет sampled GT recall без page-level precision; стоимость/скорость не являются межзапусковым SLA. | Средняя методологическая | Не заявлять универсальную точность или SLA. В следующей версии добавить GT порядка чтения, полностью размеченные visual pages и повторные запуски cloud tools. |
| A20-10 | В `pyproject.toml` нет настроенных mypy/pyright, ruff или security dependency scan. | Низкая | Добавить ruff + pyright/mypy + `pip-audit`/OSV в CI; установить baseline и постепенно убрать suppressions. |
| A20-11 | Ключи не попадают в Git/frontend и фильтруются для subprocess, но production template пока использует env file, а не Docker secrets/Vault. | Средняя для production | Для внешнего сервера перенести secrets в Docker secrets или Vault; оставить `deploy/.env.server` только для локальной демонстрации с правами `600`. |

## Что не является дефектом benchmark

Нулевые category scores не исправляются автоматически. Они часто означают, что
provider не выдаёт typed object нужного класса: например, pdfminer/Mindee не
строят таблицы, Adobe Extract хранит часть формул как figures, а многие tools
не возвращают diagram как отдельный объект. Это результат capability contract,
а не повод переопределять generic image/text как другой тип ради score.

## Финальный checklist готовности

### Исследование и benchmark — готово

- [x] Ровно 10 активных инструментов и 5 одинаковых PDF.
- [x] Все 50 tool×PDF pair rows и 400 object rows присутствуют.
- [x] Шесть категорий оценены; пропуски GT учтены через coverage, а не скрыты.
- [x] Ground Truth, manifest, hashes, matching, metrics и aggregate scores прошли контроль 625/625.
- [x] Нет подтверждённого data leakage.
- [x] Сохранён baseline, версии, raw cache, fingerprints и provenance.
- [x] 13 outliers сохранены как наблюдения, не удалены.

### Отчёт — готово

- [x] Есть количественные метрики, формулы, таблицы, графики и limits.
- [x] Есть 5 проверенных примеров для каждого из 10 tools.
- [x] Есть error analysis, обобщение, выводы, версии и официальные ссылки.
- [x] Числа и артефакты сверены: Prompt 13 — 50/50, Prompt 14 — 25/25, Prompt 15 — 29/29.

### Локальная демонстрация — готово с ограничениями

- [x] Upload/PDF validation, status, TXT/JSON export, errors, TTL cleanup и JSON logging покрыты тестами.
- [x] Docker CPU/GPU configuration, healthcheck, volumes, limits и reverse proxy configuration подготовлены.
- [x] Локальный Docker health endpoint отвечает `ok`; registry сейчас сообщает 10/10 available.
- [ ] Закрыть A20-01 и A20-02, прежде чем заявлять, что любой выбранный converter гарантированно запускается в Docker.

### Публичный внешний сервер — пока не готов

- [ ] Закрыть A20-03: authentication, ownership и rate limiting.
- [ ] Закрыть A20-04: production job/storage architecture либо явно оставить один закрытый instance.
- [ ] Закрыть A20-05 и A20-11: полный dependency lock и production secrets.
- [ ] Провести GPU/cloud smoke-test всех 10 tools и зафиксировать результат.

## Выполненные команды аудита

```powershell
.venv\Scripts\python.exe scripts\verify_prompt13_artifacts.py --experiment-id 20261004T095716Z_s42_2334c932
.venv\Scripts\python.exe scripts\verify_prompt14_artifacts.py --experiment-id 20261004T095716Z_s42_2334c932
.venv\Scripts\python.exe scripts\verify_prompt15_artifacts.py --experiment-id 20261004T095716Z_s42_2334c932
.venv\Scripts\python.exe -m pytest -q -m "not heavy" --ignore=tests/test_heavy_local_adapters.py --ignore=tests/test_marker_standardizer.py
```

Результаты: 50/50, 25/25, 29/29 и `322 passed, 1 warning` соответственно.
