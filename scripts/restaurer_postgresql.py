"""Restaure une sauvegarde PostgreSQL JORT après une confirmation explicite."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys

from postgresql_local import BIN, CLUSTER, ROOT, connect, run, secret, start, stop


def postgresql_running() -> bool:
    return run("pg_ctl.exe", "-D", CLUSTER, "status", check=False).returncode == 0


def application_running() -> bool:
    with socket.socket() as probe:
        probe.settimeout(0.5)
        return probe.connect_ex(("127.0.0.1", 8765)) == 0


def environment() -> dict[str, str]:
    config = secret(ROOT)
    return dict(
        os.environ,
        PGHOST="127.0.0.1", PGPORT=str(config["port"]),
        PGUSER=config["roles"]["owner"]["user"], PGPASSWORD=config["roles"]["owner"]["password"],
        PGDATABASE="jort", PGAPPNAME="JO-JORT-RESTORE",
    )


def verify_archive(archive: Path) -> None:
    if not archive.is_file() or not archive.stat().st_size:
        raise RuntimeError(f"Archive introuvable ou vide : {archive}")
    result = subprocess.run(
        [str(BIN / "pg_restore.exe"), "--list", str(archive)],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("Archive PostgreSQL illisible : " + (result.stderr or result.stdout)[-1500:])


def terminate_clients() -> None:
    with connect(ROOT, role="owner", database="postgres") as db:
        db.autocommit = True
        db.execute(
            "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
            "WHERE datname='jort' AND pid <> pg_backend_pid()"
        )


def restore(archive: Path) -> None:
    result = subprocess.run(
        [str(BIN / "pg_restore.exe"), "--clean", "--if-exists", "--no-owner", "--no-privileges", "--exit-on-error", "-d", "jort", str(archive)],
        env=environment(), capture_output=True, text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode:
        raise RuntimeError("Restauration PostgreSQL échouée : " + (result.stderr or result.stdout)[-1500:])


def reapply_access_rights() -> dict:
    with connect(ROOT, role="owner") as db:
        db.execute("REVOKE CREATE ON SCHEMA public FROM PUBLIC")
        db.execute("GRANT USAGE ON SCHEMA public TO jort_reader,jort_writer")
        db.execute("GRANT SELECT ON ALL TABLES IN SCHEMA public TO jort_reader")
        db.execute("GRANT SELECT,INSERT,UPDATE,DELETE ON ALL TABLES IN SCHEMA public TO jort_writer")
        db.execute("GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO jort_writer")
        return {
            "announcements": db.execute("SELECT count(*) AS n FROM jort_announcements").fetchone()["n"],
            "entities": db.execute("SELECT count(*) AS n FROM jort_entities").fetchone()["n"],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description="Restauration complète de la base PostgreSQL JORT")
    parser.add_argument("--backup", required=True, help="Chemin du fichier .dump créé par la sauvegarde JORT")
    parser.add_argument("--replace", action="store_true", help="Autorise le remplacement complet de la base locale")
    args = parser.parse_args()
    archive = Path(args.backup).expanduser()
    if not args.replace:
        raise RuntimeError("Ajoutez --replace pour confirmer le remplacement de la base locale.")
    if application_running():
        raise RuntimeError("Arrêtez JORT avant la restauration : le site ou un traitement utilise encore la base.")
    verify_archive(archive)

    was_running = postgresql_running()
    try:
        if not was_running:
            start()
        terminate_clients()
        restore(archive)
        counts = reapply_access_rights()
        print(json.dumps({"state": "ok", "backup": str(archive), "counts": counts}, ensure_ascii=False))
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
