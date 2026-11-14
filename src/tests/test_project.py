import tempfile
import unittest

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
