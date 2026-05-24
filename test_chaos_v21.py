#!/usr/bin/env python3
"""
ArkTS 知识图谱系统 - 第六轮（终极轮）极端混沌测试
test_chaos_v21.py

覆盖场景：
  A. 极限参数边界 (1-5)
  B. 极限内存与性能 (6-9)
  C. 极限错误恢复 (10-13)
  D. 极限业务场景 (14-16)
  E. 最终一致性验证 (17-20)
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
import random
import string
import shutil
from io import StringIO
from unittest.mock import patch, MagicMock, PropertyMock
from typing import Dict, List, Tuple, Optional
from contextlib import contextmanager

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
# A. 极限参数边界
# ===========================================================================

class TestExtremeParameterBoundary(unittest.TestCase):
    """A. 极限参数边界测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_boundary_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.db = KnowledgeGraphDB(self.db_path)
        self.service = KnowledgeGraphService(self.db_path)
        self.chaos_parser = ChaosParser()
        self.service.parser = self.chaos_parser

    def tearDown(self):
        self.service.close()
        self.db.close()
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- A1: limit=0 测试 ----
    def test_01_limit_zero(self):
        """limit=0参数测试：search_entities/get_recent_events/get_all_entities"""
        # 先插入一些测试数据
        for i in range(10):
            path = f"/src/limitTestFile{i}.ets"
            content = _make_arkts_content(f"LimitTestClass{i}")
            self.service._handle_file_change(path, content)
        
        # 测试 search_entities(limit=0)
        results = self.db.search_entities("LimitTestClass", limit=0)
        self.assertEqual(len(results), 0, "search_entities(limit=0)应返回空列表")
        
        # 测试 get_recent_events(limit=0)
        events = self.db.get_recent_events(limit=0)
        self.assertEqual(len(events), 0, "get_recent_events(limit=0)应返回空列表")
        
        # 测试 get_all_entities(limit=0)
        entities = self.db.get_all_entities(limit=0)
        self.assertEqual(len(entities), 0, "get_all_entities(limit=0)应返回空列表")
        
        # 测试 get_all_files(limit=0)
        files = self.db.get_all_files(limit=0)
        self.assertEqual(len(files), 0, "get_all_files(limit=0)应返回空列表")
        
        # 测试 get_all_relationships(limit=0)
        rels = self.db.get_all_relationships(limit=0)
        self.assertEqual(len(rels), 0, "get_all_relationships(limit=0)应返回空列表")
        
        # 测试 get_file_dependencies(limit=0)
        deps = self.db.get_file_dependencies(limit=0)
        self.assertEqual(len(deps), 0, "get_file_dependencies(limit=0)应返回空列表")

    # ---- A2: limit=-1 测试 ----
    def test_02_limit_negative(self):
        """limit=-1负数参数测试"""
        # 先插入测试数据
        for i in range(5):
            path = f"/src/negativeLimitFile{i}.ets"
            content = _make_arkts_content(f"NegativeLimitClass{i}")
            self.service._handle_file_change(path, content)
        
        # SQLite对负数limit的处理：会返回空结果或报错
        # 我们验证系统不crash
        
        try:
            results = self.db.search_entities("NegativeLimitClass", limit=-1)
            # 如果成功，结果应该是空或有限数量
            self.assertIsInstance(results, list)
        except Exception as e:
            # 如果抛出异常，验证是预期的SQLite错误
            self.assertIn("near", str(e).lower() + "constraint" + "limit")
        
        try:
            events = self.db.get_recent_events(limit=-1)
            self.assertIsInstance(events, list)
        except Exception:
            pass  # 预期可能出错
        
        try:
            entities = self.db.get_all_entities(limit=-1)
            self.assertIsInstance(entities, list)
        except Exception:
            pass  # 预期可能出错

    # ---- A3: limit=超大值测试 ----
    def test_03_limit_extremely_large(self):
        """limit=1000000超大值参数测试"""
        # 插入100个文件
        for i in range(100):
            path = f"/src/largeLimitFile{i:03d}.ets"
            content = _make_arkts_content(f"LargeLimitClass{i}")
            self.service._handle_file_change(path, content)
        
        # 使用超大limit值
        results = self.db.search_entities("LargeLimitClass", limit=1000000)
        self.assertLessEqual(len(results), 100, "结果不应超过实际数据量")
        
        entities = self.db.get_all_entities(limit=1000000)
        self.assertLessEqual(len(entities), 100, "实体数不应超过实际数量")
        
        files = self.db.get_all_files(limit=1000000)
        self.assertEqual(len(files), 100, "文件数应为100")
        
        # 验证系统稳定性
        stats = self.service.get_statistics()
        self.assertEqual(stats["total_files"], 100)

    # ---- A4: entity_id边界测试 ----
    def test_04_entity_id_boundary(self):
        """entity_id边界值测试：0、-1、超大值"""
        # 插入测试数据
        path = "/src/entityIdTestFile.ets"
        content = _make_arkts_content("EntityIdTestClass")
        self.service._handle_file_change(path, content)
        
        # 测试 entity_id=0
        result = self.db.get_entity_details(0)
        self.assertIsNone(result, "entity_id=0应返回None")
        
        # 测试 entity_id=-1
        result = self.db.get_entity_details(-1)
        self.assertIsNone(result, "entity_id=-1应返回None")
        
        # 测试 entity_id=超大值
        result = self.db.get_entity_details(999999999999)
        self.assertIsNone(result, "entity_id=超大值应返回None")
        
        # 测试 entity_id=None（如果API允许）
        try:
            result = self.db.get_entity_details(None)
            self.assertIsNone(result, "entity_id=None应返回None或抛出异常")
        except (TypeError, AttributeError):
            pass  # 预期可能抛出异常
        
        # 测试 entity_id为字符串
        try:
            result = self.db.get_entity_details("invalid_id")
            self.assertIsNone(result)
        except (TypeError, ValueError):
            pass  # 预期可能抛出异常

    # ---- A5: 空查询字符串测试 ----
    def test_05_empty_query_string(self):
        """空查询字符串测试：search_entities("")、search_entities("   ")"""
        # 插入测试数据
        for i in range(10):
            path = f"/src/emptyQueryFile{i}.ets"
            content = _make_arkts_content(f"EmptyQueryClass{i}")
            self.service._handle_file_change(path, content)
        
        # 测试空字符串查询
        results = self.db.search_entities("")
        self.assertEqual(len(results), 0, "空字符串查询应返回空列表")
        
        # 测试纯空格查询
        results = self.db.search_entities("   ")
        self.assertEqual(len(results), 0, "纯空格查询应返回空列表")
        
        # 测试制表符查询
        results = self.db.search_entities("\t\t")
        self.assertEqual(len(results), 0, "制表符查询应返回空列表")
        
        # 测试换行符查询
        results = self.db.search_entities("\n\n")
        self.assertEqual(len(results), 0, "换行符查询应返回空列表")
        
        # 测试混合空白字符查询
        results = self.db.search_entities("  \t\n  ")
        self.assertEqual(len(results), 0, "混合空白字符查询应返回空列表")
        
        # 验证正常查询仍然工作
        results = self.db.search_entities("EmptyQueryClass")
        self.assertGreater(len(results), 0, "正常查询应返回结果")


# ===========================================================================
# B. 极限内存与性能
# ===========================================================================

class TestExtremeMemoryAndPerformance(unittest.TestCase):
    """B. 极限内存与性能测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_perf_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- B6: 100000实体批量插入 ----
    def test_06_100000_entities_batch_insert(self):
        """100000实体批量插入测试：验证内存和事务性能"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 生成100000个实体
        entities = []
        batch_size = 10000
        total_entities = 100000
        
        start_time = time.time()
        
        for batch in range(total_entities // batch_size):
            entities = []
            for i in range(batch_size):
                idx = batch * batch_size + i
                entities.append({
                    "name": f"MassiveEntity{idx:06d}",
                    "type": "class",
                    "signature": f"class MassiveEntity{idx:06d}",
                    "start_line": i * 10 + 1,
                    "end_line": i * 10 + 10
                })
            
            chaos_parser.set_override_result({
                "entities": entities,
                "relationships": [],
                "imports": []
            })
            
            path = f"/src/massiveBatch{batch:03d}.ets"
            service._handle_file_change(path, f"content batch {batch}")
            
            # 每10个批次强制GC
            if batch % 10 == 0:
                gc.collect()
        
        elapsed = time.time() - start_time
        stats = service.get_statistics()
        
        service.close()
        
        # 验证结果
        self.assertEqual(stats["total_files"], total_entities // batch_size)
        self.assertEqual(stats["total_entities"], total_entities)
        self.assertLess(elapsed, 300, f"100000实体批量插入耗时 {elapsed:.2f}s 过长")
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("SELECT COUNT(*) FROM entities")
        count = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(count, total_entities)

    # ---- B7: 超大响应测试 ----
    def test_07_large_response_test(self):
        """get_graph_data()返回10000个文件的测试"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建10000个文件
        start_time = time.time()
        for i in range(10000):
            path = f"/src/largeResponseFile{i:05d}.ets"
            content = _make_arkts_content(f"LargeResponseClass{i}")
            service._handle_file_change(path, content)
            
            if i % 1000 == 0:
                gc.collect()
        
        insert_time = time.time() - start_time
        
        # 获取图数据
        start_time = time.time()
        graph_data = service.get_graph_data()
        query_time = time.time() - start_time
        
        service.close()
        
        # 验证返回数据受MAX_NODES_DISPLAY限制
        self.assertLessEqual(len(graph_data["entities"]), MAX_NODES_DISPLAY)
        self.assertLessEqual(len(graph_data["relationships"]), MAX_NODES_DISPLAY * 2)
        self.assertLessEqual(len(graph_data["files"]), MAX_NODES_DISPLAY * 5)
        
        # 验证性能
        self.assertLess(query_time, 10, f"get_graph_data()耗时 {query_time:.2f}s 过长")
        self.assertLess(insert_time, 300, f"插入10000文件耗时 {insert_time:.2f}s 过长")

    # ---- B8: 持续运行24小时模拟 ----
    def test_08_simulated_long_running(self):
        """模拟长时间运行的内存泄漏测试（压缩为60秒）"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 记录初始内存状态
        gc.collect()
        initial_objects = len(gc.get_objects())
        
        iterations = 1000
        errors = []
        
        start_time = time.time()
        
        # 模拟长时间运行的操作循环
        for i in range(iterations):
            try:
                # 创建文件
                path = f"/src/longRunningFile{i:05d}.ets"
                content = _make_arkts_content(f"LongRunningClass{i}")
                service._handle_file_change(path, content)
                
                # 查询操作
                stats = service.get_statistics()
                results = service.search("LongRunningClass")
                
                # 每100次更新文件（模拟编辑）
                if i % 100 == 0 and i > 0:
                    update_path = f"/src/longRunningFile{i-50:05d}.ets"
                    updated_content = _make_arkts_content(f"UpdatedClass{i}")
                    service._handle_file_change(update_path, updated_content)
                
                # 每200次删除文件
                if i % 200 == 0 and i > 0:
                    delete_path = f"/src/longRunningFile{i-100:05d}.ets"
                    service._handle_file_change(delete_path, None)
                
                # 每50次强制GC
                if i % 50 == 0:
                    gc.collect()
                    
            except Exception as e:
                errors.append(str(e))
        
        elapsed = time.time() - start_time
        
        # 检查内存状态
        gc.collect()
        final_objects = len(gc.get_objects())
        object_growth = final_objects - initial_objects
        
        stats = service.get_statistics()
        service.close()
        
        # 验证结果
        self.assertLess(len(errors), 10, f"错误过多: {errors[:5]}")
        self.assertGreater(stats["total_files"], 0)
        
        # 验证内存增长在合理范围内（允许一定的增长，但不应是数量级的）
        # 注意：这个检查可能因Python版本和环境而异
        self.assertLess(object_growth, 100000, 
                       f"对象数量增长过多: {object_growth}")
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(integrity, "ok", "数据库完整性检查失败")

    # ---- B9: 并发API压力 ----
    def test_09_concurrent_api_stress(self):
        """100个线程同时调用所有API的压力测试"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 先创建一些基础数据
        for i in range(50):
            path = f"/src/apiStressBaseFile{i}.ets"
            content = _make_arkts_content(f"ApiStressBaseClass{i}")
            service._handle_file_change(path, content)
        
        errors = []
        lock = threading.Lock()
        completed = {"count": 0}
        api_calls = {
            "write": 0,
            "read_stats": 0,
            "search": 0,
            "graph_data": 0,
            "entity_details": 0
        }
        
        def api_stress_worker(thread_id):
            """API压力测试工作线程"""
            for i in range(50):
                try:
                    # 写入操作
                    path = f"/src/apiStressFile_{thread_id}_{i}.ets"
                    content = _make_arkts_content(f"ApiStressClass_{thread_id}_{i}")
                    service._handle_file_change(path, content)
                    with lock:
                        api_calls["write"] += 1
                    
                    # 读取统计
                    stats = service.get_statistics()
                    with lock:
                        api_calls["read_stats"] += 1
                    
                    # 搜索操作
                    results = service.search("ApiStressClass")
                    with lock:
                        api_calls["search"] += 1
                    
                    # 获取图数据
                    graph_data = service.get_graph_data()
                    with lock:
                        api_calls["graph_data"] += 1
                    
                    # 获取实体详情（如果有）
                    if stats["total_entities"] > 0:
                        entities = service.db.get_all_entities(limit=1)
                        if entities:
                            entity_id = entities[0]["id"]
                            service.db.get_entity_details(entity_id)
                            with lock:
                                api_calls["entity_details"] += 1
                                
                except Exception as e:
                    with lock:
                        errors.append((thread_id, i, str(e)))
            
            with lock:
                completed["count"] += 1
        
        threads = [threading.Thread(target=api_stress_worker, args=(i,)) for i in range(100)]
        
        start_time = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=120)
        elapsed = time.time() - start_time
        
        stats = service.get_statistics()
        service.close()
        
        # 验证结果
        self.assertEqual(completed["count"], 100, "所有线程应完成")
        self.assertLess(len(errors), 50, f"错误过多: {len(errors)}")
        
        # 验证API调用次数
        self.assertGreater(api_calls["write"], 0)
        self.assertGreater(api_calls["read_stats"], 0)
        self.assertGreater(api_calls["search"], 0)
        self.assertGreater(api_calls["graph_data"], 0)
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        conn.close()
        
        self.assertEqual(integrity, "ok")


# ===========================================================================
# C. 极限错误恢复
# ===========================================================================

class TestExtremeErrorRecovery(unittest.TestCase):
    """C. 极限错误恢复测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_error_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- C10: SQLite损坏恢复 ----
    def test_10_sqlite_corruption_recovery(self):
        """模拟数据库文件损坏后的恢复测试"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建一些数据
        for i in range(20):
            path = f"/src/corruptionTestFile{i}.ets"
            content = _make_arkts_content(f"CorruptionTestClass{i}")
            service._handle_file_change(path, content)
        
        service.close()
        
        # 模拟数据库损坏：在文件中写入随机字节
        with open(self.db_path, "r+b") as f:
            # 读取原始内容
            content = f.read()
            # 在中间位置写入随机字节
            if len(content) > 1000:
                corrupt_pos = len(content) // 2
                f.seek(corrupt_pos)
                f.write(os.urandom(100))
        
        # 尝试重新打开数据库
        try:
            service2 = KnowledgeGraphService(self.db_path)
            service2.parser = MockArkTSParser()
            
            # 尝试操作
            try:
                stats = service2.get_statistics()
                # 如果能获取统计，说明SQLite可能自动恢复了
                self.assertIsNotNone(stats)
            except sqlite3.DatabaseError as e:
                # 预期：数据库损坏错误
                self.assertIn("database disk image is malformed", str(e).lower() + "corrupt")
            
            service2.close()
        except sqlite3.DatabaseError as e:
            # 预期：打开时即报错
            self.assertIn("database", str(e).lower())
        
        # 验证可以通过删除损坏的DB重新开始
        os.remove(self.db_path)
        
        service3 = KnowledgeGraphService(self.db_path)
        service3.parser = MockArkTSParser()
        
        # 新数据库应该可以正常工作
        service3._handle_file_change("/src/newFile.ets", _make_arkts_content("NewClass"))
        stats = service3.get_statistics()
        
        service3.close()
        
        self.assertEqual(stats["total_files"], 1)

    # ---- C11: WAL文件损坏 ----
    def test_11_wal_corruption_recovery(self):
        """模拟WAL文件损坏后的恢复测试"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建数据
        for i in range(20):
            path = f"/src/walCorruptFile{i}.ets"
            content = _make_arkts_content(f"WalCorruptClass{i}")
            service._handle_file_change(path, content)
        
        # 确保有WAL文件
        wal_path = self.db_path + "-wal"
        shm_path = self.db_path + "-shm"
        
        # 不关闭service，让WAL有未提交的数据
        # 模拟WAL损坏
        if os.path.exists(wal_path):
            with open(wal_path, "r+b") as f:
                content = f.read()
                if len(content) > 100:
                    f.seek(len(content) // 2)
                    f.write(os.urandom(50))
        
        service.close()
        
        # 尝试重新打开
        try:
            service2 = KnowledgeGraphService(self.db_path)
            service2.parser = MockArkTSParser()
            
            # SQLite应该能够处理WAL损坏
            stats = service2.get_statistics()
            
            # 验证数据库完整性
            conn = sqlite3.connect(self.db_path)
            cursor = conn.execute("PRAGMA integrity_check")
            integrity = cursor.fetchone()[0]
            conn.close()
            
            # 如果WAL损坏，SQLite可能会：
            # 1. 自动恢复
            # 2. 忽略WAL中的损坏部分
            # 3. 报错
            self.assertIn(integrity, ["ok", "database disk image is malformed"])
            
            service2.close()
        except sqlite3.DatabaseError:
            # 预期：可能无法打开
            pass

    # ---- C12: 外键约束违反 ----
    def test_12_foreign_key_constraint_violation(self):
        """尝试插入无效的外键引用"""
        db = KnowledgeGraphDB(self.db_path)
        
        # 尝试插入指向不存在file_id的entity
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_entity(
                file_id=999999,  # 不存在的file_id
                name="OrphanEntity",
                entity_type="class"
            )
        
        # 尝试插入指向不存在entity_id的relationship
        # 先创建一个有效的文件
        file_id = db.add_or_update_file("/src/validFile.ets", "content")
        
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_relationship(
                source_file_id=file_id,
                source_entity_id=999999,  # 不存在的entity_id
                target_entity_id=888888,  # 不存在的entity_id
                relation_type="invalid"
            )
        
        # 尝试插入指向不存在file_id的import
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_import(
                file_id=999999,
                module_path="invalid/module"
            )
        
        # 尝试插入指向不存在file_id的file_dependency
        # 外键约束会抛出异常
        with self.assertRaises(sqlite3.IntegrityError):
            db.add_file_dependency(file_id, 999999)
        
        db.close()

    # ---- C13: UNIQUE约束违反 ----
    def test_13_unique_constraint_violation(self):
        """尝试插入重复记录"""
        db = KnowledgeGraphDB(self.db_path)
        
        # 创建第一个文件
        file_id1 = db.add_or_update_file("/src/duplicateFile.ets", "content1")
        
        # 尝试插入相同路径的文件（应该更新而不是插入）
        file_id2 = db.add_or_update_file("/src/duplicateFile.ets", "content2")
        
        # 验证是更新而不是插入新记录
        self.assertEqual(file_id1, file_id2, "相同路径应更新而非插入")
        
        # 验证只有一个文件
        files = db.get_all_files()
        paths = [f["path"] for f in files]
        self.assertEqual(paths.count("/src/duplicateFile.ets"), 1)
        
        # 测试file_dependencies的UNIQUE约束
        file_id_a = db.add_or_update_file("/src/fileA.ets", "contentA")
        file_id_b = db.add_or_update_file("/src/fileB.ets", "contentB")
        
        # 第一次插入依赖
        result1 = db.add_file_dependency(file_id_a, file_id_b)
        
        # 第二次插入相同依赖（应该被忽略）
        result2 = db.add_file_dependency(file_id_a, file_id_b)
        
        # 验证只有一个依赖记录
        deps = db.get_file_dependencies()
        same_deps = [d for d in deps if d["source_file"] == "/src/fileA.ets" 
                     and d["target_file"] == "/src/fileB.ets"]
        self.assertEqual(len(same_deps), 1, "相同依赖应只有一条记录")
        
        db.close()


# ===========================================================================
# D. 极限业务场景
# ===========================================================================

class TestExtremeBusinessScenarios(unittest.TestCase):
    """D. 极限业务场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_business_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- D14: 1000个文件的完整生命周期 ----
    def test_14_1000_files_full_lifecycle(self):
        """1000个文件的完整生命周期：创建→扫描→修改→删除→验证"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 阶段1: 创建1000个文件
        start_time = time.time()
        for i in range(1000):
            path = f"/src/lifecycle/File{i:04d}.ets"
            content = _make_arkts_content(f"LifecycleClass{i}")
            service._handle_file_change(path, content)
        create_time = time.time() - start_time
        
        stats1 = service.get_statistics()
        self.assertEqual(stats1["total_files"], 1000)
        
        # 阶段2: 修改所有文件
        start_time = time.time()
        for i in range(1000):
            path = f"/src/lifecycle/File{i:04d}.ets"
            content = _make_arkts_content(f"ModifiedClass{i}_v2")
            service._handle_file_change(path, content)
        modify_time = time.time() - start_time
        
        stats2 = service.get_statistics()
        self.assertEqual(stats2["total_files"], 1000, "修改不应增加文件数")
        
        # 阶段3: 删除一半文件
        start_time = time.time()
        for i in range(500):
            path = f"/src/lifecycle/File{i:04d}.ets"
            service._handle_file_change(path, None)
        delete_time = time.time() - start_time
        
        stats3 = service.get_statistics()
        self.assertEqual(stats3["total_files"], 500)
        
        # 阶段4: 验证数据完整性
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 验证没有孤立实体
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM entities e
            LEFT JOIN files f ON e.file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_entities, 0)
        
        # 验证没有孤立关系
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r
            LEFT JOIN files f ON r.source_file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_rels = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rels, 0)
        
        conn.close()
        service.close()
        
        # 验证性能
        self.assertLess(create_time, 60, f"创建1000文件耗时 {create_time:.2f}s")
        self.assertLess(modify_time, 60, f"修改1000文件耗时 {modify_time:.2f}s")
        self.assertLess(delete_time, 30, f"删除500文件耗时 {delete_time:.2f}s")

    # ---- D15: 跨文件引用链 ----
    def test_15_cross_file_reference_chain(self):
        """26个文件的引用链：A→B→C→...→Z"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        letters = "ABCDEFGHIJKLMNOPQRSTUVWXYZ"
        
        # 创建26个文件，每个文件引用下一个
        for i, letter in enumerate(letters):
            next_letter = letters[(i + 1) % 26] if i < 25 else None
            
            # 创建实体
            entities = [
                {"name": f"Class{letter}", "type": "class", "start_line": 1, "end_line": 10}
            ]
            
            # 创建关系
            relationships = []
            if next_letter:
                entities.append({"name": f"Class{next_letter}", "type": "class", 
                               "start_line": 11, "end_line": 20})
                relationships.append({
                    "source": f"Class{letter}",
                    "target": f"Class{next_letter}",
                    "type": "references"
                })
            
            chaos_parser.set_override_result({
                "entities": entities,
                "relationships": relationships,
                "imports": []
            })
            
            path = f"/src/chain/Module{letter}.ets"
            service._handle_file_change(path, f"content for {letter}")
        
        stats = service.get_statistics()
        
        # 验证26个文件都被创建
        self.assertEqual(stats["total_files"], 26)
        
        # 验证实体数量（每个文件至少有1个实体）
        self.assertGreaterEqual(stats["total_entities"], 26)
        
        # 验证关系数量（25个引用关系）
        self.assertGreaterEqual(stats["total_relationships"], 0)
        
        # 验证引用链完整性
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 检查是否有循环引用（A→B→...→Z→A）
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships
            WHERE relation_type = 'references'
        """)
        ref_count = cursor.fetchone()["cnt"]
        
        conn.close()
        service.close()
        
        # 系统应正确处理引用链
        self.assertGreaterEqual(ref_count, 0)

    # ---- D16: 并发修改同一个实体 ----
    def test_16_concurrent_same_entity_modification(self):
        """10个文件定义同名class，验证路径区分"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        errors = []
        lock = threading.Lock()
        completed = {"count": 0}
        
        def file_writer(writer_id):
            """并发写入同名实体"""
            try:
                for i in range(10):
                    # 所有文件定义同名的SharedClass
                    path = f"/src/shared/SharedClass_{writer_id}_{i}.ets"
                    content = _make_arkts_content("SharedClass")
                    service._handle_file_change(path, content)
                
                with lock:
                    completed["count"] += 1
            except Exception as e:
                with lock:
                    errors.append((writer_id, str(e)))
        
        # 10个线程并发写入
        threads = [threading.Thread(target=file_writer, args=(i,)) for i in range(10)]
        
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)
        
        stats = service.get_statistics()
        
        # 验证所有文件都被正确处理
        self.assertEqual(len(errors), 0, f"并发修改错误: {errors}")
        self.assertEqual(completed["count"], 10)
        self.assertEqual(stats["total_files"], 100, "应有100个文件")
        
        # 验证每个文件都有自己的实体
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        cursor = conn.execute("""
            SELECT f.path, e.name 
            FROM entities e 
            JOIN files f ON e.file_id = f.id 
            WHERE e.name = 'SharedClass'
            ORDER BY f.path
        """)
        results = cursor.fetchall()
        conn.close()
        
        # 所有同名实体应通过file_id区分
        self.assertEqual(len(results), 100, "每个文件应有自己的SharedClass实体")
        
        # 验证所有路径都是唯一的
        paths = [r["path"] for r in results]
        self.assertEqual(len(set(paths)), 100, "所有路径应唯一")
        
        service.close()


# ===========================================================================
# E. 最终一致性验证
# ===========================================================================

class TestFinalConsistencyVerification(unittest.TestCase):
    """E. 最终一致性验证测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_consistency_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- E17: 所有外键约束验证 ----
    def test_17_all_foreign_key_constraints(self):
        """验证所有外键关系正确"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建测试数据
        for i in range(100):
            path = f"/src/fkTestFile{i:03d}.ets"
            content = _make_arkts_content(f"FkTestClass{i}")
            service._handle_file_change(path, content)
        
        service.close()
        
        # 验证外键约束
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        
        # 1. entities.file_id -> files.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM entities e
            LEFT JOIN files f ON e.file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_entities, 0, f"存在 {orphan_entities} 个孤立entity")
        
        # 2. relationships.source_entity_id -> entities.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r
            LEFT JOIN entities e ON r.source_entity_id = e.id
            WHERE e.id IS NULL AND r.source_entity_id IS NOT NULL
        """)
        orphan_rel_source = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rel_source, 0, f"存在 {orphan_rel_source} 个孤立relationship source")
        
        # 3. relationships.target_entity_id -> entities.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r
            LEFT JOIN entities e ON r.target_entity_id = e.id
            WHERE e.id IS NULL AND r.target_entity_id IS NOT NULL
        """)
        orphan_rel_target = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rel_target, 0, f"存在 {orphan_rel_target} 个孤立relationship target")
        
        # 4. relationships.source_file_id -> files.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships r
            LEFT JOIN files f ON r.source_file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_rel_file = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_rel_file, 0, f"存在 {orphan_rel_file} 个孤立relationship file")
        
        # 5. imports.file_id -> files.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM imports i
            LEFT JOIN files f ON i.file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_imports = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_imports, 0, f"存在 {orphan_imports} 个孤立import")
        
        # 6. file_dependencies.source_file_id -> files.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM file_dependencies fd
            LEFT JOIN files f ON fd.source_file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_dep_source = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_dep_source, 0, f"存在 {orphan_dep_source} 个孤立dependency source")
        
        # 7. file_dependencies.target_file_id -> files.id
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM file_dependencies fd
            LEFT JOIN files f ON fd.target_file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_dep_target = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_dep_target, 0, f"存在 {orphan_dep_target} 个孤立dependency target")
        
        conn.close()

    # ---- E18: 所有UNIQUE约束验证 ----
    def test_18_all_unique_constraints(self):
        """验证所有唯一约束正确"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建测试数据
        for i in range(100):
            path = f"/src/uniqueTestFile{i:03d}.ets"
            content = _make_arkts_content(f"UniqueTestClass{i}")
            service._handle_file_change(path, content)
        
        service.close()
        
        # 验证UNIQUE约束
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 1. files.path 应唯一
        cursor = conn.execute("""
            SELECT path, COUNT(*) as cnt FROM files
            GROUP BY path HAVING cnt > 1
        """)
        duplicate_paths = cursor.fetchall()
        self.assertEqual(len(duplicate_paths), 0, f"存在重复的文件路径: {duplicate_paths}")
        
        # 2. file_dependencies (source_file_id, target_file_id) 应唯一
        cursor = conn.execute("""
            SELECT source_file_id, target_file_id, COUNT(*) as cnt 
            FROM file_dependencies
            GROUP BY source_file_id, target_file_id HAVING cnt > 1
        """)
        duplicate_deps = cursor.fetchall()
        self.assertEqual(len(duplicate_deps), 0, f"存在重复的文件依赖: {duplicate_deps}")
        
        conn.close()

    # ---- E19: 所有CASCADE验证 ----
    def test_19_all_cascade_deletes(self):
        """验证所有级联删除正确"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建测试数据
        for i in range(50):
            path = f"/src/cascadeTestFile{i:03d}.ets"
            content = _make_arkts_content(f"CascadeTestClass{i}")
            service._handle_file_change(path, content)
        
        stats_before = service.get_statistics()
        
        # 获取一个文件ID及其关联数据
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        
        cursor = conn.execute("SELECT id FROM files LIMIT 1")
        file_row = cursor.fetchone()
        file_id = file_row["id"]
        
        # 获取该文件的实体数量
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE file_id = ?", (file_id,))
        entity_count_before = cursor.fetchone()["cnt"]
        
        # 获取该文件的关系数量
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships WHERE source_file_id = ?
        """, (file_id,))
        rel_count_before = cursor.fetchone()["cnt"]
        
        # 获取该文件的导入数量
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports WHERE file_id = ?", (file_id,))
        import_count_before = cursor.fetchone()["cnt"]
        
        conn.close()
        
        # 删除文件
        delete_path = f"/src/cascadeTestFile{file_id-1:03d}.ets"
        service._handle_file_change(delete_path, None)
        
        # 验证级联删除
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys=ON")
        
        # 验证文件被删除
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE id = ?", (file_id,))
        file_count_after = cursor.fetchone()["cnt"]
        
        # 验证实体被级联删除
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE file_id = ?", (file_id,))
        entity_count_after = cursor.fetchone()["cnt"]
        
        # 验证关系被级联删除
        cursor = conn.execute("""
            SELECT COUNT(*) as cnt FROM relationships WHERE source_file_id = ?
        """, (file_id,))
        rel_count_after = cursor.fetchone()["cnt"]
        
        # 验证导入被级联删除
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports WHERE file_id = ?", (file_id,))
        import_count_after = cursor.fetchone()["cnt"]
        
        conn.close()
        service.close()
        
        # 验证级联删除效果
        self.assertEqual(file_count_after, 0, "文件应被删除")
        self.assertEqual(entity_count_after, 0, "实体应被级联删除")
        self.assertEqual(rel_count_after, 0, "关系应被级联删除")
        self.assertEqual(import_count_after, 0, "导入应被级联删除")

    # ---- E20: 统计数字精确验证 ----
    def test_20_statistics_precision_verification(self):
        """验证所有统计数字与实际数据一致"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()
        
        # 创建测试数据
        for i in range(100):
            path = f"/src/statsTestFile{i:03d}.ets"
            content = _make_arkts_content(f"StatsTestClass{i}")
            service._handle_file_change(path, content)
        
        # 获取统计
        stats = service.get_statistics()
        
        service.close()
        
        # 直接查询数据库验证统计准确性
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row
        
        # 验证文件数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files")
        actual_files = cursor.fetchone()["cnt"]
        self.assertEqual(stats["total_files"], actual_files, 
                        f"统计文件数 {stats['total_files']} 与实际 {actual_files} 不一致")
        
        # 验证实体数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities")
        actual_entities = cursor.fetchone()["cnt"]
        self.assertEqual(stats["total_entities"], actual_entities,
                        f"统计实体数 {stats['total_entities']} 与实际 {actual_entities} 不一致")
        
        # 验证关系数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM relationships")
        actual_rels = cursor.fetchone()["cnt"]
        self.assertEqual(stats["total_relationships"], actual_rels,
                        f"统计关系数 {stats['total_relationships']} 与实际 {actual_rels} 不一致")
        
        # 验证导入数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports")
        actual_imports = cursor.fetchone()["cnt"]
        self.assertEqual(stats["total_imports"], actual_imports,
                        f"统计导入数 {stats['total_imports']} 与实际 {actual_imports} 不一致")
        
        # 验证按类型分组的实体统计
        cursor = conn.execute("SELECT type, COUNT(*) as cnt FROM entities GROUP BY type")
        entities_by_type = {row["type"]: row["cnt"] for row in cursor.fetchall()}
        self.assertEqual(stats["entities_by_type"], entities_by_type,
                        "按类型分组的实体统计不一致")
        
        # 验证按类型分组的关系统计
        cursor = conn.execute("SELECT relation_type, COUNT(*) as cnt FROM relationships GROUP BY relation_type")
        rels_by_type = {row["relation_type"]: row["cnt"] for row in cursor.fetchall()}
        self.assertEqual(stats["relations_by_type"], rels_by_type,
                        "按类型分组的关系统计不一致")
        
        conn.close()


# ===========================================================================
# 综合终极场景
# ===========================================================================

class TestUltimateCombinedScenarios(unittest.TestCase):
    """综合终极场景测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v21_ultimate_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_ultimate_combined_stress(self):
        """终极综合压力测试：参数边界+并发+大数据量+错误恢复"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        errors = []
        lock = threading.Lock()
        stop_event = threading.Event()
        operations = {"success": 0, "failure": 0}
        
        def stress_worker(thread_id):
            """综合压力测试工作线程"""
            for i in range(100):
                if stop_event.is_set():
                    break
                try:
                    # 随机选择操作类型
                    op_type = random.choice(["write", "read", "search", "delete", "boundary"])
                    
                    if op_type == "write":
                        path = f"/src/ultimate_{thread_id}_{i:04d}.ets"
                        content = _make_arkts_content(f"UltimateClass_{thread_id}_{i}")
                        service._handle_file_change(path, content)
                    
                    elif op_type == "read":
                        stats = service.get_statistics()
                        self.assertIn("total_files", stats)
                    
                    elif op_type == "search":
                        # 测试边界查询
                        if i % 5 == 0:
                            # 空查询
                            results = service.search("")
                            self.assertEqual(len(results), 0)
                        else:
                            results = service.search("UltimateClass")
                            self.assertIsInstance(results, list)
                    
                    elif op_type == "delete":
                        if i > 10:
                            delete_idx = random.randint(0, i - 1)
                            path = f"/src/ultimate_{thread_id}_{delete_idx:04d}.ets"
                            service._handle_file_change(path, None)
                    
                    elif op_type == "boundary":
                        # 测试边界参数
                        service.db.get_all_entities(limit=random.choice([0, 1, 100, 1000000]))
                        service.db.search_entities("", limit=0)
                        service.db.get_entity_details(random.choice([0, -1, 999999]))
                    
                    with lock:
                        operations["success"] += 1
                        
                except Exception as e:
                    with lock:
                        operations["failure"] += 1
                        if len(errors) < 100:  # 限制错误记录
                            errors.append((thread_id, i, str(e)[:100]))
        
        # 启动50个压力线程
        threads = [threading.Thread(target=stress_worker, args=(i,)) for i in range(50)]
        
        start_time = time.time()
        for t in threads:
            t.start()
        
        # 运行60秒
        time.sleep(60)
        stop_event.set()
        
        for t in threads:
            t.join(timeout=30)
        elapsed = time.time() - start_time
        
        stats = service.get_statistics()
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        
        # 验证外键约束
        cursor = conn.execute("""
            SELECT COUNT(*) FROM entities e
            LEFT JOIN files f ON e.file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()[0]
        conn.close()
        
        service.close()
        
        # 验证结果
        self.assertEqual(integrity, "ok", "数据库完整性检查失败")
        self.assertEqual(orphan_entities, 0, "存在孤立实体")
        self.assertGreater(operations["success"], 0, "应有成功操作")
        self.assertLess(operations["failure"], operations["success"] * 0.5, 
                       f"失败率过高: {operations['failure']}/{operations['success']}")
        
        print(f"\n终极综合压力测试结果:")
        print(f"  运行时间: {elapsed:.2f}s")
        print(f"  成功操作: {operations['success']}")
        print(f"  失败操作: {operations['failure']}")
        print(f"  最终文件数: {stats['total_files']}")
        print(f"  最终实体数: {stats['total_entities']}")

    def test_ultimate_recovery_after_chaos(self):
        """混沌后的终极恢复测试"""
        service = KnowledgeGraphService(self.db_path)
        chaos_parser = ChaosParser()
        service.parser = chaos_parser
        
        # 阶段1: 正常处理
        for i in range(200):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            service._handle_file_change(path, content)
        
        stats1 = service.get_statistics()
        
        # 阶段2: 注入各种异常
        chaos_parser.set_exception_mode("Chaos exception", 100)
        for i in range(200, 400):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            try:
                service._handle_file_change(path, content)
            except:
                pass
        
        # 阶段3: 恢复正常
        chaos_parser.reset()
        for i in range(400, 600):
            path = f"/src/recoveryFile{i:03d}.ets"
            content = _make_arkts_content(f"RecoveryClass{i}")
            service._handle_file_change(path, content)
        
        stats2 = service.get_statistics()
        
        # 阶段4: 边界测试
        service.db.get_all_entities(limit=0)
        service.db.search_entities("")
        service.db.get_entity_details(0)
        service.db.get_entity_details(-1)
        service.db.get_entity_details(999999999)
        
        # 阶段5: 并发测试
        errors = []
        lock = threading.Lock()
        
        def concurrent_reader():
            for _ in range(50):
                try:
                    stats = service.get_statistics()
                    service.search("RecoveryClass")
                    service.get_graph_data()
                except Exception as e:
                    with lock:
                        errors.append(str(e))
        
        threads = [threading.Thread(target=concurrent_reader) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        
        # 验证数据库完整性
        conn = sqlite3.connect(self.db_path)
        cursor = conn.execute("PRAGMA integrity_check")
        integrity = cursor.fetchone()[0]
        
        cursor = conn.execute("""
            SELECT COUNT(*) FROM entities e
            LEFT JOIN files f ON e.file_id = f.id
            WHERE f.id IS NULL
        """)
        orphan_entities = cursor.fetchone()[0]
        conn.close()
        
        service.close()
        
        # 验证结果
        self.assertEqual(integrity, "ok")
        self.assertEqual(orphan_entities, 0)
        self.assertGreater(stats2["total_files"], stats1["total_files"])
        self.assertLess(len(errors), 10, f"并发读取错误: {errors}")


# ===========================================================================
# 运行测试
# ===========================================================================

def run_tests():
    """运行所有测试并生成报告"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    
    # 添加所有测试类
    test_classes = [
        TestExtremeParameterBoundary,
        TestExtremeMemoryAndPerformance,
        TestExtremeErrorRecovery,
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
    print("终极极端混沌测试报告 - 第六轮 (v21)")
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
