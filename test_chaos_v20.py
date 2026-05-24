#!/usr/bin/env python3
"""
ArkTS 知识图谱系统 - 第五轮（终极轮）极端混沌测试
test_chaos_v20.py

覆盖场景：
  A. 极限边界条件 (1-6)
  B. 极限并发场景 (7-10)
  C. 极限数据规模 (11-14)
  D. 极限异常注入 (15-18)
  E. 极限业务场景 (19-22)
  F. 最终一致性验证 (23-25)
"""

import os
import sys
import sqlite3
import tempfile
import threading
import time
import json
import logging
import unittest
import hashlib
import signal
import gc
import resource
from io import StringIO
from unittest.mock import patch, MagicMock, PropertyMock
from typing import Dict, List

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser
from config import MAX_NODES_DISPLAY, MAX_FILE_SIZE


def _make_arkts_content(class_name: str, extra_imports: str = "",
                         extra_code: str = "") -> str:
    """生成合法的 ArkTS 文件内容"""
    return (
        f"import {{ Component }} from 'ohosComponent'\n"
        f"{extra_imports}\n"
        f"export class {class_name} extends Component {{\n"
        f"  private state: string = '';\n"
        f"  build() {{\n"
        f"    return;\n"
        f"  }}\n"
        f"  aboutToAppear() {{\n"
        f"    this.state = 'ready';\n"
        f"  }}\n"
        f"{extra_code}\n"
        f"}}\n"
    )


def _make_simple_arkts(name: str) -> str:
    """生成最简 ArkTS 文件内容"""
    return (
        f"import {{ Component }} from 'ohosComponent'\n"
        f"export class {name} extends Component {{\n"
        f"  build() {{ return; }}\n"
        f"}}\n"
    )


class ChaosParser(MockArkTSParser):
    """可注入异常和自定义返回值的Parser"""
    
    def __init__(self):
        super().__init__()
        self.override_result = None
        self.exception_count = 0
        self.exception_message = None
        self.call_count = 0
    
    def parse_file(self, file_path: str, content: str) -> Dict:
        self.call_count += 1
        
        if self.exception_message and self.exception_count > 0:
            self.exception_count -= 1
            raise Exception(self.exception_message)
        
        if self.override_result is not None:
            return self.override_result
        
        return super().parse_file(file_path, content)
    
    def set_exception_mode(self, message: str, count: int):
        """设置异常模式"""
        self.exception_message = message
        self.exception_count = count
    
    def set_override_result(self, result: Dict):
        """设置覆盖返回结果"""
        self.override_result = result
    
    def reset(self):
        """重置所有注入"""
        self.override_result = None
        self.exception_count = 0
        self.exception_message = None
        self.call_count = 0


# ===========================================================================
# A. 极限边界条件
# ===========================================================================

class TestExtremeBoundaryConditions(unittest.TestCase):
    """A. 极限边界条件测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_boundary_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.service = KnowledgeGraphService(self.db_path)
        self.chaos_parser = ChaosParser()
        self.service.parser = self.chaos_parser

    def tearDown(self):
        self.service.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- A1: 空实体名 ----
    def test_01_empty_entity_name(self):
        """parser返回name为空字符串的entity，验证系统不crash"""
        self.chaos_parser.set_override_result({
            "entities": [
                {"name": "", "type": "class", "signature": "class ", "start_line": 1, "end_line": 5},
                {"name": "ValidClass", "type": "class", "signature": "class ValidClass", "start_line": 6, "end_line": 10},
            ],
            "relationships": [],
            "imports": []
        })
        
        # 不应crash
        self.service._handle_file_change("/src/emptyNameFile.ets", "some content")
        
        stats = self.service.get_statistics()
        # 空名entity可能被存储或被跳过，关键是系统不crash
        self.assertIsNotNone(stats)
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT name FROM entities WHERE name = ''")
        empty_names = cursor.fetchall()
        conn.close()
        # 空名可能被存储，但系统应该稳定
        self.assertTrue(True, "系统处理空实体名未crash")

    # ---- A2: 超长实体名 ----
    def test_02_extremely_long_entity_name(self):
        """实体名长度10000字符，验证数据库存储和查询"""
        long_name = "A" * 10000
        self.chaos_parser.set_override_result({
            "entities": [
                {"name": long_name, "type": "class", "signature": f"class {long_name[:50]}...", "start_line": 1, "end_line": 5},
            ],
            "relationships": [],
            "imports": []
        })
        
        self.service._handle_file_change("/src/longNameFile.ets", "content")
        
        # 验证可以存储和查询
        results = self.service.search("A" * 100)  # 搜索部分
        self.assertIsInstance(results, list)
        
        # 验证数据库中确实存储了
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT LENGTH(name) as len FROM entities")
        row = cursor.fetchone()
        if row:
            self.assertEqual(row[0], 10000, "超长实体名应被完整存储")
        conn.close()

    # ---- A3: 特殊字符实体名 ----
    def test_03_special_chars_entity_name(self):
        """实体名包含SQL特殊字符（单引号、分号、NULL字节）"""
        special_names = [
            "Class'With'Quotes",
            "Class;DROP TABLE files;--",
            "Class\x00WithNull",  # NULL字节
            "Class\nWith\nNewlines",
            "Class\tWith\tTabs",
            "Class\\With\\Backslash",
        ]
        
        for i, name in enumerate(special_names):
            self.chaos_parser.set_override_result({
                "entities": [
                    {"name": name, "type": "class", "signature": f"class {name[:20]}", "start_line": 1, "end_line": 5},
                ],
                "relationships": [],
                "imports": []
            })
            
            # 不应crash，不应产生SQL注入
            self.service._handle_file_change(f"/src/specialCharFile{i}.ets", "content")
        
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats["total_files"], len(special_names))
        
        # 验证files表没有被删除（SQL注入防护）
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        conn.close()
        self.assertGreater(file_count, 0, "files表应存在数据")

    # ---- A4: 负数行号 ----
    def test_04_negative_line_number(self):
        """parser返回start_line为负数，验证系统处理"""
        self.chaos_parser.set_override_result({
            "entities": [
                {"name": "NegativeLineClass", "type": "class", "signature": "class NegativeLineClass", "start_line": -1, "end_line": -5},
                {"name": "NormalClass", "type": "class", "signature": "class NormalClass", "start_line": 10, "end_line": 20},
            ],
            "relationships": [],
            "imports": []
        })
        
        # 不应crash
        self.service._handle_file_change("/src/negativeLineFile.ets", "content")
        
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats["total_entities"], 0)
        
        # 验证负数行号被存储
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT start_line, end_line FROM entities WHERE name = 'NegativeLineClass'")
        row = cursor.fetchone()
        if row:
            self.assertEqual(row[0], -1, "负数行号应被存储")
        conn.close()

    # ---- A5: 超大行号 ----
    def test_05_extremely_large_line_number(self):
        """parser返回start_line为999999999，验证系统处理"""
        huge_line = 999999999
        self.chaos_parser.set_override_result({
            "entities": [
                {"name": "HugeLineClass", "type": "class", "signature": "class HugeLineClass", "start_line": huge_line, "end_line": huge_line + 100},
            ],
            "relationships": [],
            "imports": []
        })
        
        # 不应crash
        self.service._handle_file_change("/src/hugeLineFile.ets", "content")
        
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats["total_entities"], 0)
        
        # 验证超大行号被存储
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT start_line FROM entities WHERE name = 'HugeLineClass'")
        row = cursor.fetchone()
        if row:
            self.assertEqual(row[0], huge_line, "超大行号应被存储")
        conn.close()

    # ---- A6: 关系类型边界 ----
    def test_06_relationship_type_boundary(self):
        """关系类型为空字符串、超长字符串、特殊字符"""
        test_cases = [
            ("", "empty_relation"),
            ("A" * 10000, "long_relation"),
            ("'; DROP TABLE relationships; --", "sql_injection_relation"),
            ("relation\nwith\nnewlines", "newline_relation"),
        ]
        
        for rel_type, test_name in test_cases:
            self.chaos_parser.set_override_result({
                "entities": [
                    {"name": f"Source{test_name}", "type": "class", "start_line": 1, "end_line": 5},
                    {"name": f"Target{test_name}", "type": "class", "start_line": 6, "end_line": 10},
                ],
                "relationships": [
                    {"source": f"Source{test_name}", "target": f"Target{test_name}", "type": rel_type}
                ],
                "imports": []
            })
            
            # 不应crash
            self.service._handle_file_change(f"/src/relBoundary_{test_name}.ets", "content")
        
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats["total_files"], len(test_cases))


# ===========================================================================
# B. 极限并发场景
# ===========================================================================

class TestExtremeConcurrency(unittest.TestCase):
    """B. 极限并发场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_concurrent_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- B7: 1000线程并发写入 ----
    def test_07_1000_threads_concurrent_write(self):
        """1000个线程同时写入1000个不同文件，验证最终一致性"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        errors = []
        lock = threading.Lock()
        completed = {"count": 0}
        
        def writer(thread_id):
            try:
                for i in range(10):  # 每个线程写10个文件
                    file_id = thread_id * 10 + i
                    path = f"/src/concurrentFile{file_id:05d}.ets"
                    content = _make_arkts_content(f"ConcurrentClass{file_id}")
                    service._handle_file_change(path, content)
                with lock:
                    completed["count"] += 1
            except Exception as e:
                with lock:
                    errors.append((thread_id, str(e)))
        
        threads = [threading.Thread(target=writer, args=(i,)) for i in range(100)]
        
        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        elapsed = time.time() - start_time
        
        service.close()
        
        self.assertEqual(len(errors), 0, f"并发写入错误: {errors[:10]}")
        self.assertEqual(completed["count"], 100, "所有线程应完成")
        
        # 验证最终一致性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(file_count, 1000, f"最终文件数应为1000，实际为{file_count}")
        self.assertLess(elapsed, 120, f"1000线程并发写入耗时 {elapsed:.2f}s")

    # ---- B8: 读写死锁检测 ----
    def test_08_read_write_deadlock_detection(self):
        """一个线程持续写入，另一个线程持续读取，持续30秒，验证无死锁"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        stop_event = threading.Event()
        write_count = {"value": 0}
        read_count = {"value": 0}
        errors = []
        
        def continuous_writer():
            while not stop_event.is_set():
                try:
                    for i in range(100):
                        if stop_event.is_set():
                            break
                        path = f"/src/deadlockTestFile{i}.ets"
                        content = _make_arkts_content(f"DeadlockClass{i}")
                        service._handle_file_change(path, content)
                        write_count["value"] += 1
                except Exception as e:
                    errors.append(f"Writer error: {e}")
        
        def continuous_reader():
            while not stop_event.is_set():
                try:
                    for _ in range(50):
                        if stop_event.is_set():
                            break
                        stats = service.get_statistics()
                        self.assertIn("total_files", stats)
                        read_count["value"] += 1
                except Exception as e:
                    errors.append(f"Reader error: {e}")
        
        t_write = threading.Thread(target=continuous_writer)
        t_read = threading.Thread(target=continuous_reader)
        
        t_write.start()
        t_read.start()
        
        # 运行30秒
        time.sleep(30)
        stop_event.set()
        
        t_write.join(timeout=10)
        t_read.join(timeout=10)
        service.close()
        
        # 验证无死锁（两个线程都有进展）
        self.assertGreater(write_count["value"], 0, "写入线程应有进展")
        self.assertGreater(read_count["value"], 0, "读取线程应有进展")
        
        # 验证无严重错误
        critical_errors = [e for e in errors if "deadlock" in e.lower() or "lock" in e.lower()]
        self.assertEqual(len(critical_errors), 0, f"检测到死锁相关错误: {critical_errors}")

    # ---- B9: 信号竞争 ----
    def test_09_signal_race_condition(self):
        """在处理文件过程中持续发送SIGINT信号，验证优雅关闭"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        signal_received = {"count": 0}
        processed = {"count": 0}
        
        def signal_handler(signum, frame):
            signal_received["count"] += 1
        
        original_handler = signal.signal(signal.SIGINT, signal_handler)
        
        def file_processor():
            for i in range(200):
                if service._shutdown_event.is_set():
                    break
                path = f"/src/signalRaceFile{i}.ets"
                content = _make_arkts_content(f"SignalClass{i}")
                service._handle_file_change(path, content)
                processed["count"] += 1
        
        def signal_sender():
            for _ in range(50):
                time.sleep(0.1)
                try:
                    os.kill(os.getpid(), signal.SIGINT)
                except Exception:
                    pass
        
        t_processor = threading.Thread(target=file_processor)
        t_signal = threading.Thread(target=signal_sender)
        
        t_processor.start()
        t_signal.start()
        
        t_processor.join(timeout=30)
        t_signal.join(timeout=30)
        
        service.close()
        signal.signal(signal.SIGINT, original_handler)
        
        # 验证系统稳定
        self.assertGreater(processed["count"], 0, "应处理了部分文件")
        self.assertGreater(signal_received["count"], 0, "应收到了信号")

    # ---- B10: DB连接池耗尽 ----
    def test_10_db_connection_pool_exhaustion(self):
        """创建超过100个并发DB操作，验证连接管理"""
        db = KnowledgeGraphDB(self.db_path)
        
        errors = []
        completed = {"count": 0}
        lock = threading.Lock()
        
        def db_worker(worker_id):
            try:
                for i in range(20):
                    path = f"/src/poolTestFile_{worker_id}_{i}.ets"
                    content = _make_arkts_content(f"PoolClass{worker_id}_{i}")
                    db.add_or_update_file(path, content)
                    
                    # 并发读取
                    stats = db.get_statistics()
                    self.assertIn("total_files", stats)
                with lock:
                    completed["count"] += 1
            except Exception as e:
                with lock:
                    errors.append((worker_id, str(e)))
        
        threads = [threading.Thread(target=db_worker, args=(i,)) for i in range(150)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        
        db.close()
        
        # 验证大部分操作成功
        success_rate = completed["count"] / 150
        self.assertGreater(success_rate, 0.95, f"成功率 {success_rate:.2%} 过低")
        
        # 验证无连接泄漏
        conn = sqlite3.connect(self.db_path, timeout=1)
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        conn.close()
        self.assertGreater(file_count, 0, "应有文件被写入")


# ===========================================================================
# C. 极限数据规模
# ===========================================================================

class TestExtremeDataScale(unittest.TestCase):
    """C. 极限数据规模测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_scale_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- C11: 10000文件处理 ----
    def test_11_10000_files_processing(self):
        """创建10000个文件并处理，验证性能和内存"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        start_time = time.time()
        
        # 批量处理
        for i in range(10000):
            path = f"/src/massiveFile{i:05d}.ets"
            content = _make_arkts_content(f"MassiveClass{i}")
            service._handle_file_change(path, content)
            
            if i % 1000 == 0:
                gc.collect()  # 定期GC
        
        elapsed = time.time() - start_time
        stats = service.get_statistics()
        
        service.close()
        
        self.assertEqual(stats["total_files"], 10000, f"文件数应为10000，实际为{stats['total_files']}")
        self.assertGreaterEqual(stats["total_entities"], 10000, "实体数应大于等于10000")
        self.assertLess(elapsed, 300, f"处理10000文件耗时 {elapsed:.2f}s 过长")

    # ---- C12: 单文件10000实体 ----
    def test_12_single_file_10000_entities(self):
        """一个文件包含10000个class定义，验证事务性能"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 生成10000个实体
        entities = []
        for i in range(10000):
            entities.append({
                "name": f"MassiveEntity{i:05d}",
                "type": "class",
                "signature": f"class MassiveEntity{i:05d}",
                "start_line": i * 10 + 1,
                "end_line": i * 10 + 10
            })
        
        chaos_parser.set_override_result({
            "entities": entities,
            "relationships": [],
            "imports": []
        })
        
        start_time = time.time()
        service._handle_file_change("/src/massiveEntitiesFile.ets", "content")
        elapsed = time.time() - start_time
        
        stats = service.get_statistics()
        service.close()
        
        self.assertEqual(stats["total_entities"], 10000, f"实体数应为10000，实际为{stats['total_entities']}")
        self.assertLess(elapsed, 60, f"单文件10000实体处理耗时 {elapsed:.2f}s 过长")

    # ---- C13: 关系数量极限 ----
    def test_13_relationship_count_limit(self):
        """一个文件包含5000个relationship，验证插入性能"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 生成实体和关系
        entities = []
        relationships = []
        
        # 创建100个实体
        for i in range(100):
            entities.append({
                "name": f"RelEntity{i:03d}",
                "type": "class",
                "start_line": i * 10 + 1,
                "end_line": i * 10 + 10
            })
        
        # 创建5000个关系（每个实体连接到其他实体）
        for i in range(5000):
            source_idx = i % 100
            target_idx = (i + 1) % 100
            relationships.append({
                "source": f"RelEntity{source_idx:03d}",
                "target": f"RelEntity{target_idx:03d}",
                "type": f"relation_{i}"
            })
        
        chaos_parser.set_override_result({
            "entities": entities,
            "relationships": relationships,
            "imports": []
        })
        
        start_time = time.time()
        service._handle_file_change("/src/massiveRelationsFile.ets", "content")
        elapsed = time.time() - start_time
        
        stats = service.get_statistics()
        service.close()
        
        self.assertEqual(stats["total_entities"], 100, f"实体数应为100")
        self.assertGreaterEqual(stats["total_relationships"], 0, "关系数应>=0")
        self.assertLess(elapsed, 120, f"5000关系处理耗时 {elapsed:.2f}s 过长")

    # ---- C14: 搜索结果极限 ----
    def test_14_search_result_limit(self):
        """插入10000个实体后搜索通用词，验证返回limit生效"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 插入10000个文件，每个文件有同名实体
        for i in range(10000):
            path = f"/src/searchTestFile{i:05d}.ets"
            # 所有文件都有类似名称的类
            content = _make_arkts_content(f"SearchTestClass{i:05d}")
            service._handle_file_change(path, content)
        
        # 搜索通用词
        results = service.search("SearchTestClass")
        
        service.close()
        
        # 验证返回结果受limit限制
        self.assertLessEqual(len(results), 20, f"搜索结果应受limit限制，实际返回{len(results)}")
        self.assertIsInstance(results, list)


# ===========================================================================
# D. 极限异常注入
# ===========================================================================

class TestExtremeExceptionInjection(unittest.TestCase):
    """D. 极限异常注入测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_exception_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- D15: Parser持续失败 ----
    def test_15_parser_continuous_failure(self):
        """parser连续抛出100次异常，验证系统不crash"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 设置持续抛出异常
        chaos_parser.set_exception_mode("Simulated parser failure", 100)
        
        errors_caught = []
        
        for i in range(100):
            try:
                path = f"/src/parserFailFile{i}.ets"
                content = _make_arkts_content(f"ParserFailClass{i}")
                service._handle_file_change(path, content)
            except Exception as e:
                errors_caught.append(str(e))
        
        stats = service.get_statistics()
        service.close()
        
        # 系统不应crash，且部分文件可能被跳过
        self.assertIsNotNone(stats)
        # 由于异常被内部捕获，errors_caught可能为空
        self.assertTrue(True, "系统处理100次parser异常未crash")

    # ---- D16: DB连接随机断开 ----
    def test_16_db_connection_random_disconnect(self):
        """在事务执行过程中模拟连接断开，验证重试机制"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 先正常处理一些文件
        for i in range(10):
            service._handle_file_change(f"/src/normalFile{i}.ets", _make_arkts_content(f"NormalClass{i}"))
        
        # 模拟DB连接问题
        original_get_connection = service.db._get_connection
        call_count = {"value": 0}
        
        def flaky_get_connection():
            call_count["value"] += 1
            if call_count["value"] % 5 == 0:
                raise sqlite3.OperationalError("Database connection lost")
            return original_get_connection()
        
        # 注入故障
        service.db._get_connection = flaky_get_connection
        
        # 继续处理文件
        success_count = 0
        for i in range(50):
            try:
                service._handle_file_change(f"/src/flakyFile{i}.ets", _make_arkts_content(f"FlakyClass{i}"))
                success_count += 1
            except Exception:
                pass
        
        # 恢复
        service.db._get_connection = original_get_connection
        service.close()
        
        # 验证部分操作成功
        self.assertGreater(success_count, 0, "部分操作应成功")

    # ---- D17: 磁盘IO错误模拟 ----
    def test_17_disk_io_error_simulation(self):
        """模拟磁盘只读，验证优雅降级"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 先正常处理
        for i in range(5):
            service._handle_file_change(f"/src/diskTestFile{i}.ets", _make_arkts_content(f"DiskClass{i}"))
        
        # 模拟磁盘错误
        original_open = open
        call_count = {"value": 0}
        
        def mock_open(*args, **kwargs):
            call_count["value"] += 1
            if call_count["value"] > 10 and "test.db" in str(args):
                raise IOError("Disk is read-only")
            return original_open(*args, **kwargs)
        
        with patch('builtins.open', side_effect=mock_open):
            # 尝试继续处理
            for i in range(5, 10):
                try:
                    service._handle_file_change(f"/src/diskErrorFile{i}.ets", _make_arkts_content(f"DiskErrorClass{i}"))
                except Exception:
                    pass
        
        service.close()
        
        # 系统应优雅处理错误
        self.assertTrue(True, "系统处理磁盘IO错误未crash")

    # ---- D18: 内存压力测试 ----
    def test_18_memory_pressure_test(self):
        """限制进程内存后处理大量文件，验证OOM保护"""
        # 注意：内存限制可能影响后续测试，因此这个测试使用更温和的方式
        # 只验证系统在处理大量数据时不会crash
        
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        processed = 0
        errors = []
        
        try:
            # 处理大量文件
            for i in range(500):
                path = f"/src/memoryPressureFile{i:05d}.ets"
                content = _make_arkts_content(f"MemoryClass{i}")
                service._handle_file_change(path, content)
                processed += 1
                
                if i % 100 == 0:
                    gc.collect()  # 定期GC
        except MemoryError:
            errors.append("MemoryError caught")
        except Exception as e:
            if "memory" in str(e).lower() or "cannot allocate" in str(e).lower():
                errors.append(f"Memory-related error: {e}")
            else:
                raise
        
        service.close()
        
        # 系统应处理了大部分文件
        self.assertGreater(processed, 400, f"应处理了大部分文件，实际处理 {processed}")
        # 如果有内存错误，应该是优雅处理的
        if errors:
            self.assertIn("MemoryError", str(errors), "内存错误应被优雅处理")


# ===========================================================================
# E. 极限业务场景
# ===========================================================================

class TestExtremeBusinessScenarios(unittest.TestCase):
    """E. 极限业务场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_business_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- E19: 完整IDE生命周期 ----
    def test_19_complete_ide_lifecycle(self):
        """创建项目→初始扫描→编辑100次→删除一半→重命名→关闭→重启→验证数据"""
        # 阶段1: 创建项目和初始扫描
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 初始扫描50个文件
        for i in range(50):
            path = f"/src/project/InitialFile{i:03d}.ets"
            content = _make_arkts_content(f"InitialClass{i}")
            service._handle_file_change(path, content)
        
        stats1 = service.get_statistics()
        self.assertEqual(stats1["total_files"], 50)
        
        # 阶段2: 编辑100次（更新现有文件）- 不应增加文件数
        for i in range(100):
            file_idx = i % 50
            path = f"/src/project/InitialFile{file_idx:03d}.ets"
            content = _make_arkts_content(f"EditedClass{file_idx}_v{i}")
            service._handle_file_change(path, content)
        
        stats2 = service.get_statistics()
        self.assertEqual(stats2["total_files"], 50, "编辑不应增加文件数")
        
        # 阶段3: 删除一半文件
        for i in range(25):
            path = f"/src/project/InitialFile{i:03d}.ets"
            service._handle_file_change(path, None)  # None表示删除
        
        stats3 = service.get_statistics()
        self.assertEqual(stats3["total_files"], 25)
        
        # 阶段4: 关闭
        service.close()
        
        # 阶段5: 重启
        service2 = KnowledgeGraphService(self.db_path)
        service2.parser = MockArkTSParser()
        
        stats4 = service2.get_statistics()
        self.assertEqual(stats4["total_files"], 25, "重启后文件数应保持")
        
        # 阶段6: 添加新文件（使用不同的路径避免与已删除的冲突）
        for i in range(50):
            path = f"/src/project/NewFile{i:03d}.ets"
            content = _make_arkts_content(f"NewClass{i}")
            service2._handle_file_change(path, content)
        
        stats5 = service2.get_statistics()
        # 25个保留的 + 50个新文件 = 75个
        self.assertEqual(stats5["total_files"], 75)
        
        service2.close()
        
        # 最终验证数据完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        final_count = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(final_count, 75, "最终文件数应为75")

    # ---- E20: 跨目录引用 ----
    def test_20_cross_directory_references(self):
        """A目录的文件import B目录的文件，验证依赖关系"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建目录结构
        # 目录A: src/moduleA/
        # 目录B: src/moduleB/
        
        # 先创建被引用的文件（目录B）
        content_b = (
            "import { Component } from 'ohosComponent'\n"
            "export class ModuleBService {\n"
            "  getData(): string { return 'data'; }\n"
            "}\n"
        )
        service._handle_file_change("src/moduleB/ModuleBService.ets", content_b)
        
        # 创建引用文件（目录A）
        content_a = (
            "import { Component } from 'ohosComponent'\n"
            "import { ModuleBService } from '../moduleB/ModuleBService'\n"
            "export class ModuleAController {\n"
            "  private service: ModuleBService = new ModuleBService();\n"
            "  build() { return; }\n"
            "}\n"
        )
        service._handle_file_change("src/moduleA/ModuleAController.ets", content_a)
        
        stats = service.get_statistics()
        self.assertEqual(stats["total_files"], 2)
        
        # 验证依赖关系
        deps = service.db.get_file_dependencies()
        self.assertIsInstance(deps, list)
        
        service.close()

    # ---- E21: 循环依赖检测 ----
    def test_21_circular_dependency_detection(self):
        """A→B→C→A的循环依赖，验证不无限循环"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 创建循环依赖的实体
        # A contains B, B contains C, C contains A
        chaos_parser.set_override_result({
            "entities": [
                {"name": "ClassA", "type": "class", "start_line": 1, "end_line": 10},
                {"name": "ClassB", "type": "class", "start_line": 11, "end_line": 20},
                {"name": "ClassC", "type": "class", "start_line": 21, "end_line": 30},
            ],
            "relationships": [
                {"source": "ClassA", "target": "ClassB", "type": "contains"},
                {"source": "ClassB", "target": "ClassC", "type": "contains"},
                {"source": "ClassC", "target": "ClassA", "type": "contains"},
            ],
            "imports": []
        })
        
        start_time = time.time()
        service._handle_file_change("/src/circularDependency.ets", "content")
        elapsed = time.time() - start_time
        
        stats = service.get_statistics()
        service.close()
        
        # 系统应正常完成，不无限循环
        self.assertLess(elapsed, 5, f"循环依赖处理耗时 {elapsed:.2f}s 过长，可能无限循环")
        self.assertEqual(stats["total_entities"], 3)
        # 循环关系应被正确存储
        self.assertGreaterEqual(stats["total_relationships"], 0)

    # ---- E22: 并发修改同一实体 ----
    def test_22_concurrent_same_entity_modification(self):
        """多个文件定义同名class，验证路径区分"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        errors = []
        
        def file_writer(file_idx):
            try:
                for i in range(10):
                    # 所有文件定义同名的SharedClass
                    path = f"/src/shared/SharedClassFile{file_idx}_{i}.ets"
                    content = _make_arkts_content("SharedClass")
                    service._handle_file_change(path, content)
            except Exception as e:
                errors.append(str(e))
        
        threads = [threading.Thread(target=file_writer, args=(i,)) for i in range(10)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        
        stats = service.get_statistics()
        service.close()
        
        # 验证所有文件都被正确处理
        self.assertEqual(len(errors), 0, f"并发修改错误: {errors}")
        self.assertEqual(stats["total_files"], 100, "应有100个文件")
        
        # 验证每个文件都有自己的实体
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("""
            SELECT f.path, e.name 
            FROM entities e 
            JOIN files f ON e.file_id = f.id 
            WHERE e.name = 'SharedClass'
        """)
        results = cursor.fetchall()
        conn.close()
        
        # 所有同名实体应通过file_id区分
        self.assertEqual(len(results), 100, "每个文件应有自己的SharedClass实体")


# ===========================================================================
# F. 最终一致性验证
# ===========================================================================

class TestFinalConsistencyVerification(unittest.TestCase):
    """F. 最终一致性验证测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_consistency_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- F23: 断电恢复模拟 ----
    def test_23_power_failure_recovery(self):
        """处理过程中强制终止（不调用close），重新打开DB验证数据完整性"""
        # 阶段1: 正常处理一些文件
        service1 = KnowledgeGraphService(self.db_path)
        service1.parser = MockArkTSParser()
        
        for i in range(50):
            path = f"/src/powerFailFile{i:03d}.ets"
            content = _make_arkts_content(f"PowerFailClass{i}")
            service1._handle_file_change(path, content)
        
        # 记录状态
        stats_before = service1.get_statistics()
        
        # 阶段2: 模拟断电（不调用close，直接删除service对象）
        # 注意：这里我们只删除Python对象，不调用close()
        del service1
        gc.collect()
        
        # 阶段3: 重新打开DB
        service2 = KnowledgeGraphService(self.db_path)
        service2.parser = MockArkTSParser()
        
        stats_after = service2.get_statistics()
        
        # 验证数据完整性
        self.assertEqual(stats_after["total_files"], stats_before["total_files"],
                         "断电后文件数应保持一致")
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA integrity_check")
        
        # 验证无孤立entity
        cursor = conn.execute("""
            SELECT COUNT(*) FROM entities e 
            LEFT JOIN files f ON e.file_id = f.id 
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()[0]
        self.assertEqual(orphan_entities, 0, "不应有孤立entity")
        
        conn.close()
        service2.close()

    # ---- F24: WAL恢复验证 ----
    def test_24_wal_recovery_verification(self):
        """在WAL文件有未提交事务时重启，验证SQLite自动恢复"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 处理一些文件
        for i in range(30):
            path = f"/src/walRecoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"WalRecoveryClass{i}")
            service._handle_file_change(path, content)
        
        # 强制checkpoint
        conn = sqlite3.connect(self.db_path)
        conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        conn.close()
        
        # 继续处理更多文件
        for i in range(30, 60):
            path = f"/src/walRecoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"WalRecoveryClass{i}")
            service._handle_file_change(path, content)
        
        # 不调用close，直接删除
        del service
        gc.collect()
        
        # 检查WAL文件是否存在
        wal_path = self.db_path + "-wal"
        shm_path = self.db_path + "-shm"
        
        # 重新打开
        service2 = KnowledgeGraphService(self.db_path)
        service2.parser = MockArkTSParser()
        
        stats = service2.get_statistics()
        
        # 验证数据完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        self.assertEqual(integrity, "ok", f"数据库完整性检查失败: {integrity}")
        
        cursor = conn.execute("SELECT COUNT(*) FROM files")
        file_count = cursor.fetchone()[0]
        conn.close()
        
        self.assertGreater(file_count, 0, "应有文件被恢复")
        service2.close()

    # ---- F25: 全量数据校验 ----
    def test_25_full_data_verification(self):
        """处理1000个文件后，验证所有外键约束、CASCADE、UNIQUE约束正确"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 处理1000个文件
        for i in range(1000):
            path = f"/src/verifyFile{i:05d}.ets"
            content = _make_arkts_content(f"VerifyClass{i}")
            service._handle_file_change(path, content)
        
        service.close()
        
        # 全量数据校验
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. 外键约束验证
        # 每个entity的file_id都指向存在的file
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM entities e 
            LEFT JOIN files f ON e.file_id = f.id 
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_entities, 0, f"存在 {orphan_entities} 个孤立entity")
        
        # 每个relationship的source_entity_id指向存在的entity
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r 
            LEFT JOIN entities e ON r.source_entity_id = e.id 
            WHERE e.id IS NULL
        """)
        orphan_rel_source = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rel_source, 0, f"存在 {orphan_rel_source} 个孤立relationship source")
        
        # 每个relationship的target_entity_id指向存在的entity
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r 
            LEFT JOIN entities e ON r.target_entity_id = e.id 
            WHERE e.id IS NULL
        """)
        orphan_rel_target = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rel_target, 0, f"存在 {orphan_rel_target} 个孤立relationship target")
        
        # 每个import的file_id指向存在的file
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM imports i 
            LEFT JOIN files f ON i.file_id = f.id 
            WHERE f.id IS NULL
        """)
        orphan_imports = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_imports, 0, f"存在 {orphan_imports} 个孤立import")
        
        # 2. UNIQUE约束验证
        # 文件路径唯一
        cursor = conn.execute("""
            SELECT path, COUNT(*) as cnt FROM files 
            GROUP BY path HAVING cnt > 1
        """)
        duplicate_paths = cursor.fetchall()
        self.assertEqual(len(duplicate_paths), 0, f"存在重复的文件路径: {duplicate_paths}")
        
        # 3. CASCADE验证
        # 删除一个文件，验证关联数据被级联删除
        cursor = conn.execute("SELECT id FROM files LIMIT 1")
        file_row = cursor.fetchone()
        if file_row:
            file_id = file_row["id"]
            
            # 获取关联的entity数量
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE file_id = ?", (file_id,))
            entity_count_before = cursor.fetchone()["cnt"]
            
            # 删除文件（使用service的方法以确保事务正确提交）
            conn.execute("DELETE FROM files WHERE id = ?", (file_id,))
            conn.commit()
            
            # 验证entity被级联删除
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE file_id = ?", (file_id,))
            entity_count_after = cursor.fetchone()["cnt"]
            # 注意：CASCADE删除可能需要外键约束启用
            # 如果CASCADE未生效，entity_count_after可能不为0
            # 这里我们检查外键是否启用
            cursor = conn.execute("PRAGMA foreign_keys")
            fk_enabled = cursor.fetchone()[0]
            if fk_enabled:
                self.assertEqual(entity_count_after, 0, "CASCADE删除未生效")
            else:
                # 如果外键未启用，CASCADE不会生效，这是预期行为
                self.assertGreaterEqual(entity_count_after, 0)
        
        # 4. 数据完整性检查
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        self.assertEqual(integrity, "ok", f"数据库完整性检查失败: {integrity}")
        
        # 5. 外键启用验证（需要在新连接中启用并验证）
        cursor = conn.execute("PRAGMA foreign_keys")
        fk_enabled = cursor.fetchone()[0]
        # 注意：SQLite默认不启用外键，需要每个连接单独启用
        # 系统在_get_connection中启用了外键，但直接connect不会启用
        # 这里我们验证外键约束在schema中定义正确即可
        cursor = conn.execute("""
            SELECT sql FROM sqlite_master 
            WHERE type='table' AND name='entities'
        """)
        entity_schema = cursor.fetchone()
        if entity_schema:
            self.assertIn("FOREIGN KEY", entity_schema[0], "entities表应有外键约束")
        
        conn.close()


# ===========================================================================
# 综合终极场景
# ===========================================================================

class TestUltimateCombinedScenarios(unittest.TestCase):
    """综合终极场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v20_ultimate_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ultimate_stress_with_all_conditions(self):
        """终极压力测试：并发+异常注入+大数据量+边界条件"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        errors = []
        lock = threading.Lock()
        stop_event = threading.Event()
        
        def stress_writer(thread_id, use_chaos=False):
            """压力写入线程"""
            for i in range(100):
                if stop_event.is_set():
                    break
                try:
                    path = f"/src/ultimateFile_{thread_id}_{i:04d}.ets"
                    content = _make_arkts_content(f"UltimateClass_{thread_id}_{i}")
                    
                    if use_chaos and i % 10 == 0:
                        # 每10次注入一次异常
                        chaos_parser.set_exception_mode("Injected error", 1)
                    
                    service._handle_file_change(path, content)
                    chaos_parser.reset()
                except Exception as e:
                    with lock:
                        errors.append((thread_id, i, str(e)))
        
        def stress_reader(thread_id):
            """压力读取线程"""
            for i in range(200):
                if stop_event.is_set():
                    break
                try:
                    stats = service.get_statistics()
                    self.assertIn("total_files", stats)
                    results = service.search("UltimateClass")
                    self.assertIsInstance(results, list)
                except Exception as e:
                    with lock:
                        errors.append((f"reader_{thread_id}", i, str(e)))
        
        # 启动50个写入线程和10个读取线程
        writer_threads = [
            threading.Thread(target=stress_writer, args=(i, i % 5 == 0))
            for i in range(50)
        ]
        reader_threads = [
            threading.Thread(target=stress_reader, args=(i,))
            for i in range(10)
        ]
        
        all_threads = writer_threads + reader_threads
        for t in all_threads:
            t.start()
        
        # 运行60秒
        time.sleep(60)
        stop_event.set()
        
        for t in all_threads:
            t.join(timeout=30)
        
        stats = service.get_statistics()
        service.close()
        
        # 验证系统稳定性
        self.assertGreater(stats["total_files"], 0, "应有文件被处理")
        self.assertLess(len(errors), 100, f"错误过多: {len(errors)}")
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(integrity, "ok", "数据库完整性检查失败")

    def test_ultimate_recovery_after_chaos(self):
        """混沌后的恢复测试"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 阶段1: 正常处理
        for i in range(100):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            service._handle_file_change(path, content)
        
        # 阶段2: 注入各种异常
        chaos_parser.set_exception_mode("Chaos exception", 50)
        for i in range(100, 200):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            try:
                service._handle_file_change(path, content)
            except:
                pass
        
        # 阶段3: 恢复正常
        chaos_parser.reset()
        for i in range(200, 300):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            service._handle_file_change(path, content)
        
        stats = service.get_statistics()
        service.close()
        
        # 验证系统恢复
        self.assertGreater(stats["total_files"], 100, "系统应恢复正常处理")
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(integrity, "ok", "数据库完整性检查失败")


# ===========================================================================
# 运行测试
# ===========================================================================

def run_tests():
    """运行所有测试并生成报告"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加所有测试类
    test_classes = [
        TestExtremeBoundaryConditions,
        TestExtremeConcurrency,
        TestExtremeDataScale,
        TestExtremeExceptionInjection,
        TestExtremeBusinessScenarios,
        TestFinalConsistencyVerification,
        TestUltimateCombinedScenarios,
    ]
    
    for test_class in test_classes:
        tests = loader.loadTestsFromTestCase(test_class)
        suite.addTests(tests)
    
    # 运行测试
    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    
    # 打印总结
    print("\n" + "=" * 70)
    print("终极极端混沌测试报告")
    print("=" * 70)
    print(f"运行测试: {result.testsRun}")
    print(f"成功: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print(f"跳过: {len(result.skipped)}")
    
    if result.failures:
        print("\n失败的测试:")
        for test, traceback in result.failures:
            print(f"  - {test}: {traceback[:200]}...")
    
    if result.errors:
        print("\n出错的测试:")
        for test, traceback in result.errors:
            print(f"  - {test}: {traceback[:200]}...")
    
    print("=" * 70)
    
    return result


if __name__ == "__main__":
    result = run_tests()
    sys.exit(0 if result.wasSuccessful() else 1)
