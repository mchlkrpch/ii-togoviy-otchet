import tempfile
import unittest
from pathlib import Path

from backend.store import Store, RevisionConflict


class ProjectTests(unittest.TestCase):
    def test_rename_persists_history_and_rejects_stale_revision(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            store.save_case({"id": "project", "name": "Исходное имя", "revision": 1, "audit": [], "documents": [{"id": "source"}]})
            renamed = store.rename_case("project", "  Проверка ноября  ", 1)
            reopened = Store(directory).get_case("project")
            self.assertEqual(reopened["name"], "Проверка ноября")
            self.assertEqual(reopened["documents"], [{"id": "source"}])
            self.assertEqual(reopened["revision"], 2)
            self.assertEqual(renamed["audit"][-1]["before"], "Исходное имя")
            with self.assertRaises(RevisionConflict):
                store.rename_case("project", "Старая вкладка", 1)
            with self.assertRaises(ValueError):
                store.rename_case("project", "  ", 2)
            self.assertEqual(Store(directory).get_case("project")["name"], "Проверка ноября")

    def test_delete_checks_revision_and_removes_only_the_projects_data(self):
        with tempfile.TemporaryDirectory() as directory:
            store = Store(directory)
            for project in ("project", "other"):
                store.save_case({"id": project, "revision": 2})
                files = store.files_dir(project)
                files.mkdir(parents=True)
                (files / "report.pdf").write_bytes(b"uploaded copy")
                with store.connect() as connection:
                    connection.execute("INSERT INTO document_structure VALUES (?, 'pdf', 'digest', 1, '[]')", (project,))

            with self.assertRaises(RevisionConflict):
                store.delete_case("project", 1)
            self.assertTrue((store.files_dir("project") / "report.pdf").is_file())
            self.assertEqual(store.get_case("project")["revision"], 2)

            store.delete_case("project", 2)
            with self.assertRaises(KeyError):
                Store(directory).get_case("project")
            with self.assertRaises(KeyError):
                store.delete_case("project", 2)
            with self.assertRaises(ValueError):
                store.delete_case("../other", 2)
            self.assertFalse(store.files_dir("project").parent.exists())
            self.assertFalse(list(Path(directory).glob(".deleted-*")))
            self.assertTrue((store.files_dir("other") / "report.pdf").is_file())
            self.assertEqual(store.get_case("other")["revision"], 2)
            with store.connect() as connection:
                self.assertEqual([row[0] for row in connection.execute("SELECT case_id FROM document_structure")], ["other"])
