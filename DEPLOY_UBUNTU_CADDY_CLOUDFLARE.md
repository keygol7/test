# Deploy On Ubuntu VM (Caddy + Cloudflare Tunnel + Auto-Scaling)

## Architecture

1. `cloudflared` receives external traffic from Cloudflare.
2. `cloudflared` forwards to `caddy` inside the Docker overlay network.
3. `caddy` routes `/api/*` to FastAPI and all other paths to React.
4. `autoscaler` monitors backend CPU and adjusts backend replicas in Swarm.

## 1) Prerequisites

1. Ubuntu VM with Docker Engine and Docker Compose plugin installed.
2. Docker Swarm initialized on the VM:

```bash
docker swarm init
```

3. Cloudflare Tunnel token from Zero Trust dashboard.

## 2) Configure environment

From repo root:

```bash
cp .env.ubuntu.example .env.ubuntu
```

Update at minimum:

1. `PUBLIC_HOST`
2. `POSTGRES_PASSWORD`
3. `CLOUDFLARE_TUNNEL_TOKEN`

Do not commit `.env.ubuntu`. Keep credentials only in local env files.

## 3) Build images

```bash
docker compose --env-file .env.ubuntu -f docker-compose.build.yml build
```

## 4) Deploy stack to Swarm

Load env vars into your shell, then deploy directly from `docker-stack.ubuntu.yml`:

```bash
set -a
source .env.ubuntu
set +a
docker stack deploy -c docker-stack.ubuntu.yml "$STACK_NAME"
```

Avoid `docker compose ... config` as an intermediate step for stack deploy, because it can coerce some numeric fields (like published ports) into strings.

## 5) Cloudflare Tunnel routing

In Cloudflare Zero Trust, for the tunnel matching your token:

1. Add public hostname: `PUBLIC_HOST` value
2. Service type: `HTTP`
3. Service URL: `http://caddy:80`

## 6) Verify

1. Stack services:

```bash
docker stack services "$STACK_NAME"
```

2. Backend health through Caddy path:

```bash
curl -s http://127.0.0.1:8080/api/health
```

Expected:

```json
{"status":"ok","service":"News Situation API"}
```

`docker-stack.ubuntu.yml` currently publishes Caddy as `8080:80`.
If you need a different host port, change `services.caddy.ports[0]` to another `HOST:CONTAINER` value (for example `9090:80`).
Initial backend/frontend replicas are set as integers in the stack file (`2` and `2`).
Adjust `services.backend.deploy.replicas` and `services.frontend.deploy.replicas` directly if needed.

## Auto-scaling notes

1. Auto-scaling applies to the backend service.
2. Thresholds are controlled by `AUTOSCALE_*` variables in `.env.ubuntu`.
3. Min/max backend replicas are controlled by `BACKEND_MIN_REPLICAS` and `BACKEND_MAX_REPLICAS`.
