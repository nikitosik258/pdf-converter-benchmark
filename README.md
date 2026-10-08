# PDF Converter Benchmark

Исследовательский проект для сравнения пяти облачных сервисов и пяти локальных библиотек, которые преобразуют технические PDF в текст и структурированные данные.

Benchmark оценивает шесть типов содержимого: текст, таблицы, математические формулы, химические формулы, изображения и диаграммы. В проекте есть воспроизводимый набор тестов, количественные метрики, результаты анализа и web-интерфейс для демонстрации конвертеров.

## Инструменты

- Локальные: PyMuPDF, pdfplumber, pdfminer.six, Docling, MinerU.
- Облачные: OCR.Space, Nutrient, Mindee, Adobe Extract, LlamaParse.

## Быстрый запуск через Docker

Нужен Docker Desktop. В PowerShell из папки проекта выполните:

```powershell
docker compose up --build -d
```

После запуска откройте [http://127.0.0.1:8001](http://127.0.0.1:8001). Остановить приложение можно командой:

```powershell
docker compose down
```

## Локальный запуск без Docker

Нужен Python 3.12. Создайте окружение, установите web-зависимости и запустите сервер:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e ".[web,pymupdf,pdfplumber,pdfminer]"
pdf-benchmark-api
```

Интерфейс откроется по адресу [http://127.0.0.1:8000](http://127.0.0.1:8000), документация API — [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

Облачные конвертеры требуют ключей API в переменных окружения; ключи не должны добавляться в Git.

## Результаты исследования

Актуальный baseline: `20261004T095716Z_s42_2334c932`.

- `reports/statistical_analysis/` — таблицы, графики и статистический анализ;
- `reports/error_analysis/` — примеры характерных ошибок;
- `reports/benchmark_control/` — контроль воспроизводимости.

Большие кэши обработок намеренно исключены из Git через `.gitignore`.
