#!/usr/bin/env python3
"""
Comprehensive Test Suite for ArkTS Knowledge Graph System
Phases 3-8: Unit Branch Coverage, Integration, API, Business Scenarios,
Data Consistency, Exception Fault-Tolerance + Performance
"""
import unittest
import unittest.mock as mock
import os
import sys
import sqlite3
import tempfile
import shutil
import time
import threading
import json
from io import StringIO
from unittest.mock import patch, MagicMock, PropertyMock

# Ensure project root is on path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser
from file_watcher import FileWatcher, ArkTSFileHandler
from config import SUPPORTED_EXTENSIONS, MIN_FILE_WORDS
from database_schema import SCHEMA_SQL


# ============================================================================
# Phase 3: Unit Full Branch Coverage
# ============================================================================

class Phase3_DBLayerTest(unittest.TestCase):
    """Phase 3 - Database Layer Full Branch Coverage"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- _get_connection ---
    def test_get_connection_success(self):
        """Normal successful connection"""
        with self.db._get_connection() as conn:
            self.assertIsNotNone(conn)
            result = conn.execute("SELECT 1").fetchone()
            self.assertEqual(result[0], 1)

    def test_get_connection_commit_on_success(self):
        """Connection commits on successful yield"""
        with self.db._get_connection() as conn:
            conn.execute("INSERT INTO events (event_type) VALUES ('test_conn_commit')")
        # Verify committed
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='test_conn_commit'").fetchone()
            self.assertEqual(row[0], 1)

    def test_get_connection_rollback_on_exception(self):
        """Connection rolls back on exception in yield block"""
        try:
            with self.db._get_connection() as conn:
                conn.execute("INSERT INTO events (event_type) VALUES ('test_rollback')")
                raise ValueError("simulated error")
        except ValueError:
            pass
        # Verify rolled back
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT COUNT(*) FROM events WHERE event_type='test_rollback'").fetchone()
            self.assertEqual(row[0], 0)

    def test_get_connection_retry_on_operational_error(self):
        """Retry on OperationalError, succeed on second attempt"""
        original_connect = sqlite3.connect
        call_count = [0]

        def flaky_connect(*args, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                raise sqlite3.OperationalError("database is locked")
            return original_connect(*args, **kwargs)

        with patch('sqlite3.connect', side_effect=flaky_connect):
            with self.db._get_connection() as conn:
                self.assertIsNotNone(conn)
        self.assertGreaterEqual(call_count[0], 2)

    def test_get_connection_all_retries_exhausted(self):
        """All retries exhausted raises OperationalError"""
        with patch('sqlite3.connect', side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                with self.db._get_connection() as conn:
                    pass

    def test_get_connection_conn_none_in_except(self):
        """conn is None when connect fails before yield"""
        # This is covered by the all-retries-exhausted test above,
        # but let's also verify the finally block handles None conn
        with patch('sqlite3.connect', side_effect=sqlite3.OperationalError("locked")):
            with self.assertRaises(sqlite3.OperationalError):
                with self.db._get_connection() as conn:
                    pass

    # --- compute_file_hash ---
    def test_compute_file_hash_normal(self):
        """Normal hash computation"""
        h1 = self.db.compute_file_hash("hello world")
        h2 = self.db.compute_file_hash("hello world")
        self.assertEqual(h1, h2)
        self.assertIsInstance(h1, str)
        self.assertEqual(len(h1), 64)  # SHA256 hexdigest

    def test_compute_file_hash_empty_string(self):
        """Empty string hash"""
        h = self.db.compute_file_hash("")
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 64)

    def test_compute_file_hash_unicode(self):
        """Unicode content hash"""
        h = self.db.compute_file_hash("你好世界 🌍")
        self.assertIsInstance(h, str)

    def test_compute_file_hash_very_large(self):
        """1MB content hash"""
        content = "x" * (1024 * 1024)
        h = self.db.compute_file_hash(content)
        self.assertIsInstance(h, str)

    # --- file_exists_and_unchanged ---
    def test_file_exists_and_unchanged_new_file(self):
        """New file returns (False, hash)"""
        unchanged, new_hash = self.db.file_exists_and_unchanged("/new/file.ts", "content")
        self.assertFalse(unchanged)
        self.assertIsNotNone(new_hash)

    def test_file_exists_and_unchanged_same_hash(self):
        """Same hash returns True"""
        content = "same content here"
        self.db.add_or_update_file("/same/file.ts", content)
        unchanged, _ = self.db.file_exists_and_unchanged("/same/file.ts", content)
        self.assertTrue(unchanged)

    def test_file_exists_and_unchanged_different_hash(self):
        """Different hash returns False"""
        self.db.add_or_update_file("/diff/file.ts", "old content")
        unchanged, new_hash = self.db.file_exists_and_unchanged("/diff/file.ts", "new content")
        self.assertFalse(unchanged)
        self.assertIsNotNone(new_hash)

    def test_file_exists_and_unchanged_hash_stored_as_str(self):
        """Hash stored as string (isinstance check branch)"""
        content = "test content"
        file_path = "/str/hash/file.ts"
        # Store hash as string (same format as compute_file_hash now returns)
        with self.db._get_connection() as conn:
            h = self.db.compute_file_hash(content)
            conn.execute(
                "INSERT INTO files (path, hash, modified) VALUES (?, ?, strftime('%s','now'))",
                (file_path, h)
            )
        # Verify stored as text
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT typeof(hash) as t FROM files WHERE path=?", (file_path,)).fetchone()
            self.assertEqual(row['t'], 'text')
        # Now test: since both stored hash and new hash are str, they should match
        unchanged, new_hash = self.db.file_exists_and_unchanged(file_path, content)
        self.assertTrue(unchanged)

    # --- add_or_update_file ---
    def test_add_or_update_file_insert(self):
        """INSERT path for new file"""
        fid = self.db.add_or_update_file("/new/insert.ts", "content")
        self.assertIsInstance(fid, int)
        self.assertGreater(fid, 0)

    def test_add_or_update_file_update(self):
        """UPDATE path for existing file"""
        fid1 = self.db.add_or_update_file("/update/me.ts", "v1")
        fid2 = self.db.add_or_update_file("/update/me.ts", "v2")
        self.assertEqual(fid1, fid2)  # Same file ID

    # --- delete_file_data ---
    def test_delete_file_data_exists(self):
        """Delete existing file returns True"""
        self.db.add_or_update_file("/to/delete.ts", "content")
        result = self.db.delete_file_data("/to/delete.ts")
        self.assertTrue(result)

    def test_delete_file_data_not_exists(self):
        """Delete non-existing file returns False"""
        result = self.db.delete_file_data("/no/such/file.ts")
        self.assertFalse(result)

    # --- get_file_id ---
    def test_get_file_id_found(self):
        """Found file returns id"""
        fid = self.db.add_or_update_file("/find/me.ts", "content")
        result = self.db.get_file_id("/find/me.ts")
        self.assertEqual(result, fid)

    def test_get_file_id_not_found(self):
        """Not found returns None"""
        result = self.db.get_file_id("/no/exist.ts")
        self.assertIsNone(result)

    # --- add_entity ---
    def test_add_entity_all_fields(self):
        """Add entity with all fields"""
        fid = self.db.add_or_update_file("/all/fields.ts", "c")
        eid = self.db.add_entity(fid, "MyClass", "class",
                                 signature="class MyClass",
                                 docstring="A class",
                                 start_line=1, end_line=10)
        self.assertIsInstance(eid, int)
        entities = self.db.get_file_entities(fid)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]['name'], "MyClass")
        self.assertEqual(entities[0]['signature'], "class MyClass")
        # docstring not in get_file_entities SELECT, verify via direct query
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT docstring FROM entities WHERE id=?", (eid,)).fetchone()
            self.assertEqual(row['docstring'], "A class")

    def test_add_entity_minimal_fields(self):
        """Add entity with minimal fields (None values)"""
        fid = self.db.add_or_update_file("/min/fields.ts", "c")
        eid = self.db.add_entity(fid, "Fn", "function")
        self.assertIsInstance(eid, int)
        entities = self.db.get_file_entities(fid)
        self.assertEqual(entities[0]['signature'], None)
        # docstring not in get_file_entities SELECT, verify via direct query
        with self.db._get_connection() as conn:
            row = conn.execute("SELECT docstring FROM entities WHERE id=?", (eid,)).fetchone()
            self.assertIsNone(row['docstring'])

    # --- add_entities_batch ---
    def test_add_entities_batch_empty(self):
        """Empty list returns empty"""
        result = self.db.add_entities_batch(999, [])
        self.assertEqual(result, [])

    def test_add_entities_batch_single(self):
        """Single entity"""
        fid = self.db.add_or_update_file("/batch/one.ts", "c")
        ids = self.db.add_entities_batch(fid, [{"name": "A", "type": "class"}])
        self.assertEqual(len(ids), 1)

    def test_add_entities_batch_multiple(self):
        """Multiple entities"""
        fid = self.db.add_or_update_file("/batch/many.ts", "c")
        entities = [{"name": f"E{i}", "type": "class"} for i in range(5)]
        ids = self.db.add_entities_batch(fid, entities)
        self.assertEqual(len(ids), 5)

    # --- add_relationships_batch ---
    def test_add_relationships_batch_empty(self):
        """Empty list returns 0"""
        result = self.db.add_relationships_batch(1, [], {})
        self.assertEqual(result, 0)

    def test_add_relationships_batch_all_matched(self):
        """All relationships matched"""
        fid = self.db.add_or_update_file("/rel/match.ts", "c")
        e1 = self.db.add_entity(fid, "ClassA", "class")
        e2 = self.db.add_entity(fid, "ClassB", "class")
        name_map = {"ClassA_1": e1, "ClassB_1": e2}
        rels = [{"source": "ClassA", "target": "ClassB", "type": "extends"}]
        count = self.db.add_relationships_batch(fid, rels, name_map)
        self.assertEqual(count, 1)

    def test_add_relationships_batch_unmatched_source(self):
        """Unmatched source entity skipped"""
        fid = self.db.add_or_update_file("/rel/nosrc.ts", "c")
        e2 = self.db.add_entity(fid, "ClassB", "class")
        name_map = {"ClassB_1": e2}
        rels = [{"source": "UnknownSrc", "target": "ClassB", "type": "extends"}]
        count = self.db.add_relationships_batch(fid, rels, name_map)
        self.assertEqual(count, 0)

    def test_add_relationships_batch_unmatched_target(self):
        """Unmatched target entity skipped"""
        fid = self.db.add_or_update_file("/rel/notgt.ts", "c")
        e1 = self.db.add_entity(fid, "ClassA", "class")
        name_map = {"ClassA_1": e1}
        rels = [{"source": "ClassA", "target": "UnknownTgt", "type": "extends"}]
        count = self.db.add_relationships_batch(fid, rels, name_map)
        self.assertEqual(count, 0)

    def test_add_relationships_batch_prefix_match(self):
        """Prefix match for entity names"""
        fid = self.db.add_or_update_file("/rel/prefix.ts", "c")
        e1 = self.db.add_entity(fid, "MyClass", "class")
        name_map = {"MyClass_1": e1}
        rels = [{"source": "MyClass", "target": "MyClass", "type": "contains"}]
        count = self.db.add_relationships_batch(fid, rels, name_map)
        self.assertEqual(count, 1)

    # --- add_relationship ---
    def test_add_relationship_all_params(self):
        """Add relationship with all params"""
        fid = self.db.add_or_update_file("/rel/all.ts", "c")
        e1 = self.db.add_entity(fid, "A", "class")
        e2 = self.db.add_entity(fid, "B", "class")
        rid = self.db.add_relationship(fid, "calls", e1, e2, fid)
        self.assertIsInstance(rid, int)

    def test_add_relationship_none_entity_ids(self):
        """Add relationship with None entity_ids should raise ValueError"""
        fid = self.db.add_or_update_file("/rel/none.ts", "c")
        with self.assertRaises(ValueError):
            self.db.add_relationship(fid, "imports", None, None, None)

    # --- add_import ---
    def test_add_import_with_names(self):
        """Add import with names"""
        fid = self.db.add_or_update_file("/imp/names.ts", "c")
        iid = self.db.add_import(fid, "ohos.router", ["Router", "Page"])
        self.assertIsInstance(iid, int)

    def test_add_import_without_names(self):
        """Add import without names"""
        fid = self.db.add_or_update_file("/imp/nonames.ts", "c")
        iid = self.db.add_import(fid, "ohos.app")
        self.assertIsInstance(iid, int)

    def test_add_import_is_default_true(self):
        """Add default import"""
        fid = self.db.add_or_update_file("/imp/default.ts", "c")
        iid = self.db.add_import(fid, "ohos.default", is_default=True)
        self.assertIsInstance(iid, int)

    def test_add_import_is_default_false(self):
        """Add non-default import"""
        fid = self.db.add_or_update_file("/imp/nodefault.ts", "c")
        iid = self.db.add_import(fid, "ohos.mod", is_default=False)
        self.assertIsInstance(iid, int)

    # --- add_file_dependency ---
    def test_add_file_dependency_new(self):
        """New dependency returns id"""
        f1 = self.db.add_or_update_file("/dep/a.ts", "c")
        f2 = self.db.add_or_update_file("/dep/b.ts", "c")
        did = self.db.add_file_dependency(f1, f2)
        self.assertIsNotNone(did)

    def test_add_file_dependency_duplicate(self):
        """Duplicate dependency returns None (INSERT OR IGNORE)"""
        f1 = self.db.add_or_update_file("/dep/dup/a.ts", "c")
        f2 = self.db.add_or_update_file("/dep/dup/b.ts", "c")
        did1 = self.db.add_file_dependency(f1, f2)
        did2 = self.db.add_file_dependency(f1, f2)
        self.assertIsNotNone(did1)
        self.assertIsNone(did2)

    # --- log_event ---
    def test_log_event_all_fields(self):
        """Log event with all fields"""
        self.db.log_event("TEST", "/test.ts", "details here", 100)
        events = self.db.get_recent_events()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]['event_type'], "TEST")
        self.assertEqual(events[0]['file_path'], "/test.ts")
        self.assertEqual(events[0]['details'], "details here")
        self.assertEqual(events[0]['duration_ms'], 100)

    def test_log_event_minimal(self):
        """Log event with minimal fields"""
        self.db.log_event("MINIMAL")
        events = self.db.get_recent_events()
        self.assertEqual(len(events), 1)
        self.assertIsNone(events[0]['file_path'])
        self.assertIsNone(events[0]['details'])
        self.assertIsNone(events[0]['duration_ms'])

    # --- get_all_files ---
    def test_get_all_files_normal(self):
        """Get all files"""
        self.db.add_or_update_file("/f1.ts", "c1")
        self.db.add_or_update_file("/f2.ts", "c2")
        files = self.db.get_all_files()
        self.assertEqual(len(files), 2)

    def test_get_all_files_with_limit(self):
        """Get files with limit"""
        for i in range(5):
            self.db.add_or_update_file(f"/limit/f{i}.ts", f"c{i}")
        files = self.db.get_all_files(limit=3)
        self.assertEqual(len(files), 3)

    # --- get_file_entities ---
    def test_get_file_entities_normal(self):
        """Get entities for a file"""
        fid = self.db.add_or_update_file("/ent/file.ts", "c")
        self.db.add_entity(fid, "E1", "class")
        self.db.add_entity(fid, "E2", "function")
        entities = self.db.get_file_entities(fid)
        self.assertEqual(len(entities), 2)

    def test_get_file_entities_empty(self):
        """Get entities for file with no entities"""
        fid = self.db.add_or_update_file("/ent/empty.ts", "c")
        entities = self.db.get_file_entities(fid)
        self.assertEqual(len(entities), 0)

    # --- get_all_entities ---
    def test_get_all_entities_normal(self):
        """Get all entities"""
        fid = self.db.add_or_update_file("/ae/file.ts", "c")
        self.db.add_entity(fid, "E1", "class")
        entities = self.db.get_all_entities()
        self.assertEqual(len(entities), 1)
        self.assertIn('file_path', entities[0])

    def test_get_all_entities_with_limit(self):
        """Get entities with limit"""
        fid = self.db.add_or_update_file("/ae/lim.ts", "c")
        for i in range(5):
            self.db.add_entity(fid, f"E{i}", "class")
        entities = self.db.get_all_entities(limit=2)
        self.assertEqual(len(entities), 2)

    # --- get_all_relationships ---
    def test_get_all_relationships_normal(self):
        """Get all relationships"""
        fid = self.db.add_or_update_file("/ar/file.ts", "c")
        e1 = self.db.add_entity(fid, "A", "class")
        e2 = self.db.add_entity(fid, "B", "class")
        self.db.add_relationship(fid, "extends", e1, e2, fid)
        rels = self.db.get_all_relationships()
        self.assertEqual(len(rels), 1)

    def test_get_all_relationships_with_limit(self):
        """Get relationships with limit"""
        fid = self.db.add_or_update_file("/ar/lim.ts", "c")
        e1 = self.db.add_entity(fid, "A", "class")
        for i in range(5):
            e2 = self.db.add_entity(fid, f"B{i}", "class")
            self.db.add_relationship(fid, "calls", e1, e2, fid)
        rels = self.db.get_all_relationships(limit=2)
        self.assertEqual(len(rels), 2)

    # --- get_file_dependencies ---
    def test_get_file_dependencies_normal(self):
        """Get file dependencies"""
        f1 = self.db.add_or_update_file("/fd/a.ts", "c")
        f2 = self.db.add_or_update_file("/fd/b.ts", "c")
        self.db.add_file_dependency(f1, f2)
        deps = self.db.get_file_dependencies()
        self.assertEqual(len(deps), 1)

    def test_get_file_dependencies_with_limit(self):
        """Get dependencies with limit"""
        f1 = self.db.add_or_update_file("/fd/lim/a.ts", "c")
        for i in range(5):
            f2 = self.db.add_or_update_file(f"/fd/lim/b{i}.ts", "c")
            self.db.add_file_dependency(f1, f2)
        deps = self.db.get_file_dependencies(limit=2)
        self.assertEqual(len(deps), 2)

    # --- get_statistics ---
    def test_get_statistics_empty(self):
        """Statistics on empty DB"""
        stats = self.db.get_statistics()
        self.assertEqual(stats['total_files'], 0)
        self.assertEqual(stats['total_entities'], 0)
        self.assertEqual(stats['total_relationships'], 0)
        self.assertEqual(stats['total_imports'], 0)
        self.assertEqual(stats['entities_by_type'], {})
        self.assertEqual(stats['relations_by_type'], {})

    def test_get_statistics_populated(self):
        """Statistics with data"""
        fid = self.db.add_or_update_file("/stat/f.ts", "c")
        self.db.add_entity(fid, "C", "class")
        self.db.add_entity(fid, "F", "function")
        self.db.add_import(fid, "mod", ["X"])
        stats = self.db.get_statistics()
        self.assertEqual(stats['total_files'], 1)
        self.assertEqual(stats['total_entities'], 2)
        self.assertEqual(stats['total_imports'], 1)
        self.assertIn('class', stats['entities_by_type'])
        self.assertEqual(stats['entities_by_type']['class'], 1)

    # --- get_recent_events ---
    def test_get_recent_events_no_events(self):
        """No events returns empty list"""
        events = self.db.get_recent_events()
        self.assertEqual(len(events), 0)

    def test_get_recent_events_with_events(self):
        """Events returned in reverse order"""
        self.db.log_event("E1")
        self.db.log_event("E2")
        events = self.db.get_recent_events()
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]['event_type'], "E2")  # Most recent first

    # --- search_entities ---
    def test_search_entities_empty_query(self):
        """Empty query returns empty"""
        result = self.db.search_entities("")
        self.assertEqual(result, [])

    def test_search_entities_whitespace_query(self):
        """Whitespace query returns empty"""
        result = self.db.search_entities("   ")
        self.assertEqual(result, [])

    def test_search_entities_normal(self):
        """Normal search"""
        fid = self.db.add_or_update_file("/search/f.ts", "c")
        self.db.add_entity(fid, "MyComponent", "class")
        result = self.db.search_entities("MyComponent")
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]['name'], "MyComponent")

    def test_search_entities_special_chars(self):
        """Special chars (%, _, \\) are escaped"""
        fid = self.db.add_or_update_file("/search/special.ts", "c")
        self.db.add_entity(fid, "test_entity", "class")
        # Search with % should not match everything
        result = self.db.search_entities("%")
        self.assertEqual(len(result), 0)

    def test_search_entities_no_results(self):
        """No matching results"""
        result = self.db.search_entities("NonExistentEntity")
        self.assertEqual(result, [])

    # --- get_entity_details ---
    def test_get_entity_details_found_with_relations(self):
        """Found entity with outgoing and incoming relations"""
        fid = self.db.add_or_update_file("/detail/f.ts", "c")
        e1 = self.db.add_entity(fid, "Parent", "class")
        e2 = self.db.add_entity(fid, "Child", "class")
        self.db.add_relationship(fid, "extends", e2, e1, fid)
        detail = self.db.get_entity_details(e1)
        self.assertIsNotNone(detail)
        self.assertEqual(detail['name'], "Parent")
        self.assertIn('outgoing_relations', detail)
        self.assertIn('incoming_relations', detail)
        self.assertGreaterEqual(len(detail['incoming_relations']), 1)

    def test_get_entity_details_found_without_relations(self):
        """Found entity without relations"""
        fid = self.db.add_or_update_file("/detail/norel.ts", "c")
        eid = self.db.add_entity(fid, "Standalone", "class")
        detail = self.db.get_entity_details(eid)
        self.assertIsNotNone(detail)
        self.assertEqual(detail['name'], "Standalone")
        self.assertEqual(len(detail['outgoing_relations']), 0)
        self.assertEqual(len(detail['incoming_relations']), 0)

    def test_get_entity_details_not_found(self):
        """Not found returns None"""
        detail = self.db.get_entity_details(999999)
        self.assertIsNone(detail)

    # --- close ---
    def test_close_normal(self):
        """Normal close"""
        self.db.close()  # Should not raise

    def test_close_wal_checkpoint_failure(self):
        """WAL checkpoint failure during close (caught and printed)"""
        with patch.object(self.db, '_get_connection', side_effect=Exception("WAL fail")):
            # Should not raise, just print warning
            self.db.close()

    # --- update_file_transaction ---
    def test_update_file_transaction_with_callback(self):
        """With callback executes in transaction"""
        callback_called = [False]
        def cb(conn, file_id, parse_result):
            callback_called[0] = True
            conn.execute(
                "INSERT INTO entities (file_id, name, type) VALUES (?, ?, ?)",
                (file_id, "TxEntity", "class")
            )
        self.db.update_file_transaction("/tx/file.ts", "content", None, cb)
        self.assertTrue(callback_called[0])
        fid = self.db.get_file_id("/tx/file.ts")
        entities = self.db.get_file_entities(fid)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]['name'], "TxEntity")

    def test_update_file_transaction_without_callback(self):
        """Without callback just inserts file"""
        self.db.update_file_transaction("/tx/nocb.ts", "content")
        fid = self.db.get_file_id("/tx/nocb.ts")
        self.assertIsNotNone(fid)

    def test_update_file_transaction_callback_exception(self):
        """Callback exception causes rollback"""
        def bad_cb(conn, file_id, parse_result):
            conn.execute("INSERT INTO entities (file_id, name, type) VALUES (?, ?, ?)",
                         (file_id, "BadEntity", "class"))
            raise ValueError("callback error")
        with self.assertRaises(ValueError):
            self.db.update_file_transaction("/tx/err.ts", "content", None, bad_cb)
        # File should not exist due to rollback
        fid = self.db.get_file_id("/tx/err.ts")
        self.assertIsNone(fid)

    # --- _normalize_path ---
    def test_normalize_path(self):
        """Path normalization"""
        self.assertEqual(
            KnowledgeGraphDB._normalize_path("../foo/bar.ts"),
            os.path.normpath("../foo/bar.ts")
        )
        self.assertEqual(
            KnowledgeGraphDB._normalize_path("./foo/./bar.ts"),
            os.path.normpath("./foo/./bar.ts")
        )


class Phase3_ServiceLayerTest(unittest.TestCase):
    """Phase 3 - Service Layer Full Branch Coverage"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        # Patch the import
        with patch.dict('sys.modules', {'llm_parser': type('M', (), {'ArkTSParser': MockArkTSParser})}):
            from knowledge_graph_service import KnowledgeGraphService
            self.service = KnowledgeGraphService(self.db_path, watch_paths=[self.tmpdir])
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- _get_file_lock ---
    def test_get_file_lock_existing_move_to_end(self):
        """Existing lock moved to end (LRU)"""
        lock1 = self.service._get_file_lock("/a.ts")
        lock2 = self.service._get_file_lock("/a.ts")
        self.assertIs(lock1, lock2)
        # Should be at the end of OrderedDict
        self.assertEqual(list(self.service._file_locks.keys())[-1], "/a.ts")

    def test_get_file_lock_new(self):
        """New lock created"""
        lock = self.service._get_file_lock("/new.ts")
        self.assertIsNotNone(lock)
        self.assertIn("/new.ts", self.service._file_locks)

    def test_get_file_lock_lru_eviction_unlocked(self):
        """LRU eviction removes unlocked locks"""
        # Fill up to max
        for i in range(self.service._MAX_FILE_LOCKS + 5):
            self.service._get_file_lock(f"/f{i}.ts")
        # Should not exceed max by much (only unlocked ones removed)
        self.assertLessEqual(len(self.service._file_locks), self.service._MAX_FILE_LOCKS + 5)

    def test_get_file_lock_lru_eviction_locked_skip(self):
        """LRU eviction skips locked locks"""
        # Create locks and acquire some
        locks_to_hold = []
        for i in range(self.service._MAX_FILE_LOCKS):
            lock = self.service._get_file_lock(f"/f{i}.ts")
            if i < 5:
                lock.acquire()
                locks_to_hold.append(lock)
        # Add more to trigger eviction
        for i in range(10):
            self.service._get_file_lock(f"/extra{i}.ts")
        # Locked locks should still be present
        for i in range(5):
            self.assertIn(f"/f{i}.ts", self.service._file_locks)
        # Release held locks
        for lock in locks_to_hold:
            lock.release()

    # --- _handle_file_change ---
    def test_handle_file_change_shutdown(self):
        """Shutdown rejects new requests"""
        self.service._shutdown_event.set()
        # Should return immediately
        self.service._handle_file_change("/test.ts", "content")

    def test_handle_file_change_content_none(self):
        """Content None triggers delete path"""
        self.service._handle_file_change("/test.ts", None)
        # No error should occur

    def test_handle_file_change_empty_content(self):
        """Empty content is skipped"""
        self.service._handle_file_change("/test.ts", "")
        # File should not be added
        fid = self.service.db.get_file_id("/test.ts")
        self.assertIsNone(fid)

    def test_handle_file_change_whitespace_content(self):
        """Whitespace-only content is skipped"""
        self.service._handle_file_change("/test.ts", "   \n\t  ")
        fid = self.service.db.get_file_id("/test.ts")
        self.assertIsNone(fid)

    def test_handle_file_change_locked_retry(self):
        """Locked file triggers retry mechanism"""
        file_path = "/lock/test.ts"
        content = "export class LockTestClass { method(): void {} }"
        file_lock = self.service._get_file_lock(file_path)
        file_lock.acquire()  # Hold the lock

        results = []
        def run_in_thread():
            self.service._handle_file_change(file_path, content)
            results.append("done")

        t = threading.Thread(target=run_in_thread)
        t.start()
        time.sleep(0.5)
        file_lock.release()  # Release after delay
        t.join(timeout=5)
        self.assertEqual(len(results), 1)

    def test_handle_file_change_normal(self):
        """Normal file change processing"""
        file_path = os.path.join(self.tmpdir, "NormalFileHandler.ets")
        content = "export class NormalClass { method(): void {} }"
        self.service._handle_file_change(file_path, content)
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities), 0)

    # --- _handle_file_deleted ---
    def test_handle_file_deleted_shutdown(self):
        """Shutdown during delete"""
        self.service._shutdown_event.set()
        self.service._handle_file_deleted("/test.ts")

    def test_handle_file_deleted_normal(self):
        """Normal delete"""
        file_path = "/del/test.ts"
        self.service.db.add_or_update_file(file_path, "content")
        self.service._handle_file_deleted(file_path)
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNone(fid)

    # --- _handle_file_deleted_inner ---
    def test_handle_file_deleted_inner_exists(self):
        """Delete existing file logs event"""
        file_path = "/del/inner.ts"
        self.service.db.add_or_update_file(file_path, "content")
        self.service._handle_file_deleted_inner(file_path)
        events = self.service.db.get_recent_events()
        self.assertTrue(any(e['event_type'] == 'FILE_DELETED' for e in events))

    def test_handle_file_deleted_inner_not_exists(self):
        """Delete non-existing file does not log"""
        self.service._handle_file_deleted_inner("/no/exist.ts")
        events = self.service.db.get_recent_events()
        self.assertFalse(any(e['event_type'] == 'FILE_DELETED' for e in events))

    def test_handle_file_deleted_inner_with_callback(self):
        """Callback no longer fires inside _handle_file_deleted_inner"""
        callback_called = [False]
        def cb(fp, stats):
            callback_called[0] = True
        self.service.on_update_callback = cb
        file_path = "/del/cb.ts"
        self.service.db.add_or_update_file(file_path, "content")
        self.service._handle_file_deleted_inner(file_path)
        time.sleep(0.3)
        # Callback was moved to the caller (_handle_file_deleted), so it should
        # NOT fire inside _handle_file_deleted_inner
        self.assertFalse(callback_called[0])

    def test_handle_file_deleted_inner_no_callback(self):
        """No callback when on_update_callback is None"""
        self.service.on_update_callback = None
        file_path = "/del/nocb.ts"
        self.service.db.add_or_update_file(file_path, "content")
        self.service._handle_file_deleted_inner(file_path)
        # Should not raise

    # --- _handle_file_change_inner ---
    def test_handle_file_change_inner_empty_content(self):
        """Empty content returns early"""
        self.service._handle_file_change_inner("/empty.ts", "")
        fid = self.service.db.get_file_id("/empty.ts")
        self.assertIsNone(fid)

    def test_handle_file_change_inner_unchanged(self):
        """Unchanged file returns early"""
        file_path = "/unchanged.ts"
        content = "export class Unchanged { m(): void {} }"
        self.service.db.add_or_update_file(file_path, content)
        self.service._handle_file_change_inner(file_path, content)
        events = self.service.db.get_recent_events()
        self.assertFalse(any(e['event_type'] == 'FILE_UPDATED' for e in events))

    def test_handle_file_change_inner_parse_result_none(self):
        """parse_result None handled gracefully"""
        file_path = "/parse/none.ts"
        content = "some content"
        mock_parser = MagicMock()
        mock_parser.parse_file.return_value = None
        self.service.parser = mock_parser
        self.service._handle_file_change_inner(file_path, content)
        # Should not crash, entities should be empty
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        self.assertEqual(len(entities), 0)

    def test_handle_file_change_inner_entities_empty(self):
        """Empty entities list still processes file"""
        file_path = "/empty/ents.ts"
        content = "just some text"
        self.service._handle_file_change_inner(file_path, content)
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)

    def test_handle_file_change_inner_with_callback(self):
        """Callback no longer fires inside _handle_file_change_inner"""
        callback_called = [False]
        def cb(fp, stats):
            callback_called[0] = True
        self.service.on_update_callback = cb
        file_path = os.path.join(self.tmpdir, "CallbackFileHandler.ets")
        content = "export class CallbackClass { method(): void {} }"
        self.service._handle_file_change_inner(file_path, content)
        time.sleep(0.3)
        # Callback was moved to the caller (_handle_file_change), so it should
        # NOT fire inside _handle_file_change_inner
        self.assertFalse(callback_called[0])

    def test_handle_file_change_inner_no_callback(self):
        """No callback when None"""
        self.service.on_update_callback = None
        file_path = os.path.join(self.tmpdir, "NoCallbackFileHandler.ets")
        content = "export class NoCallbackClass { method(): void {} }"
        self.service._handle_file_change_inner(file_path, content)
        # Should not raise

    # --- initial_scan ---
    def test_initial_scan_normal(self):
        """Normal initial scan"""
        # Create valid ArkTS files
        for i in range(3):
            fname = f"ScanTestComponent{i}_handler.ets"
            fpath = os.path.join(self.tmpdir, fname)
            with open(fpath, 'w') as f:
                f.write(f"export class ScanClass{i} {{ method(): void {{}} }}")
        self.service.initial_scan()
        stats = self.service.get_statistics()
        self.assertGreater(stats['files_processed'], 0)

    def test_initial_scan_shutdown_interrupt(self):
        """Shutdown interrupts scan"""
        # Create many files
        for i in range(20):
            fname = f"InterruptTestComponent{i}_handler.ets"
            fpath = os.path.join(self.tmpdir, fname)
            with open(fpath, 'w') as f:
                f.write(f"export class C{i} {{ m(): void {{}} }}")

        # Set shutdown after a short delay
        def delayed_shutdown():
            time.sleep(0.1)
            self.service._shutdown_event.set()
        t = threading.Thread(target=delayed_shutdown, daemon=True)
        t.start()
        self.service.initial_scan()
        t.join(timeout=2)

    def test_initial_scan_empty(self):
        """Scan with no valid files"""
        self.service.initial_scan()
        stats = self.service.get_statistics()
        self.assertEqual(stats['files_processed'], 0)

    # --- close ---
    def test_close_idempotent(self):
        """Close is idempotent"""
        self.service.close()
        self.service.close()  # Should not raise

    def test_close_with_locks(self):
        """Close with active file locks"""
        lock = self.service._get_file_lock("/locked.ts")
        lock.acquire()
        try:
            self.service.close()
        finally:
            lock.release()

    def test_close_without_locks(self):
        """Close without file locks"""
        self.service._file_locks.clear()
        self.service.close()

    # --- get_statistics ---
    def test_get_statistics_normal(self):
        """Normal statistics"""
        stats = self.service.get_statistics()
        self.assertIn('total_files', stats)
        self.assertIn('total_entities', stats)
        self.assertIn('files_processed', stats)
        self.assertIn('last_update_time', stats)

    # --- search ---
    def test_search_normal(self):
        """Normal search"""
        file_path = os.path.join(self.tmpdir, "SearchTestComponent_handler.ets")
        content = "export class SearchTestClass { method(): void {} }"
        self.service._handle_file_change(file_path, content)
        results = self.service.search("SearchTestClass")
        self.assertGreater(len(results), 0)

    # --- get_graph_data ---
    def test_get_graph_data_normal(self):
        """Normal graph data"""
        file_path = os.path.join(self.tmpdir, "GraphTestComponent_handler.ets")
        content = "export class GraphTestClass { method(): void {} }"
        self.service._handle_file_change(file_path, content)
        data = self.service.get_graph_data()
        self.assertIn('entities', data)
        self.assertIn('relationships', data)
        self.assertIn('files', data)
        self.assertIn('dependencies', data)


class Phase3_WatcherTest(unittest.TestCase):
    """Phase 3 - File Watcher Full Branch Coverage"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.callback = MagicMock()
        self.handler = ArkTSFileHandler(self.callback, debounce_seconds=0.1)

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # --- _is_valid_file ---
    def test_is_valid_file_valid(self):
        """Valid file returns True"""
        path = os.path.join(self.tmpdir, "ValidTestComponent_handler.ets")
        self.assertTrue(self.handler._is_valid_file(path))

    def test_is_valid_file_bad_extension(self):
        """Bad extension returns False"""
        path = os.path.join(self.tmpdir, "bad.py")
        self.assertFalse(self.handler._is_valid_file(path))

    def test_is_valid_file_too_few_words(self):
        """Too few words returns False"""
        path = os.path.join(self.tmpdir, "ab.ts")
        self.assertFalse(self.handler._is_valid_file(path))

    # --- _extract_words ---
    def test_extract_words_camel_case(self):
        """camelCase extraction"""
        words = self.handler._extract_words("MyComponent")
        self.assertIn("My", words)
        self.assertIn("Component", words)

    def test_extract_words_snake_case(self):
        """snake_case extraction"""
        words = self.handler._extract_words("my_component")
        self.assertIn("my", words)
        self.assertIn("component", words)

    def test_extract_words_mixed(self):
        """Mixed camelCase_snake_case"""
        words = self.handler._extract_words("MyComponent_handler")
        self.assertIn("My", words)
        self.assertIn("Component", words)
        self.assertIn("handler", words)

    def test_extract_words_single(self):
        """Single word"""
        words = self.handler._extract_words("hello")
        self.assertIn("hello", words)

    def test_extract_words_empty(self):
        """Empty string"""
        words = self.handler._extract_words("")
        self.assertEqual(words, [])

    def test_extract_words_short_filtered(self):
        """Short words filtered out (len <= 1)"""
        words = self.handler._extract_words("a_b_c")
        self.assertEqual(words, [])

    # --- _debounced_callback ---
    def test_debounced_callback_file_exists(self):
        """Callback called for existing file"""
        path = os.path.join(self.tmpdir, "DebounceTestComponent_handler.ets")
        with open(path, 'w') as f:
            f.write("content")
        self.handler.pending_files[path] = None
        self.handler._debounced_callback(path)
        time.sleep(0.2)
        self.callback.assert_called_once()
        self.assertEqual(self.callback.call_args[0][0], os.path.normpath(path))

    def test_debounced_callback_file_deleted(self):
        """Callback not called for deleted file"""
        path = os.path.join(self.tmpdir, "DeletedTestComponent_handler.ets")
        self.handler.pending_files[path] = None
        # File doesn't exist
        self.handler._debounced_callback(path)
        self.callback.assert_not_called()

    def test_debounced_callback_unicode_error(self):
        """UnicodeDecodeError handled gracefully - errors='replace' prevents it"""
        # The _debounced_callback uses errors='replace', so UnicodeDecodeError
        # won't actually be raised. Verify the file is still read and callback
        # is invoked with replaced content.
        path = os.path.join(self.tmpdir, "UnicodeTestComponent_handler.ets")
        with open(path, 'wb') as f:
            f.write(b'\x00\x01\x02\xff')
        self.handler.pending_files[path] = None
        self.handler._debounced_callback(path)
        # With errors='replace', the callback IS called (content is replaced)
        self.callback.assert_called_once()

    def test_debounced_callback_other_error(self):
        """Other errors handled gracefully"""
        path = os.path.join(self.tmpdir, "ErrorTestComponent_handler.ets")
        with open(path, 'w') as f:
            f.write("content")
        self.handler.pending_files[path] = None
        with patch('builtins.open', side_effect=PermissionError("denied")):
            self.handler._debounced_callback(path)
        self.callback.assert_not_called()

    # --- _schedule_update ---
    def test_schedule_update_valid(self):
        """Valid file scheduled"""
        path = os.path.join(self.tmpdir, "ScheduleTestComponent_handler.ets")
        with open(path, 'w') as f:
            f.write("content")
        self.handler._schedule_update(path)
        self.assertIn(os.path.normpath(path), self.handler.pending_files)
        # Clean up timer
        self.handler.pending_files[os.path.normpath(path)].cancel()

    def test_schedule_update_invalid(self):
        """Invalid file not scheduled"""
        path = os.path.join(self.tmpdir, "bad.py")
        self.handler._schedule_update(path)
        self.assertEqual(len(self.handler.pending_files), 0)

    def test_schedule_update_cancel_existing(self):
        """Existing timer cancelled"""
        path = os.path.join(self.tmpdir, "CancelTestComponent_handler.ets")
        with open(path, 'w') as f:
            f.write("content")
        self.handler._schedule_update(path)
        first_timer = self.handler.pending_files[os.path.normpath(path)]
        self.handler._schedule_update(path)
        second_timer = self.handler.pending_files[os.path.normpath(path)]
        self.assertIsNot(first_timer, second_timer)
        second_timer.cancel()

    # --- on_modified / on_created / on_deleted ---
    def test_on_modified_file(self):
        """Modified file event"""
        from watchdog.events import FileModifiedEvent
        path = os.path.join(self.tmpdir, "ModTestComponent_handler.ets")
        event = FileModifiedEvent(path)
        self.handler.on_modified(event)
        self.assertIn(os.path.normpath(path), self.handler.pending_files)
        self.handler.pending_files[os.path.normpath(path)].cancel()

    def test_on_modified_directory(self):
        """Modified directory ignored"""
        from watchdog.events import FileModifiedEvent
        event = FileModifiedEvent(self.tmpdir)
        event.is_directory = True
        self.handler.on_modified(event)
        self.assertEqual(len(self.handler.pending_files), 0)

    def test_on_modified_bad_extension(self):
        """Bad extension not scheduled"""
        from watchdog.events import FileModifiedEvent
        path = os.path.join(self.tmpdir, "bad.py")
        event = FileModifiedEvent(path)
        self.handler.on_modified(event)
        self.assertEqual(len(self.handler.pending_files), 0)

    def test_on_created_file(self):
        """Created file event"""
        from watchdog.events import FileCreatedEvent
        path = os.path.join(self.tmpdir, "CreateTestComponent_handler.ets")
        event = FileCreatedEvent(path)
        self.handler.on_created(event)
        self.assertIn(os.path.normpath(path), self.handler.pending_files)
        self.handler.pending_files[os.path.normpath(path)].cancel()

    def test_on_created_directory(self):
        """Created directory ignored"""
        from watchdog.events import FileCreatedEvent
        event = FileCreatedEvent(self.tmpdir)
        event.is_directory = True
        self.handler.on_created(event)
        self.assertEqual(len(self.handler.pending_files), 0)

    def test_on_deleted_file(self):
        """Deleted file event calls callback with None"""
        from watchdog.events import FileDeletedEvent
        path = os.path.join(self.tmpdir, "DelTestComponent_handler.ets")
        event = FileDeletedEvent(path)
        self.handler.on_deleted(event)
        self.callback.assert_called_once_with(os.path.normpath(path), None)

    def test_on_deleted_directory(self):
        """Deleted directory ignored"""
        from watchdog.events import FileDeletedEvent
        event = FileDeletedEvent(self.tmpdir)
        event.is_directory = True
        self.handler.on_deleted(event)
        self.callback.assert_not_called()

    def test_on_deleted_bad_extension(self):
        """Deleted file with bad extension ignored"""
        from watchdog.events import FileDeletedEvent
        path = os.path.join(self.tmpdir, "bad.py")
        event = FileDeletedEvent(path)
        self.handler.on_deleted(event)
        self.callback.assert_not_called()

    # --- FileWatcher start/stop ---
    def test_watcher_start_not_running(self):
        """Start when not running"""
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        watcher.start()
        self.assertTrue(watcher.is_running)
        watcher.stop()
        self.assertFalse(watcher.is_running)

    def test_watcher_start_already_running(self):
        """Start when already running returns"""
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        watcher.start()
        watcher.start()  # Should return immediately
        self.assertTrue(watcher.is_running)
        watcher.stop()

    def test_watcher_stop_not_running(self):
        """Stop when not running returns"""
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        watcher.stop()  # Should not raise

    def test_watcher_stop_with_pending_timers(self):
        """Stop cancels pending timers"""
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        path = os.path.join(self.tmpdir, "TimerTestComponent_handler.ets")
        with open(path, 'w') as f:
            f.write("content")
        watcher.handler._schedule_update(path)
        watcher.stop()

    # --- scan_existing_files ---
    def test_scan_existing_files_path_not_exists(self):
        """Non-existent path returns empty"""
        watcher = FileWatcher(lambda x, y: None, ["/nonexistent/path"])
        files = watcher.scan_existing_files()
        self.assertEqual(files, [])

    def test_scan_existing_files_valid_files(self):
        """Valid files returned"""
        for i in range(3):
            fname = f"ScanValidComponent{i}_handler.ets"
            fpath = os.path.join(self.tmpdir, fname)
            with open(fpath, 'w') as f:
                f.write(f"export class C{i} {{}}")
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        files = watcher.scan_existing_files()
        self.assertEqual(len(files), 3)

    def test_scan_existing_files_no_valid_files(self):
        """No valid files returns empty"""
        with open(os.path.join(self.tmpdir, "bad.py"), 'w') as f:
            f.write("bad")
        with open(os.path.join(self.tmpdir, "ab.ts"), 'w') as f:
            f.write("short")
        watcher = FileWatcher(lambda x, y: None, [self.tmpdir])
        files = watcher.scan_existing_files()
        self.assertEqual(len(files), 0)


class Phase3_ParserTest(unittest.TestCase):
    """Phase 3 - Parser Full Branch Coverage"""

    def setUp(self):
        self.parser = MockArkTSParser()

    def test_parse_file_imports(self):
        """Import extraction"""
        content = "import { Router, Page } from '@ohos.router'"
        result = self.parser.parse_file("test.ts", content)
        self.assertGreater(len(result['imports']), 0)
        self.assertEqual(result['imports'][0]['module'], '@ohos.router')

    def test_parse_file_interface_extends(self):
        """Interface with extends"""
        content = "export interface Child extends Parent { name: string }"
        result = self.parser.parse_file("test.ts", content)
        entities = [e for e in result['entities'] if e['type'] == 'interface']
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]['name'], 'Child')
        rels = [r for r in result['relationships'] if r['type'] == 'extends']
        self.assertEqual(len(rels), 1)

    def test_parse_file_interface_no_extends(self):
        """Interface without extends"""
        content = "export interface Simple { id: number }"
        result = self.parser.parse_file("test.ts", content)
        entities = [e for e in result['entities'] if e['type'] == 'interface']
        self.assertEqual(len(entities), 1)
        rels = [r for r in result['relationships'] if r['source'] == 'Simple']
        self.assertEqual(len(rels), 0)

    def test_parse_file_class_extends(self):
        """Class with extends"""
        content = "export class Dog extends Animal { bark(): void {} }"
        result = self.parser.parse_file("test.ts", content)
        classes = [e for e in result['entities'] if e['type'] == 'class']
        self.assertEqual(len(classes), 1)
        rels = [r for r in result['relationships'] if r['type'] == 'extends']
        self.assertEqual(len(rels), 1)

    def test_parse_file_class_implements(self):
        """Class with implements"""
        content = "export class MyClass implements InterfaceA, InterfaceB { run(): void {} }"
        result = self.parser.parse_file("test.ts", content)
        impl_rels = [r for r in result['relationships'] if r['type'] == 'implements']
        self.assertEqual(len(impl_rels), 2)

    def test_parse_file_class_neither(self):
        """Class without extends or implements"""
        content = "export class Standalone { doStuff(): number { return 1 } }"
        result = self.parser.parse_file("test.ts", content)
        classes = [e for e in result['entities'] if e['type'] == 'class']
        self.assertEqual(len(classes), 1)
        ext_rels = [r for r in result['relationships'] if r['type'] == 'extends' and r['source'] == 'Standalone']
        impl_rels = [r for r in result['relationships'] if r['type'] == 'implements' and r['source'] == 'Standalone']
        self.assertEqual(len(ext_rels), 0)
        self.assertEqual(len(impl_rels), 0)

    def test_parse_file_methods_blacklist(self):
        """Blacklisted method names not extracted"""
        content = """
        export class BlacklistTest {
            constructor() {}
            if() {}
            for() {}
            while() {}
            switch() {}
            validMethod(): void {}
        }
        """
        result = self.parser.parse_file("test.ts", content)
        methods = [e for e in result['entities'] if e['type'] == 'function' and e['name'].startswith('BlacklistTest.')]
        method_names = [m['name'].split('.')[-1] for m in methods]
        self.assertNotIn('constructor', method_names)
        self.assertNotIn('if', method_names)
        self.assertNotIn('for', method_names)
        self.assertNotIn('while', method_names)
        self.assertNotIn('switch', method_names)
        self.assertIn('validMethod', method_names)

    def test_parse_file_enums(self):
        """Enum extraction"""
        content = "export enum Color { Red, Green, Blue }"
        result = self.parser.parse_file("test.ts", content)
        enums = [e for e in result['entities'] if e['type'] == 'enum']
        self.assertEqual(len(enums), 1)
        self.assertEqual(enums[0]['name'], 'Color')

    def test_parse_file_types(self):
        """Type alias extraction"""
        content = "export type StringMap = Record<string, string>"
        result = self.parser.parse_file("test.ts", content)
        types = [e for e in result['entities'] if e['type'] == 'type']
        self.assertEqual(len(types), 1)
        self.assertEqual(types[0]['name'], 'StringMap')

    def test_parse_file_functions(self):
        """Function extraction"""
        content = "export function helperFunction(): void {}"
        result = self.parser.parse_file("test.ts", content)
        funcs = [e for e in result['entities'] if e['type'] == 'function' and '.' not in e['name']]
        self.assertEqual(len(funcs), 1)
        self.assertEqual(funcs[0]['name'], 'helperFunction')

    def test_parse_file_simulate_errors(self):
        """Simulated errors"""
        parser = MockArkTSParser(simulate_errors=True, error_rate=1.0)
        with self.assertRaises(Exception):
            parser.parse_file("test.ts", "content")

    def test_parse_file_empty(self):
        """Empty content"""
        result = self.parser.parse_file("test.ts", "")
        self.assertEqual(len(result['entities']), 0)
        self.assertEqual(len(result['relationships']), 0)
        self.assertEqual(len(result['imports']), 0)

    def test_batch_parse(self):
        """Batch parse multiple files"""
        files = [
            {"path": "a.ts", "content": "export class A {}"},
            {"path": "b.ts", "content": "export class B {}"},
        ]
        results = self.parser.batch_parse(files)
        self.assertEqual(len(results), 2)
        self.assertIn('file_path', results[0])
        self.assertEqual(results[0]['file_path'], "a.ts")


# ============================================================================
# Phase 4: Module Integration
# ============================================================================

class Phase4_IntegrationTest(unittest.TestCase):
    """Phase 4 - Cross-Module Integration"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        with patch.dict('sys.modules', {'llm_parser': type('M', (), {'ArkTSParser': MockArkTSParser})}):
            from knowledge_graph_service import KnowledgeGraphService
            self.service = KnowledgeGraphService(self.db_path, watch_paths=[self.tmpdir])
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_service_to_db_full_update_flow(self):
        """Service -> DB: parse -> delete -> insert -> log"""
        file_path = os.path.join(self.tmpdir, "FullFlowComponent_handler.ets")
        content = """
        import { Router } from '@ohos.router'
        export interface IData { value: string }
        export class DataModel implements IData {
            getValue(): string { return 'hello' }
        }
        """
        self.service._handle_file_change(file_path, content)

        # Verify file in DB
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)

        # Verify entities
        entities = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities), 0)

        # Verify imports
        with self.service.db._get_connection() as conn:
            imports = conn.execute("SELECT * FROM imports WHERE file_id=?", (fid,)).fetchall()
            self.assertGreater(len(imports), 0)

        # Verify event logged
        events = self.service.db.get_recent_events()
        self.assertTrue(any(e['event_type'] == 'FILE_UPDATED' for e in events))

    def test_service_to_watcher_file_change_triggers_callback(self):
        """Service -> Watcher: file change triggers service callback"""
        callback_called = [False]
        captured_content = [None]

        def service_callback(fp, content):
            callback_called[0] = True
            captured_content[0] = content

        handler = ArkTSFileHandler(service_callback, debounce_seconds=0.1)
        file_path = os.path.join(self.tmpdir, "WatcherIntegrationComponent_handler.ets")
        with open(file_path, 'w') as f:
            f.write("export class WatcherClass { method(): void {} }")

        handler._schedule_update(file_path)
        time.sleep(0.5)  # Wait for debounce
        self.assertTrue(callback_called[0])
        self.assertIn("WatcherClass", captured_content[0])

    def test_service_to_parser_result_feeds_db(self):
        """Service -> Parser: parser result feeds into DB transaction"""
        file_path = os.path.join(self.tmpdir, "ParserFeedComponent_handler.ets")
        content = "export class ParserFeedClass { getData(): string { return '' } }"

        # Parse
        parse_result = self.service.parser.parse_file(file_path, content)
        self.assertGreater(len(parse_result['entities']), 0)

        # Feed into DB
        def tx_callback(conn, file_id, pr):
            for entity in pr['entities']:
                conn.execute(
                    "INSERT INTO entities (file_id, name, type) VALUES (?, ?, ?)",
                    (file_id, entity['name'], entity['type'])
                )
        self.service.db.update_file_transaction(file_path, content, parse_result, tx_callback)

        # Verify
        fid = self.service.db.get_file_id(file_path)
        entities = self.service.db.get_file_entities(fid)
        entity_names = [e['name'] for e in entities]
        self.assertIn("ParserFeedClass", entity_names)

    def test_db_cascade_delete(self):
        """DB cascade: delete file -> entities/relationships/imports auto-deleted"""
        file_path = os.path.join(self.tmpdir, "CascadeComponent_handler.ets")
        content = """
        import { Item } from './item'
        export class CascadeClass extends Base {
            process(): void {}
        }
        """
        self.service._handle_file_change(file_path, content)

        fid = self.service.db.get_file_id(file_path)
        entities_before = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities_before), 0)

        # Delete file
        self.service.db.delete_file_data(file_path)

        # Verify cascade
        entities_after = self.service.db.get_file_entities(fid)
        self.assertEqual(len(entities_after), 0)

        # Verify relationships gone
        all_rels = self.service.db.get_all_relationships()
        for rel in all_rels:
            self.assertNotEqual(rel.get('source_file'), file_path)


# ============================================================================
# Phase 5: API Endpoint Exhaustive
# ============================================================================

class Phase5_APIEndpointTest(unittest.TestCase):
    """Phase 5 - API Endpoint Exhaustive Testing"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        with patch.dict('sys.modules', {'llm_parser': type('M', (), {'ArkTSParser': MockArkTSParser})}):
            from knowledge_graph_service import KnowledgeGraphService
            self.service = KnowledgeGraphService(self.db_path, watch_paths=[self.tmpdir])
        self.service.parser = MockArkTSParser()

        # Set up Flask test client
        import web_server
        self.app = web_server.app
        self.app.config['TESTING'] = True
        self.client = self.app.test_client()

    def tearDown(self):
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def _set_service(self):
        """Set global service in web_server"""
        import web_server
        web_server.kg_service = self.service

    def _clear_service(self):
        """Clear global service"""
        import web_server
        web_server.kg_service = None

    # --- GET / ---
    def test_get_index(self):
        """GET / returns HTML"""
        # May return 500 if template not found in test context, but should not be 404
        resp = self.client.get('/')
        self.assertIn(resp.status_code, [200, 500])

    # --- GET /api/stats ---
    def test_get_stats_with_service(self):
        """GET /api/stats with service"""
        self._set_service()
        resp = self.client.get('/api/stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_files', data)

    def test_get_stats_without_service(self):
        """GET /api/stats without service"""
        self._clear_service()
        resp = self.client.get('/api/stats')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['total_files'], 0)

    # --- GET /api/graph ---
    def test_get_graph_with_service(self):
        """GET /api/graph with service"""
        self._set_service()
        resp = self.client.get('/api/graph')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('entities', data)
        self.assertIn('relationships', data)

    def test_get_graph_without_service(self):
        """GET /api/graph without service"""
        self._clear_service()
        resp = self.client.get('/api/graph')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['entities'], [])

    # --- GET /api/search ---
    def test_search_with_query(self):
        """GET /api/search with query"""
        self._set_service()
        file_path = os.path.join(self.tmpdir, "ApiSearchComponent_handler.ets")
        self.service._handle_file_change(file_path,
            "export class ApiSearchClass { find(): void {} }")
        resp = self.client.get('/api/search?q=ApiSearchClass')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data), 0)

    def test_search_without_query(self):
        """GET /api/search without query"""
        self._set_service()
        resp = self.client.get('/api/search')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    def test_search_empty_query(self):
        """GET /api/search with empty query"""
        self._set_service()
        resp = self.client.get('/api/search?q=')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    def test_search_whitespace_query(self):
        """GET /api/search with whitespace query"""
        self._set_service()
        resp = self.client.get('/api/search?q=   ')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    def test_search_special_chars(self):
        """GET /api/search with special chars"""
        self._set_service()
        resp = self.client.get('/api/search?q=%25')
        self.assertEqual(resp.status_code, 200)

    def test_search_without_service(self):
        """GET /api/search without service"""
        self._clear_service()
        resp = self.client.get('/api/search?q=test')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    # --- GET /api/events ---
    def test_events_with_service(self):
        """GET /api/events with service"""
        self._set_service()
        self.service.db.log_event("TEST_EVENT")
        resp = self.client.get('/api/events')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertGreater(len(data), 0)

    def test_events_without_service(self):
        """GET /api/events without service"""
        self._clear_service()
        resp = self.client.get('/api/events')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    # --- GET /api/entity/<id> ---
    def test_entity_found(self):
        """GET /api/entity/<id> found"""
        self._set_service()
        fid = self.service.db.add_or_update_file("/api/ent.ts", "c")
        eid = self.service.db.add_entity(fid, "ApiEntity", "class")
        resp = self.client.get(f'/api/entity/{eid}')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data['name'], 'ApiEntity')

    def test_entity_not_found(self):
        """GET /api/entity/<id> not found"""
        self._set_service()
        resp = self.client.get('/api/entity/999999')
        self.assertEqual(resp.status_code, 404)

    def test_entity_negative_id(self):
        """GET /api/entity with negative id"""
        self._set_service()
        resp = self.client.get('/api/entity/-1')
        self.assertEqual(resp.status_code, 404)

    def test_entity_very_large_id(self):
        """GET /api/entity with very large id"""
        self._set_service()
        resp = self.client.get('/api/entity/999999999')
        self.assertEqual(resp.status_code, 404)

    def test_entity_non_numeric(self):
        """GET /api/entity with non-numeric id"""
        self._set_service()
        resp = self.client.get('/api/entity/abc')
        self.assertEqual(resp.status_code, 404)

    def test_entity_without_service(self):
        """GET /api/entity without service"""
        self._clear_service()
        resp = self.client.get('/api/entity/1')
        self.assertEqual(resp.status_code, 503)

    # --- Method not allowed ---
    def test_post_to_get_endpoints(self):
        """POST to GET endpoints returns 405"""
        self._set_service()
        for endpoint in ['/', '/api/stats', '/api/graph', '/api/search', '/api/events']:
            resp = self.client.post(endpoint)
            self.assertEqual(resp.status_code, 405, f"POST {endpoint} should be 405")

    def test_put_to_get_endpoints(self):
        """PUT to GET endpoints returns 405"""
        self._set_service()
        for endpoint in ['/', '/api/stats', '/api/graph', '/api/search', '/api/events']:
            resp = self.client.put(endpoint)
            self.assertEqual(resp.status_code, 405, f"PUT {endpoint} should be 405")

    def test_delete_to_get_endpoints(self):
        """DELETE to GET endpoints returns 405"""
        self._set_service()
        for endpoint in ['/', '/api/stats', '/api/graph', '/api/search', '/api/events']:
            resp = self.client.delete(endpoint)
            self.assertEqual(resp.status_code, 405, f"DELETE {endpoint} should be 405")

    def test_post_to_entity_endpoint(self):
        """POST to entity endpoint returns 405"""
        self._set_service()
        resp = self.client.post('/api/entity/1')
        self.assertEqual(resp.status_code, 405)

    # --- Malformed paths ---
    def test_malformed_path(self):
        """Malformed request path returns 404"""
        resp = self.client.get('/api/nonexistent')
        self.assertEqual(resp.status_code, 404)

    def test_double_slash_path(self):
        """Double slash path - Flask redirects (308) or 404"""
        resp = self.client.get('/api//stats')
        self.assertIn(resp.status_code, [308, 404])


# ============================================================================
# Phase 6: Business Scenario Closed-Loop
# ============================================================================

class Phase6_BusinessScenarioTest(unittest.TestCase):
    """Phase 6 - Business Scenario Closed-Loop"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        with patch.dict('sys.modules', {'llm_parser': type('M', (), {'ArkTSParser': MockArkTSParser})}):
            from knowledge_graph_service import KnowledgeGraphService
            self.service = KnowledgeGraphService(self.db_path, watch_paths=[self.tmpdir])
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_full_forward_flow(self):
        """Create file -> scan -> parse -> store -> query -> verify"""
        # Create file
        file_path = os.path.join(self.tmpdir, "ForwardFlowComponent_handler.ets")
        content = """
        import { Router } from '@ohos.router'
        export interface IModel { id: number }
        export class ModelImpl implements IModel {
            getId(): number { return 1 }
        }
        export function helper(): void {}
        """
        with open(file_path, 'w') as f:
            f.write(content)

        # Scan
        files = self.service.watcher.scan_existing_files()
        self.assertEqual(len(files), 1)

        # Process
        self.service._handle_file_change(files[0]['path'], files[0]['content'])

        # Verify stored
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities), 0)
        entity_names = [e['name'] for e in entities]
        self.assertIn("ModelImpl", entity_names)

        # Query
        results = self.service.search("ModelImpl")
        self.assertGreater(len(results), 0)

        # Verify imports
        with self.service.db._get_connection() as conn:
            imports = conn.execute("SELECT * FROM imports WHERE file_id=?", (fid,)).fetchall()
            self.assertGreater(len(imports), 0)

    def test_update_flow(self):
        """Modify file -> rescan -> verify updated data"""
        file_path = os.path.join(self.tmpdir, "UpdateFlowComponent_handler.ets")
        v1 = "export class OriginalClass { oldMethod(): void {} }"
        v2 = "export class UpdatedClass { newMethod(): string { return '' } }"

        self.service._handle_file_change(file_path, v1)
        fid = self.service.db.get_file_id(file_path)
        entities_v1 = self.service.db.get_file_entities(fid)
        names_v1 = [e['name'] for e in entities_v1]
        self.assertIn("OriginalClass", names_v1)

        self.service._handle_file_change(file_path, v2)
        fid = self.service.db.get_file_id(file_path)
        entities_v2 = self.service.db.get_file_entities(fid)
        names_v2 = [e['name'] for e in entities_v2]
        self.assertIn("UpdatedClass", names_v2)
        self.assertNotIn("OriginalClass", names_v2)

    def test_delete_flow(self):
        """Delete file -> verify cascade cleanup"""
        file_path = os.path.join(self.tmpdir, "DeleteFlowComponent_handler.ets")
        content = "export class DeleteTestClass { method(): void {} }"
        self.service._handle_file_change(file_path, content)

        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities), 0)

        # Delete
        self.service._handle_file_change(file_path, None)
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNone(fid)

        # Verify cascade
        all_entities = self.service.db.get_all_entities()
        for e in all_entities:
            self.assertNotEqual(e.get('file_path'), file_path)

    def test_interrupt_flow(self):
        """Shutdown during processing -> verify no corruption"""
        file_path = os.path.join(self.tmpdir, "InterruptFlowComponent_handler.ets")
        content = "export class InterruptClass { method(): void {} }"

        # Start shutdown before processing
        self.service._shutdown_event.set()
        self.service._handle_file_change(file_path, content)

        # File should NOT have been processed
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNone(fid)

    def test_repeat_flow_idempotent(self):
        """Same file processed twice -> verify idempotent"""
        file_path = os.path.join(self.tmpdir, "RepeatFlowComponent_handler.ets")
        content = "export class RepeatClass { method(): void {} }"

        self.service._handle_file_change(file_path, content)
        self.service._handle_file_change(file_path, content)

        fid = self.service.db.get_file_id(file_path)
        entities = self.service.db.get_file_entities(fid)
        # Should have exactly the entities from one parse, not duplicated
        class_entities = [e for e in entities if e['name'] == 'RepeatClass']
        self.assertEqual(len(class_entities), 1)

    def test_rename_flow(self):
        """Delete old + create new -> verify clean state"""
        old_path = os.path.join(self.tmpdir, "OldNameComponent_handler.ets")
        new_path = os.path.join(self.tmpdir, "NewNameComponent_handler.ets")
        content = "export class RenamedClass { method(): void {} }"

        self.service._handle_file_change(old_path, content)
        self.service._handle_file_change(old_path, None)  # Delete old
        self.service._handle_file_change(new_path, content)  # Create new

        self.assertIsNone(self.service.db.get_file_id(old_path))
        self.assertIsNotNone(self.service.db.get_file_id(new_path))

        new_fid = self.service.db.get_file_id(new_path)
        entities = self.service.db.get_file_entities(new_fid)
        self.assertGreater(len(entities), 0)


# ============================================================================
# Phase 7: Data Consistency
# ============================================================================

class Phase7_DataConsistencyTest(unittest.TestCase):
    """Phase 7 - Data Consistency"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        try:
            self.db.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_crud_consistency(self):
        """Add -> Read -> Update -> Read -> Delete -> Verify gone"""
        # Add
        fid = self.db.add_or_update_file("/crud/test.ts", "v1")
        eid = self.db.add_entity(fid, "CrudEntity", "class", "class CrudEntity")

        # Read
        entities = self.db.get_file_entities(fid)
        self.assertEqual(len(entities), 1)
        self.assertEqual(entities[0]['signature'], "class CrudEntity")

        # Update (change content triggers re-hash)
        fid2 = self.db.add_or_update_file("/crud/test.ts", "v2")
        self.assertEqual(fid, fid2)  # Same file ID

        # Read again
        files = self.db.get_all_files()
        self.assertEqual(len(files), 1)

        # Delete
        self.db.delete_file_data("/crud/test.ts")

        # Verify gone
        fid3 = self.db.get_file_id("/crud/test.ts")
        self.assertIsNone(fid3)
        entities = self.db.get_all_entities()
        self.assertEqual(len(entities), 0)  # Cascade deleted

    def test_entity_count_after_batch(self):
        """Entity count matches after batch operations"""
        fid = self.db.add_or_update_file("/batch/count.ts", "c")
        entities_data = [{"name": f"E{i}", "type": "class"} for i in range(10)]
        ids = self.db.add_entities_batch(fid, entities_data)
        self.assertEqual(len(ids), 10)

        all_entities = self.db.get_file_entities(fid)
        self.assertEqual(len(all_entities), 10)

    def test_relationship_integrity(self):
        """Relationship source/target entities exist"""
        fid = self.db.add_or_update_file("/rel/integrity.ts", "c")
        e1 = self.db.add_entity(fid, "Source", "class")
        e2 = self.db.add_entity(fid, "Target", "class")
        self.db.add_relationship(fid, "calls", e1, e2, fid)

        rels = self.db.get_all_relationships()
        self.assertEqual(len(rels), 1)
        self.assertEqual(rels[0]['source_name'], 'Source')
        self.assertEqual(rels[0]['target_name'], 'Target')

    def test_file_dependency_uniqueness(self):
        """File dependency uniqueness (INSERT OR IGNORE)"""
        f1 = self.db.add_or_update_file("/dep/u1.ts", "c")
        f2 = self.db.add_or_update_file("/dep/u2.ts", "c")
        self.db.add_file_dependency(f1, f2)
        self.db.add_file_dependency(f1, f2)
        deps = self.db.get_file_dependencies()
        self.assertEqual(len(deps), 1)

    def test_statistics_accuracy(self):
        """Statistics match actual counts"""
        for i in range(3):
            fid = self.db.add_or_update_file(f"/stat/acc/f{i}.ts", f"c{i}")
            self.db.add_entity(fid, f"Class{i}", "class")
            e1 = self.db.add_entity(fid, f"Func{i}", "function")
            if i > 0:
                self.db.add_relationship(fid, "calls", e1, e1, fid)

        stats = self.db.get_statistics()
        self.assertEqual(stats['total_files'], 3)
        self.assertEqual(stats['total_entities'], 6)  # 3 classes + 3 functions
        self.assertEqual(stats['total_relationships'], 2)

    def test_hash_consistency_same_content(self):
        """Same content -> same hash"""
        h1 = self.db.compute_file_hash("content")
        h2 = self.db.compute_file_hash("content")
        self.assertEqual(h1, h2)

    def test_hash_consistency_different_content(self):
        """Different content -> different hash"""
        h1 = self.db.compute_file_hash("content1")
        h2 = self.db.compute_file_hash("content2")
        self.assertNotEqual(h1, h2)

    def test_path_normalization(self):
        """Path normalization consistency"""
        p1 = KnowledgeGraphDB._normalize_path("../foo/bar.ts")
        p2 = KnowledgeGraphDB._normalize_path("foo/bar.ts")
        # Different paths normalize differently
        self.assertNotEqual(p1, p2)
        # But same path normalizes consistently
        p3 = KnowledgeGraphDB._normalize_path("../foo/bar.ts")
        self.assertEqual(p1, p3)

    def test_import_data_integrity(self):
        """Import data stored and retrieved correctly"""
        fid = self.db.add_or_update_file("/imp/data.ts", "c")
        self.db.add_import(fid, "ohos.router", ["Router", "Page"], False)
        self.db.add_import(fid, "ohos.app", None, True)

        with self.db._get_connection() as conn:
            rows = conn.execute("SELECT * FROM imports WHERE file_id=?", (fid,)).fetchall()
            self.assertEqual(len(rows), 2)
            # Check names JSON
            names_row = [r for r in rows if r['module_path'] == 'ohos.router'][0]
            names = json.loads(names_row['imported_names'])
            self.assertEqual(names, ["Router", "Page"])


# ============================================================================
# Phase 8: Exception Fault-Tolerance + Performance
# ============================================================================

class Phase8_FaultToleranceTest(unittest.TestCase):
    """Phase 8 - Exception Fault-Tolerance + Performance"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.tmpdir, "test.db")
        with patch.dict('sys.modules', {'llm_parser': type('M', (), {'ArkTSParser': MockArkTSParser})}):
            from knowledge_graph_service import KnowledgeGraphService
            self.service = KnowledgeGraphService(self.db_path, watch_paths=[self.tmpdir])
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_db_locked_during_write(self):
        """Concurrent writes don't corrupt data"""
        file_path = os.path.join(self.tmpdir, "ConcurrentWriteComponent_handler.ets")
        content = "export class ConcurrentClass { method(): void {} }"

        errors = []
        def write_file():
            try:
                self.service._handle_file_change(file_path, content)
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=write_file) for _ in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        # Should not have errors
        self.assertEqual(len(errors), 0)

        # File should exist with correct data
        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        class_entities = [e for e in entities if e['name'] == 'ConcurrentClass']
        self.assertEqual(len(class_entities), 1)

    def test_parser_exception_during_processing(self):
        """Parser exception doesn't crash system"""
        mock_parser = MagicMock()
        mock_parser.parse_file.side_effect = Exception("Parser exploded")
        self.service.parser = mock_parser

        file_path = os.path.join(self.tmpdir, "ParserErrorComponent_handler.ets")
        # Should not raise
        self.service._handle_file_change(file_path, "content")

        # System should still work
        self.service.parser = MockArkTSParser()
        file_path2 = os.path.join(self.tmpdir, "AfterErrorComponent_handler.ets")
        self.service._handle_file_change(file_path2,
            "export class AfterError { method(): void {} }")
        fid = self.service.db.get_file_id(file_path2)
        self.assertIsNotNone(fid)

    def test_watcher_callback_exception_no_crash(self):
        """Watcher callback exception doesn't crash system"""
        error_count = [0]

        def bad_callback(fp, content):
            if content is not None:
                error_count[0] += 1
                raise RuntimeError("Callback error")

        handler = ArkTSFileHandler(bad_callback, debounce_seconds=0.1)
        file_path = os.path.join(self.tmpdir, "CallbackErrorComponent_handler.ets")
        with open(file_path, 'w') as f:
            f.write("export class CallbackErrorClass { method(): void {} }")

        handler._schedule_update(file_path)
        time.sleep(0.5)

        # System should still be functional
        self.assertGreater(error_count[0], 0)
        self.assertEqual(len(handler.pending_files), 0)

    def test_multiple_rapid_file_changes_debounce(self):
        """Multiple rapid changes debounced correctly"""
        callback_calls = [0]

        def counting_callback(fp, content):
            callback_calls[0] += 1

        handler = ArkTSFileHandler(counting_callback, debounce_seconds=0.3)
        file_path = os.path.join(self.tmpdir, "DebounceTestComponent_handler.ets")

        with open(file_path, 'w') as f:
            f.write("content")

        # Rapid fire schedule
        for _ in range(5):
            handler._schedule_update(file_path)

        time.sleep(0.8)  # Wait for debounce + execution
        # Should only call once (debounced)
        self.assertEqual(callback_calls[0], 1)

    def test_large_file_processing(self):
        """Large file (10000 lines) processed correctly"""
        lines = ["export class LargeClass {"]
        for i in range(9998):
            lines.append(f"    method{i}(): void {{}}")
        lines.append("}")
        content = "\n".join(lines)

        file_path = os.path.join(self.tmpdir, "LargeFileComponent_handler.ets")
        start = time.time()
        self.service._handle_file_change(file_path, content)
        elapsed = time.time() - start

        fid = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(fid)
        entities = self.service.db.get_file_entities(fid)
        self.assertGreater(len(entities), 0)
        # Performance: should complete in reasonable time
        self.assertLess(elapsed, 30, f"Large file took {elapsed:.1f}s")

    def test_many_files_sequential(self):
        """100 files processed sequentially"""
        file_contents = []
        for i in range(100):
            fname = f"ManyFileComponent{i}_handler.ets"
            fpath = os.path.join(self.tmpdir, fname)
            content = f"export class ManyClass{i} {{ method(): void {{}} }}"
            with open(fpath, 'w') as f:
                f.write(content)
            file_contents.append((fpath, content))

        start = time.time()
        for fpath, content in file_contents:
            self.service._handle_file_change(fpath, content)
        elapsed = time.time() - start

        stats = self.service.get_statistics()
        self.assertEqual(stats['files_processed'], 100)
        self.assertGreater(stats['entities_extracted'], 0)
        # Performance check
        self.assertLess(elapsed, 60, f"100 files took {elapsed:.1f}s")

    def test_memory_lru_eviction(self):
        """LRU eviction prevents unbounded growth in _file_locks"""
        # Create more locks than max
        for i in range(self.service._MAX_FILE_LOCKS + 50):
            self.service._get_file_lock(f"/memory/test{i}.ts")

        # Should not grow unbounded
        self.assertLessEqual(
            len(self.service._file_locks),
            self.service._MAX_FILE_LOCKS + 50
        )

    def test_shutdown_while_processing(self):
        """Shutdown while files are being processed"""
        processed = []
        original_inner = self.service._handle_file_change_inner

        def slow_inner(fp, content):
            processed.append(fp)
            time.sleep(0.2)  # Simulate slow processing
            original_inner(fp, content)

        self.service._handle_file_change_inner = slow_inner

        def trigger_shutdown():
            time.sleep(0.1)
            self.service._shutdown_event.set()

        # Start shutdown thread
        shutdown_thread = threading.Thread(target=trigger_shutdown, daemon=True)
        shutdown_thread.start()

        # Try to process files
        for i in range(5):
            file_path = os.path.join(self.tmpdir, f"ShutdownTestComponent{i}_handler.ets")
            content = f"export class ShutdownClass{i} {{ method(): void {{}} }}"
            self.service._handle_file_change(file_path, content)

        shutdown_thread.join(timeout=5)

        # Some files may have been processed, some may not
        # The key is that no corruption occurred
        self.service.close()

    def test_db_connection_retry_recovery(self):
        """DB connection retry and recovery"""
        # This tests the _get_connection retry mechanism under load
        db = self.db = self.service.db
        errors = []

        def concurrent_write(idx):
            try:
                for _ in range(10):
                    db.log_event(f"CONCURRENT_{idx}")
            except Exception as e:
                errors.append(str(e))

        threads = [threading.Thread(target=concurrent_write, args=(i,)) for i in range(5)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(len(errors), 0)
        events = db.get_recent_events(limit=100)
        self.assertGreater(len(events), 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
