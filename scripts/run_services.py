"""Run every Schedence backend service with one command (local development).

Usage (PowerShell, repository root, venv active):
    python scripts\\run_services.py              # check Neon, then start all services
    python scripts\\run_services.py --reload     # restart a service when its code changes
    python scripts\\run_services.py --only identity
    python scripts\\run_services.py --smoke      # start, check /health and /health/db, then stop

What it does:
1. Wakes the Neon compute and checks the connection (SELECT 1). Credentials are never printed.
2. Checks each service's migration version (read-only). It never applies migrations;
   review and run `alembic upgrade head` yourself when a service is behind.
3. Starts each service with uvicorn on its own port, prefixes its log lines, and waits for /health.
4. Ctrl+C stops everything. If one service crashes, the others are stopped too.

Entry point of each service: services/<name>/app/main.py -> create_app() (uvicorn --factory).
"""

from __future__ import annotations

import argparse
import os
import socket
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
COLOUR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")
RESET = "\033[0m" if COLOUR else ""
BOLD = "\033[1m" if COLOUR else ""


@dataclass(frozen=True)
class Service:
    key: str
    name: str
    schema: str
    default_port: int
    color: str

    @property
    def directory(self) -> Path:
        return ROOT / "services" / self.name


SERVICES = (
    Service("identity", "identity-service", "identity", 8001, "\033[36m" if COLOUR else ""),
    Service("academic", "academic-data-service", "academic", 8002, "\033[35m" if COLOUR else ""),
)


def say(message: str) -> None:
    print(f"{BOLD}[schedence]{RESET} {message}", flush=True)


# --------------------------------------------------------------------------- preflight


def check_database(services: list[Service]) -> bool:
    """SELECT 1 (also wakes a suspended Neon compute) and compare migration versions. Read-only."""
    from alembic.config import Config
    from alembic.script import ScriptDirectory
    from sqlalchemy import text
    from sqlalchemy.pool import NullPool

    from schedence_db import DatabaseConfigurationError, check_database as select_one, create_db_engine, dispose_engine, load_settings
    from schedence_db.runtime import run

    try:
        settings = load_settings()
    except DatabaseConfigurationError as exc:
        say(f"Database configuration problem:\n{exc}")
        return False

    async def probe():
        engine = create_db_engine(settings, poolclass=NullPool, connect_args={"connect_timeout": 30})
        try:
            health = await select_one(engine, timeout=60)
            versions: dict[str, str | None] = {}
            if health.healthy:
                for service in services:
                    async with engine.connect() as conn:
                        try:
                            versions[service.schema] = (await conn.execute(
                                text(f"SELECT version_num FROM {service.schema}.alembic_version"))).scalar_one_or_none()
                        except Exception:
                            versions[service.schema] = None
            return health, versions
        finally:
            await dispose_engine(engine)

    say("Checking Neon (the first connection after idle can take ~10-20 s while the compute wakes)...")
    health, versions = run(probe())
    if not health.healthy:
        say(f"Database: UNHEALTHY - {health.error}")
        return False
    say(f"Database: healthy ({health.latency_ms} ms)")

    for service in services:
        head = ScriptDirectory.from_config(Config(str(service.directory / "alembic.ini"))).get_current_head()
        current = versions.get(service.schema)
        if current == head:
            say(f"  {service.schema} schema: up to date ({head})")
        elif current is None:
            say(f"  {service.schema} schema: NOT MIGRATED - review and run `alembic upgrade head` in services\\{service.name}")
        else:
            say(f"  {service.schema} schema: at {current}, latest is {head} - review and run `alembic upgrade head` "
                f"in services\\{service.name}")
    return True


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        return sock.connect_ex(("127.0.0.1", port)) == 0


# --------------------------------------------------------------------------- processes


class Running:
    def __init__(self, service: Service, port: int, process: subprocess.Popen) -> None:
        self.service, self.port, self.process = service, port, process
        self.recent: list[str] = []
        threading.Thread(target=self._pump, daemon=True).start()

    def _pump(self) -> None:
        prefix = f"{self.service.color}[{self.service.key:<8}]{RESET} "
        for line in self.process.stdout:  # type: ignore[union-attr]
            line = line.rstrip()
            self.recent = (self.recent + [line])[-20:]
            print(prefix + line, flush=True)

    @property
    def alive(self) -> bool:
        return self.process.poll() is None


def start(service: Service, port: int, *, host: str, reload: bool, env_overrides: dict[str, str]) -> Running:
    command = [sys.executable, "-m", "uvicorn", "app.main:create_app", "--factory", "--host", host, "--port", str(port)]
    if os.name == "nt":
        command += ["--loop", "asyncio:SelectorEventLoop"]  # psycopg async needs a selector loop on Windows
    if reload:
        command += ["--reload", "--reload-dir", "app", "--reload-dir", str(ROOT / "packages")]
    env = {**os.environ, "PYTHONUNBUFFERED": "1", **env_overrides}
    process = subprocess.Popen(command, cwd=service.directory, env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               text=True, encoding="utf-8", errors="replace", bufsize=1)
    return Running(service, port, process)


def wait_until_healthy(running: Running, timeout: float = 90) -> bool:
    deadline = time.monotonic() + timeout
    url = f"http://127.0.0.1:{running.port}/health"
    while time.monotonic() < deadline and running.alive:
        try:
            if httpx.get(url, timeout=2).status_code == 200:
                return True
        except httpx.HTTPError:
            pass
        time.sleep(0.5)
    return False


def kill_tree(process: subprocess.Popen) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":  # also stops uvicorn's reload worker
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
    else:
        process.terminate()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        process.kill()


def stop_all(processes: list[Running], *, graceful_seconds: float = 0) -> None:
    deadline = time.monotonic() + graceful_seconds
    while graceful_seconds and time.monotonic() < deadline and any(r.alive for r in processes):
        time.sleep(0.2)
    for running in processes:
        kill_tree(running.process)


# --------------------------------------------------------------------------- main


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python scripts\\run_services.py", description="Run all Schedence backend services.")
    parser.add_argument("--only", choices=[s.key for s in SERVICES], action="append", help="Start only this service (repeatable)")
    parser.add_argument("--reload", action="store_true", help="Restart a service automatically when its code changes")
    parser.add_argument("--host", default="127.0.0.1", help="Bind address (default 127.0.0.1 = this computer only)")
    parser.add_argument("--identity-port", type=int, default=8001)
    parser.add_argument("--academic-port", type=int, default=8002)
    parser.add_argument("--skip-db-check", action="store_true")
    parser.add_argument("--smoke", action="store_true", help="Start, check /health and /health/db, then stop")
    args = parser.parse_args(argv)

    if os.name == "nt":
        os.system("")  # enable ANSI colours in older Windows consoles
    os.chdir(ROOT)  # the root .env is found from here
    services = [s for s in SERVICES if not args.only or s.key in args.only]
    ports = {"identity": args.identity_port, "academic": args.academic_port}

    busy = [f"{s.name} ({ports[s.key]})" for s in services if port_in_use(ports[s.key])]
    if busy:
        say(f"Port already in use: {', '.join(busy)}. Is another copy running? Stop it or choose other ports.")
        return 1
    if not args.skip_db_check and not check_database(services):
        say("Fix the database connection, or use --skip-db-check to start anyway.")
        return 1

    overrides: dict[str, dict[str, str]] = {s.key: {} for s in services}
    if "academic" in overrides and any(s.key == "identity" for s in services):
        overrides["academic"]["IDENTITY_SERVICE_URL"] = f"http://127.0.0.1:{args.identity_port}"

    processes: list[Running] = []
    try:
        for service in services:
            say(f"Starting {service.name} on port {ports[service.key]}...")
            processes.append(start(service, ports[service.key], host=args.host, reload=args.reload,
                                   env_overrides=overrides[service.key]))
        for running in processes:
            if not wait_until_healthy(running):
                say(f"{running.service.name} did not start. Last output:")
                for line in running.recent[-10:]:
                    print("    " + line)
                stop_all(processes)
                return 1

        ok = True
        for running in processes:
            try:
                db = httpx.get(f"http://127.0.0.1:{running.port}/health/db", timeout=60).json()
                state = db.get("status")
            except (httpx.HTTPError, ValueError):
                state = "unreachable"
            ok &= state == "healthy"
            say(f"  {running.service.name:<22} http://localhost:{running.port}/docs   (database: {state})")

        if args.smoke:
            stop_all(processes)
            say("Smoke check " + ("passed." if ok else "FAILED."))
            return 0 if ok else 1

        say("All services are running. Press Ctrl+C to stop.")
        while all(r.alive for r in processes):
            time.sleep(0.5)
        crashed = next(r for r in processes if not r.alive)
        say(f"{crashed.service.name} stopped unexpectedly (exit code {crashed.process.returncode}); stopping the others.")
        stop_all(processes)
        return 1
    except KeyboardInterrupt:
        say("Stopping...")
        stop_all(processes, graceful_seconds=8)  # Ctrl+C also reaches the services, which shut down cleanly
        say("Stopped.")
        return 0


if __name__ == "__main__":
    raise SystemExit(main())
