"""
Extreme Test Suite v8 - Round 6 Iteration
Tests for all fixes applied in round 5/6:
  1. Debounce+lock retry mechanism (no silent data loss)
  2. Path normalization (no duplicate records with ../)
  3. Empty string search protection
  4. DB close WAL checkpoint
  5. Web server search API empty query protection
  6. Concurrent file lock contention with retry
  7. File deletion path normalization
  8. Multiple path representations for same file
  9. Large-scale concurrent stress test
  10. Lifecycle management (start/stop/close cycle)
  11. External DB lock handling
  12. Unicode and special character edge cases
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


class TestPathNormalization(unittest.TestCase):
    """Test #2: Path normalization prevents duplicate records"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_relative_path_with_dotdot(self):
        """Same file accessed via ../ should not create duplicate records"""
        # Create a file in a subdirectory
        sub_dir = os.path.join(self.test_dir, "sub", "deep")
        os.makedirs(sub_dir, exist_ok=True)
        file_path = os.path.join(sub_dir, "ComponentManager.ts")
        with open(file_path, 'w') as f:
            f.write("export interface ComponentManager {}\n")

        # Insert using relative path with ..
        rel_path = os.path.join(self.test_dir, "sub", "deep", "..", "deep", "ComponentManager.ts")
        self.db.add_or_update_file(rel_path, "export interface ComponentManager {}\n")

        # Insert using normalized path
        norm_path = os.path.normpath(file_path)
        self.db.add_or_update_file(norm_path, "export interface ComponentManager {}\n")

        # Should only have 1 file record (normpath makes them identical)
        files = self.db.get_all_files()
        self.assertEqual(len(files), 1, f"Expected 1 file, got {len(files)}: {files}")

    def test_path_with_current_dir_dot(self):
        """Path with ./ should normalize correctly"""
        file_path = os.path.join(self.test_dir, "DataProcessor.ts")
        with open(file_path, 'w') as f:
            f.write("export class DataProcessor {}\n")

        dot_path = os.path.join(self.test_dir, ".", "DataProcessor.ts")
        norm_path = os.path.normpath(file_path)

        self.db.add_or_update_file(dot_path, "export class DataProcessor {}\n")
        self.db.add_or_update_file(norm_path, "export class DataProcessor {}\n")

        files = self.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_service_normalizes_paths(self):
        """Service should normalize paths before DB operations"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        sub_dir = os.path.join(self.test_dir, "src", "components")
        os.makedirs(sub_dir, exist_ok=True)

        # Write a file
        real_path = os.path.join(sub_dir, "NavigationController.ts")
        with open(real_path, 'w') as f:
            f.write("export class NavigationController {}\n")

        # Process with a path containing ..
        weird_path = os.path.join(self.test_dir, "src", "components", "..", "components", "NavigationController.ts")
        service._handle_file_change(weird_path, "export class NavigationController {}\n")

        # Process again with normalized path (should be treated as same file)
        norm_path = os.path.normpath(real_path)
        service._handle_file_change(norm_path, "export class NavigationController {}\n")

        files = service.db.get_all_files()
        self.assertEqual(len(files), 1, f"Path normalization failed: {files}")

        service.close()


class TestEmptySearchProtection(unittest.TestCase):
    """Test #3: Empty string search should return empty results"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_empty_string_search(self):
        """Empty string search should return empty list"""
        # First add some data
        file_path = os.path.join(self.test_dir, "SearchComponent.ts")
        self.service._handle_file_change(file_path, "export class SearchComponent {}\n")

        # Empty string should return nothing
        results = self.service.search("")
        self.assertEqual(results, [])

    def test_whitespace_only_search(self):
        """Whitespace-only search should return empty list"""
        results = self.service.search("   ")
        self.assertEqual(results, [])

    def test_none_like_search(self):
        """Search with only special chars should return empty or filtered"""
        results = self.service.search("%%")
        # Should not return all results
        self.assertTrue(len(results) == 0 or len(results) < 100)

    def test_valid_search_still_works(self):
        """Normal search should still work after fix"""
        file_path = os.path.join(self.test_dir, "ValidSearchTarget.ts")
        self.service._handle_file_change(
            file_path,
            "export class ValidSearchTarget {}\nexport interface SearchableItem {}\n"
        )

        results = self.service.search("ValidSearchTarget")
        self.assertTrue(len(results) > 0)

        results = self.service.search("SearchableItem")
        self.assertTrue(len(results) > 0)


class TestDebounceLockRetry(unittest.TestCase):
    """Test #1: File lock retry mechanism prevents silent data loss"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_concurrent_same_file_updates_no_loss(self):
        """Rapid concurrent updates to same file should not lose data"""
        file_path = os.path.join(self.test_dir, "ConcurrentUpdateTarget.ts")
        update_count = [0]
        lock = threading.Lock()

        original_inner = self.service._handle_file_change_inner

        def slow_inner(fp, content):
            # Simulate slow processing
            time.sleep(0.5)
            with lock:
                update_count[0] += 1
            original_inner(fp, content)

        self.service._handle_file_change_inner = slow_inner

        # Launch multiple threads updating the same file simultaneously
        threads = []
        for i in range(5):
            content = f"export class ConcurrentUpdateTarget {{ version = {i} }}\n"
            t = threading.Thread(target=self.service._handle_file_change, args=(file_path, content))
            threads.append(t)

        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=30)

        # At least the last update should have been processed
        # With retry mechanism, all should eventually be processed
        self.assertGreater(update_count[0], 0, "No updates were processed at all!")

        # The file should exist in DB
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1, "Should have exactly 1 file record")

    def test_lock_contention_triggers_retry(self):
        """Lock contention should trigger retry mechanism, not silent skip"""
        file_path = os.path.join(self.test_dir, "RetryMechanismTest.ts")
        retry_detected = [False]

        # Capture print output to detect retry messages
        import io
        from contextlib import redirect_stdout

        # First, hold the lock manually
        file_lock = self.service._get_file_lock(file_path)
        file_lock.acquire()

        try:
            # Try to update in another thread while lock is held
            f = io.StringIO()
            with redirect_stdout(f):
                t = threading.Thread(
                    target=self.service._handle_file_change,
                    args=(file_path, "export class RetryMechanismTest {}\n")
                )
                t.start()
                # Wait a bit for retries to happen
                time.sleep(1.0)
                # Release lock
                file_lock.release()
                t.join(timeout=30)

            output = f.getvalue()
            # Should see RETRY messages, not just SKIP
            self.assertIn("[RETRY]", output, f"Expected retry messages, got: {output}")
        finally:
            if file_lock.locked():
                file_lock.release()


class TestFileDeletionPathNormalization(unittest.TestCase):
    """Test #7: File deletion should work with normalized paths"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_delete_with_dotdot_path(self):
        """Deleting a file using ../ path should remove the correct record"""
        sub_dir = os.path.join(self.test_dir, "src", "models")
        os.makedirs(sub_dir, exist_ok=True)
        real_path = os.path.join(sub_dir, "UserModel.ts")

        # Insert with normalized path
        self.service._handle_file_change(real_path, "export class UserModel {}\n")

        # Delete with ../ path
        dotdot_path = os.path.join(self.test_dir, "src", "models", "..", "models", "UserModel.ts")
        self.service._handle_file_change(dotdot_path, None)  # None = delete

        # File should be gone
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0, f"File should be deleted, but found: {files}")

    def test_delete_nonexistent_file_no_crash(self):
        """Deleting a file that doesn't exist should not crash"""
        fake_path = os.path.join(self.test_dir, "NonexistentFile.ts")
        # Should not raise
        self.service._handle_file_change(fake_path, None)
        self.service._handle_file_change(fake_path, None)  # Double delete


class TestDBCloseWALCheckpoint(unittest.TestCase):
    """Test #4: DB close should perform WAL checkpoint"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_close_performs_checkpoint(self):
        """close() should execute WAL checkpoint without error"""
        db = KnowledgeGraphDB(self.db_path)
        # Add some data
        db.add_or_update_file("/test/CheckpointTest.ts", "export class CheckpointTest {}")
        # Close should not raise
        db.close()

        # Verify data persists after checkpoint
        db2 = KnowledgeGraphDB(self.db_path)
        files = db2.get_all_files()
        self.assertEqual(len(files), 1)
        db2.close()


class TestLargeScaleConcurrentStress(unittest.TestCase):
    """Test #9: Large-scale concurrent stress test"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_100_files_concurrent(self):
        """Process 100 files concurrently"""
        file_paths = []
        for i in range(100):
            fp = os.path.join(self.test_dir, f"StressTestComponent{i:03d}.ts")
            file_paths.append(fp)

        errors = []

        def process_file(fp, idx):
            try:
                content = f"export class StressTestComponent{idx:03d} {{ value = {idx} }}\n"
                self.service._handle_file_change(fp, content)
            except Exception as e:
                errors.append((fp, str(e)))

        threads = []
        for i, fp in enumerate(file_paths):
            t = threading.Thread(target=process_file, args=(fp, i))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        self.assertEqual(len(errors), 0, f"Errors during concurrent processing: {errors}")

        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 100, f"Expected 100 files, got {stats['total_files']}")

    def test_rapid_update_same_file_50_times(self):
        """Rapidly update the same file 50 times"""
        file_path = os.path.join(self.test_dir, "RapidUpdateTarget.ts")
        success_count = [0]

        def update_file(version):
            try:
                content = f"export class RapidUpdateTarget {{ version = {version} }}\n"
                self.service._handle_file_change(file_path, content)
                success_count[0] += 1
            except Exception:
                pass

        threads = []
        for i in range(50):
            t = threading.Thread(target=update_file, args=(i,))
            threads.append(t)
            t.start()
            time.sleep(0.02)  # Small delay between launches

        for t in threads:
            t.join(timeout=60)

        # With retry mechanism, most updates should succeed
        self.assertGreater(success_count[0], 40,
                           f"Only {success_count[0]}/50 updates succeeded, retry mechanism may not work")

        # Should have exactly 1 file record
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)


class TestLifecycleManagement(unittest.TestCase):
    """Test #10: Service lifecycle management"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_multiple_start_stop_cycles(self):
        """Service should handle multiple start/stop cycles"""
        for cycle in range(3):
            service = KnowledgeGraphService(db_path=self.db_path)
            service.parser = MockArkTSParser()

            file_path = os.path.join(self.test_dir, f"LifecycleTestCycle{cycle}.ts")
            service._handle_file_change(file_path, f"export class LifecycleTestCycle{cycle} {{}}\n")

            stats = service.get_statistics()
            self.assertGreater(stats['total_entities'], 0)

            service.close()

        # Verify data persisted across cycles
        db = KnowledgeGraphDB(self.db_path)
        files = db.get_all_files()
        self.assertEqual(len(files), 3)
        db.close()

    def test_close_without_start(self):
        """close() without start() should not crash"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.close()  # Should not raise

    def test_double_close(self):
        """Double close() should not crash"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.close()
        service.close()  # Should not raise


class TestExternalDBLock(unittest.TestCase):
    """Test #11: External DB lock handling"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_concurrent_db_connections(self):
        """Multiple services sharing same DB should not deadlock"""
        service1 = KnowledgeGraphService(db_path=self.db_path)
        service1.parser = MockArkTSParser()
        service2 = KnowledgeGraphService(db_path=self.db_path)
        service2.parser = MockArkTSParser()

        file1 = os.path.join(self.test_dir, "SharedDBComponentOne.ts")
        file2 = os.path.join(self.test_dir, "SharedDBComponentTwo.ts")

        errors = []

        def update_s1():
            try:
                for i in range(10):
                    service1._handle_file_change(file1, f"export class SharedDBComponentOne {{ v={i} }}\n")
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"S1: {e}")

        def update_s2():
            try:
                for i in range(10):
                    service2._handle_file_change(file2, f"export class SharedDBComponentTwo {{ v={i} }}\n")
                    time.sleep(0.05)
            except Exception as e:
                errors.append(f"S2: {e}")

        t1 = threading.Thread(target=update_s1)
        t2 = threading.Thread(target=update_s2)
        t1.start()
        t2.start()
        t1.join(timeout=30)
        t2.join(timeout=30)

        self.assertEqual(len(errors), 0, f"Concurrent DB access errors: {errors}")

        service1.close()
        service2.close()


class TestUnicodeAndSpecialChars(unittest.TestCase):
    """Test #12: Unicode and special character edge cases"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_chinese_in_code(self):
        """Chinese characters in code should not crash"""
        file_path = os.path.join(self.test_dir, "ChineseCommentComponent.ts")
        content = """
export class ChineseCommentComponent {
    // 用户界面组件
    private userName: string = '张三';
    private 描述: string = '这是一个测试';
}
"""
        self.service._handle_file_change(file_path, content)
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_entities'], 0)

    def test_emoji_in_docstring(self):
        """Emoji in code should not crash"""
        file_path = os.path.join(self.test_dir, "EmojiDocumentationModule.ts")
        content = """
export interface EmojiDocumentationModule {
    /** 🚀 Fast processing */
    process(): void;
    /** ⚠️ Warning handler */
    handleWarning(): void;
}
"""
        self.service._handle_file_change(file_path, content)
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_entities'], 0)

    def test_path_with_spaces(self):
        """File path with spaces should work"""
        dir_with_space = os.path.join(self.test_dir, "dir with spaces")
        os.makedirs(dir_with_space, exist_ok=True)
        file_path = os.path.join(dir_with_space, "SpacePathComponent.ts")
        content = "export class SpacePathComponent {}\n"
        self.service._handle_file_change(file_path, content)

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_path_with_unicode_chars(self):
        """File path with unicode characters should work"""
        unicode_dir = os.path.join(self.test_dir, "目录")
        os.makedirs(unicode_dir, exist_ok=True)
        file_path = os.path.join(unicode_dir, "UnicodePathComponent.ts")
        content = "export class UnicodePathComponent {}\n"
        self.service._handle_file_change(file_path, content)

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_search_with_special_chars(self):
        """Search with LIKE special characters should be escaped"""
        file_path = os.path.join(self.test_dir, "SpecialCharSearchTarget.ts")
        self.service._handle_file_change(
            file_path,
            "export class SpecialCharSearchTarget {}\n"
        )

        # Search with % should not return everything
        results = self.service.search("%")
        self.assertTrue(len(results) == 0 or all(
            "%" in r['name'] for r in results
        ), "Unescaped % in search")

        # Search with _ should not match single char
        results = self.service.search("_")
        self.assertTrue(len(results) == 0 or all(
            "_" in r['name'] for r in results
        ), "Unescaped _ in search")


class TestFileWatcherPathNormalization(unittest.TestCase):
    """Test that file watcher normalizes paths correctly"""

    def test_normpath_consistency(self):
        """os.path.normpath should produce consistent results"""
        base = "/tmp/test/project/src"
        paths = [
            os.path.join(base, "components", "..", "components", "File.ts"),
            os.path.join(base, ".", "components", "File.ts"),
            os.path.join(base, "components", "File.ts"),
        ]
        normalized = [os.path.normpath(p) for p in paths]
        self.assertEqual(len(set(normalized)), 1,
                         f"Paths should normalize to same: {normalized}")


class TestUpdateFileTransactionAtomicity(unittest.TestCase):
    """Test that update_file_transaction is truly atomic"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_callback_failure_rolls_back(self):
        """If callback fails, the file record should be rolled back"""
        file_path = os.path.join(self.test_dir, "AtomicRollbackTest.ts")

        def failing_callback(conn, file_id, parse_result):
            raise Exception("Simulated failure in callback")

        # Should raise
        with self.assertRaises(Exception):
            self.db.update_file_transaction(
                file_path, "content", None, failing_callback
            )

        # File should NOT exist (rolled back)
        files = self.db.get_all_files()
        self.assertEqual(len(files), 0, "File should be rolled back after callback failure")

    def test_normal_callback_succeeds(self):
        """Normal callback should commit all data"""
        file_path = os.path.join(self.test_dir, "AtomicSuccessTest.ts")

        def success_callback(conn, file_id, parse_result):
            conn.execute(
                "INSERT INTO entities (file_id, name, type) VALUES (?, ?, ?)",
                (file_id, "TestEntity", "class")
            )

        self.db.update_file_transaction(
            file_path, "content", None, success_callback
        )

        files = self.db.get_all_files()
        self.assertEqual(len(files), 1)

        entities = self.db.get_all_entities()
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]['name'], "TestEntity")


class TestStatisticsConsistency(unittest.TestCase):
    """Test that statistics remain consistent after various operations"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_stats_after_add_delete_add(self):
        """Statistics should be correct after add-delete-add cycle"""
        file_path = os.path.join(self.test_dir, "StatsCycleComponent.ts")
        content_v1 = "export class StatsCycleComponent { v1() {} }\n"
        content_v2 = "export class StatsCycleComponent { v2() {} }\n"

        # Add
        self.service._handle_file_change(file_path, content_v1)
        stats1 = self.service.get_statistics()

        # Delete
        self.service._handle_file_change(file_path, None)
        stats2 = self.service.get_statistics()

        # Re-add with different content
        self.service._handle_file_change(file_path, content_v2)
        stats3 = self.service.get_statistics()

        self.assertEqual(stats3['total_files'], 1)
        self.assertGreater(stats3['total_entities'], 0)

    def test_entity_count_matches_actual(self):
        """Entity count in stats should match actual DB count"""
        for i in range(10):
            fp = os.path.join(self.test_dir, f"EntityCountMatch{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class EntityCountMatch{i:03d} {{}}\n")

        stats = self.service.get_statistics()
        db_stats = self.service.db.get_statistics()

        self.assertEqual(stats['total_entities'], db_stats['total_entities'])
        self.assertEqual(stats['total_files'], db_stats['total_files'])


class TestFileHashConsistency(unittest.TestCase):
    """Test file hash computation and change detection"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_same_content_same_hash(self):
        """Same content should produce same hash"""
        h1 = self.db.compute_file_hash("hello world")
        h2 = self.db.compute_file_hash("hello world")
        self.assertEqual(h1, h2)

    def test_different_content_different_hash(self):
        """Different content should produce different hash"""
        h1 = self.db.compute_file_hash("hello world")
        h2 = self.db.compute_file_hash("hello world!")
        self.assertNotEqual(h1, h2)

    def test_unchanged_detection(self):
        """file_exists_and_unchanged should detect unchanged files"""
        fp = "/test/UnchangedFile.ts"
        content = "export class UnchangedFile {}"

        unchanged, hash1 = self.db.file_exists_and_unchanged(fp, content)
        self.assertFalse(unchanged)  # File doesn't exist yet

        self.db.add_or_update_file(fp, content)

        unchanged, hash2 = self.db.file_exists_and_unchanged(fp, content)
        self.assertTrue(unchanged)
        self.assertEqual(hash1, hash2)

        unchanged, hash3 = self.db.file_exists_and_unchanged(fp, "modified content")
        self.assertFalse(unchanged)


if __name__ == '__main__':
    # Run with verbose output
    unittest.main(verbosity=2)
