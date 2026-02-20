# Backend (FastAPI)

## Setup

1. Create and activate a virtual environment.
2. Install dependencies:

```bash
pip install -r backend/requirements.txt
```

3. Copy env template and update values:

```bash
cp backend/.env.example backend/.env
```

4. Create the PostgreSQL schema:

```bash
psql "$DATABASE_URL" -f db/schema.sql
```

5. Run the API:

```bash
uvicorn backend.app.main:app --reload
```

## Initial endpoint flow

1. `POST /users`
2. `POST /situations`
3. `POST /articles/ingest`
4. `GET /situations/{situation_id}/articles`
5. `GET /situations/{situation_id}/dashboard`

## Containers

1. Backend container image is defined in `backend/Dockerfile`.
2. Full Unraid stack is defined in `docker-compose.unraid.yml`.
3. Unraid deployment instructions are in `DEPLOY_UNRAID.md`.
