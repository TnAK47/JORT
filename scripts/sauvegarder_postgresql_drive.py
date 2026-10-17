"""Sauvegarde vérifiable de la base PostgreSQL JORT sur Google Drive.

L'archive ne contient pas les PDF : ceux-ci restent dans leur arborescence
Drive. Le format pg_dump personnalisé est compact et importable sur une
nouvelle installation JORT avec restaurer_postgresql.py.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

from postgresql_local import BIN, CLUSTER, ROOT, connect, run, secret, start, stop


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def postgresql_running() -> bool:
    return run("pg_ctl.exe", "-D", CLUSTER, "status", check=False).returncode == 0


def postgres_environment() -> dict[str, str]:
    config = secret(ROOT)
    return dict(
        os.environ,
        PGHOST="127.0.0.1",
        PGPORT=str(config["port"]),
        PGUSER=config["roles"]["owner"]["user"],
        PGPASSWORD=config["roles"]["owner"]["password"],
        PGDATABASE="jort",
        PGAPPNAME="JO-JORT-BACKUP",
    )


def run_backup(destination: Path) -> None:
    partial = destination.with_suffix(destination.suffix + ".partial")
    partial.unlink(missing_ok=True)
    try:
        result = subprocess.run(
            [str(BIN / "pg_dump.exe"), "-Fc", "--no-owner", "--no-privileges", "-f", str(partial)],
            env=postgres_environment(), capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if result.returncode:
            raise RuntimeError("pg_dump a échoué : " + (result.stderr or result.stdout)[-1500:])
        checked = subprocess.run(
            [str(BIN / "pg_restore.exe"), "--list", str(partial)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if checked.returncode:
            raise RuntimeError("Archive PostgreSQL illisible : " + (checked.stderr or checked.stdout)[-1500:])
        os.replace(partial, destination)
    finally:
        partial.unlink(missing_ok=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Sauvegarde PostgreSQL JORT dans Google Drive")
    parser.add_argument("--drive-root", default=os.environ.get("JORT_GOOGLE_DRIVE_ROOT", r"G:\\Mon Drive\\JORT"))
    args = parser.parse_args()
    drive_root = Path(args.drive_root).expanduser()
    if not drive_root.is_dir():
        raise RuntimeError(f"Drive inaccessible : {drive_root}. Vérifiez Google Drive et les droits de votre session Windows.")
    destination_dir = drive_root / "sauvegardes-base" / "postgresql"
    destination_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().astimezone().strftime("%Y%m%d-%H%M%S")
    destination = destination_dir / f"jort-postgresql-{stamp}.dump"
    if destination.exists():
        raise RuntimeError(f"La sauvegarde existe déjà : {destination}")

    was_running = postgresql_running()
    try:
        if not was_running:
            start()
        with connect(ROOT, role="reader") as db:
            summary = db.execute(
                "SELECT pg_database_size(current_database()) AS database_bytes, "
                "(SELECT count(*) FROM jort_announcements) AS announcements, "
                "(SELECT count(*) FROM jort_entities) AS entities"
            ).fetchone()
        run_backup(destination)
        manifest = {
            "format": "pg_dump custom", "database": "jort",
            "created_at": datetime.now(timezone.utc).isoformat(), "file": destination.name,
            "bytes": destination.stat().st_size, "sha256": sha256(destination),
            "source": dict(summary), "restore_script": "restaurer-base-postgresql.ps1",
        }
        manifest_path = destination.with_suffix(".json")
        temporary_manifest = manifest_path.with_suffix(".json.partial")
        temporary_manifest.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        os.replace(temporary_manifest, manifest_path)
        print(json.dumps({"state": "ok", "backup": str(destination), "manifest": str(manifest_path), **manifest}, ensure_ascii=False))
    finally:
        if not was_running:
            stop()
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        print(json.dumps({"state": "error", "message": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
