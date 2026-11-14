import json
import logging
import os
import shutil
import sqlite3
from datetime import datetime, timezone
from uuid import uuid4
from contextlib import contextmanager
from pathlib import Path


DATA_ROOT = Path(os.environ.get("AUDIT_DATA_DIR", Path(__file__).resolve().parents[1] / ".data")).resolve()
logger = logging.getLogger(__name__)


class RevisionConflict(ValueError):
    pass


class Store:
    def __init__(self, root=DATA_ROOT):
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)
        self.database = self.root / "audit.sqlite3"
        with self.connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("CREATE TABLE IF NOT EXISTS cases (id TEXT PRIMARY KEY, revision INTEGER NOT NULL, data TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS jobs (id TEXT PRIMARY KEY, data TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE IF NOT EXISTS document_structure (case_id TEXT NOT NULL, document_id TEXT NOT NULL, "
                "digest TEXT NOT NULL, version INTEGER NOT NULL, corrections TEXT NOT NULL, "
                "PRIMARY KEY (case_id, document_id, digest))"
            )

    def recover_interrupted_jobs(self):
        with self.connect() as connection:
            for row in connection.execute("SELECT id, data FROM jobs"):
                job = json.loads(row["data"])
                if job["status"] in ("queued", "processing"):
                    job.update(status="failed", error="Приложение перезапустилось во время импорта. Повторите загрузку.")
                    connection.execute("UPDATE jobs SET data=? WHERE id=?", (json.dumps(job, ensure_ascii=False), row["id"]))

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database, timeout=30)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def list_cases(self):
        with self.connect() as connection:
            return [json.loads(row["data"]) for row in connection.execute("SELECT data FROM cases ORDER BY rowid DESC")]

    def get_case(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT data FROM cases WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Проверка не найдена")
        return json.loads(row["data"])

    def save_case(self, case, expected_revision=None):
        with self.connect() as connection:
            if expected_revision is None:
                connection.execute("INSERT INTO cases(id, revision, data) VALUES(?,?,?)",
                                   (case["id"], case["revision"], json.dumps(case, ensure_ascii=False)))
            else:
                cursor = connection.execute("UPDATE cases SET revision=?,data=? WHERE id=? AND revision=?",
                                            (case["revision"], json.dumps(case, ensure_ascii=False), case["id"], expected_revision))
                if cursor.rowcount != 1:
                    raise RevisionConflict("Данные уже изменились. Обновите проверку и повторите исправление.")

    def save_job(self, job):
        with self.connect() as connection:
            connection.execute("INSERT INTO jobs(id,data) VALUES(?,?) ON CONFLICT(id) DO UPDATE SET data=excluded.data",
                               (job["id"], json.dumps(job, ensure_ascii=False)))

    def rename_case(self, identifier, name, expected_revision):
        name = name.strip()
        if not name or len(name) > 200:
            raise ValueError("Название должно содержать от 1 до 200 символов")
        case = self.get_case(identifier)
        if case["revision"] != expected_revision:
            raise RevisionConflict("Проект уже изменился. Обновите проверку.")
        if case["name"] == name:
            return case
        previous = case["name"]
        case["name"] = name
        case["revision"] += 1
        case["audit"].append({"id": uuid4().hex, "at": datetime.now(timezone.utc).isoformat(), "kind": "project", "target": identifier,
                              "before": previous, "after": name, "reason": "Переименование проекта", "revision": case["revision"]})
        self.save_case(case, expected_revision=expected_revision)
        return case

    def delete_case(self, identifier, expected_revision):
        directory = self.files_dir(identifier).parent
        removed = self.root / f".deleted-{uuid4().hex}"
        moved = False
        try:
            with self.connect() as connection:
                connection.execute("BEGIN IMMEDIATE")
                row = connection.execute("SELECT revision FROM cases WHERE id=?", (identifier,)).fetchone()
                if row is None:
                    raise KeyError("Проверка не найдена")
                if expected_revision != row["revision"]:
                    raise RevisionConflict("Проект уже изменился. Проверьте его перед удалением.")
                if directory.exists():
                    directory.rename(removed)
                    moved = True
                connection.execute("DELETE FROM document_structure WHERE case_id=?", (identifier,))
                connection.execute("DELETE FROM cases WHERE id=?", (identifier,))
        except BaseException:
            if moved:
                removed.rename(directory)
            raise
        if moved:
            try:
                shutil.rmtree(removed)
            except OSError:
                logger.exception("Could not clean up files of deleted project %s", identifier)

    def get_job(self, identifier):
        with self.connect() as connection:
            row = connection.execute("SELECT data FROM jobs WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise KeyError("Задание не найдено")
        return json.loads(row["data"])

    def files_dir(self, case_id):
        if not case_id.isalnum() or len(case_id) > 40:
            raise ValueError("Некорректный идентификатор проверки")
        return self.root / case_id / "files"
