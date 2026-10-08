# Docker и развёртывание PDF Converter

## Состав

| Файл | Назначение |
|---|---|
| `Dockerfile` | CPU-образ: PyMuPDF, pdfplumber, pdfminer.six и клиенты cloud API. |
| `Dockerfile.gpu` | GPU-образ: CPU-набор, Docling и MinerU в отдельной среде. |
| `compose.yaml` | Один FastAPI-контейнер, volume, healthcheck, JSON-логи и профиль Caddy. |
| `compose.gpu.yaml` | GPU override и постоянный cache моделей. |
| `deploy/.env.server.example` | Шаблон несекретной серверной конфигурации. |
| `deploy/Caddyfile` | Reverse proxy, автоматический HTTPS и лимит тела запроса. |
| `requirements-web-pinned.txt` | Используемый Docker build файл с зафиксированными прямыми зависимостями web/CPU runtime. |

Docker-контекст намеренно не содержит `documents/`, Ground Truth, benchmark cache,
`outputs/`, `.env` и локальные venv. Пользовательские файлы хранятся только в
именованном Docker volume `web-runtime` и удаляются API по TTL (по умолчанию 24
часа) или по `DELETE /api/v1/documents/{id}`. Фоновая очистка запускается каждые
15 минут. Логи идут в stdout в JSON, Docker сохраняет не более пяти файлов по
10 MiB.

Контейнер явно задаёт `PDF_BENCHMARK_WEB_PROJECT_ROOT=/opt/pdf-benchmark`.
Это необходимо, потому что установленный Python package находится внутри venv,
а worker environments `.venv`, `.venv-cloud` и `.venv-mineru` расположены в
корне контейнера. Без этой переменной список converters был бы полностью
недоступен и selector в интерфейсе оставался бы заблокированным.

## Режимы

### CPU

Образ `Dockerfile` предназначен для PyMuPDF, pdfplumber и pdfminer.six. Он также
содержит изолированную `.venv-cloud` для пяти выбранных облачных adapters, но
cloud jobs по умолчанию отключены. Docling и MinerU не установлены, поэтому UI
правильно покажет их как недоступные. Подходит для Docker Desktop и небольшого
Ubuntu-сервера без GPU.

### GPU

`Dockerfile.gpu` использует CUDA 12.6 runtime на Ubuntu 24.04, содержит Docling
в `.venv` и MinerU в `.venv-mineru`. `compose.gpu.yaml` передаёт все NVIDIA GPU и
ограничивает API одним одновременным job: ML-конвертеры потребляют много RAM/VRAM.
Первый запуск Docling/MinerU может скачать модели; `model-cache` сохраняет их
между перезапусками. Перед запуском нужен NVIDIA driver на хосте и NVIDIA
Container Toolkit. Marker не добавлен: он не входит в активный roster.

Внутренний backend остаётся single-node runtime из Prompt 17. Поэтому здесь
только один контейнер `app`; горизонтальное масштабирование до PostgreSQL +
Redis/Celery + object storage из Prompt 16 не включается автоматически.

## Docker Desktop: локальная проверка

1. Запустите Docker Desktop и дождитесь статуса **Engine running**.
2. В PowerShell в корне проекта выполните:

   ```powershell
   Copy-Item deploy/.env.server.example deploy/.env.server
   docker compose up --build
   ```

3. Откройте `http://127.0.0.1:8001/`. Порт 8001 выбран для Docker Desktop,
   чтобы не конфликтовать с локально запущенным FastAPI на 8000.
4. Статус контейнера и healthcheck:

   ```powershell
   docker compose ps
   docker compose logs --follow app
   ```

5. Остановка: `docker compose down`. Данные временных jobs останутся в named
   volume. Для удаления и данных, и volume: `docker compose down --volumes`.

Для Docker Desktop GPU требуется WSL 2 GPU support и работающий NVIDIA driver.
После этого используйте:

```powershell
docker compose -f compose.yaml -f compose.gpu.yaml up --build
```

## Ubuntu Server: CPU

Ниже предполагается Ubuntu 24.04, DNS-запись домена, указывающая на IP сервера,
и вход по SSH с пользователем, имеющим `sudo`.

1. Установите Docker Engine и Compose plugin по [официальной инструкции Docker](https://docs.docker.com/engine/install/ubuntu/). Проверьте:

   ```bash
   docker --version
   docker compose version
   ```

2. Скопируйте репозиторий на сервер без `.env`, benchmark outputs и виртуальных
   сред. В его корне:

   ```bash
   cp deploy/.env.server.example deploy/.env.server
   chmod 600 deploy/.env.server
   ```

3. Отредактируйте `deploy/.env.server`: оставьте cloud выключенным, либо добавьте
   ключи только на сервере и установите `PDF_BENCHMARK_WEB_ALLOW_CLOUD=true`.
   Не передавайте ключи через frontend, Git или образ.

4. Временно для проверки на сервере без proxy:

   ```bash
   docker compose up --build -d
   curl --fail http://127.0.0.1:8001/api/v1/health
   docker compose logs --follow app
   ```

5. Для публичного HTTPS задайте DNS имя и e-mail ACME в окружении текущей shell,
   затем включите профиль proxy:

   ```bash
   export APP_DOMAIN=pdf.example.org
   export ACME_EMAIL=admin@example.org
   docker compose --profile proxy up --build -d
   ```

   Caddy получит и продлит сертификат автоматически. Порты приложения 8000 не
   публикуются наружу, proxy слушает 80/443.

## Ubuntu Server: GPU

1. Выполните CPU-шаги 1–3 и установите проверенный NVIDIA driver для своей GPU.
2. Установите [NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html), перезапустите Docker и проверьте доступ:

   ```bash
   docker run --rm --gpus all nvidia/cuda:12.6.3-cudnn-runtime-ubuntu24.04 nvidia-smi
   ```

3. Запустите GPU-конфигурацию:

   ```bash
   docker compose -f compose.yaml -f compose.gpu.yaml --profile proxy up --build -d
   docker compose -f compose.yaml -f compose.gpu.yaml ps
   ```

Не включайте более одного worker без измерения VRAM на целевой GPU. Для больших
PDF увеличивайте timeout осознанно: API уже ограничивает размер, страницы и
время subprocess через `deploy/.env.server`.

## Сеть, секреты и эксплуатация

- **Firewall:** разрешите только `22/tcp`, `80/tcp` и `443/tcp`; не открывайте
  `8000/tcp` и `8001/tcp`. Например: `sudo ufw allow OpenSSH && sudo ufw allow 80/tcp && sudo
  ufw allow 443/tcp && sudo ufw enable`.
- **HTTPS:** Caddy требует доступные извне 80/443 и корректную DNS A/AAAA запись.
  Если TLS уже завершается внешним reverse proxy, не включайте профиль `proxy` и
  проксируйте на `app:8000` во внутренней сети.
- **Секреты:** `deploy/.env.server` имеет права `600`, игнорируется Git и не
  попадает в Docker build context. Для production лучше заменить его Docker
  secrets/Vault и передавать секреты только process окружению app.
- **Upload и timeout:** лимит reverse proxy — 100 MB; API по умолчанию — 100 MiB,
  300 страниц, 5400 секунд. При изменении сохраняйте proxy-лимит не больше API
  лимита и соразмеряйте timeout с максимальным PDF.
- **Мониторинг:** проверяйте `/api/v1/health`, `docker compose ps`, JSON-логи
  `docker compose logs app`, свободное место Docker volume и GPU через
  `nvidia-smi`. Настройте внешний uptime-check на HTTPS health endpoint.
- **Резервное копирование:** runtime intentionally temporary. Не добавляйте его
  в benchmark artifacts; при необходимости сохраняйте только audit-логи в
  отдельную защищённую систему.

## Проверка конфигурации

До первого build можно проверить состав Compose:

```bash
docker compose config
docker compose -f compose.yaml -f compose.gpu.yaml config
```

Это не вызывает cloud adapters и не запускает benchmark или тяжёлые parsers.
