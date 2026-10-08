# Codex handoff package

Files:

- `CODEX_CONTEXT.md` — full technical/project context
- `AGENTS.md` — persistent repository operating rules for Codex
- `FIRST_CODEX_PROMPT.md` — first prompt to send to Codex

Recommended placement in the repository root:

```text
C:\Users\Nocomp\Desktop\nlp\CODEX_CONTEXT.md
C:\Users\Nocomp\Desktop\nlp\AGENTS.md
C:\Users\Nocomp\Desktop\nlp\FIRST_CODEX_PROMPT.md
```

Then open the repository in Codex and paste the contents of `FIRST_CODEX_PROMPT.md`.

Do not include credentials or secret values in these files.

## FastAPI backend

The FastAPI backend is documented in
`reports/PROMPT17_FASTAPI_BACKEND.md`. After installing the `web` extra, run:

```powershell
.venv\Scripts\python.exe -m pip install -e ".[web]"
.venv\Scripts\pdf-benchmark-api.exe
```

Swagger UI is available at `http://127.0.0.1:8000/docs`. Cloud converters are
disabled by default and require server-side environment variables.

The browser interface is available at `http://127.0.0.1:8000/`; its details
are in `reports/PROMPT18_WEB_INTERFACE.md`.

## Docker deployment

CPU/GPU Docker images, Compose, HTTPS reverse proxy configuration and Ubuntu /
Docker Desktop instructions are in `reports/PROMPT19_DOCKER_DEPLOYMENT.md`.

## Research outputs

The current reproducible benchmark baseline is
`20261004T095716Z_s42_2334c932`. The main report materials are stored under:

- `reports/statistical_analysis/` — quantitative comparison and figures;
- `reports/error_analysis/` — deterministic error examples;
- `reports/final_report/` — the full report, including Word editions;
- `reports/benchmark_control/` — quality-control evidence.

Large cached adapter outputs are deliberately excluded from Git. They can be
recreated from compatible saved data or retained separately as an archive.
