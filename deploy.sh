#!/usr/bin/env bash
set -euo pipefail

# Load environment
set -a
source ~/test/.env.ubuntu
source ~/test/.env
set +a

echo "=== Pulling latest code ==="
git pull

echo "=== Building images ==="
docker compose -f docker-compose.build.yml build --no-cache frontend backend

echo "=== Deploying stack ==="
docker stack deploy -c docker-stack.ubuntu.yml newsdash

echo "=== Force-updating services to pick up new images ==="
docker service update --force newsdash_frontend
docker service update --force newsdash_backend
docker service update --force newsdash_rss-worker
docker service update --force newsdash_categorizer

echo "=== Deploy complete ==="
docker service ls --filter name=newsdash
