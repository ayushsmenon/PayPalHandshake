import argparse
import os
import pathlib
import signal
import subprocess
import sys
import time
import urllib.request
from sqlalchemy import create_engine, text

REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent
PYTHON_EXE = sys.executable

PG_BIN = REPO_ROOT / ".pgsql" / "pgsql" / "bin"
PG_DATA = REPO_ROOT / ".pgsql" / "data"
DATABASE_URL = os.environ.get(
    "DATABASE_URL",
    "postgresql+psycopg://postgres@127.0.0.1:5432/paypal_handshake"
)

NODEJS_DIR = pathlib.Path(r"C:\Program Files\nodejs")

def ensure_postgres_running():
    engine = create_engine(DATABASE_URL)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return
    except Exception:
        pass

    if (PG_BIN / "postgres.exe").exists() and (PG_DATA / "PG_VERSION").exists():
        pg_ctl = PG_BIN / "pg_ctl.exe"
        cmd = [str(pg_ctl), "-D", str(PG_DATA), "start"]
        subprocess.run(cmd, check=False)
        for _ in range(20):
            time.sleep(0.5)
            try:
                with engine.connect() as conn:
                    conn.execute(text("SELECT 1"))
                return
            except Exception:
                pass
    raise RuntimeError("Could not establish connection to PostgreSQL at " + DATABASE_URL)

def run_migrations():
    engine = create_engine(DATABASE_URL)
    migration_files = [
        REPO_ROOT / "migrations" / "001_initial_schema.sql",
        REPO_ROOT / "migrations" / "002_protocol_reconcile.sql",
        REPO_ROOT / "migrations" / "003_durable_consent_and_v2_reconcile.sql",
    ]
    with engine.connect() as conn:
        with conn.begin():
            for m_path in migration_files:
                if m_path.exists():
                    sql = m_path.read_text(encoding="utf-8")
                    conn.execute(text(sql))

def poll_health(url: str, timeout: float = 25.0) -> bool:
    start = time.time()
    req = urllib.request.Request(url, headers={"User-Agent": "HealthChecker/1.0"})
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(req, timeout=2.0) as resp:
                if resp.status in (200, 304):
                    return True
        except Exception:
            pass
        time.sleep(0.5)
    return False

def free_port(port: int):
    if os.name == "nt":
        cmd = f"Get-NetTCPConnection -LocalPort {port} -State Listen -ErrorAction SilentlyContinue | ForEach-Object {{ Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }}"
        subprocess.run(["powershell", "-NoProfile", "-Command", cmd], check=False, capture_output=True)

def start_services(run_frontend: bool = True):
    for p in (8001, 8002, 8003, 3000):
        free_port(p)
    time.sleep(1)

    processes = []
    env_base = os.environ.copy()
    env_base["PYTHONPATH"] = str(REPO_ROOT)
    env_base["DATABASE_URL"] = DATABASE_URL
    if NODEJS_DIR.exists():
        env_base["PATH"] = str(NODEJS_DIR) + os.pathsep + env_base.get("PATH", "")

    b_dir = REPO_ROOT / "services" / "mandate-gate"
    b_cmd = [PYTHON_EXE, "-m", "uvicorn", "mandate_gate.api:app", "--host", "127.0.0.1", "--port", "8002"]
    proc_b = subprocess.Popen(b_cmd, cwd=str(b_dir), env=env_base)
    processes.append(("Process B (Mandate Gate)", proc_b, "http://127.0.0.1:8002/health", "8002"))

    c_dir = REPO_ROOT / "services" / "payment-policy-svc"
    c_cmd = [PYTHON_EXE, "-m", "uvicorn", "payment_policy.api:app", "--host", "127.0.0.1", "--port", "8003"]
    proc_c = subprocess.Popen(c_cmd, cwd=str(c_dir), env=env_base)
    processes.append(("Process C (Payment Policy)", proc_c, "http://127.0.0.1:8003/health", "8003"))

    a_dir = REPO_ROOT / "services" / "agent-orchestrator"
    a_cmd = [PYTHON_EXE, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", "8001"]
    proc_a = subprocess.Popen(a_cmd, cwd=str(a_dir), env=env_base)
    processes.append(("Process A (Agent Orchestrator)", proc_a, "http://127.0.0.1:8001/health", "8001"))

    if run_frontend:
        f_dir = REPO_ROOT / "frontend"
        npm_cmd = str(NODEJS_DIR / "npm.cmd") if (NODEJS_DIR / "npm.cmd").exists() else "npm"
        f_cmd = [npm_cmd, "start", "--", "-p", "3000"]
        proc_f = subprocess.Popen(f_cmd, cwd=str(f_dir), env=env_base, shell=(os.name == "nt"))
        processes.append(("Frontend (Next.js)", proc_f, "http://127.0.0.1:3000/", "3000"))

    return processes

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--health-check-only", action="store_true")
    parser.add_argument("--no-frontend", action="store_true")
    args = parser.parse_args()

    print("=" * 60)
    print("PAYPAL HANDSHAKE: UNIFIED DEV STACK RUNNER")
    print("=" * 60)

    print("Verifying PostgreSQL connectivity...")
    ensure_postgres_running()
    print("PostgreSQL is running and accepting connections.")

    print("Executing database schema migrations (001-003)...")
    run_migrations()
    print("Migrations reconciled successfully.")

    print("Launching service stack...")
    processes = start_services(run_frontend=not args.no_frontend)

    all_healthy = True
    print("\nVerifying health endpoints on all services:")
    for name, proc, health_url, port in processes:
        ok = poll_health(health_url)
        if ok:
            print(f"  [HEALTHY] {name:<30} -> {health_url}")
        else:
            print(f"  [UNHEALTHY] {name:<28} -> {health_url}")
            all_healthy = False

    if not all_healthy:
        print("\nOne or more services failed health check.")
        for _, proc, _, _ in processes:
            proc.terminate()
        sys.exit(1)

    print("\nAll services are up and healthy!")
    print("  - Frontend:               http://127.0.0.1:3000")
    print("  - Process A (Orchestrator): http://127.0.0.1:8001/docs")
    print("  - Process B (Mandate Gate): http://127.0.0.1:8002/docs")
    print("  - Process C (Payment Policy): http://127.0.0.1:8003/docs")
    print("=" * 60)

    if args.health_check_only:
        print("Health check only mode requested. Terminating background services.")
        for _, proc, _, _ in processes:
            proc.terminate()
        sys.exit(0)

    print("Press Ctrl+C to terminate all services.")
    try:
        while True:
            time.sleep(1)
            for name, proc, _, _ in processes:
                if proc.poll() is not None:
                    print(f"Service {name} exited unexpectedly with code {proc.returncode}.")
                    raise KeyboardInterrupt
    except KeyboardInterrupt:
        print("\nShutting down service stack...")
        for name, proc, _, _ in processes:
            proc.terminate()
        print("All processes terminated cleanly.")

if __name__ == "__main__":
    main()
