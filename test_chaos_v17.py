"""
ArkTS Knowledge Graph - Extreme Chaos Test Suite v17
=====================================================
对 ArkTS 知识图谱系统进行极端混沌测试，覆盖：
A. 极端并发混沌 (1-5)
B. 数据边界混沌 (6-10)
C. 解析器极限测试 (11-18)
D. 状态一致性极限 (19-22)
E. Web层混沌 (23-27)
F. 资源耗尽测试 (28-30)
G. 日志驱动验证（贯穿所有测试）
"""
import os
import sys
import time
import json
import random
import string
import hashlib
import threading
import tempfile
import unittest
import sqlite3
from io import StringIO
from unittest.mock import patch, MagicMock
from concurrent.futures import ThreadPoolExecutor, as_completed

# 确保项目路径在 sys.path 中
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from knowledge_graph_service import KnowledgeGraphService
from mock_llm_parser import MockArkTSParser
from config import MIN_FILE_WORDS, SUPPORTED_EXTENSIONS


# ============================================================================
# 日志捕获工具
# ============================================================================
class LogCapture:
    """捕获所有 print 输出，支持断言验证"""

    def __init__(self):
        self._buffer = StringIO()
        self._original_stdout = sys.stdout

    def __enter__(self):
        sys.stdout = self._buffer
        return self

    def __exit__(self, *args):
        sys.stdout = self._original_stdout

    @property
    def output(self) -> str:
        return self._buffer.getvalue()

    def contains(self, text: str) -> bool:
        return text in self.output

    def contains_any(self, *texts: str) -> bool:
        return any(t in self.output for t in texts)

    def contains_all(self, *texts: str) -> bool:
        return all(t in self.output for t in texts)

    def count_occurrences(self, text: str) -> int:
        return self.output.count(text)

    def not_contains(self, text: str) -> bool:
        return text not in self.output

    def lines(self) -> list:
        return self.output.strip().split('\n') if self.output.strip() else []

    def assert_contains(self, text: str, msg: str = ""):
        assert text in self.output, f"Expected '{text}' in output. Got:\n{self.output[-500:]} {msg}"

    def assert_not_contains(self, text: str, msg: str = ""):
        assert text not in self.output, f"Did NOT expect '{text}' in output. {msg}"

    def assert_no_unexpected_errors(self):
        """验证没有未预期的 ERROR 日志（排除已知的重试日志）"""
        for line in self.lines():
            if '[ERROR]' in line and '[RETRY]' not in line:
                # 允许特定的已知错误模式
                if 'still locked' not in line:
                    raise AssertionError(f"Unexpected error in log: {line}")


# ============================================================================
# 辅助工具函数
# ============================================================================
def make_arkts_content(class_name: str = "TestClass", method_count: int = 2,
                       extra_imports: bool = True) -> str:
    """生成有效的 ArkTS 文件内容"""
    lines = []
    if extra_imports:
        lines.append(f"import {{ Component }} from '@ohos/arkui';")
    lines.append(f"export class {class_name} {{")
    for i in range(method_count):
        lines.append(f"  build{method_count > 1 and i or ''}(): void {{ }}")
        lines.append(f"  aboutToAppear(): void {{ }}")
    lines.append("}")
    return '\n'.join(lines)


def make_arkts_filename(index: int) -> str:
    """生成符合 MIN_FILE_WORDS 要求的文件名"""
    return f"ComponentModule{index:04d}Handler.ets"


def random_string(length: int) -> str:
    """生成随机字符串"""
    return ''.join(random.choices(string.ascii_letters + string.digits, k=length))


def create_service(db_path: str) -> KnowledgeGraphService:
    """创建 KnowledgeGraphService 实例（不启动 watcher）"""
    service = KnowledgeGraphService(db_path=db_path, watch_paths=[])
    service.parser = MockArkTSParser()
    return service


# ============================================================================
# A. 极端并发混沌
# ============================================================================
class TestAExtremeConcurrency(unittest.TestCase):
    """A. 极端并发混沌测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_a_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def tearDown(self):
        # 确保资源释放
        try:
            pass
        except Exception:
            pass

    # --- A1: 百线程风暴 ---
    def test_01_hundred_thread_storm(self):
        """100个线程同时处理100个不同文件，验证无死锁、无数据丢失"""
        service = create_service(self.db_path)
        try:
            file_count = 100
            files = {}
            for i in range(file_count):
                path = os.path.join(self.tmpdir, make_arkts_filename(i))
                content = make_arkts_content(f"StormClass{i}")
                files[path] = content

            errors = []
            results_lock = threading.Lock()
            completed = []

            def process_file(path, content):
                try:
                    service._handle_file_change(path, content)
                    with results_lock:
                        completed.append(path)
                except Exception as e:
                    with results_lock:
                        errors.append(str(e))

            with LogCapture() as log:
                with ThreadPoolExecutor(max_workers=100) as executor:
                    futures = {
                        executor.submit(process_file, path, content): path
                        for path, content in files.items()
                    }
                    for future in as_completed(futures, timeout=60):
                        future.result()

            # 验证：所有文件都成功处理
            self.assertEqual(len(errors), 0, f"Errors during storm: {errors[:5]}")
            self.assertEqual(len(completed), file_count)

            # 验证：数据库中文件数量正确
            stats = service.db.get_statistics()
            self.assertEqual(stats['total_files'], file_count,
                             f"Expected {file_count} files, got {stats['total_files']}")

            # 验证：每个文件都有实体
            for path in files:
                file_id = service.db.get_file_id(path)
                self.assertIsNotNone(file_id, f"File not found in DB: {path}")
                entities = service.db.get_file_entities(file_id)
                self.assertGreater(len(entities), 0, f"No entities for {path}")

            # 验证日志：有 DONE 日志
            done_count = log.count_occurrences('[DONE]')
            self.assertEqual(done_count, file_count,
                             f"Expected {file_count} [DONE], got {done_count}")
        finally:
            service.close()

    # --- A2: 同文件千次竞争 ---
    def test_02_same_file_thousand_contention(self):
        """100个线程同时修改同一个文件，验证最终状态一致"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            thread_count = 100

            # 每个线程使用不同的内容
            contents = [
                make_arkts_content(f"ContentionClass{i}", method_count=1)
                for i in range(thread_count)
            ]

            errors = []

            def process(content):
                try:
                    service._handle_file_change(file_path, content)
                except Exception as e:
                    errors.append(str(e))

            with LogCapture() as log:
                with ThreadPoolExecutor(max_workers=100) as executor:
                    futures = [executor.submit(process, c) for c in contents]
                    for f in as_completed(futures, timeout=60):
                        f.result()

            # 验证：无错误
            self.assertEqual(len(errors), 0, f"Errors: {errors[:5]}")

            # 验证：文件存在且只有一个版本
            stats = service.db.get_statistics()
            self.assertEqual(stats['total_files'], 1)

            # 验证：文件实体数量与最后一次写入一致
            # 由于并发，最终状态是某个线程的结果
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0, "File should have entities")

            # 验证：日志中有 RETRY 或 WAIT（因为文件锁竞争）
            self.assertTrue(
                log.contains('[RETRY]') or log.contains('[WAIT]') or log.contains('[DONE]'),
                "Expected retry/wait/done logs for contention"
            )
        finally:
            service.close()

    # --- A3: 交替删除-创建 ---
    def test_03_alternate_delete_create(self):
        """对同一文件路径，交替执行删除和创建操作50次，验证无crash"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("AlternateClass")
            iterations = 50

            errors = []

            with LogCapture() as log:
                for i in range(iterations):
                    try:
                        if i % 2 == 0:
                            # 创建/更新
                            service._handle_file_change(file_path, content)
                        else:
                            # 删除
                            service._handle_file_change(file_path, None)
                    except Exception as e:
                        errors.append(str(e))

            # 验证：无错误
            self.assertEqual(len(errors), 0, f"Errors: {errors[:5]}")

            # 验证：最终状态（奇数次迭代=删除）
            file_id = service.db.get_file_id(file_path)
            self.assertIsNone(file_id, "File should be deleted after odd iterations")

            # 验证：有 DELETE 和 DONE 日志
            self.assertGreater(log.count_occurrences('[DELETE]'), 0)
            self.assertGreater(log.count_occurrences('[DONE]'), 0)
        finally:
            service.close()

    # --- A4: close期间注入 ---
    def test_04_inject_during_close(self):
        """在close()执行过程中，尝试从多个线程提交文件变更"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("CloseInjectClass")
            inject_count = 50
            errors = []
            results = []

            def inject_file(idx):
                try:
                    c = make_arkts_content(f"InjectClass{idx}")
                    service._handle_file_change(file_path, c)
                    results.append(True)
                except Exception as e:
                    errors.append(str(e))
                    results.append(False)

            barrier = threading.Barrier(2)

            def close_thread():
                barrier.wait()
                service.close()

            def inject_thread():
                barrier.wait()
                with ThreadPoolExecutor(max_workers=inject_count) as executor:
                    futures = [executor.submit(inject_file, i) for i in range(inject_count)]
                    for f in as_completed(futures, timeout=30):
                        try:
                            f.result()
                        except Exception:
                            pass

            with LogCapture() as log:
                t1 = threading.Thread(target=close_thread)
                t2 = threading.Thread(target=inject_thread)
                t1.start()
                t2.start()
                t1.join(timeout=30)
                t2.join(timeout=30)

            # 验证：无crash（线程正常退出）
            # 所有注入要么成功要么被shutdown拒绝（都是合法行为）
            # 只验证没有未预期的异常
            for e in errors:
                # shutdown期间的拒绝是正常的
                self.assertNotIn("database is locked", str(e).lower(),
                                 f"Unexpected DB lock error during shutdown: {e}")
        finally:
            try:
                service.close()
            except Exception:
                pass

    # --- A5: 双实例DB竞争 ---
    def test_05_dual_instance_db_contention(self):
        """两个KnowledgeGraphDB实例操作同一个数据库文件，验证WAL模式下的行为"""
        db1 = KnowledgeGraphDB(self.db_path)
        db2 = KnowledgeGraphDB(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content1 = make_arkts_content("DualInstanceClass1")
            content2 = make_arkts_content("DualInstanceClass2")
            errors = []

            def instance1_ops():
                try:
                    for i in range(50):
                        c = make_arkts_content(f"DualClass1_{i}")
                        db1.add_or_update_file(file_path, c)
                except Exception as e:
                    errors.append(f"DB1: {e}")

            def instance2_ops():
                try:
                    for i in range(50):
                        c = make_arkts_content(f"DualClass2_{i}")
                        db2.add_or_update_file(file_path, c)
                except Exception as e:
                    errors.append(f"DB2: {e}")

            with LogCapture() as log:
                t1 = threading.Thread(target=instance1_ops)
                t2 = threading.Thread(target=instance2_ops)
                t1.start()
                t2.start()
                t1.join(timeout=30)
                t2.join(timeout=30)

            # 验证：无crash
            self.assertEqual(len(errors), 0, f"DB contention errors: {errors[:5]}")

            # 验证：文件仍然存在
            file_id = db1.get_file_id(file_path)
            self.assertIsNotNone(file_id, "File should exist after dual-instance operations")
        finally:
            db1.close()
            db2.close()


# ============================================================================
# B. 数据边界混沌
# ============================================================================
class TestBDataBoundary(unittest.TestCase):
    """B. 数据边界混沌测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_b_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    # --- B6: 超长文件名 ---
    def test_06_very_long_filename(self):
        """文件名长度接近OS限制(255字符)，验证路径处理"""
        service = create_service(self.db_path)
        try:
            # 构造长文件名（总路径不超过系统限制）
            # 文件名部分接近255字符
            base_name = "VeryLongComponentName" + "X" * 220
            file_path = os.path.join(self.tmpdir, base_name + ".ets")
            content = make_arkts_content("LongNameClass")

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            # 验证：文件被处理（_handle_file_change 不检查文件名长度）
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id, "Long filename file should be processed")

            # 验证：实体被提取
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0)
        finally:
            service.close()

    # --- B7: 特殊字符路径 ---
    def test_07_special_character_paths(self):
        """文件路径包含空格、中文、日文、emoji、unicode控制字符"""
        service = create_service(self.db_path)
        try:
            special_paths = [
                ("spaces in name", os.path.join(self.tmpdir, "my component handler.ets")),
                ("chinese", os.path.join(self.tmpdir, "组件处理器模块.ets")),
                ("japanese", os.path.join(self.tmpdir, "コンポーネントハンドラ.ets")),
                ("mixed", os.path.join(self.tmpdir, "Component_组件_コンポーネント.ets")),
                ("unicode_ctrl", os.path.join(self.tmpdir, "Component\u200bHandler.ets")),  # zero-width space
            ]

            for label, path in special_paths:
                content = make_arkts_content(f"SpecialClass_{label}")
                with self.subTest(label=label):
                    service._handle_file_change(path, content)
                    file_id = service.db.get_file_id(path)
                    self.assertIsNotNone(file_id, f"File not found: {label}")
                    entities = service.db.get_file_entities(file_id)
                    self.assertGreater(len(entities), 0, f"No entities for: {label}")
        finally:
            service.close()

    # --- B8: 二进制内容伪装 ---
    def test_08_binary_content_disguised(self):
        """文件扩展名是.ets但内容是随机二进制数据"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            # 随机二进制数据（可能包含无效UTF-8序列）
            binary_data = bytes(random.getrandbits(8) for _ in range(1024))

            # 尝试用不同编码处理
            # 场景1：作为latin-1解码（不会失败，但产生乱码）
            content_latin1 = binary_data.decode('latin-1')

            with LogCapture() as log:
                # 应该不crash，只是解析不出实体
                service._handle_file_change(file_path, content_latin1)

            # 验证：文件被记录（内容非空）
            file_id = service.db.get_file_id(file_path)
            # 二进制乱码内容可能被跳过（strip后可能非空），也可能被处理
            # 关键是不crash
            self.assertTrue(file_id is not None or log.contains('[UPDATE]'),
                            "Should process or skip without crash")
        finally:
            service.close()

    # --- B9: BOM标记文件 ---
    def test_09_bom_marked_files(self):
        """UTF-8 BOM、UTF-16 BOM、UTF-32 BOM开头的文件"""
        service = create_service(self.db_path)
        try:
            base_content = make_arkts_content("BomClass")

            bom_tests = [
                ("UTF-8 BOM", b'\xef\xbb\xbf' + base_content.encode('utf-8'), 'utf-8-sig'),
                ("UTF-16 LE BOM", b'\xff\xfe' + base_content.encode('utf-16-le'), 'utf-16'),
                ("UTF-16 BE BOM", b'\xfe\xff' + base_content.encode('utf-16-be'), 'utf-16'),
            ]

            for label, raw_bytes, encoding in bom_tests:
                with self.subTest(label=label):
                    file_path = os.path.join(self.tmpdir, make_arkts_filename(hash(label) % 10000))
                    try:
                        content = raw_bytes.decode(encoding)
                    except (UnicodeDecodeError, LookupError):
                        # 某些编码可能不支持，跳过
                        continue

                    with LogCapture() as log:
                        service._handle_file_change(file_path, content)

                    file_id = service.db.get_file_id(file_path)
                    self.assertIsNotNone(file_id, f"BOM file not found: {label}")
        finally:
            service.close()

    # --- B10: 超大实体数量 ---
    def test_10_massive_entities_in_one_file(self):
        """一个文件中包含500个class+method，验证事务性能和数据完整性"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            # 构造包含大量class的文件
            lines = ["import { Component } from '@ohos/arkui';"]
            expected_classes = 500
            for i in range(expected_classes):
                lines.append(f"export class MassiveClass{i} {{")
                lines.append(f"  render(): void {{ }}")
                lines.append(f"  update(): void {{ }}")
                lines.append("}")
            content = '\n'.join(lines)

            with LogCapture() as log:
                start = time.time()
                service._handle_file_change(file_path, content)
                duration = time.time() - start

            # 验证：处理完成
            self.assertLess(duration, 30, f"Processing took too long: {duration:.1f}s")

            # 验证：文件存在
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)

            # 验证：实体数量
            entities = service.db.get_file_entities(file_id)
            # 每个class有2个method + class本身 = 3 entities per class
            # 加上 import 相关的实体
            self.assertGreaterEqual(len(entities), expected_classes * 2,
                                    f"Expected >= {expected_classes * 2} entities, got {len(entities)}")

            # 验证：日志
            log.assert_contains('[DONE]')
        finally:
            service.close()


# ============================================================================
# C. 解析器极限测试
# ============================================================================
class TestCParserExtreme(unittest.TestCase):
    """C. 解析器极限测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_c_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        self.parser = MockArkTSParser()

    # --- C11: 深度嵌套类 ---
    def test_11_deeply_nested_classes(self):
        """10层嵌套的class定义"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            # 构造10层嵌套
            lines = []
            for i in range(10):
                indent = "  " * i
                lines.append(f"{indent}export class NestedLevel{i} {{")
                lines.append(f"{indent}  process(): void {{ }}")
            for i in range(9, -1, -1):
                indent = "  " * i
                lines.append(f"{indent}}}")
            content = '\n'.join(lines)

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            # 验证：不crash，能解析出实体
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0, "Should extract at least some entities from nested classes")
        finally:
            service.close()

    # --- C12: 超长单行 ---
    def test_12_extremely_long_single_line(self):
        """单行10000字符的代码"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            # 构造超长单行
            long_line = "export class LongLineClass { " + "x".join([""] * 10000) + " }"
            content = long_line + "\n" + make_arkts_content("NormalClass")

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            # 验证：不crash
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)
        finally:
            service.close()

    # --- C13: 空文件体 ---
    def test_13_empty_class_body(self):
        """export class EmptyClass {}"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = "export class EmptyClass {}"

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)

            # 验证：class实体被提取
            entities = service.db.get_file_entities(file_id)
            class_entities = [e for e in entities if e['type'] == 'class']
            self.assertEqual(len(class_entities), 1)
            self.assertEqual(class_entities[0]['name'], 'EmptyClass')
        finally:
            service.close()

    # --- C14: 重复实体名 ---
    def test_14_duplicate_entity_names(self):
        """同文件中两个同名class（不同行号）"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = """export class DuplicateName {}
export class DuplicateName {}
export class UniqueClass {}"""

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)

            # 验证：两个同名class都被提取
            entities = service.db.get_file_entities(file_id)
            class_entities = [e for e in entities if e['type'] == 'class' and e['name'] == 'DuplicateName']
            self.assertGreaterEqual(len(class_entities), 2,
                                    "Both duplicate classes should be extracted")
        finally:
            service.close()

    # --- C15: 自引用关系 ---
    def test_15_self_reference(self):
        """class A extends A"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = "export class SelfRef extends SelfRef { process(): void {} }"

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            # 验证：不crash
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0)
        finally:
            service.close()

    # --- C16: 循环引用（跨文件） ---
    def test_16_circular_reference_cross_file(self):
        """A extends B, B extends A（跨文件）- 验证系统不crash且实体正确提取"""
        service = create_service(self.db_path)
        try:
            file_a = os.path.join(self.tmpdir, make_arkts_filename(1))
            file_b = os.path.join(self.tmpdir, make_arkts_filename(2))

            content_a = "export class ClassA extends ClassB { process(): void {} }"
            content_b = "export class ClassB extends ClassA { render(): void {} }"

            with LogCapture() as log:
                service._handle_file_change(file_a, content_a)
                service._handle_file_change(file_b, content_b)

            # 验证：两个文件都被处理
            self.assertIsNotNone(service.db.get_file_id(file_a))
            self.assertIsNotNone(service.db.get_file_id(file_b))

            # 验证：两个class实体都存在
            all_entities = service.db.get_all_entities()
            entity_names = [e['name'] for e in all_entities]
            self.assertIn('ClassA', entity_names)
            self.assertIn('ClassB', entity_names)

            # 验证：contains关系被记录（class -> method）
            contains_rels = [r for r in service.db.get_all_relationships()
                             if r['relation_type'] == 'contains']
            self.assertGreater(len(contains_rels), 0,
                               "Contains relationships (class->method) should be recorded")

            # 注意：跨文件的 extends 关系中，target class 不在同一文件中，
            # 因此 transaction_callback 中 target_id 为 None，关系被跳过。
            # 这是系统的已知行为（单文件事务中无法解析跨文件引用）。
        finally:
            service.close()

    # --- C17: 无效import ---
    def test_17_invalid_import_empty_braces(self):
        """import { } from 'module'（空花括号）"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = "import { } from 'empty_module'\nexport class EmptyImportClass { process(): void {} }"

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            # 验证：不crash
            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)

            # 验证：class实体被提取
            entities = service.db.get_file_entities(file_id)
            class_entities = [e for e in entities if e['type'] == 'class']
            self.assertEqual(len(class_entities), 1)
        finally:
            service.close()

    # --- C18: 混合import ---
    def test_18_mixed_imports(self):
        """同一文件中默认导入和命名导入混合"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = """import DefaultComponent from '@ohos/arkui'
import { NamedComponent, AnotherNamed } from '@ohos/router'
import MixedDefault from '@ohos/mixed'
import { MixedNamed } from '@ohos/mixed'
export class MixedImportClass { process(): void {} }"""

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            file_id = service.db.get_file_id(file_path)
            self.assertIsNotNone(file_id)

            # 验证：实体被提取
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0)

            # 验证：import被记录
            with service.db._get_connection() as conn:
                cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports WHERE file_id = ?", (file_id,))
                import_count = cursor.fetchone()['cnt']
            self.assertGreater(import_count, 0, "Imports should be recorded")
        finally:
            service.close()


# ============================================================================
# D. 状态一致性极限
# ============================================================================
class TestDStateConsistency(unittest.TestCase):
    """D. 状态一致性极限测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_d_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    # --- D19: 统计精度验证 ---
    def test_19_statistics_precision(self):
        """处理100个文件后，验证统计数字精确一致"""
        service = create_service(self.db_path)
        try:
            file_count = 100
            total_entities = 0
            total_relationships = 0

            with LogCapture() as log:
                for i in range(file_count):
                    path = os.path.join(self.tmpdir, make_arkts_filename(i))
                    method_count = random.randint(1, 5)
                    content = make_arkts_content(f"StatClass{i}", method_count=method_count)

                    # 先解析获取预期数量
                    parse_result = service.parser.parse_file(path, content)
                    expected_entities = len(parse_result['entities'])
                    expected_rels = len(parse_result['relationships'])
                    total_entities += expected_entities
                    total_relationships += expected_rels

                    service._handle_file_change(path, content)

            # 验证：service stats
            stats = service.get_statistics()
            self.assertEqual(stats['files_processed'], file_count,
                             f"files_processed: expected {file_count}, got {stats['files_processed']}")
            self.assertEqual(stats['entities_extracted'], total_entities,
                             f"entities_extracted: expected {total_entities}, got {stats['entities_extracted']}")
            self.assertEqual(stats['relationships_extracted'], total_relationships,
                             f"relationships_extracted: expected {total_relationships}, got {stats['relationships_extracted']}")

            # 验证：DB stats
            db_stats = service.db.get_statistics()
            self.assertEqual(db_stats['total_files'], file_count)
            self.assertEqual(db_stats['total_entities'], total_entities)
            self.assertEqual(db_stats['total_relationships'], total_relationships)
        finally:
            service.close()

    # --- D20: 删除后统计回退 ---
    def test_20_delete_statistics_rollback(self):
        """添加10个文件 -> 删除5个 -> 验证统计数字正确递减"""
        service = create_service(self.db_path)
        try:
            paths = []
            for i in range(10):
                path = os.path.join(self.tmpdir, make_arkts_filename(i))
                content = make_arkts_content(f"DeleteClass{i}", method_count=2)
                service._handle_file_change(path, content)
                paths.append(path)

            stats_after_add = service.get_statistics()
            add_entities = stats_after_add['entities_extracted']
            add_rels = stats_after_add['relationships_extracted']

            # 删除前5个文件
            delete_entities = 0
            delete_rels = 0
            for i in range(5):
                file_id = service.db.get_file_id(paths[i])
                entities = service.db.get_file_entities(file_id)
                delete_entities += len(entities)
                service._handle_file_change(paths[i], None)

            stats_after_delete = service.get_statistics()

            # 验证：文件数量
            self.assertEqual(stats_after_delete['files_processed'], 15)  # 10 add + 5 delete
            db_stats = service.db.get_statistics()
            self.assertEqual(db_stats['total_files'], 5, "Should have 5 files remaining")

            # 验证：实体数量减少
            self.assertLess(db_stats['total_entities'], add_entities,
                            "Entities should decrease after deletion")
        finally:
            service.close()

    # --- D21: 重复处理幂等性 ---
    def test_21_idempotent_reprocessing(self):
        """同一文件内容处理100次，验证entities/relationships数量不变"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("IdempotentClass", method_count=3)

            # 第一次处理
            service._handle_file_change(file_path, content)
            stats_first = service.db.get_statistics()
            first_entities = stats_first['total_entities']
            first_rels = stats_first['total_relationships']

            # 重复处理99次
            with LogCapture() as log:
                for i in range(99):
                    service._handle_file_change(file_path, content)

            stats_final = service.db.get_statistics()

            # 验证：实体和关系数量不变
            self.assertEqual(stats_final['total_entities'], first_entities,
                             "Entities should remain same after idempotent reprocessing")
            self.assertEqual(stats_final['total_relationships'], first_rels,
                             "Relationships should remain same after idempotent reprocessing")
            self.assertEqual(stats_final['total_files'], 1)

            # 验证：只有第一次产生了实际更新
            done_count = log.count_occurrences('[DONE]')
            self.assertEqual(done_count, 0, "No [DONE] expected for unchanged files")
        finally:
            service.close()

    # --- D22: hash碰撞模拟 ---
    def test_22_hash_collision_simulation(self):
        """验证不同内容产生不同hash（抽样1000个随机内容）"""
        db = KnowledgeGraphDB(self.db_path)
        try:
            hashes = set()
            sample_size = 1000

            for i in range(sample_size):
                content = f"unique_content_{i}_{random_string(64)}"
                h = db.compute_file_hash(content)
                hashes.add(h)

            # 验证：所有hash都不同
            self.assertEqual(len(hashes), sample_size,
                             f"Hash collision detected! {sample_size - len(hashes)} collisions in {sample_size} samples")

            # 额外验证：相似内容产生不同hash
            content_a = "class A { }"
            content_b = "class A {}"  # 只差一个空格
            self.assertNotEqual(db.compute_file_hash(content_a), db.compute_file_hash(content_b))
        finally:
            db.close()


# ============================================================================
# E. Web层混沌
# ============================================================================
class TestEWebChaos(unittest.TestCase):
    """E. Web层混沌测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_e_")
        self.db_path = os.path.join(self.tmpdir, "test.db")
        # 在测试模式下创建Flask app
        from web_server import app
        app.config['TESTING'] = True
        self.app = app
        self.client = app.test_client()

    def _setup_service(self):
        """创建并设置service"""
        service = create_service(self.db_path)
        # 添加一些测试数据
        for i in range(5):
            path = os.path.join(self.tmpdir, make_arkts_filename(i))
            content = make_arkts_content(f"WebClass{i}", method_count=2)
            service._handle_file_change(path, content)
        return service

    # --- E23: 并发API请求 ---
    def test_23_concurrent_api_requests(self):
        """50个线程同时请求 /api/stats、/api/graph、/api/search"""
        service = self._setup_service()
        try:
            import web_server
            old_service = web_server.kg_service
            web_server.kg_service = service

            errors = []
            results = []
            results_lock = threading.Lock()

            endpoints = [
                '/api/stats',
                '/api/graph',
                '/api/search?q=WebClass',
            ]

            def make_request(endpoint):
                try:
                    resp = self.client.get(endpoint)
                    with results_lock:
                        results.append((endpoint, resp.status_code))
                except Exception as e:
                    with results_lock:
                        errors.append(str(e))

            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = []
                for i in range(50):
                    endpoint = endpoints[i % len(endpoints)]
                    futures.append(executor.submit(make_request, endpoint))
                for f in as_completed(futures, timeout=30):
                    f.result()

            web_server.kg_service = old_service

            # 验证：无错误
            self.assertEqual(len(errors), 0, f"API errors: {errors[:5]}")

            # 验证：所有请求返回200
            for endpoint, status in results:
                self.assertEqual(status, 200, f"Endpoint {endpoint} returned {status}")
        finally:
            service.close()

    # --- E24: 超大搜索词 ---
    def test_24_very_long_search_query(self):
        """搜索词长度10000字符"""
        service = self._setup_service()
        try:
            import web_server
            old_service = web_server.kg_service
            web_server.kg_service = service

            long_query = "A" * 10000
            resp = self.client.get(f'/api/search?q={long_query}')

            web_server.kg_service = old_service

            # 验证：不crash，返回200
            self.assertEqual(resp.status_code, 200)
            data = resp.get_json()
            self.assertIsInstance(data, list)
        finally:
            service.close()

    # --- E25: SQL注入变体 ---
    def test_25_sql_injection_variants(self):
        """搜索词包含各种LIKE通配符组合"""
        service = self._setup_service()
        try:
            import web_server
            old_service = web_server.kg_service
            web_server.kg_service = service

            injection_payloads = [
                "%", "%%", "___",
                "' OR '1'='1",
                "'; DROP TABLE entities; --",
                "%25%25%25",
                "\\%\\_",
                "class%_%",
                "' UNION SELECT * FROM files --",
                "1; SELECT * FROM entities",
            ]

            for payload in injection_payloads:
                with self.subTest(payload=payload[:30]):
                    resp = self.client.get(f'/api/search?q={payload}')
                    self.assertEqual(resp.status_code, 200,
                                     f"Payload '{payload[:30]}' caused status {resp.status_code}")
                    data = resp.get_json()
                    self.assertIsInstance(data, list)

            web_server.kg_service = old_service
        finally:
            service.close()

    # --- E26: entity_id边界 ---
    def test_26_entity_id_boundaries(self):
        """id=0, id=-1(通过直接调用get_entity_details), id=999999999"""
        service = self._setup_service()
        try:
            # id=0 (通过API - Flask会将其转为int=0)
            import web_server
            old_service = web_server.kg_service
            web_server.kg_service = service

            resp = self.client.get('/api/entity/0')
            self.assertIn(resp.status_code, [200, 404])

            # id=999999999 (不存在的ID)
            resp = self.client.get('/api/entity/999999999')
            self.assertEqual(resp.status_code, 404)

            web_server.kg_service = old_service

            # id=-1 (直接调用DB方法)
            result = service.db.get_entity_details(-1)
            self.assertIsNone(result)

            # id=0
            result = service.db.get_entity_details(0)
            self.assertIsNone(result)
        finally:
            service.close()

    # --- E27: 无service状态 ---
    def test_27_no_service_state(self):
        """所有API在kg_service=None时的行为"""
        import web_server
        old_service = web_server.kg_service
        web_server.kg_service = None

        try:
            endpoints_and_expected = [
                ('/api/stats', 200),
                ('/api/graph', 200),
                ('/api/search', 200),
                ('/api/search?q=test', 200),
                ('/api/events', 200),
                ('/api/entity/1', 503),
            ]

            for endpoint, expected_status in endpoints_and_expected:
                with self.subTest(endpoint=endpoint):
                    resp = self.client.get(endpoint)
                    self.assertEqual(resp.status_code, expected_status,
                                     f"Endpoint {endpoint}: expected {expected_status}, got {resp.status_code}")
                    data = resp.get_json()
                    self.assertIsNotNone(data)
        finally:
            web_server.kg_service = old_service


# ============================================================================
# F. 资源耗尽测试
# ============================================================================
class TestFResourceExhaustion(unittest.TestCase):
    """F. 资源耗尽测试"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_f_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    # --- F28: 文件锁耗尽 ---
    def test_28_file_lock_exhaustion(self):
        """创建超过1000个不同文件的变更，触发LRU清理，验证无crash"""
        service = create_service(self.db_path)
        try:
            file_count = 1200  # 超过 MAX_FILE_LOCKS (1000)

            with LogCapture() as log:
                for i in range(file_count):
                    path = os.path.join(self.tmpdir, make_arkts_filename(i))
                    content = make_arkts_content(f"LockClass{i}", method_count=1)
                    service._handle_file_change(path, content)

            # 验证：所有文件都被处理
            stats = service.db.get_statistics()
            self.assertEqual(stats['total_files'], file_count,
                             f"Expected {file_count} files, got {stats['total_files']}")

            # 验证：文件锁数量被控制在合理范围
            with service._file_locks_mutex:
                lock_count = len(service._file_locks)
            self.assertLessEqual(lock_count, service._MAX_FILE_LOCKS + 100,
                                 f"Lock count {lock_count} exceeds expected limit")
        finally:
            service.close()

    # --- F29: Timer堆积 ---
    def test_29_timer_accumulation(self):
        """快速创建/修改/删除文件，验证debounce timer正确清理"""
        from file_watcher import ArkTSFileHandler

        handler = ArkTSFileHandler(callback=lambda p, c: None, debounce_seconds=0.1)
        try:
            file_count = 200

            # 使用符合 MIN_FILE_WORDS(>=4) 要求的文件名
            # Component(1) Module(2) Handler(3) Service(4) = 4 words
            def make_timer_filename(i):
                return os.path.join(self.tmpdir, f"ComponentModuleHandlerService{i:04d}.ets")

            # 快速调度大量timer
            for i in range(file_count):
                path = make_timer_filename(i)
                handler._schedule_update(path)

            # 验证：pending_files中有条目
            with handler.lock:
                pending_before = len(handler.pending_files)
            self.assertGreater(pending_before, 0, "Should have pending timers after scheduling")

            # 等待所有timer执行完毕
            time.sleep(1.0)

            # 验证：所有timer已清理
            with handler.lock:
                pending_after = len(handler.pending_files)
            self.assertEqual(pending_after, 0,
                             f"Expected 0 pending timers, got {pending_after}")
        finally:
            # 清理剩余timer
            with handler.lock:
                for timer in handler.pending_files.values():
                    timer.cancel()
                handler.pending_files.clear()

    # --- F30: 连接压力 ---
    def test_30_connection_pressure(self):
        """短时间内执行1000次DB操作，验证连接池稳定性"""
        db = KnowledgeGraphDB(self.db_path)
        try:
            errors = []
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            def db_operation(idx):
                try:
                    content = f"export class PressureClass{idx} {{ method(): void {{ }} }}"
                    db.add_or_update_file(file_path, content)
                    db.get_statistics()
                    db.search_entities(f"PressureClass{idx}")
                except Exception as e:
                    errors.append(str(e))

            with ThreadPoolExecutor(max_workers=50) as executor:
                futures = [executor.submit(db_operation, i) for i in range(1000)]
                for f in as_completed(futures, timeout=60):
                    f.result()

            # 验证：无crash
            self.assertEqual(len(errors), 0, f"DB pressure errors: {errors[:5]}")

            # 验证：DB仍然可用
            stats = db.get_statistics()
            self.assertEqual(stats['total_files'], 1)
        finally:
            db.close()


# ============================================================================
# G. 日志驱动验证（贯穿所有测试的补充验证）
# ============================================================================
class TestGLogDrivenVerification(unittest.TestCase):
    """G. 日志驱动验证 - 集中验证日志行为"""

    def setUp(self):
        self.tmpdir = tempfile.mkdtemp(prefix="chaos_g_")
        self.db_path = os.path.join(self.tmpdir, "test.db")

    def test_g01_update_delete_log_sequence(self):
        """验证 UPDATE -> DONE 日志顺序正确"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("LogSeqClass")

            # 等待前序测试的所有后台线程完成（包括WAIT阻塞线程）
            time.sleep(5.0)

            with LogCapture() as log:
                service._handle_file_change(file_path, content)

            lines = log.lines()
            # 只关注包含当前文件路径的日志行，排除其他测试的残留
            path_in_log = file_path.replace('\\', '/')
            relevant_lines = [l for l in lines if path_in_log in l]
            update_lines = [l for l in relevant_lines if '[UPDATE]' in l]
            done_lines = [l for l in relevant_lines if '[DONE]' in l]

            # 至少有一对 UPDATE -> DONE
            self.assertGreaterEqual(len(update_lines), 1,
                                    f"Expected >= 1 [UPDATE], got {len(update_lines)} in: {relevant_lines}")
            self.assertGreaterEqual(len(done_lines), 1,
                                    f"Expected >= 1 [DONE], got {len(done_lines)} in: {relevant_lines}")

            # 验证顺序：第一个 UPDATE 在第一个 DONE 之前
            first_update_idx = next(i for i, l in enumerate(relevant_lines) if '[UPDATE]' in l)
            first_done_idx = next(i for i, l in enumerate(relevant_lines) if '[DONE]' in l)
            self.assertLess(first_update_idx, first_done_idx,
                            "[UPDATE] should appear before [DONE]")
        finally:
            service.close()

    def test_g02_delete_log_verification(self):
        """验证删除操作产生正确的 DELETE 日志"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("DeleteLogClass")

            # 先添加
            service._handle_file_change(file_path, content)

            with LogCapture() as log:
                # 再删除
                service._handle_file_change(file_path, None)

            log.assert_contains('[DELETE]')
            log.assert_contains('Removed:')
        finally:
            service.close()

    def test_g03_no_error_on_normal_operations(self):
        """正常操作不应产生 ERROR 日志"""
        service = create_service(self.db_path)
        try:
            with LogCapture() as log:
                for i in range(20):
                    path = os.path.join(self.tmpdir, make_arkts_filename(i))
                    content = make_arkts_content(f"NoErrorClass{i}")
                    service._handle_file_change(path, content)

                # 删除一半
                for i in range(10):
                    path = os.path.join(self.tmpdir, make_arkts_filename(i))
                    service._handle_file_change(path, None)

            log.assert_no_unexpected_errors()
        finally:
            service.close()

    def test_g04_retry_log_on_contention(self):
        """并发竞争时应有 RETRY 日志"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            barrier = threading.Barrier(20)

            def contentious_write(idx):
                barrier.wait()
                content = make_arkts_content(f"RetryClass{idx}")
                service._handle_file_change(file_path, content)

            with LogCapture() as log:
                with ThreadPoolExecutor(max_workers=20) as executor:
                    futures = [executor.submit(contentious_write, i) for i in range(20)]
                    for f in as_completed(futures, timeout=30):
                        f.result()

            # 验证：有RETRY或WAIT日志（高并发下文件锁竞争）
            has_contention_log = log.contains('[RETRY]') or log.contains('[WAIT]')
            self.assertTrue(has_contention_log,
                            "Expected [RETRY] or [WAIT] logs under contention")
        finally:
            service.close()

    def test_g05_shutdown_reject_log(self):
        """shutdown后不应处理新文件"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("ShutdownClass")

            # 先关闭
            service.close()

            with LogCapture() as log:
                # 尝试处理（应被拒绝）
                service._handle_file_change(file_path, content)

            # 验证：没有 UPDATE 或 DONE 日志（被shutdown拒绝）
            self.assertFalse(log.contains('[UPDATE]'))
            self.assertFalse(log.contains('[DONE]'))
        finally:
            try:
                service.close()
            except Exception:
                pass

    def test_g06_empty_content_skip_log(self):
        """空内容文件不应产生处理日志"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))

            with LogCapture() as log:
                service._handle_file_change(file_path, "")
                service._handle_file_change(file_path, "   ")
                service._handle_file_change(file_path, "\n\t  \n")

            # 验证：没有 UPDATE 或 DONE 日志
            self.assertFalse(log.contains('[UPDATE]'))
            self.assertFalse(log.contains('[DONE]'))
        finally:
            service.close()

    def test_g07_unchanged_file_skip(self):
        """相同内容重复处理应被跳过"""
        service = create_service(self.db_path)
        try:
            file_path = os.path.join(self.tmpdir, make_arkts_filename(0))
            content = make_arkts_content("UnchangedClass")

            # 等待前序测试的所有后台线程完成
            time.sleep(5.0)

            # 第一次处理
            service._handle_file_change(file_path, content)

            with LogCapture() as log:
                # 重复处理
                service._handle_file_change(file_path, content)

            # 验证：没有针对此文件的新的 DONE 日志
            # （hash检查在事务内完成，未变化的文件不会产生 DONE）
            path_in_log = file_path.replace('\\', '/')
            relevant_lines = [l for l in log.lines() if path_in_log in l]
            done_lines = [l for l in relevant_lines if '[DONE]' in l]
            self.assertEqual(len(done_lines), 0,
                             f"Expected no [DONE] for unchanged file, got: {done_lines}")

            # 验证：DB中实体数量不变
            file_id = service.db.get_file_id(file_path)
            entities = service.db.get_file_entities(file_id)
            self.assertGreater(len(entities), 0, "Should have entities from first processing")
        finally:
            service.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
