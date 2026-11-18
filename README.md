# E-commerce Analytics Assistant

> An end-to-end data platform that turns raw e-commerce data into a trustworthy analytical model, then lets people ask business questions in plain language. Answers come from SQL (numbers) or RAG over customer reviews (reasons), with sources cited.

**Status: work in progress.** The ingestion layer is finished and tested. Modeling, the data API and the AI assistant are being built in the open; see the [roadmap](#roadmap).

---

## Why this project

Most portfolio projects stop at "load a CSV and draw a chart". This one is built around the problems that make real data engineering hard:

- **Repeatable, verifiable loads**: running a pipeline twice must not duplicate data, and every load must prove it loaded what it expected.
- **Messy, realistic data**: the dataset has real quality issues (listed [below](#real-data-problems-this-project-tackles)) that a naive pipeline silently gets wrong.
- **Two kinds of questions**: "what was monthly revenue?" needs exact SQL, while "why are customers unhappy with delivery?" needs retrieval over text. The assistant routes between both and cites its evidence.

## Architecture

```mermaid
flowchart LR
    A[Olist CSVs<br/>9 files, ~100k orders] --> C[Python ingestion<br/>validate + COPY]
    B[Frankfurter REST API<br/>BRL→EUR rates] --> C
    C --> D[(PostgreSQL 16<br/>schema raw)]
    D -->|dbt: staging + tests| E[(schema core<br/>star schema)]
    D -->|reviews → embeddings| F[(pgvector)]
    E --> G{FastAPI}
    F --> G
    G -->|metrics| H[Data API<br/>/v1/metrics, /v1/orders]
    G -->|question| I[Router: SQL or RAG]
    I --> J[LLM answer<br/>with cited sources]
```

Solid parts are built today (ingestion → `raw`); the rest is on the roadmap.

## Tech stack

| Area | Technology |
|---|---|
| Languages | Python 3.12, SQL |
| Database | PostgreSQL 16 + pgvector, run in Docker |
| Ingestion | pandas, psycopg (`COPY`), `requests` with retry/backoff, run log (`etl_log`) |
| Transformation | dbt (staging + core models, data tests) *(planned)* |
| AI | multilingual embeddings, RAG, LLM API, read-only text-to-SQL *(planned)* |
| API | FastAPI, Pydantic, OpenAPI docs *(planned)* |
| Quality & delivery | pytest, GitHub Actions CI *(planned)*, Git/GitHub |

## What works today

### Batch ingestion of the Olist dataset
- Loads 9 CSV files into the `raw` schema with PostgreSQL `COPY`.
- **Repeatable**: a second run does not duplicate rows.
- **Verified**: row counts are compared against the source file after each load.
- **Logged**: every run is recorded in `raw.etl_log` (run id, source, rows expected vs loaded, status, error, timestamps).
- Handles a real-world quirk: a BOM in the category-translation file.

### REST API ingestion (Frankfurter exchange rates)
- Pulls daily BRL→EUR rates for the full order period (555 business days) from a public REST API.
- **Resilient**: timeouts, retry with exponential backoff on `429`/`5xx`, requests split into bounded date windows.
- **Raw first**: the unmodified JSON response is stored in `raw.fx_rates_raw` before any transformation, so loads can be audited and replayed.
- **Idempotent**: upsert on `(rate_date, base, quote)`; running it four times in a row still yields exactly 555 rows.
- Unit-tested with pytest (date windowing, response flattening).

## Real data problems this project tackles

These issues are in the source data. They will be handled in dbt with automated tests and documented here as they are resolved.

| Problem | Why it matters | Planned handling |
|---|---|---|
| 814 duplicate `review_id`s | Inflates review counts and sentiment | Deduplicate in staging, enforce `unique` test |
| Orders marked `delivered` with no delivery date | Breaks delivery-time metrics | Flag and test; exclude from delivery KPIs |
| 2 product categories without translation | Missing labels in reports | Explicit fallback + `relationships` test |
| 610 products without a category | Silent gaps in category analysis | Map to an explicit `unknown` member |
| Joining `order_items` with `order_payments` | **Duplicates revenue** (fan-out) | Separate fact tables at the correct grain |
| FX rates exist only on business days | Weekend orders would have no rate | As-of join: last available rate on or before the order date |

## Roadmap

- [x] Dockerized PostgreSQL 16 + pgvector with `raw`, `staging`, `core` schemas
- [x] Repeatable CSV ingestion with row-count validation and run log
- [x] REST API ingestion (Frankfurter) with retry and idempotent load
- [ ] dbt: staging models, star schema (`fact_order_items`, `fact_payments`, `fact_reviews`, dimensions, `dim_fx_daily`), data-quality tests
- [ ] Orchestration (Airflow or Dagster), optional
- [ ] Review embeddings stored in pgvector
- [ ] Data API (FastAPI): metrics, filters, cursor pagination, API-key auth, read-only DB role, pytest
- [ ] `/v1/ask`: question routing to RAG or read-only text-to-SQL, answers with cited reviews
- [ ] Evaluation on a set of 20–30 test questions
- [ ] CI (GitHub Actions), containerized app, architecture write-up

## Getting started

Requirements: Docker, Python 3.12.

```bash
# 1. Start PostgreSQL + pgvector (creates the raw/staging/core schemas and tables)
docker compose up -d

# 2. Python environment
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -r requirements.txt

# 3. Configure the database connection
cp .env.example .env             # then fill in your values

# 4. Download the Olist dataset from Kaggle (link below) and place the CSVs in data/

# 5. Load the data (both scripts are safe to re-run)
python src/ingest/load_csv.py
python src/ingest/ingest_fx.py

# 6. Run the tests
pytest
```

Dataset: [Brazilian E-Commerce Public Dataset by Olist](https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce) (~100k orders, 2016–2018). Raw data is not committed to this repository.

## Repository layout

```
sql/init/        Database initialization (schemas, raw tables, FX tables)
src/ingest/      Ingestion scripts (CSV loader, Frankfurter API, data inspection)
tests/           pytest unit tests
```

More folders (`dbt/`, `app/`) will appear as the roadmap items land.

## Design decisions

- **Raw layer is append-faithful.** Data lands in `raw` exactly as received; all cleaning happens later in version-controlled, tested SQL. That keeps every transformation auditable and replayable.
- **Idempotency over cleverness.** Every load can be re-run safely, which makes failures cheap to recover from.
- **Text-to-SQL will be restricted.** The assistant will query through a read-only database role limited to the `core` schema, so a model mistake cannot modify or leak other data.
- **Alternatives considered for serving data:** PostgREST/Hasura (auto-generated APIs) are useful in practice, but a hand-built FastAPI layer shows validation, pagination, versioning and auth explicitly.

## Author

Built by Necula Madalina Nicoleta · [LinkedIn](https://www.linkedin.com/in/m%C4%83d%C4%83lina-necula-5270b8305/) ·
