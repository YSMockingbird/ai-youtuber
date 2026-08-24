from pathlib import Path


def secure_sqlite_storage(database_path):
    database_path = Path(database_path)
    database_path.parent.mkdir(parents=True, exist_ok=True)
    database_path.parent.chmod(0o700)
    for path in (
        database_path,
        Path(f"{database_path}-wal"),
        Path(f"{database_path}-shm"),
    ):
        if path.exists():
            path.chmod(0o600)
