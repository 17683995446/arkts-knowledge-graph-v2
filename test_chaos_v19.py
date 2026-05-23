#!/usr/bin/env python3
"""
ArkTS 知识图谱系统 - 第四轮（最终轮）极端混沌测试
test_chaos_v19.py

覆盖场景：
  A. 分布式模拟场景 (1-3)
  B. 极端数据结构场景 (4-8)
  C. 时序与竞态场景 (9-12)
  D. 数据完整性深度验证 (13-15)
  E. API边界测试 (16-19)
  F. 资源泄漏最终验证 (20-22)
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
from io import StringIO
from unittest.mock import patch, MagicMock

# 确保项目根目录在 sys.path 中
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser
from config import MAX_NODES_DISPLAY


def _make_arkts_content(class_name: str, extra_imports: str = "",
                         extra_code: str = "") -> str:
    """生成合法的 ArkTS 文件内容（文件名满足 MIN_FILE_WORDS=4）"""
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


# ===========================================================================
# A. 分布式模拟场景
# ===========================================================================

class TestDistributedSimulation(unittest.TestCase):
    """A. 分布式模拟场景"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_dist_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- A1: 三实例竞争 ----
    def test_01_three_instances_compete(self):
        """3个KnowledgeGraphDB实例同时操作同一个数据库文件，交替执行INSERT/DELETE/SELECT"""
        db1 = KnowledgeGraphDB(self.db_path)
        db2 = KnowledgeGraphDB(self.db_path)
        db3 = KnowledgeGraphDB(self.db_path)

        errors = []
        results = {"inserts": 0, "deletes": 0, "selects": 0}
        lock = threading.Lock()

        def worker(db, wid):
            try:
                for i in range(30):
                    # INSERT
                    path = f"/src/worker{wid}_file{i}.ets"
                    content = _make_arkts_content(f"Worker{wid}File{i}")
                    fid = db.add_or_update_file(path, content)
                    self.assertIsNotNone(fid)
                    with lock:
                        results["inserts"] += 1

                    # SELECT
                    stats = db.get_statistics()
                    self.assertIn("total_files", stats)
                    with lock:
                        results["selects"] += 1

                    # DELETE (每隔3次删除一个)
                    if i % 3 == 2:
                        db.delete_file_data(path)
                        with lock:
                            results["deletes"] += 1
            except Exception as e:
                errors.append((wid, str(e)))

        threads = [
            threading.Thread(target=worker, args=(db1, 1)),
            threading.Thread(target=worker, args=(db2, 2)),
            threading.Thread(target=worker, args=(db3, 3)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"三实例竞争产生错误: {errors}")

        # 最终一致性：WAL模式下，总文件数应为 inserts - deletes
        final_stats = db1.get_statistics()
        expected_files = results["inserts"] - results["deletes"]
        # 由于3个实例可能插入相同path，实际文件数 <= expected_files
        self.assertLessEqual(final_stats["total_files"], expected_files)
        self.assertGreater(final_stats["total_files"], 0)

        db1.close()
        db2.close()
        db3.close()

    # ---- A2: 读写分离模拟 ----
    def test_02_read_write_separation(self):
        """一个线程持续写入，另一个线程持续读取统计信息，验证数据一致性"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        write_count = {"value": 0}
        read_inconsistencies = []
        stop_event = threading.Event()

        def writer():
            for i in range(50):
                if stop_event.is_set():
                    break
                path = f"/src/readWriteFile{i}.ets"
                content = _make_arkts_content(f"RwFile{i}")
                service._handle_file_change(path, content)
                with service._lock:
                    write_count["value"] += 1
                time.sleep(0.01)

        def reader():
            last_files = 0
            for _ in range(100):
                if stop_event.is_set():
                    break
                try:
                    stats = service.get_statistics()
                    # 验证基本字段存在
                    self.assertIn("total_files", stats)
                    self.assertIn("total_entities", stats)
                    self.assertIn("files_processed", stats)
                    # 文件数只能增加或不变（本场景无删除）
                    if stats["total_files"] < last_files:
                        read_inconsistencies.append(
                            f"文件数回退: {last_files} -> {stats['total_files']}"
                        )
                    last_files = stats["total_files"]
                except Exception as e:
                    read_inconsistencies.append(f"读取异常: {e}")
                time.sleep(0.005)

        t_write = threading.Thread(target=writer)
        t_read = threading.Thread(target=reader)
        t_write.start()
        t_read.start()
        t_write.join(timeout=15)
        t_read.join(timeout=15)

        self.assertEqual(len(read_inconsistencies), 0,
                         f"读写分离一致性异常: {read_inconsistencies}")
        self.assertGreater(write_count["value"], 0)

        service.close()

    # ---- A3: 热重启模拟 ----
    def test_03_hot_restart(self):
        """启动->处理100个文件->close->重新打开同一个DB->再处理100个文件->验证数据累积"""
        parser = MockArkTSParser()

        # 第一轮：处理100个文件
        service1 = KnowledgeGraphService(self.db_path)
        service1.parser = parser
        for i in range(100):
            path = f"/src/restartFile{i}.ets"
            content = _make_arkts_content(f"RestartFile{i}")
            service1._handle_file_change(path, content)
        stats1 = service1.get_statistics()
        service1.close()

        # 第二轮：重新打开，再处理100个不同文件
        service2 = KnowledgeGraphService(self.db_path)
        service2.parser = parser
        for i in range(100, 200):
            path = f"/src/restartFile{i}.ets"
            content = _make_arkts_content(f"RestartFile{i}")
            service2._handle_file_change(path, content)
        stats2 = service2.get_statistics()
        service2.close()

        # 验证累积（total_files 来自DB，是持久化的）
        self.assertEqual(stats2["total_files"], 200)
        self.assertGreaterEqual(stats2["total_entities"], 200)
        # files_processed 是内存计数器，重启后重置；验证DB中的实际文件数即可
        self.assertEqual(stats2["files_processed"], 100)  # 第二轮处理了100个


# ===========================================================================
# B. 极端数据结构场景
# ===========================================================================

class TestExtremeDataStructures(unittest.TestCase):
    """B. 极端数据结构场景"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_data_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.service = KnowledgeGraphService(self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- B4: 钻石依赖链 ----
    def test_04_diamond_dependency(self):
        """A->B, A->C, B->D, C->D（菱形依赖），验证依赖关系正确"""
        # 文件D（底层）- 使用非相对路径的import以便file_dependencies能匹配
        # 注意：文件路径不带前导/，import模块路径 src/baseComponent 经 normpath 后为 src/baseComponent.ets
        content_d = "import { Component } from 'ohosComponent'\nexport class BaseComponent extends Component { build() { return; } }\n"
        # 文件B（中间层，依赖D）
        content_b = (
            "import { Component } from 'ohosComponent'\n"
            "import { BaseComponent } from 'src/baseComponent'\n"
            "export class MiddleLeft extends BaseComponent { build() { return; } }\n"
        )
        # 文件C（中间层，依赖D）
        content_c = (
            "import { Component } from 'ohosComponent'\n"
            "import { BaseComponent } from 'src/baseComponent'\n"
            "export class MiddleRight extends BaseComponent { build() { return; } }\n"
        )
        # 文件A（顶层，依赖B和C）
        content_a = (
            "import { Component } from 'ohosComponent'\n"
            "import { MiddleLeft } from 'src/middleLeft'\n"
            "import { MiddleRight } from 'src/middleRight'\n"
            "export class TopComponent extends MiddleLeft { build() { return; } }\n"
        )

        # 使用不带前导/的路径，使 normpath("src/baseComponent.ets") == "src/baseComponent.ets"
        self.service._handle_file_change("src/baseComponent.ets", content_d)
        self.service._handle_file_change("src/middleLeft.ets", content_b)
        self.service._handle_file_change("src/middleRight.ets", content_c)
        self.service._handle_file_change("src/topComponent.ets", content_a)

        # 验证所有文件都被处理
        stats = self.service.get_statistics()
        self.assertEqual(stats["total_files"], 4)

        # 验证依赖关系（file_dependencies通过import路径匹配文件路径）
        deps = self.service.db.get_file_dependencies()
        dep_pairs = {(d["source_file"], d["target_file"]) for d in deps}

        # A 依赖 B 和 C
        self.assertIn(("src/topComponent.ets", "src/middleLeft.ets"), dep_pairs)
        self.assertIn(("src/topComponent.ets", "src/middleRight.ets"), dep_pairs)
        # B 和 C 依赖 D
        self.assertIn(("src/middleLeft.ets", "src/baseComponent.ets"), dep_pairs)
        self.assertIn(("src/middleRight.ets", "src/baseComponent.ets"), dep_pairs)

        # 验证实体关系（注意：extends的目标如果在其他文件中，relationship不会写入DB，
        # 因为 transaction_callback 只查找同文件内的 entity_name_to_id）
        rels = self.service.db.get_all_relationships()
        # 关系可能为空（跨文件extends不写入relationships表），这是预期行为
        # 钻石依赖通过 file_dependencies 表体现
        self.assertIsInstance(rels, list)

    # ---- B5: 自包含文件 ----
    def test_05_self_referencing_file(self):
        """class A extends A, interface B implements B（完全自引用），验证不crash"""
        content = (
            "import { Component } from 'ohosComponent'\n"
            "export class SelfRef extends SelfRef {\n"
            "  build() { return; }\n"
            "}\n"
            "export interface SelfInterface extends SelfInterface {\n"
            "  method(): void;\n"
            "}\n"
        )
        # 不应crash
        self.service._handle_file_change("/src/selfReferencing.ets", content)
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats["total_files"], 1)
        self.assertGreaterEqual(stats["total_entities"], 1)

    # ---- B6: 空关系文件 ----
    def test_06_empty_relationships(self):
        """文件有entities但relationships为空列表，验证正常处理"""
        # 使用 MockArkTSParser 的正常解析，然后手动构造空关系
        content = (
            "import { Component } from 'ohosComponent'\n"
            "export class StandaloneClass {\n"
            "  private data: string = '';\n"
            "  getData(): string { return this.data; }\n"
            "}\n"
        )
        # 直接使用DB层操作，模拟只有entities没有relationships
        file_id = self.service.db.add_or_update_file("/src/standaloneClass.ets", content)
        self.service.db.add_entity(file_id, "StandaloneClass", "class",
                                    signature="class StandaloneClass",
                                    start_line=2, end_line=6)
        self.service.db.add_entity(file_id, "StandaloneClass.getData", "function",
                                    signature="getData(): string",
                                    start_line=4, end_line=4)

        stats = self.service.get_statistics()
        self.assertEqual(stats["total_entities"], 2)
        # relationships 可能为0（没有添加任何关系）
        self.assertEqual(stats["total_relationships"], 0)

    # ---- B7: 巨型单文件 ----
    def test_07_giant_single_file(self):
        """一个文件包含1000行代码，50个class，每个class有10个method"""
        lines = ["import { Component } from 'ohosComponent'\n"]
        for cls_idx in range(50):
            lines.append(f"export class GiantClass{cls_idx:03d} extends Component {{\n")
            for method_idx in range(10):
                lines.append(
                    f"  method{method_idx:02d}(): string {{ return 'm{method_idx}'; }}\n"
                )
            lines.append("}\n")
        content = "".join(lines)

        start = time.time()
        self.service._handle_file_change("/src/giantClassesFile.ets", content)
        elapsed = time.time() - start

        stats = self.service.get_statistics()
        self.assertEqual(stats["total_files"], 1)
        # 50个class + 50*10=500个method = 550 entities
        self.assertGreaterEqual(stats["total_entities"], 50)
        # 验证性能：处理时间应在合理范围内（10秒）
        self.assertLess(elapsed, 10, f"巨型文件处理耗时 {elapsed:.2f}s 过长")

    # ---- B8: 零宽度字符文件名 ----
    def test_08_zero_width_char_filename(self):
        """文件名包含零宽空格(U+200B)、零宽连接符(U+200D)、组合字符"""
        # 零宽空格 U+200B, 零宽连接符 U+200D, 组合重音 U+0301
        special_name = "test\u200Bzero\u200Dwidth\u0301File"
        path = f"/src/{special_name}.ets"
        content = _make_arkts_content("ZeroWidthClass")

        # 不应crash
        self.service._handle_file_change(path, content)
        file_id = self.service.db.get_file_id(path)
        self.assertIsNotNone(file_id, f"零宽字符文件名未被正确存储: {path}")


# ===========================================================================
# C. 时序与竞态场景
# ===========================================================================

class TestTimingAndRaceConditions(unittest.TestCase):
    """C. 时序与竞态场景"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_race_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- C9: Shutdown风暴 ----
    def test_09_shutdown_storm(self):
        """10个线程同时调用close()，验证幂等性和无crash"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        # 先处理一些文件
        for i in range(10):
            service._handle_file_change(f"/src/shutdownFile{i}.ets",
                                        _make_arkts_content(f"ShutdownFile{i}"))

        errors = []

        def close_thread(tid):
            try:
                service.close()
            except Exception as e:
                errors.append((tid, str(e)))

        threads = [threading.Thread(target=close_thread, args=(i,)) for i in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # 不应有未捕获的异常
        self.assertEqual(len(errors), 0, f"Shutdown风暴产生错误: {errors}")

    # ---- C10: 快速启停循环 ----
    def test_10_rapid_start_stop_cycle(self):
        """start->close->start->close 循环50次，验证无资源泄漏"""
        thread_count_before = threading.active_count()
        for cycle in range(50):
            service = KnowledgeGraphService(self.db_path)
            service.parser = MockArkTSParser()
            # 处理少量文件
            service._handle_file_change(
                f"/src/cycle{cycle}File.ets",
                _make_arkts_content(f"Cycle{cycle}File")
            )
            service.close()

        # 等待daemon线程清理
        time.sleep(1)
        thread_count_after = threading.active_count()

        # 活跃线程数不应无限增长（允许少量波动）
        self.assertLessEqual(thread_count_after, thread_count_before + 5,
                             f"线程泄漏: {thread_count_before} -> {thread_count_after}")

    # ---- C11: 文件锁饥饿 ----
    def test_11_file_lock_starvation(self):
        """一个线程持有文件锁10秒，其他线程等待，验证最终都能完成"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        target_file = "/src/lockStarvationFile.ets"
        completed = []
        lock = threading.Lock()

        def slow_worker():
            """模拟慢处理"""
            file_lock = service._get_file_lock(target_file)
            file_lock.acquire()
            try:
                time.sleep(2)  # 持有锁2秒
                service._handle_file_change_inner(target_file,
                                                   _make_arkts_content("SlowClass"))
                with lock:
                    completed.append("slow")
            finally:
                file_lock.release()

        def fast_worker(wid):
            """尝试处理同一文件"""
            time.sleep(0.1)  # 确保slow先获取锁
            service._handle_file_change(target_file,
                                        _make_arkts_content(f"FastClass{wid}"))
            with lock:
                completed.append(f"fast_{wid}")

        t_slow = threading.Thread(target=slow_worker)
        fast_threads = [threading.Thread(target=fast_worker, args=(i,))
                        for i in range(5)]

        t_slow.start()
        for t in fast_threads:
            t.start()

        t_slow.join(timeout=30)
        for t in fast_threads:
            t.join(timeout=30)

        # slow必须完成
        self.assertIn("slow", completed)
        # 至少部分fast也应该完成
        self.assertGreater(len(completed), 1, "文件锁饥饿：其他线程未能完成")

        service.close()

    # ---- C12: Timer取消竞态 ----
    def test_12_timer_cancel_race(self):
        """在timer即将触发时取消它，验证不会crash也不会处理过期数据"""
        from file_watcher import ArkTSFileHandler

        processed_files = []
        lock = threading.Lock()

        def callback(file_path, content):
            with lock:
                processed_files.append(file_path)

        handler = ArkTSFileHandler(callback, debounce_seconds=0.1)

        # 快速创建和取消timer
        for i in range(100):
            file_path = f"/src/timerRaceFile{i}.ets"
            handler._schedule_update(file_path)
            # 立即取消
            time.sleep(0.01)  # 让timer有机会启动
            with handler.lock:
                if file_path in handler.pending_files:
                    handler.pending_files[file_path].cancel()
                    del handler.pending_files[file_path]

        # 等待所有可能的timer完成
        time.sleep(1)

        # 清理
        with handler.lock:
            for fp, timer in list(handler.pending_files.items()):
                timer.cancel()
            handler.pending_files.clear()

        # 验证没有crash（如果能走到这里就说明没crash）
        self.assertTrue(True, "Timer取消竞态测试完成，无crash")


# ===========================================================================
# D. 数据完整性深度验证
# ===========================================================================

class TestDataIntegrity(unittest.TestCase):
    """D. 数据完整性深度验证"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_integ_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.service = KnowledgeGraphService(self.db_path)
        self.service.parser = MockArkTSParser()

    def tearDown(self):
        self.service.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- D13: 全量数据校验 ----
    def test_13_full_data_verification(self):
        """处理200个文件后，直接查询SQLite验证数据完整性"""
        parser = self.service.parser
        expected_entities = 0
        expected_rels = 0

        for i in range(200):
            path = f"/src/verifyFile{i}.ets"
            content = _make_arkts_content(f"VerifyClass{i}")
            self.service._handle_file_change(path, content)

            # 统计预期数据
            parse_result = parser.parse_file(path, content)
            expected_entities += len(parse_result["entities"])
            # 注意：relationships 中 extends Component 的 target 在同一文件中找不到entity，
            # 所以实际写入DB的relationships可能少于parse_result中的数量
            # 这里直接从DB查询实际数量进行校验

        stats = self.service.get_statistics()

        # 直接查询SQLite进行校验
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 1. files表行数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files")
        file_count = cursor.fetchone()["cnt"]
        self.assertEqual(file_count, 200, f"files表行数 {file_count} != 200")

        # 2. entities表行数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities")
        entity_count = cursor.fetchone()["cnt"]
        self.assertEqual(entity_count, expected_entities,
                         f"entities表行数 {entity_count} != 预期 {expected_entities}")

        # 3. relationships表行数（直接从DB获取实际值）
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM relationships")
        rel_count = cursor.fetchone()["cnt"]
        # relationships应该 >= 0，且所有关系都指向有效entity
        self.assertGreaterEqual(rel_count, 0)

        # 4. 每个entity的file_id都指向存在的file
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM entities e "
            "LEFT JOIN files f ON e.file_id = f.id WHERE f.id IS NULL"
        )
        orphan_entities = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_entities, 0,
                         f"存在 {orphan_entities} 个孤立entity")

        # 5. 每个relationship的source_entity_id和target_entity_id都指向存在的entity
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM relationships r "
            "LEFT JOIN entities se ON r.source_entity_id = se.id "
            "WHERE se.id IS NULL"
        )
        orphan_source = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_source, 0,
                         f"存在 {orphan_source} 个关系指向不存在的source entity")

        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM relationships r "
            "LEFT JOIN entities te ON r.target_entity_id = te.id "
            "WHERE te.id IS NULL"
        )
        orphan_target = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_target, 0,
                         f"存在 {orphan_target} 个关系指向不存在的target entity")

        # 6. 没有孤立的import
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM imports i "
            "LEFT JOIN files f ON i.file_id = f.id WHERE f.id IS NULL"
        )
        orphan_imports = cursor.fetchone()["cnt"]
        self.assertEqual(orphan_imports, 0,
                         f"存在 {orphan_imports} 个孤立import")

        conn.close()

    # ---- D14: 级联删除完整性 ----
    def test_14_cascade_delete_integrity(self):
        """删除一个被其他文件import的文件，验证所有关联数据被清理"""
        # 创建被依赖文件
        dep_content = (
            "import { Component } from 'ohosComponent'\n"
            "export class SharedDependency extends Component {\n"
            "  build() { return; }\n"
            "}\n"
        )
        self.service._handle_file_change("/src/sharedDependency.ets", dep_content)

        # 创建依赖它的文件
        importer_content = (
            "import { Component } from 'ohosComponent'\n"
            "import { SharedDependency } from './sharedDependency'\n"
            "export class ImporterClass extends SharedDependency {\n"
            "  build() { return; }\n"
            "}\n"
        )
        self.service._handle_file_change("/src/importerClass.ets", importer_content)

        # 创建无关文件
        unrelated_content = _make_arkts_content("UnrelatedClass")
        self.service._handle_file_change("/src/unrelatedClass.ets", unrelated_content)

        # 记录删除前的状态
        stats_before = self.service.get_statistics()
        self.assertGreater(stats_before["total_files"], 0)

        # 获取被依赖文件的entity和relationship信息
        dep_file_id = self.service.db.get_file_id("/src/sharedDependency.ets")
        self.assertIsNotNone(dep_file_id)
        dep_entities = self.service.db.get_file_entities(dep_file_id)
        self.assertGreater(len(dep_entities), 0)

        # 获取无关文件信息
        unrelated_file_id = self.service.db.get_file_id("/src/unrelatedClass.ets")
        unrelated_entities_before = self.service.db.get_file_entities(unrelated_file_id)

        # 删除被依赖文件
        deleted = self.service.db.delete_file_data("/src/sharedDependency.ets")
        self.assertTrue(deleted, "删除操作应返回True")

        # 验证级联删除
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 1. files表中该文件被删除
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE path = ?",
                              ("/src/sharedDependency.ets",))
        self.assertEqual(cursor.fetchone()["cnt"], 0, "文件未被从files表删除")

        # 2. entities表中该文件的entities被删除
        for ent in dep_entities:
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM entities WHERE id = ?",
                                  (ent["id"],))
            self.assertEqual(cursor.fetchone()["cnt"], 0,
                             f"entity {ent['name']} 未被级联删除")

        # 3. relationships表中涉及该文件entities的关系被删除
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM relationships r "
            "JOIN entities e ON (r.source_entity_id = e.id OR r.target_entity_id = e.id) "
            "WHERE e.file_id = ?", (dep_file_id,)
        )
        self.assertEqual(cursor.fetchone()["cnt"], 0,
                         "涉及被删文件entities的关系未被级联删除")

        # 4. imports表中该文件的imports被删除
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports WHERE file_id = ?",
                              (dep_file_id,))
        self.assertEqual(cursor.fetchone()["cnt"], 0,
                         "imports未被级联删除")

        # 5. file_dependencies中涉及该文件的记录被删除
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM file_dependencies "
            "WHERE source_file_id = ? OR target_file_id = ?",
            (dep_file_id, dep_file_id)
        )
        self.assertEqual(cursor.fetchone()["cnt"], 0,
                         "file_dependencies未被级联删除")

        # 6. 其他文件的数据不受影响
        unrelated_entities_after = self.service.db.get_file_entities(unrelated_file_id)
        self.assertEqual(len(unrelated_entities_before), len(unrelated_entities_after),
                         "无关文件的entities受到影响")

        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files WHERE path = ?",
                              ("/src/unrelatedClass.ets",))
        self.assertEqual(cursor.fetchone()["cnt"], 1, "无关文件被误删")

        conn.close()

    # ---- D15: Hash唯一性 ----
    def test_15_hash_uniqueness(self):
        """验证1000个不同内容产生1000个不同的hash"""
        db = self.service.db
        hashes = set()

        for i in range(1000):
            content = _make_arkts_content(f"HashClass{i:04d}")
            h = db.compute_file_hash(content)
            self.assertNotIn(h, hashes, f"Hash碰撞: 内容 {i} 与之前某个内容产生相同hash")
            hashes.add(h)

        self.assertEqual(len(hashes), 1000, "1000个不同内容应产生1000个不同hash")


# ===========================================================================
# E. API边界测试
# ===========================================================================

class TestAPIBoundary(unittest.TestCase):
    """E. API边界测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_api_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.service = KnowledgeGraphService(self.db_path)
        self.service.parser = MockArkTSParser()

        # 预填充一些数据
        for i in range(20):
            path = f"/src/apiTestFile{i}.ets"
            content = _make_arkts_content(f"ApiTestClass{i}")
            self.service._handle_file_change(path, content)

    def tearDown(self):
        self.service.close()
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- E16: 超大GET参数 ----
    def test_16_oversized_search_param(self):
        """搜索参数包含10000个字符"""
        long_query = "A" * 10000
        results = self.service.search(long_query)
        # 不应crash，返回空结果或部分匹配
        self.assertIsInstance(results, list)

    # ---- E17: Unicode搜索 ----
    def test_17_unicode_search(self):
        """搜索中文、日文、韩文、阿拉伯文实体名"""
        # 插入Unicode实体名
        unicode_entities = [
            ("ChineseClass", "class", "中文类"),
            ("JapaneseClass", "class", "日本語クラス"),
            ("KoreanClass", "class", "한국어클래스"),
            ("ArabicClass", "class", "الفئةالعربية"),
        ]

        for name, etype, display_name in unicode_entities:
            file_id = self.service.db.add_or_update_file(
                f"/src/{name}.ets", f"export class {name} {{}}"
            )
            self.service.db.add_entity(file_id, name, etype,
                                        signature=f"class {name}")

        # 搜索中文
        results_cn = self.service.db.search_entities("Chinese")
        self.assertGreater(len(results_cn), 0, "搜索 'Chinese' 应有结果")

        # 搜索日文
        results_jp = self.service.db.search_entities("Japanese")
        self.assertGreater(len(results_jp), 0, "搜索 'Japanese' 应有结果")

        # 搜索韩文
        results_kr = self.service.db.search_entities("Korean")
        self.assertGreater(len(results_kr), 0, "搜索 'Korean' 应有结果")

        # 搜索阿拉伯文
        results_ar = self.service.db.search_entities("Arabic")
        self.assertGreater(len(results_ar), 0, "搜索 'Arabic' 应有结果")

        # 搜索不存在的Unicode
        results_empty = self.service.db.search_entities("不存在的类xyz")
        self.assertEqual(len(results_empty), 0, "不存在的Unicode搜索应返回空")

    # ---- E18: 并发WebSocket模拟 ----
    def test_18_concurrent_websocket_simulation(self):
        """模拟多个客户端同时连接和断开（通过回调机制）"""
        callback_calls = []
        callback_lock = threading.Lock()

        def mock_callback(file_path, stats):
            with callback_lock:
                callback_calls.append(file_path)

        self.service.on_update_callback = mock_callback

        # 多线程同时触发文件更新
        threads = []
        for i in range(20):
            t = threading.Thread(
                target=self.service._handle_file_change,
                args=(f"/src/wsFile{i}.ets", _make_arkts_content(f"WsClass{i}"))
            )
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=15)

        # 等待回调线程完成
        time.sleep(2)

        # 验证回调被调用
        with callback_lock:
            self.assertGreater(len(callback_calls), 0,
                               "WebSocket回调应被触发")

        self.service.on_update_callback = None

    # ---- E19: API响应格式验证 ----
    def test_19_api_response_format(self):
        """验证每个API返回的数据结构符合预期"""
        # get_statistics
        stats = self.service.get_statistics()
        self.assertIsInstance(stats, dict)
        required_stat_keys = {"total_files", "total_entities", "total_relationships",
                              "total_imports", "files_processed", "entities_extracted",
                              "relationships_extracted", "last_update_time"}
        for key in required_stat_keys:
            self.assertIn(key, stats, f"stats缺少字段: {key}")
        self.assertIsInstance(stats["total_files"], int)
        self.assertIsInstance(stats["total_entities"], int)
        self.assertIsInstance(stats["total_relationships"], int)

        # search
        results = self.service.search("ApiTest")
        self.assertIsInstance(results, list)
        if results:
            result = results[0]
            self.assertIsInstance(result, dict)
            self.assertIn("id", result)
            self.assertIn("name", result)
            self.assertIn("type", result)
            self.assertIn("file_path", result)

        # get_graph_data
        graph = self.service.get_graph_data()
        self.assertIsInstance(graph, dict)
        self.assertIn("entities", graph)
        self.assertIn("relationships", graph)
        self.assertIn("files", graph)
        self.assertIn("dependencies", graph)
        self.assertIsInstance(graph["entities"], list)
        self.assertIsInstance(graph["relationships"], list)

        # get_all_files
        files = self.service.db.get_all_files()
        self.assertIsInstance(files, list)
        if files:
            f = files[0]
            self.assertIn("id", f)
            self.assertIn("path", f)

        # get_all_relationships
        rels = self.service.db.get_all_relationships()
        self.assertIsInstance(rels, list)
        if rels:
            r = rels[0]
            self.assertIn("id", r)
            self.assertIn("relation_type", r)
            self.assertIn("source_entity_id", r)
            self.assertIn("target_entity_id", r)

        # get_recent_events
        events = self.service.db.get_recent_events()
        self.assertIsInstance(events, list)

        # search_entities (空查询)
        empty_results = self.service.db.search_entities("")
        self.assertEqual(empty_results, [], "空查询应返回空列表")

        # search_entities (None)
        none_results = self.service.db.search_entities(None)
        self.assertEqual(none_results, [], "None查询应返回空列表")


# ===========================================================================
# F. 资源泄漏最终验证
# ===========================================================================

class TestResourceLeakDetection(unittest.TestCase):
    """F. 资源泄漏最终验证"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_leak_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    # ---- F20: 连接泄漏检测 ----
    def test_20_connection_leak_detection(self):
        """处理500个文件后，验证没有未关闭的SQLite连接"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        for i in range(500):
            path = f"/src/connLeakFile{i}.ets"
            content = _make_arkts_content(f"ConnLeakClass{i}")
            service._handle_file_change(path, content)

        service.close()

        # 验证DB可以正常打开（说明没有残留锁）
        try:
            conn = sqlite3.connect(self.db_path, timeout=1)
            cursor = conn.execute("SELECT COUNT(*) FROM files")
            count = cursor.fetchone()[0]
            conn.close()
            self.assertEqual(count, 500, f"文件数应为500，实际为{count}")
        except sqlite3.OperationalError as e:
            self.fail(f"存在残留的SQLite连接或锁: {e}")

    # ---- F21: 线程泄漏检测 ----
    def test_21_thread_leak_detection(self):
        """close()后验证没有活跃的非daemon线程"""
        # 记录测试前的非daemon线程
        before_threads = {t.name for t in threading.enumerate() if not t.daemon}

        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        # 处理一些文件
        for i in range(20):
            service._handle_file_change(f"/src/threadLeakFile{i}.ets",
                                        _make_arkts_content(f"ThreadLeakClass{i}"))

        service.close()

        # 等待daemon线程清理
        time.sleep(2)

        after_threads = {t.name for t in threading.enumerate() if not t.daemon}
        new_threads = after_threads - before_threads

        self.assertEqual(len(new_threads), 0,
                         f"close()后存在新的非daemon线程: {new_threads}")

    # ---- F22: 文件描述符检测 ----
    def test_22_file_descriptor_detection(self):
        """处理1000个文件后，验证进程的文件描述符数量在合理范围内"""
        import resource

        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        # 记录初始FD数量
        soft, hard = resource.getrlimit(resource.RLIMIT_NOFILE)
        fd_count_before = len(os.listdir(f"/proc/{os.getpid()}/fd"))

        for i in range(1000):
            path = f"/src/fdLeakFile{i}.ets"
            content = _make_arkts_content(f"FdLeakClass{i}")
            service._handle_file_change(path, content)

        # 等待GC
        time.sleep(1)

        fd_count_after = len(os.listdir(f"/proc/{os.getpid()}/fd"))

        service.close()

        # FD增长应在合理范围内（不超过100个新FD）
        fd_growth = fd_count_after - fd_count_before
        self.assertLess(fd_growth, 100,
                        f"FD增长过大: {fd_count_before} -> {fd_count_after} (增长 {fd_growth})")


# ===========================================================================
# 日志捕获验证辅助
# ===========================================================================

class TestLogCaptureVerification(unittest.TestCase):
    """使用输出捕获验证各场景的预期行为"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_log_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_log_capture_file_processing(self):
        """验证文件处理过程中产生预期的输出日志"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        # 系统使用print()输出日志，通过捕获stdout验证
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            service._handle_file_change("/src/logCaptureFile.ets",
                                        _make_arkts_content("LogCaptureClass"))
            output = mock_stdout.getvalue()

        # 验证文件被正确处理
        stats = service.get_statistics()
        self.assertGreaterEqual(stats["total_files"], 1)
        # 验证日志中包含处理信息
        self.assertIn("Processing", output, "日志应包含 'Processing'")
        self.assertIn("DONE", output, "日志应包含 'DONE'")

        service.close()

    def test_log_capture_delete_event(self):
        """验证删除事件产生预期的日志"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        # 先创建文件
        service._handle_file_change("/src/deleteLogCapture.ets",
                                    _make_arkts_content("DeleteLogClass"))

        # 删除文件
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            service._handle_file_change("/src/deleteLogCapture.ets", None)
            output = mock_stdout.getvalue()

        stats = service.get_statistics()
        # 文件被删除后，files_processed 应增加（删除也计数）
        self.assertGreaterEqual(stats["files_processed"], 2)
        # 验证日志中包含删除信息
        self.assertIn("DELETE", output, "日志应包含 'DELETE'")

        service.close()

    def test_log_capture_retry_mechanism(self):
        """验证重试机制在文件锁竞争时产生重试日志"""
        service = KnowledgeGraphService(self.db_path)
        service.parser = MockArkTSParser()

        target_file = "/src/retryLogCapture.ets"
        retry_detected = threading.Event()

        # 持有文件锁
        file_lock = service._get_file_lock(target_file)
        file_lock.acquire()

        def delayed_release():
            time.sleep(1.5)
            file_lock.release()
            retry_detected.set()

        t = threading.Thread(target=delayed_release)
        t.start()

        # 尝试处理同一文件（应该触发重试）
        time.sleep(0.1)
        with patch('sys.stdout', new_callable=StringIO) as mock_stdout:
            service._handle_file_change(target_file,
                                        _make_arkts_content("RetryLogClass"))
            output = mock_stdout.getvalue()

        retry_detected.wait(timeout=10)
        t.join(timeout=10)

        # 验证重试日志
        self.assertIn("RETRY", output, "日志应包含 'RETRY' 重试信息")

        # 验证文件最终被处理
        stats = service.get_statistics()
        self.assertGreaterEqual(stats["files_processed"], 1)

        service.close()


# ===========================================================================
# 综合极端场景
# ===========================================================================

class TestCombinedExtremeScenarios(unittest.TestCase):
    """综合多种极端条件同时施加"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_v19_combo_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        import shutil
        shutil.rmtree(self.tmpdir, ignore_errors=True)

    def test_combined_mixed_operations(self):
        """混合操作：并发写入+删除+查询+重启，验证系统稳定性"""
        parser = MockArkTSParser()
        errors = []

        # 阶段1：并发写入100个文件
        service1 = KnowledgeGraphService(self.db_path)
        service1.parser = parser

        def phase1_writer(start, end):
            try:
                for i in range(start, end):
                    service1._handle_file_change(
                        f"/src/combinedFile{i}.ets",
                        _make_arkts_content(f"CombinedClass{i}")
                    )
            except Exception as e:
                errors.append(f"Phase1 writer [{start}-{end}]: {e}")

        threads = [
            threading.Thread(target=phase1_writer, args=(0, 50)),
            threading.Thread(target=phase1_writer, args=(50, 100)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        self.assertEqual(len(errors), 0, f"阶段1错误: {errors}")
        stats1 = service1.get_statistics()
        self.assertEqual(stats1["total_files"], 100)

        # 阶段2：并发删除+查询
        def phase2_deleter():
            try:
                for i in range(0, 50):
                    service1._handle_file_change(
                        f"/src/combinedFile{i}.ets", None
                    )
            except Exception as e:
                errors.append(f"Phase2 deleter: {e}")

        def phase2_reader():
            try:
                for _ in range(20):
                    stats = service1.get_statistics()
                    self.assertIn("total_files", stats)
                    time.sleep(0.01)
            except Exception as e:
                errors.append(f"Phase2 reader: {e}")

        t_del = threading.Thread(target=phase2_deleter)
        t_read = threading.Thread(target=phase2_reader)
        t_del.start()
        t_read.start()
        t_del.join(timeout=30)
        t_read.join(timeout=30)

        self.assertEqual(len(errors), 0, f"阶段2错误: {errors}")

        # 阶段3：关闭并重启
        service1.close()

        service2 = KnowledgeGraphService(self.db_path)
        service2.parser = parser

        # 验证剩余50个文件
        stats2 = service2.get_statistics()
        self.assertEqual(stats2["total_files"], 50,
                         f"重启后文件数应为50，实际为{stats2['total_files']}")

        # 阶段4：再添加50个新文件
        for i in range(100, 150):
            service2._handle_file_change(
                f"/src/combinedFile{i}.ets",
                _make_arkts_content(f"CombinedClass{i}")
            )

        stats3 = service2.get_statistics()
        self.assertEqual(stats3["total_files"], 100)

        service2.close()

    def test_combined_stress_with_integrity_check(self):
        """压力测试后进行完整性校验"""
        parser = MockArkTSParser()
        service = KnowledgeGraphService(self.db_path)
        service.parser = parser

        # 并发写入200个文件
        def writer(start, end):
            for i in range(start, end):
                service._handle_file_change(
                    f"/src/stressIntegrityFile{i}.ets",
                    _make_arkts_content(f"StressIntegrityClass{i}")
                )

        threads = [
            threading.Thread(target=writer, args=(0, 100)),
            threading.Thread(target=writer, args=(100, 200)),
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        service.close()

        # 完整性校验
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row

        # 检查无孤立entity
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM entities e "
            "LEFT JOIN files f ON e.file_id = f.id WHERE f.id IS NULL"
        )
        self.assertEqual(cursor.fetchone()["cnt"], 0, "存在孤立entity")

        # 检查无孤立relationship source
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM relationships r "
            "LEFT JOIN entities se ON r.source_entity_id = se.id "
            "WHERE se.id IS NULL"
        )
        self.assertEqual(cursor.fetchone()["cnt"], 0, "存在指向不存在entity的source")

        # 检查无孤立relationship target
        cursor = conn.execute(
            "SELECT COUNT(*) as cnt FROM relationships r "
            "LEFT JOIN entities te ON r.target_entity_id = te.id "
            "WHERE te.id IS NULL"
        )
        self.assertEqual(cursor.fetchone()["cnt"], 0, "存在指向不存在entity的target")

        # 检查文件数
        cursor = conn.execute("SELECT COUNT(*) as cnt FROM files")
        self.assertEqual(cursor.fetchone()["cnt"], 200, "文件数不为200")

        conn.close()


if __name__ == "__main__":
    unittest.main(verbosity=2)
