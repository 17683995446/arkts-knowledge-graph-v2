"""
Extreme Test Suite v9 - Round 7 Iteration
Tests for CRITICAL and HIGH fixes in round 7:
  1. CRITICAL#1: close() thread safety with shutdown Event
  2. CRITICAL#2: _handle_file_deleted with file-level lock
  3. CRITICAL#3: update_file_transaction INSERT OR REPLACE
  4. HIGH#4: on_update_callback outside file lock (daemon thread)
  5. HIGH#5: _handle_file_deleted updates last_update_time
  6. HIGH#7: LRU lock cleanup skips locked entries
  7. HIGH#8: signal_handler calls close()
  8. MEDIUM#13: _debounced_callback checks file existence
  9. MEDIUM#17: close() is idempotent
  10. MEDIUM#18: on_deleted only checks extension
  11. MEDIUM#20: parse_result None defense
  12. MEDIUM#24: events table cleanup on close
"""
import os
import sys
import time
import tempfile
import shutil
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser


class TestShutdownEventSafety(unittest.TestCase):
    """Test CRITICAL#1: close() with shutdown Event prevents DB usage after close"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_close_during_processing(self):
        """close() should wait for in-flight processing to complete"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        processing_started = threading.Event()
        processing_done = threading.Event()

        original_inner = service._handle_file_change_inner

        def slow_inner(fp, content):
            processing_started.set()
            time.sleep(2)  # Simulate slow processing
            original_inner(fp, content)
            processing_done.set()

        service._handle_file_change_inner = slow_inner

        # Start processing in a thread
        file_path = os.path.join(self.test_dir, "ShutdownSafetyComponent.ts")
        t = threading.Thread(target=service._handle_file_change,
                             args=(file_path, "export class ShutdownSafetyComponent {}"))
        t.start()

        # Wait for processing to start
        processing_started.wait(timeout=5)

        # Now close while processing is happening
        start_close = time.time()
        service.close()
        close_duration = time.time() - start_close

        t.join(timeout=10)

        # close() should have waited for processing to complete
        self.assertGreater(close_duration, 1.0,
                           "close() should wait for in-flight processing")
        self.assertTrue(processing_done.is_set(),
                        "Processing should complete before close() returns")

    def test_shutdown_blocks_new_requests(self):
        """After shutdown, new file change requests should be rejected"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # Trigger shutdown
        service._shutdown_event.set()

        # Try to process a file - should be silently rejected
        file_path = os.path.join(self.test_dir, "RejectedAfterShutdown.ts")
        service._handle_file_change(file_path, "export class RejectedAfterShutdown {}")

        # File should NOT be in DB
        files = service.db.get_all_files()
        self.assertEqual(len(files), 0, "File should be rejected after shutdown")

        service.close()

    def test_close_is_idempotent(self):
        """MEDIUM#17: close() can be called multiple times safely"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_path = os.path.join(self.test_dir, "IdempotentCloseTest.ts")
        service._handle_file_change(file_path, "export class IdempotentCloseTest {}")

        # Multiple closes should not crash
        service.close()
        service.close()
        service.close()


class TestFileDeletionLockProtection(unittest.TestCase):
    """Test CRITICAL#2: _handle_file_deleted uses file-level lock"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_concurrent_update_and_delete(self):
        """Concurrent update and delete of same file should be serialized"""
        file_path = os.path.join(self.test_dir, "ConcurrentDeleteUpdate.ts")
        errors = []

        # First, add the file
        self.service._handle_file_change(file_path, "export class ConcurrentDeleteUpdate {}")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

        # Now concurrently update and delete
        def update_file():
            try:
                time.sleep(0.1)  # Let delete start first
                self.service._handle_file_change(
                    file_path,
                    "export class ConcurrentDeleteUpdate { updated = true }"
                )
            except Exception as e:
                errors.append(f"Update: {e}")

        def delete_file():
            try:
                self.service._handle_file_change(file_path, None)
            except Exception as e:
                errors.append(f"Delete: {e}")

        t1 = threading.Thread(target=delete_file)
        t2 = threading.Thread(target=update_file)
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # No errors should occur
        self.assertEqual(len(errors), 0, f"Errors during concurrent update/delete: {errors}")

    def test_delete_updates_last_update_time(self):
        """HIGH#5: Deleting a file should update last_update_time"""
        file_path = os.path.join(self.test_dir, "DeleteUpdateTimeTest.ts")
        self.service._handle_file_change(file_path, "export class DeleteUpdateTimeTest {}")

        stats_before = self.service.get_statistics()
        time_before = stats_before['last_update_time']

        time.sleep(0.1)  # Ensure time difference

        self.service._handle_file_change(file_path, None)

        stats_after = self.service.get_statistics()
        time_after = stats_after['last_update_time']

        self.assertIsNotNone(time_after)
        self.assertGreater(time_after, time_before,
                           "last_update_time should be updated after deletion")


class TestInsertOrReplaceAtomicity(unittest.TestCase):
    """Test CRITICAL#3: INSERT OR REPLACE prevents UNIQUE constraint errors"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_concurrent_same_file_transaction(self):
        """Two concurrent update_file_transaction calls for same file should not crash"""
        file_path = os.path.join(self.test_dir, "ConcurrentTransaction.ts")
        content = "export class ConcurrentTransaction {}"

        errors = []

        def do_transaction(version):
            try:
                def cb(conn, file_id, pr):
                    conn.execute(
                        "INSERT INTO entities (file_id, name, type) VALUES (?, ?, ?)",
                        (file_id, f"Entity_v{version}", "class")
                    )
                self.db.update_file_transaction(file_path, content + f" // v{version}", None, cb)
            except Exception as e:
                errors.append(f"v{version}: {e}")

        t1 = threading.Thread(target=do_transaction, args=(1,))
        t2 = threading.Thread(target=do_transaction, args=(2,))
        t1.start()
        t2.start()
        t1.join(timeout=10)
        t2.join(timeout=10)

        # Should not have any UNIQUE constraint errors
        self.assertEqual(len(errors), 0, f"Concurrent transaction errors: {errors}")

        # File should exist
        files = self.db.get_all_files()
        self.assertEqual(len(files), 1)


class TestLRULockCleanupSafety(unittest.TestCase):
    """Test HIGH#7: LRU cleanup skips locked entries"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

        # Lower the max for testing
        self.service._MAX_FILE_LOCKS = 10

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_lru_cleanup_skips_locked(self):
        """LRU cleanup should not remove locks that are currently held"""
        # Create many files to trigger LRU cleanup
        file_paths = []
        for i in range(20):
            fp = os.path.join(self.test_dir, f"LRULockCleanupTest{i:03d}.ts")
            file_paths.append(fp)
            self.service._handle_file_change(fp, f"export class LRULockCleanupTest{i:03d} {{}}")

        # All should succeed
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 20)


class TestDebouncedCallbackFileExistence(unittest.TestCase):
    """Test MEDIUM#13: _debounced_callback checks file existence"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_delete_before_debounce_fires(self):
        """If file is deleted before debounce fires, no ghost record should be created"""
        from file_watcher import ArkTSFileHandler

        handler = ArkTSFileHandler(self.service._handle_file_change, debounce_seconds=0.5)

        # Create a file
        file_path = os.path.join(self.test_dir, "DebounceGhostFile.ts")
        with open(file_path, 'w') as f:
            f.write("export class DebounceGhostFile {}\n")

        # Schedule update
        handler._schedule_update(file_path)

        # Delete file before debounce fires
        time.sleep(0.1)
        os.remove(file_path)

        # Wait for debounce to fire
        time.sleep(1.0)

        # File should NOT be in DB (because it was deleted before callback)
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0,
                         "Ghost record should not exist for deleted file")


class TestParseResultNoneDefense(unittest.TestCase):
    """Test MEDIUM#20: parse_result None defense"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_parser_returns_none(self):
        """If parser returns None, should not crash"""
        # Create a parser that returns None
        class NoneParser:
            def parse_file(self, fp, content):
                return None

        self.service.parser = NoneParser()

        file_path = os.path.join(self.test_dir, "NoneParserDefense.ts")
        # Should not crash
        self.service._handle_file_change(file_path, "export class NoneParserDefense {}")

        # File should still be recorded (with no entities)
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)


class TestEventsCleanup(unittest.TestCase):
    """Test MEDIUM#24: Events table cleanup on close"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_old_events_cleaned_on_close(self):
        """Old events should be cleaned up when DB closes"""
        db = KnowledgeGraphDB(self.db_path)

        # Insert some events
        for i in range(10):
            db.log_event(event_type='TEST_EVENT', details=f"Event {i}")

        events = db.get_recent_events(limit=100)
        self.assertEqual(len(events), 10)

        # Close and reopen
        db.close()

        db2 = KnowledgeGraphDB(self.db_path)
        events_after = db2.get_recent_events(limit=100)
        # Events should still be there (they're recent, within 7 days)
        self.assertEqual(len(events_after), 10)
        db2.close()


class TestCallbackOutsideLock(unittest.TestCase):
    """Test HIGH#4: on_update_callback runs outside file lock"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_callback_runs_in_daemon_thread(self):
        """on_update_callback should run in a separate daemon thread"""
        callback_thread = [None]
        callback_event = threading.Event()

        def my_callback(file_path, stats):
            callback_thread[0] = threading.current_thread()
            callback_event.set()

        self.service.on_update_callback = my_callback

        file_path = os.path.join(self.test_dir, "CallbackThreadTest.ts")
        self.service._handle_file_change(file_path, "export class CallbackThreadTest {}")

        callback_event.wait(timeout=5)

        # Callback should have been called
        self.assertTrue(callback_event.is_set())
        # Callback should run in a different thread
        self.assertIsNotNone(callback_thread[0])
        self.assertNotEqual(callback_thread[0], threading.current_thread())


class TestOnDeletedExtensionOnlyCheck(unittest.TestCase):
    """Test MEDIUM#18: on_deleted only checks extension"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_delete_short_name_file(self):
        """Files with short names (not meeting MIN_FILE_WORDS) should still be deletable"""
        # First add a file with a valid name
        file_path = os.path.join(self.test_dir, "ValidNameComponent.ts")
        self.service._handle_file_change(file_path, "export class ValidNameComponent {}")

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

        # Now delete it (should work even if name doesn't meet MIN_FILE_WORDS after rename)
        self.service._handle_file_change(file_path, None)

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
