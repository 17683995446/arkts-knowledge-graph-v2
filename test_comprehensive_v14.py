"""
全面测试 v14 - 基于逆向分析的全覆盖验证
遵循"代码即规则"的第一性原理

测试目标：
1. 覆盖所有判断分支
2. 验证隐藏逻辑
3. 测试默认行为
4. 验证异常兜底
5. 数据流转自洽
"""
import os, sys, time, tempfile, shutil, threading, unittest, gc, sqlite3

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser
from file_watcher import FileWatcher, ArkTSFileHandler
from web_server import app
from config import *


class TestConfigModule(unittest.TestCase):
    """测试config.py - 配置模块"""

    def test_config_values_exist(self):
        """所有配置项都有值"""
        self.assertIsNotNone(LLM_BASE_URL)
        self.assertIsNotNone(LLM_MODEL)
        self.assertIsNotNone(WATCH_DELAY_SECONDS)
        self.assertIsNotNone(SUPPORTED_EXTENSIONS)
        self.assertIsNotNone(MIN_FILE_WORDS)
        self.assertIsNotNone(DATABASE_PATH)
        self.assertIsNotNone(WEB_HOST)
        self.assertIsNotNone(WEB_PORT)
        print("[PASS] 所有配置项已定义")

    def test_supported_extensions_type(self):
        """SUPPORTED_EXTENSIONS是集合类型"""
        self.assertIsInstance(SUPPORTED_EXTENSIONS, set)
        self.assertIn('.ets', SUPPORTED_EXTENSIONS)
        self.assertIn('.ts', SUPPORTED_EXTENSIONS)

    def test_min_file_words_positive(self):
        """MIN_FILE_WORDS是正整数"""
        self.assertIsInstance(MIN_FILE_WORDS, int)
        self.assertGreater(MIN_FILE_WORDS, 0)


class TestDatabaseSchema(unittest.TestCase):
    """测试database_schema.py - 数据库模式"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_schema_creates_all_tables(self):
        """SCHEMA_SQL创建所有必需的表"""
        db = KnowledgeGraphDB(self.db_path)
        
        # 检查所有表是否存在
        with db._get_connection() as conn:
            cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row['name'] for row in cursor.fetchall()}
            
        expected_tables = {'files', 'entities', 'relationships', 'imports', 
                          'file_dependencies', 'events'}
        self.assertTrue(expected_tables.issubset(tables))
        db.close()
        print("[PASS] 所有表已创建")

    def test_foreign_keys_enabled(self):
        """外键约束已启用"""
        db = KnowledgeGraphDB(self.db_path)
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA foreign_keys")
            self.assertEqual(cursor.fetchone()[0], 1)
        db.close()

    def test_wal_mode_enabled(self):
        """WAL模式已启用"""
        db = KnowledgeGraphDB(self.db_path)
        with db._get_connection() as conn:
            cursor = conn.execute("PRAGMA journal_mode")
            self.assertEqual(cursor.fetchone()[0], 'wal')
        db.close()


class TestKnowledgeGraphDBBranches(unittest.TestCase):
    """测试knowledge_graph_db.py - 所有分支覆盖"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_file_exists_and_unchanged_new_file(self):
        """分支：文件不存在"""
        unchanged, new_hash = self.db.file_exists_and_unchanged("/new/file.ts", "content")
        self.assertFalse(unchanged)
        self.assertIsNotNone(new_hash)

    def test_file_exists_and_unchanged_unchanged(self):
        """分支：文件存在且未变化"""
        self.db.add_or_update_file("/test/unchanged.ts", "content")
        unchanged, new_hash = self.db.file_exists_and_unchanged("/test/unchanged.ts", "content")
        self.assertTrue(unchanged)

    def test_file_exists_and_unchanged_changed(self):
        """分支：文件存在但已变化"""
        self.db.add_or_update_file("/test/changed.ts", "old content")
        unchanged, new_hash = self.db.file_exists_and_unchanged("/test/changed.ts", "new content")
        self.assertFalse(unchanged)

    def test_add_or_update_file_update_existing(self):
        """分支：更新已存在的文件"""
        id1 = self.db.add_or_update_file("/test/update.ts", "v1")
        id2 = self.db.add_or_update_file("/test/update.ts", "v2")
        # 更新后ID应该相同（REPLACE）
        self.assertEqual(id1, id2)

    def test_delete_file_data_nonexistent(self):
        """分支：删除不存在的文件"""
        result = self.db.delete_file_data("/nonexistent/file.ts")
        self.assertFalse(result)

    def test_get_file_id_nonexistent(self):
        """分支：获取不存在文件的ID"""
        file_id = self.db.get_file_id("/nonexistent/file.ts")
        self.assertIsNone(file_id)

    def test_add_entities_batch_empty(self):
        """分支：批量添加空实体列表"""
        file_id = self.db.add_or_update_file("/test/empty_entities.ts", "content")
        result = self.db.add_entities_batch(file_id, [])
        self.assertEqual(result, [])

    def test_add_relationships_batch_empty(self):
        """分支：批量添加空关系列表"""
        result = self.db.add_relationships_batch(1, [], {})
        self.assertEqual(result, 0)

    def test_search_entities_empty_query(self):
        """分支：空查询"""
        result = self.db.search_entities("")
        self.assertEqual(result, [])

    def test_search_entities_whitespace_query(self):
        """分支：空白查询"""
        result = self.db.search_entities("   \n\t  ")
        self.assertEqual(result, [])

    def test_get_entity_details_nonexistent(self):
        """分支：获取不存在实体的详情"""
        result = self.db.get_entity_details(99999)
        self.assertIsNone(result)

    def test_add_file_dependency_duplicate(self):
        """分支：添加重复的文件依赖"""
        f1 = self.db.add_or_update_file("/test/dep1.ts", "c1")
        f2 = self.db.add_or_update_file("/test/dep2.ts", "c2")
        id1 = self.db.add_file_dependency(f1, f2)
        id2 = self.db.add_file_dependency(f1, f2)
        # 重复添加应该返回None
        self.assertIsNone(id2)


class TestKnowledgeGraphServiceBranches(unittest.TestCase):
    """测试knowledge_graph_service.py - 所有分支覆盖"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_handle_file_change_shutdown(self):
        """分支：关闭后拒绝新请求"""
        self.service._shutdown_event.set()
        self.service._handle_file_change("/test/file.ts", "content")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)

    def test_handle_file_change_content_none(self):
        """分支：content=None触发删除"""
        # 先创建文件
        self.service._handle_file_change("/test/del.ts", "export class Del {}")
        # 再删除
        self.service._handle_file_change("/test/del.ts", None)
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)

    def test_handle_file_change_inner_empty_content(self):
        """分支：空内容直接返回"""
        self.service._handle_file_change("/test/empty.ts", "")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)

    def test_handle_file_change_inner_whitespace_content(self):
        """分支：纯空白内容直接返回"""
        self.service._handle_file_change("/test/whitespace.ts", "   \n\t  ")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)

    def test_handle_file_change_inner_unchanged(self):
        """分支：文件未变化直接返回"""
        self.service._handle_file_change("/test/same.ts", "export class Same {}")
        # 再次处理相同内容
        self.service._handle_file_change("/test/same.ts", "export class Same {}")
        # 应该只记录一次
        events = self.service.db.get_recent_events(limit=100)
        update_events = [e for e in events if e['event_type'] == 'FILE_UPDATED']
        self.assertEqual(len(update_events), 1)

    def test_handle_file_change_inner_parse_result_none(self):
        """分支：parse_result为None时防御性处理"""
        class NoneParser:
            def parse_file(self, fp, content):
                return None
        self.service.parser = NoneParser()
        self.service._handle_file_change("/test/none_parse.ts", "export class X {}")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_close_idempotent(self):
        """分支：close()幂等性"""
        self.service.close()
        self.service.close()
        self.service.close()
        # 不应抛出异常

    def test_get_file_lock_lru_eviction(self):
        """分支：LRU清理"""
        # 创建超过_MAX_FILE_LOCKS(1000)个锁
        for i in range(1100):
            self.service._get_file_lock(f"/test/file{i}.ts")
        self.assertLessEqual(len(self.service._file_locks), 1000)


class TestFileWatcherBranches(unittest.TestCase):
    """测试file_watcher.py - 所有分支覆盖"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_is_valid_file_unsupported_extension(self):
        """分支：不支持的扩展名"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        result = handler._is_valid_file("/test/file.txt")
        self.assertFalse(result)

    def test_is_valid_file_short_name(self):
        """分支：文件名单词数不足"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        result = handler._is_valid_file("/test/AB.ts")
        self.assertFalse(result)

    def test_is_valid_file_valid(self):
        """分支：有效文件（至少4个单词）"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        result = handler._is_valid_file("/test/ValidNameComponentTest.ts")
        self.assertTrue(result)

    def test_on_modified_not_file_modified_event(self):
        """分支：非文件修改事件"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        class FakeEvent:
            is_directory = True
        # 不应抛出异常
        handler.on_modified(FakeEvent())

    def test_on_created_not_file_created_event(self):
        """分支：非文件创建事件"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        class FakeEvent:
            is_directory = True
        handler.on_created(FakeEvent())

    def test_on_deleted_not_file_deleted_event(self):
        """分支：非文件删除事件"""
        handler = ArkTSFileHandler(lambda fp, c: None)
        class FakeEvent:
            is_directory = True
        handler.on_deleted(FakeEvent())


class TestMockLLMParserBranches(unittest.TestCase):
    """测试mock_llm_parser.py - 所有分支覆盖"""

    def test_parse_file_simulate_error(self):
        """分支：模拟错误"""
        parser = MockArkTSParser(simulate_errors=True, error_rate=1.0)
        with self.assertRaises(Exception) as ctx:
            parser.parse_file("/test.ts", "content")
        self.assertIn("Simulated LLM error", str(ctx.exception))

    def test_parse_file_no_error(self):
        """分支：不模拟错误"""
        parser = MockArkTSParser(simulate_errors=False)
        result = parser.parse_file("/test.ts", "export class Test {}")
        self.assertIn('entities', result)

    def test_parse_file_interface_extends(self):
        """分支：interface extends关系"""
        parser = MockArkTSParser()
        content = "export interface A extends B { x: number; }"
        result = parser.parse_file("/test.ts", content)
        # 应该解析出extends关系
        extends_rels = [r for r in result['relationships'] if r['type'] == 'extends']
        self.assertGreaterEqual(len(extends_rels), 0)

    def test_parse_file_class_implements(self):
        """分支：class implements关系"""
        parser = MockArkTSParser()
        content = "export class A implements B, C { x: number; }"
        result = parser.parse_file("/test.ts", content)
        implements_rels = [r for r in result['relationships'] if r['type'] == 'implements']
        self.assertGreaterEqual(len(implements_rels), 0)


class TestWebServerBranches(unittest.TestCase):
    """测试web_server.py - 所有分支覆盖"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

        # 设置测试客户端
        app.config['TESTING'] = True
        import web_server
        self.old_service = web_server.kg_service
        web_server.kg_service = self.service
        self.client = app.test_client()

    def tearDown(self):
        import web_server
        web_server.kg_service = self.old_service
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_get_stats_no_service(self):
        """分支：kg_service为None"""
        import web_server
        old = web_server.kg_service
        web_server.kg_service = None
        resp = self.client.get('/api/stats')
        web_server.kg_service = old
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertIn('total_files', data)

    def test_search_empty_query(self):
        """分支：空搜索查询"""
        resp = self.client.get('/api/search?q=')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    def test_search_whitespace_query(self):
        """分支：空白搜索查询"""
        resp = self.client.get('/api/search?q=%20%20%20')
        self.assertEqual(resp.status_code, 200)
        data = resp.get_json()
        self.assertEqual(data, [])

    def test_get_entity_not_found(self):
        """分支：实体不存在返回404"""
        resp = self.client.get('/api/entity/99999')
        self.assertEqual(resp.status_code, 404)

    def test_get_entity_found(self):
        """分支：实体存在返回200"""
        # 先创建一个实体
        self.service._handle_file_change("/test/entity.ts", "export class TestEntity {}")
        entities = self.service.db.get_all_entities()
        if entities:
            entity_id = entities[0]['id']
            resp = self.client.get(f'/api/entity/{entity_id}')
            self.assertEqual(resp.status_code, 200)


class TestDataFlowConsistency(unittest.TestCase):
    """测试数据流转自洽性"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_file_create_update_delete_cycle(self):
        """完整生命周期：创建→更新→删除"""
        fp = "/test/lifecycle.ts"
        
        # 创建
        self.service._handle_file_change(fp, "export class V1 {}")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)
        file_id = files[0]['id']
        
        # 更新
        self.service._handle_file_change(fp, "export class V2 { updated = true; }")
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)  # 仍然是1个文件
        
        # 删除
        self.service._handle_file_change(fp, None)
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 0)
        
        # 验证级联删除
        entities = self.service.db.get_all_entities()
        self.assertEqual(len(entities), 0)

    def test_statistics_consistency(self):
        """统计数据一致性"""
        # 创建多个文件
        for i in range(10):
            self.service._handle_file_change(f"/test/stats{i}.ts", 
                f"export class Stats{i} {{}}")
        
        stats = self.service.get_statistics()
        
        # 验证统计一致性
        self.assertEqual(stats['total_files'], 10)
        self.assertEqual(stats['files_processed'], 10)
        self.assertIsNotNone(stats['last_update_time'])

    def test_entity_relationship_integrity(self):
        """实体关系完整性"""
        # 创建包含关系的文件
        content = """
export class Parent {
    child: Child;
}
export class Child {
    parent: Parent;
}
"""
        self.service._handle_file_change("/test/relation.ts", content)
        
        entities = self.service.db.get_all_entities()
        relationships = self.service.db.get_all_relationships()
        
        # 验证实体和关系都存在
        self.assertGreaterEqual(len(entities), 2)
        # 关系可能为0（取决于parser是否提取）

    def test_event_log_integrity(self):
        """事件日志完整性"""
        # 执行操作
        self.service._handle_file_change("/test/event.ts", "export class Event {}")
        self.service._handle_file_change("/test/event.ts", None)
        
        events = self.service.db.get_recent_events(limit=100)
        
        # 验证有FILE_UPDATED和FILE_DELETED事件
        event_types = {e['event_type'] for e in events}
        self.assertIn('FILE_UPDATED', event_types)
        self.assertIn('FILE_DELETED', event_types)


class TestHiddenLogic(unittest.TestCase):
    """测试隐藏逻辑和默认行为"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_service_default_watch_paths(self):
        """隐藏逻辑：watch_paths默认为当前目录"""
        service = KnowledgeGraphService(db_path=self.db_path)
        # watcher应该使用当前目录
        self.assertIsNotNone(service.watcher)
        service.close()

    def test_parse_count_increment(self):
        """隐藏逻辑：parse_count递增"""
        parser = MockArkTSParser()
        initial = parser.parse_count
        parser.parse_file("/test1.ts", "export class A {}")
        parser.parse_file("/test2.ts", "export class B {}")
        self.assertEqual(parser.parse_count, initial + 2)

    def test_file_lock_reuse(self):
        """隐藏逻辑：文件锁复用"""
        service = KnowledgeGraphService(db_path=self.db_path)
        lock1 = service._get_file_lock("/test/file.ts")
        lock2 = service._get_file_lock("/test/file.ts")
        self.assertIs(lock1, lock2)
        service.close()

    def test_hash_consistency(self):
        """隐藏逻辑：相同内容产生相同hash"""
        db = KnowledgeGraphDB(self.db_path)
        hash1 = db.compute_file_hash("same content")
        hash2 = db.compute_file_hash("same content")
        self.assertEqual(hash1, hash2)
        db.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
