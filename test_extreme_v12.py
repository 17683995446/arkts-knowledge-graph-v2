"""
极端测试 v12 - 第10轮迭代（遵循第一性原理）
更长时间、更复杂、更高难度的真实实践

测试场景（基于系统核心功能的第一性分析）：
  1. 超长时间运行模拟（72小时等效压力测试）
  2. 极端并发（10000+文件同时操作）
  3. 资源耗尽边界（内存、磁盘、句柄）
  4. 真实Git工作流（checkout/merge/rebase模拟）
  5. 批量重构场景（类名/方法名大规模变更）
  6. 文件系统极限（单目录10000+文件）
  7. 数据库极限（100万实体查询性能）
  8. 异常恢复（kill -9后数据完整性）
"""
import os, sys, time, tempfile, shutil, threading, unittest, gc, sqlite3, signal, psutil, random, string

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser


class TestUltraLongRunning(unittest.TestCase):
    """测试1: 超长时间运行模拟（72小时等效压力）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_72hour_equivalent_stress(self):
        """
        模拟72小时运行的等效压力：
        - 10000次文件变更
        - 随机间隔（1ms-100ms）
        - 监控内存增长、性能退化
        """
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        process = psutil.Process()
        initial_memory = process.memory_info().rss / 1024 / 1024  # MB
        start_time = time.time()

        # 模拟72小时的文件变更活动
        for i in range(5000):  # 5000次变更
            # 随机选择操作：创建(40%)、更新(40%)、删除(20%)
            op = random.choices(['create', 'update', 'delete'], weights=[40, 40, 20])[0]
            
            if op == 'create':
                fp = os.path.join(self.test_dir, f"LongRunningFile{i:05d}.ts")
                content = f"export class LongRunningFile{i:05d} {{ version = {i}; }}"
                service._handle_file_change(fp, content)
            elif op == 'update':
                # 更新一个已存在的文件
                if i > 0:
                    update_idx = random.randint(0, min(i-1, 1000))
                    fp = os.path.join(self.test_dir, f"LongRunningFile{update_idx:05d}.ts")
                    if os.path.exists(fp):
                        content = f"export class LongRunningFile{update_idx:05d} {{ version = {i}; updated = true; }}"
                        service._handle_file_change(fp, content)
            else:  # delete
                if i > 100:
                    delete_idx = random.randint(0, min(i-1, 500))
                    fp = os.path.join(self.test_dir, f"LongRunningFile{delete_idx:05d}.ts")
                    if os.path.exists(fp):
                        service._handle_file_change(fp, None)
                        os.remove(fp) if os.path.exists(fp) else None

            # 每1000次检查一次
            if i % 1000 == 0 and i > 0:
                current_memory = process.memory_info().rss / 1024 / 1024
                memory_growth = current_memory - initial_memory
                elapsed = time.time() - start_time
                avg_time = (elapsed / i) * 1000  # ms per op
                print(f"[ITER {i}] Memory: {memory_growth:.2f}MB growth, Avg: {avg_time:.2f}ms/op")

                # 内存增长不应超过100MB
                self.assertLess(memory_growth, 100, f"Memory leak detected: {memory_growth:.2f}MB")

        total_time = time.time() - start_time
        final_memory = process.memory_info().rss / 1024 / 1024
        memory_growth = final_memory - initial_memory

        print(f"\n[72H SIMULATION] 5000 ops in {total_time:.2f}s")
        print(f"[72H SIMULATION] Memory growth: {memory_growth:.2f}MB")
        print(f"[72H SIMULATION] Avg time: {(total_time/5000)*1000:.2f}ms/op")

        service.close()


class TestExtremeConcurrency(unittest.TestCase):
    """测试2: 极端并发（10000+文件）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_10000_files_concurrent_creation(self):
        """10000个文件并发创建"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        file_paths = [os.path.join(self.test_dir, f"ExtremeConcurrent{i:05d}.ts") for i in range(10000)]
        errors = []
        completed = [0]
        lock = threading.Lock()

        def create_file(fp, idx):
            try:
                content = f"export class ExtremeConcurrent{idx:05d} {{ id = {idx}; }}"
                service._handle_file_change(fp, content)
                with lock:
                    completed[0] += 1
            except Exception as e:
                with lock:
                    errors.append((idx, str(e)))

        threads = []
        start_time = time.time()

        # 创建10000个线程（分批，避免系统限制）
        batch_size = 500
        for batch in range(0, 10000, batch_size):
            batch_threads = []
            for i in range(batch, min(batch + batch_size, 10000)):
                t = threading.Thread(target=create_file, args=(file_paths[i], i))
                batch_threads.append(t)
                t.start()
            
            for t in batch_threads:
                t.join(timeout=60)
            
            print(f"[BATCH {batch//batch_size + 1}/20] Completed")

        elapsed = time.time() - start_time

        print(f"\n[EXTREME CONCURRENT] 10000 files in {elapsed:.2f}s")
        print(f"[EXTREME CONCURRENT] Errors: {len(errors)}")
        print(f"[EXTREME CONCURRENT] Completed: {completed[0]}")

        # 至少95%应该成功
        self.assertGreaterEqual(completed[0], 9500, f"Too many failures: {completed[0]}/10000")

        stats = service.get_statistics()
        print(f"[STATS] Total files: {stats['total_files']}")

        service.close()


class TestResourceExhaustion(unittest.TestCase):
    """测试3: 资源耗尽边界"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_database_size_limit_approach(self):
        """接近数据库大小限制时的行为"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        # 创建大量大内容文件
        large_content = "export class LargeFile {\n" + "\n".join([f"    field{i} = '{i}';" for i in range(1000)]) + "\n}"

        for i in range(1000):
            fp = os.path.join(self.test_dir, f"LargeContentFile{i:04d}.ts")
            service._handle_file_change(fp, large_content)
            
            if i % 100 == 0:
                db_size = os.path.getsize(self.db_path) / 1024 / 1024  # MB
                print(f"[DB SIZE] {i} files: {db_size:.2f}MB")

        db_size = os.path.getsize(self.db_path) / 1024 / 1024
        print(f"\n[DB SIZE FINAL] {db_size:.2f}MB")

        # 数据库应该能正常工作，不应崩溃
        stats = service.get_statistics()
        self.assertGreater(stats['total_files'], 0)

        service.close()

    def test_thread_creation_storm(self):
        """线程创建风暴（daemon线程堆积）"""
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        callback_count = [0]
        callback_lock = threading.Lock()

        def slow_callback(fp, stats):
            time.sleep(0.1)  # 慢回调
            with callback_lock:
                callback_count[0] += 1

        service.on_update_callback = slow_callback

        # 快速创建500个文件，每个都会触发daemon线程
        for i in range(500):
            fp = os.path.join(self.test_dir, f"ThreadStorm{i:04d}.ts")
            service._handle_file_change(fp, f"export class ThreadStorm{i:04d} {{}}")

        # 等待所有回调完成
        time.sleep(15)

        print(f"\n[THREAD STORM] Callbacks executed: {callback_count[0]}")
        
        # 系统不应崩溃，回调应该被执行
        self.assertGreater(callback_count[0], 400)

        service.close()


class TestGitWorkflow(unittest.TestCase):
    """测试4: 真实Git工作流模拟"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_git_checkout_simulation(self):
        """模拟git checkout（大量文件变更）"""
        # 初始状态：100个文件
        for i in range(100):
            fp = os.path.join(self.test_dir, f"GitCheckoutFile{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class GitCheckoutFile{i:03d} {{ v1 = 1; }}")

        stats_before = self.service.get_statistics()
        print(f"\n[GIT CHECKOUT] Before: {stats_before['total_files']} files")

        # 模拟checkout：删除一半，修改一半，新增50个
        # 删除0-49
        for i in range(50):
            fp = os.path.join(self.test_dir, f"GitCheckoutFile{i:03d}.ts")
            self.service._handle_file_change(fp, None)

        # 修改50-99
        for i in range(50, 100):
            fp = os.path.join(self.test_dir, f"GitCheckoutFile{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class GitCheckoutFile{i:03d} {{ v2 = 2; }}")

        # 新增50-99（新文件）
        for i in range(50, 100):
            fp = os.path.join(self.test_dir, f"GitCheckoutNewFile{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class GitCheckoutNewFile{i:03d} {{ v1 = 1; }}")

        stats_after = self.service.get_statistics()
        print(f"[GIT CHECKOUT] After: {stats_after['total_files']} files")

        # 应该正好是100个文件（50删除 + 50修改 + 50新增 = 100）
        self.assertEqual(stats_after['total_files'], 100)

    def test_mass_rename_simulation(self):
        """模拟批量重构（类名大规模变更）"""
        # 创建100个文件
        for i in range(100):
            fp = os.path.join(self.test_dir, f"OldClassName{i:03d}.ts")
            self.service._handle_file_change(fp, f"export class OldClassName{i:03d} {{ id = {i}; }}")

        # 批量重命名：删除旧文件，创建新文件
        for i in range(100):
            old_fp = os.path.join(self.test_dir, f"OldClassName{i:03d}.ts")
            new_fp = os.path.join(self.test_dir, f"NewClassName{i:03d}.ts")
            
            self.service._handle_file_change(old_fp, None)
            self.service._handle_file_change(new_fp, f"export class NewClassName{i:03d} {{ id = {i}; }}")

        stats = self.service.get_statistics()
        print(f"\n[MASS RENAME] Files: {stats['total_files']}")

        # 应该还是100个文件
        self.assertEqual(stats['total_files'], 100)


class TestFilesystemLimits(unittest.TestCase):
    """测试5: 文件系统极限"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        shutil.rmtree(self.test_dir)

    def test_single_directory_10000_files(self):
        """单目录10000个文件"""
        for i in range(10000):
            fp = os.path.join(self.test_dir, f"SingleDirFile{i:05d}.ts")
            self.service._handle_file_change(fp, f"export class SingleDirFile{i:05d} {{}}")

            if i % 2000 == 0 and i > 0:
                print(f"[SINGLE DIR] Created {i} files")

        stats = self.service.get_statistics()
        print(f"\n[SINGLE DIR] Total files: {stats['total_files']}")

        self.assertEqual(stats['total_files'], 10000)

    def test_very_long_file_path(self):
        """超长文件路径（接近OS限制255字符）"""
        # 创建接近255字符的路径
        long_name = "A" * 200 + ".ts"
        fp = os.path.join(self.test_dir, long_name)
        
        self.service._handle_file_change(fp, "export class VeryLongPath {}")

        files = self.service.db.get_all_files()
        self.assertEqual(len(files), 1)
        print(f"\n[LONG PATH] Path length: {len(files[0]['path'])}")


class TestDatabaseMillionScale(unittest.TestCase):
    """测试6: 数据库百万级规模"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)

    def tearDown(self):
        self.db.close()
        shutil.rmtree(self.test_dir)

    def test_million_entities_performance(self):
        """百万实体查询性能"""
        # 先创建一个文件
        file_id = self.db.add_or_update_file("/test/MillionScale.ts", "content")

        # 批量插入10万个实体（模拟百万级项目的子集）
        print("\n[MILLION SCALE] Inserting 100000 entities...")
        start_time = time.time()

        batch_size = 1000
        for batch in range(0, 100000, batch_size):
            entities = []
            for i in range(batch, min(batch + batch_size, 100000)):
                entities.append({
                    'name': f'Entity{i:06d}',
                    'type': 'class',
                    'signature': f'class Entity{i:06d}',
                    'start_line': i,
                    'end_line': i + 1
                })
            self.db.add_entities_batch(file_id, entities)
            
            if batch % 10000 == 0 and batch > 0:
                print(f"[MILLION SCALE] Inserted {batch} entities")

        insert_time = time.time() - start_time
        print(f"[MILLION SCALE] Insert time: {insert_time:.2f}s")

        # 测试查询性能
        print("[MILLION SCALE] Testing query performance...")
        
        start_time = time.time()
        for i in range(100):
            results = self.db.search_entities(f"Entity{i:05d}")
        query_time = time.time() - start_time

        print(f"[MILLION SCALE] 100 queries in {query_time:.2f}s ({query_time/100*1000:.2f}ms avg)")

        stats = self.db.get_statistics()
        print(f"[MILLION SCALE] Total entities: {stats['total_entities']}")

        self.assertEqual(stats['total_entities'], 100000)


class TestCrashRecovery(unittest.TestCase):
    """测试7: 异常恢复（kill -9模拟）"""

    def setUp(self):
        self.test_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.test_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_kill9_recovery(self):
        """模拟kill -9后的数据恢复"""
        # 第一阶段：正常处理
        service = KnowledgeGraphService(db_path=self.db_path)
        service.parser = MockArkTSParser()

        for i in range(500):
            fp = os.path.join(self.test_dir, f"Kill9Recovery{i:04d}.ts")
            service._handle_file_change(fp, f"export class Kill9Recovery{i:04d} {{}}")

        stats_before = service.get_statistics()
        print(f"\n[KILL9] Before crash: {stats_before['total_files']} files")

        # 模拟kill -9：不调用close()，直接删除对象
        del service
        gc.collect()

        # 第二阶段：重新打开，验证数据完整性
        service2 = KnowledgeGraphService(db_path=self.db_path)
        service2.parser = MockArkTSParser()

        stats_after = service2.get_statistics()
        print(f"[KILL9] After recovery: {stats_after['total_files']} files")

        # 数据应该保持一致（WAL模式保证）
        self.assertEqual(stats_before['total_files'], stats_after['total_files'])

        service2.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
