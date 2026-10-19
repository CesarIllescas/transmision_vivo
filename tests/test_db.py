"""Database regressions run exclusively against temporary files."""

import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from db.db_conn import (
    DEFAULT_DATABASE_PATH,
    TransmissionRepository,
    connect_sqlite,
    create_table,
    get_transmisiones,
    insert_transmision,
)


class TransmissionRepositoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.database_path = Path(self.temporary_directory.name) / "nested" / "data.db"
        self.repository = TransmissionRepository(self.database_path)

    def test_new_database_loads_empty_keys_and_creates_parent_directory(self):
        self.assertEqual(self.repository.load_keys(), {})
        self.assertTrue(self.database_path.is_file())

    def test_default_path_is_anchored_to_project(self):
        expected = Path(__file__).resolve().parent.parent / "statics" / "data.db"
        self.assertEqual(DEFAULT_DATABASE_PATH, expected)
        self.assertEqual(TransmissionRepository().database_path, expected)

    def test_save_replaces_selected_keys_and_preserves_other_platforms(self):
        self.repository.save_keys({"Youtube": "first", "Facebook": "retained"})
        self.repository.save_keys({"Youtube": "updated"})

        self.assertEqual(
            TransmissionRepository(self.database_path).load_keys(),
            {"Youtube": "updated", "Facebook": "retained"},
        )
        with closing(sqlite3.connect(self.database_path)) as connection:
            row = connection.execute("SELECT COUNT(*) FROM transmisiones").fetchone()
        self.assertEqual(row[0], 2)

    def test_save_treats_platforms_and_keys_as_data(self):
        keys = {"Platform'); DROP TABLE transmisiones; --": "a'quoted;key"}
        self.repository.save_keys(keys)
        self.assertEqual(self.repository.load_keys(), keys)

    def test_failed_save_rolls_back_every_key(self):
        original = {"Youtube": "first", "Facebook": "retained"}
        self.repository.save_keys(original)

        with self.assertRaises(sqlite3.IntegrityError):
            self.repository.save_keys({"Youtube": "updated", "Facebook": None})

        self.assertEqual(self.repository.load_keys(), original)

    def test_reads_existing_schema_and_favors_last_duplicate(self):
        connection, cursor = connect_sqlite(self.database_path)
        with closing(connection), connection:
            create_table(cursor)
            cursor.executemany(
                "INSERT INTO transmisiones (plataforma, key) VALUES (?, ?)",
                [("Youtube", "old"), ("Youtube", "new"), ("Facebook", "saved")],
            )

        self.assertEqual(self.repository.load_keys(), {"Youtube": "new", "Facebook": "saved"})

    def test_connections_close_after_reads_writes_and_failures(self):
        original_connect = sqlite3.connect
        connections = []

        def track_connection(*args, **kwargs):
            connection = original_connect(*args, **kwargs)
            connections.append(connection)
            return connection

        with patch("db.db_conn.sqlite3.connect", side_effect=track_connection):
            self.repository.load_keys()
            self.repository.save_keys({"Youtube": "saved"})
            with self.assertRaises(sqlite3.IntegrityError):
                self.repository.save_keys({"Youtube": None})

        self.assertEqual(len(connections), 3)
        for connection in connections:
            with self.assertRaises(sqlite3.ProgrammingError):
                connection.execute("SELECT 1")

    def test_legacy_helpers_keep_original_row_shape(self):
        connection, cursor = connect_sqlite(self.database_path)
        with closing(connection), connection:
            create_table(cursor)
            insert_transmision(cursor, "Youtube", "old")
            insert_transmision(cursor, "Youtube", "new")
            rows = get_transmisiones(cursor)

        self.assertEqual(len(rows), 1)
        self.assertIsInstance(rows[0][0], int)
        self.assertEqual(rows[0][1:], ("Youtube", "new"))


if __name__ == "__main__":
    unittest.main()
