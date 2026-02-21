import logging
import os
import time
from collections import deque

import docker
from docker.errors import APIError, NotFound


def as_int(name: str, default: int) -> int:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return int(value)
    except ValueError:
        return default


def as_float(name: str, default: float) -> float:
    value = os.getenv(name)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError:
        return default


STACK_NAME = os.getenv("STACK_NAME", "news-dashboard")
TARGET_SERVICE = os.getenv("TARGET_SERVICE", "backend")
FULL_SERVICE_NAME = os.getenv("FULL_SERVICE_NAME", f"{STACK_NAME}_{TARGET_SERVICE}")
MIN_REPLICAS = as_int("MIN_REPLICAS", 2)
MAX_REPLICAS = as_int("MAX_REPLICAS", 8)
CHECK_INTERVAL_SEC = as_int("CHECK_INTERVAL_SEC", 30)
COOLDOWN_SEC = as_int("COOLDOWN_SEC", 120)
SCALE_STEP = as_int("SCALE_STEP", 1)
SCALE_UP_CPU = as_float("SCALE_UP_CPU", 75.0)
SCALE_DOWN_CPU = as_float("SCALE_DOWN_CPU", 25.0)
WINDOW_SIZE = as_int("WINDOW_SIZE", 3)


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(message)s",
)


def cpu_percent(stats: dict) -> float | None:
    cpu_stats = stats.get("cpu_stats") or {}
    pre_cpu_stats = stats.get("precpu_stats") or {}
    total_usage = (cpu_stats.get("cpu_usage") or {}).get("total_usage")
    pre_total_usage = (pre_cpu_stats.get("cpu_usage") or {}).get("total_usage")
    system_usage = cpu_stats.get("system_cpu_usage")
    pre_system_usage = pre_cpu_stats.get("system_cpu_usage")
    if (
        total_usage is None
        or pre_total_usage is None
        or system_usage is None
        or pre_system_usage is None
    ):
        return None

    cpu_delta = total_usage - pre_total_usage
    system_delta = system_usage - pre_system_usage
    if cpu_delta <= 0 or system_delta <= 0:
        return None

    online_cpus = cpu_stats.get("online_cpus")
    if not online_cpus:
        online_cpus = len((cpu_stats.get("cpu_usage") or {}).get("percpu_usage") or [1])

    return (cpu_delta / system_delta) * online_cpus * 100.0


def get_replicas(service) -> int:
    mode = (service.attrs.get("Spec") or {}).get("Mode") or {}
    replicated = mode.get("Replicated") or {}
    return int(replicated.get("Replicas", 1))


def get_running_container_ids(service) -> list[str]:
    ids: list[str] = []
    for task in service.tasks():
        status = task.get("Status") or {}
        if status.get("State") != "running":
            continue
        container_status = status.get("ContainerStatus") or {}
        container_id = container_status.get("ContainerID")
        if container_id:
            ids.append(container_id)
    return ids


def get_average_cpu(client, container_ids: list[str]) -> float | None:
    values: list[float] = []
    for container_id in container_ids:
        try:
            stats = client.api.stats(container_id, stream=False)
            value = cpu_percent(stats)
            if value is not None:
                values.append(value)
        except APIError as exc:
            logging.warning("Failed to read stats for container %s: %s", container_id, exc)
    if not values:
        return None
    return sum(values) / len(values)


def scale(service, new_replicas: int) -> None:
    service.scale(new_replicas)
    logging.info("Scaled %s to %s replicas", FULL_SERVICE_NAME, new_replicas)


def main() -> None:
    client = docker.from_env()
    recent_cpu = deque(maxlen=max(WINDOW_SIZE, 1))
    last_scaled_at = 0.0

    logging.info(
        "Autoscaler started for %s (min=%s max=%s up=%.1f%% down=%.1f%%)",
        FULL_SERVICE_NAME,
        MIN_REPLICAS,
        MAX_REPLICAS,
        SCALE_UP_CPU,
        SCALE_DOWN_CPU,
    )

    while True:
        try:
            service = client.services.get(FULL_SERVICE_NAME)
        except NotFound:
            logging.warning("Service %s not found yet", FULL_SERVICE_NAME)
            time.sleep(CHECK_INTERVAL_SEC)
            continue
        except APIError as exc:
            logging.error("Docker API error while loading service: %s", exc)
            time.sleep(CHECK_INTERVAL_SEC)
            continue

        service.reload()
        current_replicas = get_replicas(service)
        container_ids = get_running_container_ids(service)
        avg_cpu = get_average_cpu(client, container_ids)
        if avg_cpu is None:
            logging.info("No CPU data available (replicas=%s)", current_replicas)
            time.sleep(CHECK_INTERVAL_SEC)
            continue

        recent_cpu.append(avg_cpu)
        smoothed_cpu = sum(recent_cpu) / len(recent_cpu)
        now = time.time()

        logging.info(
            "replicas=%s running=%s avg_cpu=%.2f smoothed_cpu=%.2f",
            current_replicas,
            len(container_ids),
            avg_cpu,
            smoothed_cpu,
        )

        if now - last_scaled_at < COOLDOWN_SEC:
            time.sleep(CHECK_INTERVAL_SEC)
            continue

        if smoothed_cpu >= SCALE_UP_CPU and current_replicas < MAX_REPLICAS:
            target = min(MAX_REPLICAS, current_replicas + max(SCALE_STEP, 1))
            scale(service, target)
            last_scaled_at = now
        elif smoothed_cpu <= SCALE_DOWN_CPU and current_replicas > MIN_REPLICAS:
            target = max(MIN_REPLICAS, current_replicas - max(SCALE_STEP, 1))
            scale(service, target)
            last_scaled_at = now

        time.sleep(CHECK_INTERVAL_SEC)


if __name__ == "__main__":
    main()
