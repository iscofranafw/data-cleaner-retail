"""Consistent local SQLite backups and offline restoration helpers."""
from datetime import datetime,timezone
from pathlib import Path
import hashlib
import sqlite3
import shutil
import tempfile

class BackupManager:
    def __init__(self, db, backup_dir):
        self.db=db
        self.backup_dir=Path(backup_dir)

    def create(self,reason="manual"):
        if reason not in ("manual","daily","initial","pre_migration"):
            raise ValueError("Alasan backup tidak valid")
        folder=self.backup_dir/reason
        folder.mkdir(parents=True,exist_ok=True)
        stamp=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        path=folder/f"olsera_{stamp}.db"
        try:
            with self.db.connect() as src:
                dst=sqlite3.connect(path)
                try:
                    src.backup(dst)
                finally:
                    dst.close()
            with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as chk:
                if chk.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("Integrity check backup gagal")
            digest=hashlib.sha256(path.read_bytes()).hexdigest()
            with self.db.connect() as con:
                con.execute("INSERT INTO backup_history(path,sha256,size_bytes,reason) VALUES (?,?,?,?)",
                            (str(path),digest,path.stat().st_size,reason))
            return path
        except Exception:
            path.unlink(missing_ok=True)
            raise

    @staticmethod
    def verify_file(path):
        path=Path(path)
        with sqlite3.connect(f"file:{path.as_posix()}?mode=ro",uri=True) as con:
            return con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"

    @staticmethod
    def restore_offline(backup_path, destination):
        """Must ONLY be called after closing Streamlit and all workers. Never exposed as a UI button."""
        backup_path=Path(backup_path)
        destination=Path(destination)
        if not BackupManager.verify_file(backup_path):
            raise ValueError("Backup tidak valid")
        destination.parent.mkdir(parents=True,exist_ok=True)
        with tempfile.NamedTemporaryFile(dir=destination.parent,suffix=".db",delete=False) as f:
            temp=Path(f.name)
        try:
            shutil.copy2(backup_path,temp)
            # Replace only when application and all SQLite connections are stopped.
            temp.replace(destination)
        finally:
            temp.unlink(missing_ok=True)
