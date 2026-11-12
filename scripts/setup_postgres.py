import os
import pathlib
import subprocess
import sys
import time
import urllib.request
import zipfile

ROOT_DIR = pathlib.Path(__file__).resolve().parent.parent
PGSQL_DIR = ROOT_DIR / ".pgsql"
ZIP_PATH = PGSQL_DIR / "pg16.zip"
PG_BIN_DIR = PGSQL_DIR / "pgsql" / "bin"
DATA_DIR = PGSQL_DIR / "data"
LOG_FILE = PGSQL_DIR / "pg.log"

DOWNLOAD_URL = "https://get.enterprisedb.com/postgresql/postgresql-16.8-1-windows-x64-binaries.zip"

def ensure_postgres_installed():
    PGSQL_DIR.mkdir(parents=True, exist_ok=True)
    if not (PG_BIN_DIR / "postgres.exe").exists():
        if not ZIP_PATH.exists():
            print(f"Downloading PostgreSQL 16 from {DOWNLOAD_URL}...")
            req = urllib.request.Request(DOWNLOAD_URL, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req) as resp, open(ZIP_PATH, "wb") as f:
                total = int(resp.headers.get("Content-Length", 0))
                downloaded = 0
                while True:
                    chunk = resp.read(1024 * 1024)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total > 0:
                        pct = int(downloaded * 100 / total)
                        sys.stdout.write(f"\rDownloading: {pct}% ({downloaded // (1024 * 1024)}MB / {total // (1024 * 1024)}MB)")
                        sys.stdout.flush()
            print("\nDownload complete.")
        print("Extracting PostgreSQL 16 archive...")
        with zipfile.ZipFile(ZIP_PATH, "r") as z:
            z.extractall(PGSQL_DIR)
        print("Extraction complete.")

def ensure_cluster_initialized():
    if not (DATA_DIR / "PG_VERSION").exists():
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        initdb = PG_BIN_DIR / "initdb.exe"
        cmd = [
            str(initdb),
            "-D", str(DATA_DIR),
            "-U", "postgres",
            "-A", "trust",
            "-E", "UTF8",
        ]
        print("Initializing PostgreSQL cluster...")
        subprocess.run(cmd, check=True)

def ensure_server_running():
    pg_ctl = PG_BIN_DIR / "pg_ctl.exe"
    status_cmd = [str(pg_ctl), "-D", str(DATA_DIR), "status"]
    res = subprocess.run(status_cmd, capture_output=True, text=True)
    if "is running" not in res.stdout:
        print("Starting PostgreSQL server...")
        start_cmd = [str(pg_ctl), "-D", str(DATA_DIR), "-l", str(LOG_FILE), "start"]
        subprocess.run(start_cmd, check=True)
        time.sleep(2)

def ensure_database_exists():
    createdb = PG_BIN_DIR / "createdb.exe"
    psql = PG_BIN_DIR / "psql.exe"
    check_cmd = [
        str(psql),
        "-h", "127.0.0.1",
        "-p", "5432",
        "-U", "postgres",
        "-d", "postgres",
        "-tAc", "SELECT 1 FROM pg_database WHERE datname='paypal_handshake'",
    ]
    res = subprocess.run(check_cmd, capture_output=True, text=True)
    if "1" not in res.stdout.strip():
        print("Creating target database 'paypal_handshake'...")
        create_cmd = [
            str(createdb),
            "-h", "127.0.0.1",
            "-p", "5432",
            "-U", "postgres",
            "paypal_handshake",
        ]
        subprocess.run(create_cmd, check=True)
    print("Database 'paypal_handshake' ready.")

if __name__ == "__main__":
    ensure_postgres_installed()
    ensure_cluster_initialized()
    ensure_server_running()
    ensure_database_exists()
