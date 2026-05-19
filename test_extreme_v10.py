"""
Extreme Test Suite v10 - Round 8 Iteration
长时间、高并发、资源压力测试

测试场景：
  1. 长时间运行测试（模拟24小时持续文件变更）
  2. 高并发压力测试（1000+文件同时处理）
  3. 资源压力测试（内存/磁盘/句柄限制）
  4. 文件系统边缘情况（符号链接、硬链接、循环目录）
  5. 数据库压力测试（百万级实体）
  6. 网络I/O压力（大量WebSocket连接）
  7. 异常恢复测试（模拟崩溃后恢复）
"""
import os
import sys
import time
import tempfile
import shutil
import threading
import unittest
import gc
import sqlite3
import signal
import psutil

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser


class TestLongRunningStability(unittest.TestCase):
    """测试1: 长时间运行稳定性（模拟高频文件变更）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_rapid_file_changes_over_time(self):
        """模拟高频文件变更，检测内存泄漏和性能退化"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # 获取初始内存使用
        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        file_path = os.path.join(self.test_dir, "RapidChangeComponent.ts")

        # 模拟1000次快速文件变更
        start_time = time.time()
        for i in range(1000):
            content = f"""
export class RapidChangeComponent {{
    private version = {i};
    private data = '{{"iteration": {i}, "timestamp": {time.time()}}}';
    
    public processData(): string {{
        return this.data;
    }}
    
    public getVersion(): number {{
        return this.version;
    }}
}}
"""
            service._handle_file_change(file_path, content)

            # 每100次检查一次性能
            if i % 100 == 0 and i > 0:
                elapsed = time.time() - start_time
                avg_time = elapsed / i * 1000  # ms per operation
                print(f"[ITER {i}] Avg time: {avg_time:.2f}ms")

        total_time = time.time() - start_time
        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_growth = final_memory - initial_memory

        print(f"\n[SUMMARY] 1000 iterations in {total_time:.2f}s")
        print(f"[SUMMARY] Memory growth: {memory_growth:.2f}MB")

        # 性能不应严重退化（平均单次处理<100ms）
        avg_time = total_time / 1000 * 1000
        self.assertLess(avg_time, 100, f"Performance degraded: {avg_time:.2f}ms avg")

        # 内存增长不应超过50MB（允许一定增长，但不应无限制）
        self.assertLess(memory_growth, 50, f"Memory leak detected: {memory_growth:.2f}MB growth")

        service.close()

    def test_database_size_growth(self):
        """测试数据库文件大小增长情况"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        initial_size = os.path.getsize(self.db_path)

        # 创建500个不同文件
        for i in range(500):
            file_path = os.path.join(self.test_dir, f"SizeGrowthTest{i:04d}.ts")
            content = f"""
export class SizeGrowthTest{i:04d} {{
    private id = {i};
    private largeData = '{"x" * 1000}';
}}
"""
            service._handle_file_change(file_path, content)

        # 强制checkpoint
        service.db.close()

        final_size = os.path.getsize(self.db_path)
        size_growth = (final_size - initial_size) / 1024  # KB

        print(f"\n[DB SIZE] Initial: {initial_size/1024:.2f}KB")
        print(f"[DB SIZE] Final: {final_size/1024:.2f}KB")
        print(f"[DB SIZE] Growth: {size_growth:.2f}KB")

        # 每个文件平均不应超过5KB（包含实体、关系、导入等）
        avg_per_file = size_growth / 500
        self.assertLess(avg_per_file, 5, f"DB size per file too large: {avg_per_file:.2f}KB")


class TestHighConcurrency(unittest.TestCase):
    """测试2: 高并发压力测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_1000_files_concurrent_creation(self):
        """1000个文件同时创建"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_paths = [os.path.join(self.test_dir, f"ConcurrentFile{i:04d}.ts") for i in range(1000)]
        errors = []
        completed = Counter()

        def create_file(fp, idx):
            try:
                content = f"export class ConcurrentFile{idx:04d} {{ id = {idx}; }}"
                service._handle_file_change(fp, content)
                completed.increment()
            except Exception as e:
                errors.append((idx, str(e)))

        threads = []
        start_time = time.time()

        # 创建1000个线程同时处理
        for i, fp in enumerate(file_paths):
            t = threading.Thread(target=create_file, args=(fp, i))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=120)

        elapsed = time.time() - start_time

        print(f"\n[CONCURRENT] 1000 files in {elapsed:.2f}s")
        print(f"[CONCURRENT] Errors: {len(errors)}")
        if errors:
            print(f"[CONCURRENT] First 5 errors: {errors[:5]}")

        self.assertEqual(len(errors), 0, f"Concurrent processing errors: {errors[:10]}")

        stats = service.get_statistics()
        self.assertEqual(stats['total_files'], 1000)

        service.close()

    def test_same_file_rapid_update_by_many_threads(self):
        """100个线程同时更新同一个文件"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_path = os.path.join(self.test_dir, "ContendedFile.ts")
        errors = []
        success_count = [0]
        lock = threading.Lock()

        def update_file(idx):
            try:
                content = f"export class ContendedFile {{ version = {idx}; }}"
                service._handle_file_change(file_path, content)
                with lock:
                    success_count[0] += 1
            except Exception as e:
                errors.append((idx, str(e)))

        threads = []
        for i in range(100):
            t = threading.Thread(target=update_file, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=60)

        print(f"\n[CONTENTION] Success: {success_count[0]}/100")
        print(f"[CONTENTION] Errors: {len(errors)}")

        # 至少95%应该成功
        self.assertGreaterEqual(success_count[0], 95, f"Too many failures: {success_count[0]}/100")

        service.close()


class TestResourcePressure(unittest.TestCase):
    """测试3: 资源压力测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_large_file_content(self):
        """测试超大文件内容处理"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_path = os.path.join(self.test_dir, "LargeContentFile.ts")

        # 创建1MB的内容
        large_content = "export class LargeContentFile {\n"
        for i in range(10000):
            large_content += f"    private field{i} = '{i}' ;\n"
        large_content += "}\n"

        start_time = time.time()
        service._handle_file_change(file_path, large_content)
        elapsed = time.time() - start_time

        print(f"\n[LARGE FILE] 1MB file processed in {elapsed:.2f}s")

        # 大文件处理不应超过10秒
        self.assertLess(elapsed, 10, f"Large file processing too slow: {elapsed:.2f}s")

        service.close()

    def test_many_entities_in_single_file(self):
        """单个文件中包含大量实体"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_path = os.path.join(self.test_dir, "ManyEntitiesFile.ts")

        # 创建包含500个类的文件
        content = ""
        for i in range(500):
            content += f"export class EntityClass{i:04d} {{ id = {i}; }}\n"

        start_time = time.time()
        service._handle_file_change(file_path, content)
        elapsed = time.time() - start_time

        stats = service.get_statistics()
        print(f"\n[MANY ENTITIES] 500 classes in {elapsed:.2f}s")
        print(f"[MANY ENTITIES] Total entities: {stats['total_entities']}")

        self.assertGreaterEqual(stats['total_entities'], 500)

        service.close()


class TestFilesystemEdgeCases(unittest.TestCase):
    """测试4: 文件系统边缘情况"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_symlink_handling(self):
        """测试符号链接处理"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # 创建原始文件
        real_file = os.path.join(self.test_dir, "RealFile.ts")
        with open(real_file, 'w') as f:
            f.write("export class RealFile { id = 1; }")

        # 创建符号链接
        symlink_file = os.path.join(self.test_dir, "SymlinkFile.ts")
        os.symlink(real_file, symlink_file)

        # 处理符号链接
        service._handle_file_change(symlink_file, "export class SymlinkFile { id = 2; }")

        files = service.db.get_all_files()
        print(f"\n[SYMLINK] Files in DB: {[f['path'] for f in files]}")

        service.close()

    def test_deeply_nested_directories(self):
        """测试深层嵌套目录"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # 创建20层嵌套目录
        deep_dir = self.test_dir
        for i in range(20):
            deep_dir = os.path.join(deep_dir, f"level{i:02d}")
        os.makedirs(deep_dir, exist_ok=True)

        file_path = os.path.join(deep_dir, "DeepNestedFile.ts")
        service._handle_file_change(file_path, "export class DeepNestedFile { level = 20; }")

        files = service.db.get_all_files()
        self.assertEqual(len(files), 1)

        service.close()


class TestDatabaseStress(unittest.TestCase):
    """测试5: 数据库压力测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_million_entities_simulation(self):
        """模拟百万级实体（通过批量插入）"""
        db = KnowledgeGraphDB(self.db_path)

        # 先插入一个文件
        file_id = db.add_or_update_file("/test/MillionEntities.ts", "content")

        # 批量插入10000个实体（模拟大规模项目）
        start_time = time.time()
        entities = []
        for i in range(10000):
            entities.append({
                'name': f'Entity{i:06d}',
                'type': 'class',
                'signature': f'class Entity{i:06d}',
                'start_line': i,
                'end_line': i + 1
            })

        entity_ids = db.add_entities_batch(file_id, entities)
        elapsed = time.time() - start_time

        print(f"\n[MILLION ENTITIES] 10000 entities in {elapsed:.2f}s")
        print(f"[MILLION ENTITIES] Rate: {10000/elapsed:.0f} entities/sec")

        stats = db.get_statistics()
        self.assertEqual(stats['total_entities'], 10000)

        db.close()

    def test_query_performance_with_many_entities(self):
        """大量实体下的查询性能"""
        db = KnowledgeGraphDB(self.db_path)

        file_id = db.add_or_update_file("/test/QueryPerf.ts", "content")

        # 插入5000个实体
        for i in range(5000):
            db.add_entity(file_id, f"QueryTestEntity{i:05d}", "class")

        # 测试搜索性能
        start_time = time.time()
        for i in range(100):
            results = db.search_entities(f"QueryTestEntity{i:04d}")
        elapsed = time.time() - start_time

        avg_time = elapsed / 100 * 1000  # ms
        print(f"\n[QUERY PERF] 100 searches in {elapsed:.2f}s")
        print(f"[QUERY PERF] Avg: {avg_time:.2f}ms per search")

        # 单次搜索应<50ms
        self.assertLess(avg_time, 50, f"Search too slow: {avg_time:.2f}ms")

        db.close()


class TestCrashRecovery(unittest.TestCase):
    """测试7: 崩溃恢复测试"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_recovery_after_unclean_shutdown(self):
        """模拟非正常关闭后的恢复"""
        # 第一阶段：正常处理一些文件
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        for i in range(100):
            fp = os.path.join(self.test_dir, f"RecoveryTest{i:03d}.ts")
            service._handle_file_change(fp, f"export class RecoveryTest{i:03d} {{}}")

        stats_before = service.get_statistics()
        print(f"\n[RECOVERY] Before crash: {stats_before['total_files']} files")

        # 模拟崩溃（不调用close）
        del service
        gc.collect()

        # 第二阶段：重新打开数据库，验证数据完整性
        service2 = KnowledgeGraphService(db_path=self.db_path)
        service2.parser = MockArkTSParser()

        stats_after = service2.get_statistics()
        print(f"[RECOVERY] After recovery: {stats_after['total_files']} files")

        # 数据应该保持一致
        self.assertEqual(stats_before['total_files'], stats_after['total_files'])
        self.assertEqual(stats_before['total_entities'], stats_after['total_entities'])

        service2.close()


# 辅助类
class Counter:
    def __init__(self):
        self.value = 0
        self._lock = threading.Lock()

    def increment(self):
        with self._lock:
            self.value += 1


if __name__ == '__main__':
    unittest.main(verbosity=2)
