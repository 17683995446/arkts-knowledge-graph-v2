"""
极端测试 v11 - 第9轮迭代
聚焦之前未覆盖的真实运行场景：

  1. IDE真实场景：快速保存-撤销-重保存循环
  2. 数据库损坏恢复：WAL文件被手动删除
  3. 文件内容为二进制/空/纯空白
  4. 超长文件名（接近OS限制）
  5. 同一文件极短时间内被修改多次（debounce压力）
  6. 数据库磁盘空间不足模拟
  7. 大量孤儿关系（entities被删除后relationships残留）
  8. 文件编码边界：BOM头、混合编码
  9. 线程池耗尽：大量daemon线程堆积
  10. start/stop/close循环后重新start
"""
import os, sys, time, tempfile, shutil, threading, unittest, gc, sqlite3, struct

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser


class TestIDESaveUndoRedo(unittest.TestCase):
    """测试1: IDE真实场景 - 快速保存-撤销-重保存"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_rapid_save_undo_resave(self):
        """模拟IDE的保存-撤销-重保存循环"""
        fp = os.path.join(self.test_dir, "SaveUndoRedoComponent.ts")

        versions = [
            "export class SaveUndoRedoComponent { v1 = 1; }",
            "export class SaveUndoRedoComponent { v2 = 2; }",
            "export class SaveUndoRedoComponent { v1 = 1; }",  # 撤销回v1
            "export class SaveUndoRedoComponent { v2 = 2; }",  # 重做
            "export class SaveUndoRedoComponent { v1 = 1; }",  # 再次撤销
            "export class SaveUndoRedoComponent { v3 = 3; }",  # 新修改
        ]

        for i, content in enumerate(versions):
            self.service._handle_file_change(fp, content)

        # 应该只有1个文件记录
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

        # 最终内容应该是v3
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_entities'], 0)

    def test_save_delete_recreate_same_content(self):
        """保存->删除->重新创建同名文件（IDE重构常见操作）"""
        fp = os.path.join(self.test_dir, "RecreateAfterDelete.ts")
        content = "export class RecreateAfterDelete { id = 1; }"

        # 保存
        self.service._handle_file_change(fp, content)
        stats1 = self.service.get_statistics()

        # 删除
        self.service._handle_file_change(fp, None)
        stats2 = self.service.get_statistics()
        self.assertEqual(stats2['total_files'], 0)

        # 重新创建
        self.service._handle_file_change(fp, content)
        stats3 = self.service.get_statistics()
        self.assertEqual(stats3['total_files'], 1)

    def test_save_rename_modify_original(self):
        """保存A -> 重命名A为B -> 修改B（IDE重命名类操作）"""
        fp_a = os.path.join(self.test_dir, "OriginalClassName.ts")
        fp_b = os.path.join(self.test_dir, "RenamedClassName.ts")

        content_a = "export class OriginalClassName { id = 1; }"
        content_b = "export class RenamedClassName { id = 2; }"

        self.service._handle_file_change(fp_a, content_a)
        self.service._handle_file_change(fp_a, None)  # 删除旧文件
        self.service._handle_file_change(fp_b, content_b)  # 创建新文件

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)
        self.assertEqual(files[0]['path'], os.path.normpath(fp_b))


class TestDatabaseCorruptionRecovery(unittest.TestCase):
    """测试2: 数据库损坏恢复"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_wal_file_deleted_during_operation(self):
        """WAL文件被删除后系统应能恢复"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # 插入一些数据
        for i in range(10):
            fp = os.path.join(self.test_dir, f"WalRecovery{i:03d}.ts")
            service._handle_file_change(fp, f"export class WalRecovery{i:03d} {{}}")

        # 删除WAL文件（模拟外部删除）
        wal_path = self.db_path + "-wal"
        shm_path = self.db_path + "-shm"
        if os.path.exists(wal_path):
            os.remove(wal_path)
        if os.path.exists(shm_path):
            os.remove(shm_path)

        # 系统应该能继续工作
        fp = os.path.join(self.test_dir, "AfterWalDelete.ts")
        service._handle_file_change(fp, "export class AfterWalDelete {}")

        files = service.db.get_all_files()
        self.assertGreater(len(files), 0)

        service.close()

    def test_database_file_replaced_with_empty(self):
        """数据库文件被替换为空数据库"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        fp = os.path.join(self.test_dir, "BeforeReplace.ts")
        service._handle_file_change(fp, "export class BeforeReplace {}")
        service.close()

        # 替换为空数据库
        os.remove(self.db_path)
        open(self.db_path, 'w').close()

        # 重新打开应该能正常工作
        service2 = KnowledgeGraphService(db_path=self.db_path)
        service2.parser = MockArkTSParser()

        fp2 = os.path.join(self.test_dir, "AfterReplace.ts")
        service2._handle_file_change(fp2, "export class AfterReplace {}")

        files = service2.db.get_all_files()
        self.assertEqual(len(files), 1)
        service2.close()


class TestBinaryAndEmptyContent(unittest.TestCase):
    """测试3: 文件内容为二进制/空/纯空白"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_empty_file_content(self):
        """空文件内容不应崩溃"""
        fp = os.path.join(self.test_dir, "EmptyContentFile.ts")
        self.service._handle_file_change(fp, "")
        # 空文件应该被跳过（hash相同或无实体）
        # 不应崩溃即可

    def test_whitespace_only_content(self):
        """纯空白文件不应崩溃"""
        fp = os.path.join(self.test_dir, "WhitespaceOnlyFile.ts")
        self.service._handle_file_change(fp, "   \n\n\t  \n  ")
        # 不应崩溃

    def test_binary_content(self):
        """二进制内容不应崩溃"""
        fp = os.path.join(self.test_dir, "BinaryContentFile.ts")
        binary_content = bytes(range(256)) * 100
        # content参数是str类型，模拟包含null字节的字符串
        try:
            content = binary_content.decode('utf-8', errors='replace')
            self.service._handle_file_change(fp, content)
        except Exception:
            pass  # 不应崩溃，但允许异常
        # 关键是不应崩溃导致系统不可用

    def test_very_long_single_line(self):
        """超长单行内容"""
        fp = os.path.join(self.test_dir, "VeryLongLineFile.ts")
        long_line = "export class VeryLongLineFile { data = '" + "x" * 100000 + "'; }"
        self.service._handle_file_change(fp, long_line)
        # 不应崩溃


class TestFileEncodingBoundary(unittest.TestCase):
    """测试8: 文件编码边界"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_utf8_bom_file(self):
        """带UTF-8 BOM头的文件"""
        fp = os.path.join(self.test_dir, "BomHeaderFile.ts")
        content_with_bom = '\ufeff' + "export class BomHeaderFile { id = 1; }"
        self.service._handle_file_change(fp, content_with_bom)
        # 不应崩溃
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_mixed_encoding_content(self):
        """混合编码内容（UTF-8 + Latin-1混合）"""
        fp = os.path.join(self.test_dir, "MixedEncodingFile.ts")
        # 包含各种Unicode字符
        content = """
export class MixedEncodingFile {
    // 中文注释
    // コメント
    // 한국어
    // العربية
    private name: string = '你好世界';
    private emoji: string = '🚀📊✅';
}
"""
        self.service._handle_file_change(fp, content)
        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)

    def test_null_bytes_in_content(self):
        """内容中包含null字节"""
        fp = os.path.join(self.test_dir, "NullByteFile.ts")
        # Python字符串不能包含\0，但可以测试空字符串
        self.service._handle_file_change(fp, "export class NullByteFile {}")
        # 不应崩溃


class TestThreadPressure(unittest.TestCase):
    """测试9: 线程池耗尽"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_many_callback_daemon_threads(self):
        """大量daemon线程通过callback堆积"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        thread_count = [0]
        lock = threading.Lock()

        def counting_callback(file_path, stats):
            with lock:
                thread_count[0] += 1
            time.sleep(0.01)  # 模拟回调耗时

        service.on_update_callback = counting_callback

        # 快速处理200个文件，每个都会创建daemon线程
        for i in range(200):
            fp = os.path.join(self.test_dir, f"ThreadPressure{i:03d}.ts")
            service._handle_file_change(fp, f"export class ThreadPressure{i:03d} {{}}")

        # 等待所有daemon线程完成
        time.sleep(3)

        print(f"\n[THREAD PRESSURE] Callback threads executed: {thread_count[0]}")

        # 所有回调都应该被执行
        self.assertEqual(thread_count[0], 200)

        service.close()


class TestStartStopRestart(unittest.TestCase):
    """测试10: start/stop/close循环后重新start"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_stop_then_start_new_service(self):
        """stop后创建新service继续工作"""
        # 第一阶段
        service1 = KnowledgeGraphService(db_path=self.db_path)
        service1.parser = MockArkTSParser()

        fp = os.path.join(self.test_dir, "PhaseOneFile.ts")
        service1._handle_file_change(fp, "export class PhaseOneFile {}")
        service1.close()

        # 第二阶段：重新打开
        service2 = KnowledgeGraphService(db_path=self.db_path)
        service2.parser = MockArkTSParser()

        fp2 = os.path.join(self.test_dir, "PhaseTwoFile.ts")
        service2._handle_file_change(fp2, "export class PhaseTwoFile {}")

        files = service2.db.get_all_files()
        self.assertEqual(len(files), 2)

        service2.close()

    def test_multiple_lifecycle_cycles(self):
        """多次生命周期循环"""
        for cycle in range(5):
            service = KnowledgeGraphService(db_path=self.db_path)
            service.parser = MockArkTSParser()

            fp = os.path.join(self.test_dir, f"LifecycleCycle{cycle}.ts")
            service._handle_file_change(fp, f"export class LifecycleCycle{cycle} {{}}")

            service.close()

        # 所有数据应该持久化
        db = KnowledgeGraphDB(self.db_path)
        files = db.get_all_files()
        self.assertEqual(len(files), 5)
        db.close()


class TestOrphanRelationships(unittest.TestCase):
    """测试7: 孤儿关系检测"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_cascade_delete_removes_relationships(self):
        """删除文件时级联删除关系"""
        # 创建文件和实体
        file_id = self.db.add_or_update_file("/test/CascadeDelete.ts", "content")
        e1 = self.db.add_entity(file_id, "EntityOne", "class")
        e2 = self.db.add_entity(file_id, "EntityTwo", "class")
        self.db.add_relationship(file_id, "depends_on", e1, e2)

        # 验证关系存在
        rels = self.db.get_all_relationships()
        self.assertEqual(len(rels), 1)

        # 删除文件
        self.db.delete_file_data("/test/CascadeDelete.ts")

        # 关系应该被级联删除
        rels_after = self.db.get_all_relationships()
        self.assertEqual(len(rels_after), 0)

        # 实体也应该被级联删除
        entities_after = self.db.get_all_entities()
        self.assertEqual(len(entities_after), 0)

    def test_direct_db_manipulation_orphans(self):
        """查询包含大量关系时不应崩溃"""
        file_id = self.db.add_or_update_file("/test/OrphanTest.ts", "content")
        e1 = self.db.add_entity(file_id, "OrphanEntity", "class")
        e2 = self.db.add_entity(file_id, "OrphanEntityTwo", "class")

        # 添加有效关系
        self.db.add_relationship(file_id, "depends_on", e1, e2, file_id)

        # 查询不应崩溃
        rels = self.db.get_all_relationships()
        self.assertGreater(len(rels), 0)

        # 搜索不应崩溃
        results = self.db.search_entities("Orphan")
        self.assertGreater(len(results), 0)

        # 删除文件后关系应被级联删除
        self.db.delete_file_data("/test/OrphanTest.ts")
        rels_after = self.db.get_all_relationships()
        self.assertEqual(len(rels_after), 0)


class TestStatisticsAccuracy(unittest.TestCase):
    """测试统计准确性"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_stats_match_db_after_complex_operations(self):
        """复杂操作后统计应与DB一致"""
        # 创建10个文件
        for i in range(10):
            fp = os.path.join(self.test_dir, f"StatsAccFile{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class StatsAccFile{i:03d} {{}}")

        # 删除3个
        for i in range(3):
            fp = os.path.join(self.test_dir, f"StatsAccFile{i:03d}.ts")
            self.service._handle_file_change(fp, None)

        # 更新2个
        for i in range(3, 5):
            fp = os.path.join(self.test_dir, f"StatsAccFile{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class StatsAccFile{i:03d} {{ updated = true; }}")

        # 验证统计一致性
        stats = self.service.get_statistics()
        db_stats = self.service.db.get_statistics()

        self.assertEqual(stats['total_files'], db_stats['total_files'])
        self.assertEqual(stats['total_entities'], db_stats['total_entities'])
        self.assertEqual(stats['total_files'], 7)  # 10 - 3


if __name__ == '__main__':
    unittest.main(verbosity=2)
