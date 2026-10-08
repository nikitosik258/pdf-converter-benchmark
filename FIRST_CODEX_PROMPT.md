# FIRST_CODEX_PROMPT.md

Use this as the first task message in Codex after opening the repository.

---

Ты продолжаешь существующий проект benchmark PDF -> TXT / structured extraction.

Сначала ничего крупного не меняй.

1. Прочитай полностью:
   - `CODEX_CONTEXT.md`
   - `AGENTS.md`

2. Затем изучи текущий репозиторий:
   - `git status`
   - `git diff`
   - структуру `src/`, `scripts/`, `tests/`, `config/`, `ground_truth/`
   - `outputs/benchmark/cache/`
   - последний clean experiment `20261004T013334Z_s42_20904978`

3. Проверь, что понимаешь текущую архитектуру:
   - 5 фиксированных PDF D01-D05
   - 40 GT объектов
   - 10 активных инструментов
   - Marker исключён
   - cloud API нельзя вызывать повторно без явного разрешения
   - dataset имеет структурный status=clean; это не подтверждает правильность оценивания
   - аудит от 2026-10-02 выявил ошибки кода и GT: `reports/PROMPT12_CODE_AUDIT.md`
   - Prompt 13 пересчитан для текущего baseline: `reports/PROMPT13_ANALYSIS.md`
   - исправленная стандартизация текста OCR.Space включена в текущий официальный baseline; изолированная проверка находится в `outputs/repairs/ocr_space_text_blocks_v1/`
   - pdfplumber использует word-level extraction
   - text matcher поддерживает multi-block fallback
   - повторяющиеся слова в разных bbox сохраняются; удаляются только дубли текста с тем же bbox
   - LlamaParse fixes сохранены
   - VRAM logic исправлена
   - в выбранном официальном experiment общий GT-независимый детектор химии применяется ко всем десяти инструментам; у всех Detection=100,0, а условный Structured Extraction различается

4. Не запускай:
   - `--force-api`
   - `--force-rerun`
   - тяжёлые Docling/MinerU runs
   - любые cloud calls

5. Для безопасной проверки можно использовать:

```powershell
.venv\Scripts\python.exe -m pytest -q `
  -m "not heavy" `
  --ignore=tests/test_heavy_local_adapters.py `
  --ignore=tests/test_marker_standardizer.py
```

Текущий ожидаемый baseline: `308 passed`.

После изучения репозитория ответь мне кратким technical audit:

- что ты увидел;
- какие ключевые компоненты проекта существуют;
- какие изменения уже зафиксированы;
- какие legacy/технические долги остались;
- что ты считаешь безопасным следующим шагом.

Не меняй benchmark semantics, GT или cached cloud raw без отдельного согласования.

---

## Обновление 2026-10-03 после Промта 13

После анализа исправлены OCR.Space text blocks и два явно разрешённых пункта
химии: строгие линейные формулы из `ParsedText` и нативные CodeCogs
`\\chem{...}` структуры теперь преобразуются в типизированные
`ChemicalObject`. Изолированные проверки находятся в
`outputs/repairs/ocr_space_text_blocks_v1/` и
`outputs/repairs/ocr_space_chemistry_objects_v1/`. Исправления включены в
официальный эксперимент `20261003T202925Z_s42_ee4d6ac4`; Промпт 13 пересчитан
для этого baseline. Текущий безопасный набор тестов: `305 passed`.

Раздельная оценка химии реализована по явно подтверждённому решению пользователя:
`chemistry_detection_score` оценивает покрытие всех Chemistry GT, а
`chemistry_structured_extraction_score` — качество только сопоставленных объектов.
Если химия не обнаружена, качество извлечения имеет значение N/A. Оба показателя
диагностические и не меняют существующие Chemistry Score и Overall. В изолированной
проверке и официальном эксперименте OCR.Space Detection = 100,0, Structured
Extraction = 61,6905. Контрольный Промпт 12 прошёл 623/623 проверок; подготовка
Промпта 13 прошла 601/601 проверку, а артефакты — 50/50. Отчёт содержит 24 CSV
таблицы, 17 графиков и 17-страничный PDF-атлас. Предложенных пунктов ремонта не
осталось.

---

## Обновление 2026-10-04: общий детектор химии

Raw-ответы всех десяти инструментов проверены. Общий слой стандартизации
создаёт `ChemicalObject` только из явного синтаксиса формул, семантики столбца
«Структурная формула», нативного изображения под этим заголовком или
координатных атомных меток в таком столбце. Ground Truth детектор не использует;
generic math/image/diagram не переобъявляются химией.

Изолированный аудит находится в
`outputs/repairs/shared_chemistry_detection_v1/`: 50/50 raw-кэшей совместимы,
все планируют `standardize_only`, parser acquisition и cloud API calls равны 0,
нехимические оценки не изменились. Все инструменты сопоставили 6/6 Chemistry
GT, Detection=100, а Structured Extraction лежит в диапазоне 39,04–75,13.

Текущий официальный эксперимент — `20261004T013334Z_s42_20904978`: 10 tool
rows, 50 pairs, 400 object rows, 0 errors, 13 сохранённых IQR-предупреждений.
Контрольный Промпт 12 прошёл 623/623 проверки. Промпт 13 пересчитан: 24 CSV,
17 фигур, 17-страничный PDF; подготовка 601/601, проверка артефактов 50/50,
безопасный набор — 308 tests.
