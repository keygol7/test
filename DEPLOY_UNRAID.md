# Deploy On Unraid

## 1) Prerequisites

1. Docker service enabled in Unraid.
2. Community Applications plugin installed.
3. Docker Compose Manager installed from Community Applications.

## 2) Copy this repo onto Unraid

Example path:

`/mnt/user/appdata/news-dashboard/repo`

## 3) Create runtime env file

From repo root:

```bash
cp .env.unraid.example .env.unraid
```

Update these values in `.env.unraid`:

1. `POSTGRES_PASSWORD`
2. `VITE_API_BASE_URL` (Unraid host/IP + backend port)
3. `CORS_ORIGINS_CSV` (Unraid host/IP + frontend port)
4. `POSTGRES_DATA_PATH` if you want a different persistent location

## 4) Deploy stack

From repo root:

```bash
docker compose -f docker-compose.unraid.yml --env-file .env.unraid up -d --build
```

If using Compose Manager UI, point the stack to:

1. Compose file: `docker-compose.unraid.yml`
2. Env file: `.env.unraid`

## 5) Verify

1. Backend health:

```bash
curl http://<UNRAID_HOST_OR_IP>:8000/health
```

Expected:

```json
{"status":"ok","service":"News Situation API"}
```

2. Frontend:

Open `http://<UNRAID_HOST_OR_IP>:3000`

## Notes

1. `db/schema.sql` runs automatically only when PostgreSQL initializes an empty data directory.
2. If the data directory already has a database, schema init scripts in `/docker-entrypoint-initdb.d` are skipped.
