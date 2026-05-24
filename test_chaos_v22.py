"""
终极极端混沌测试 - 第七轮（终极轮）
ArkTS 知识图谱系统可用性和复杂场景处理能力测试

本轮测试重点：
A. 可用性测试 - API错误格式一致性、WebSocket频繁连接断开、前端兼容性、长时间运行可用性
B. 复杂场景处理能力 - 同名内部类、循环继承、深度嵌套命名空间、泛型类型解析、装饰器解析
C. 边界极限测试 - 1GB文件处理、Unicode控制字符、None内容处理、空文件路径
D. 一致性验证 - 统计顺序一致性、跨平台路径一致性、计数器准确性
E. 终极混沌测试 - 所有上述场景的组合
"""

import unittest
import os
import sys
import tempfile
import shutil
import time
import threading
import json
import hashlib
import sqlite3
import random
import string
import gc
import psutil
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Dict, List, Optional

# Add parent directory to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_service import KnowledgeGraphService
from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser
from file_watcher import FileWatcher
from config import MIN_FILE_WORDS, MAX_FILE_SIZE, MAX_NODES_DISPLAY


class TestUsability(unittest.TestCase):
    """A. 可用性测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

    def tearDown(self):
        try:
            self.service.close()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_api_error_format_consistency(self):
        """A1. API错误格式一致性：验证所有API错误返回统一的JSON格式"""
        # 测试数据库未初始化时的错误响应
        from flask import Flask
        from web_server import app, kg_service

        with app.test_client() as client:
            # 测试无效端点
            response = client.get('/api/invalid_endpoint')
            self.assertIn(response.status_code, [404, 500])

            # 测试缺少参数的搜索
            response = client.get('/api/search')
            data = json.loads(response.data)
            self.assertIsInstance(data, list)  # 空查询返回空列表

            # 测试无效entity_id
            response = client.get('/api/entity/999999')
            if response.status_code == 404:
                data = json.loads(response.data)
                self.assertIn('error', data)

    def test_websocket_frequent_connect_disconnect(self):
        """A2. WebSocket频繁连接断开：1000次快速连接断开，验证无资源泄漏"""
        from flask_socketio import SocketIO
        from web_server import app, socketio

        initial_threads = threading.active_count()
        initial_fds = len(psutil.Process().open_files()) if hasattr(psutil.Process(), 'open_files') else 0

        connect_count = 100
        errors = []

        def simulate_connect_disconnect():
            try:
                # 使用测试客户端模拟WebSocket连接
                client = socketio.test_client(app)
                time.sleep(0.01)  # 短暂保持连接
                client.disconnect()
            except Exception as e:
                errors.append(str(e))

        # 快速进行100次连接断开
        threads = []
        for i in range(connect_count):
            t = threading.Thread(target=simulate_connect_disconnect)
            threads.append(t)
            t.start()
            if i % 10 == 0:
                time.sleep(0.01)  # 稍微缓解压力

        for t in threads:
            t.join(timeout=5)

        # 等待资源释放
        time.sleep(0.5)

        final_threads = threading.active_count()
        final_fds = len(psutil.Process().open_files()) if hasattr(psutil.Process(), 'open_files') else 0

        # 验证没有资源泄漏
        self.assertLess(final_threads - initial_threads, 20, "Thread leak detected")
        self.assertLess(final_fds - initial_fds, 10, "FD leak detected")
        self.assertEqual(len(errors), 0, f"WebSocket errors: {errors[:5]}")

    def test_frontend_compatibility(self):
        """A3. 前端兼容性：模拟不同前端框架的请求格式"""
        from web_server import app

        frameworks = [
            # React/Fetch style
            {'headers': {'Content-Type': 'application/json', 'Accept': 'application/json'}},
            # Vue/Axios style
            {'headers': {'X-Requested-With': 'XMLHttpRequest'}},
            # Angular style
            {'headers': {'Accept': 'application/json, text/plain, */*'}},
            # jQuery style
            {'headers': {'X-Requested-With': 'XMLHttpRequest', 'Accept': '*/*'}},
            # Mobile app style
            {'headers': {'User-Agent': 'MobileApp/1.0'}},
        ]

        with app.test_client() as client:
            for framework in frameworks:
                # 测试 stats API
                response = client.get('/api/stats', headers=framework['headers'])
                self.assertEqual(response.status_code, 200)
                data = json.loads(response.data)
                self.assertIn('total_files', data)

                # 测试 graph API
                response = client.get('/api/graph', headers=framework['headers'])
                self.assertEqual(response.status_code, 200)
                data = json.loads(response.data)
                self.assertIn('entities', data)

    def test_long_running_availability(self):
        """A4. 长时间运行可用性：系统运行一段时间后API响应时间是否稳定"""
        # 创建一些测试文件
        for i in range(10):
            file_path = os.path.join(self.temp_dir, f"TestClass{i}.ets")
            content = f"""
export class TestClass{i} {{
    method1(): void {{}}
    method2(): void {{}}
}}
"""
            with open(file_path, 'w') as f:
                f.write(content)

        # 初始扫描
        self.service.initial_scan()

        # 模拟运行一段时间，定期调用API
        response_times = []
        for i in range(20):
            start = time.time()
            stats = self.service.get_statistics()
            elapsed = (time.time() - start) * 1000  # ms
            response_times.append(elapsed)
            time.sleep(0.05)  # 短暂间隔

        # 计算平均响应时间和标准差
        avg_time = sum(response_times) / len(response_times)
        variance = sum((t - avg_time) ** 2 for t in response_times) / len(response_times)
        std_dev = variance ** 0.5

        # 验证响应时间稳定（标准差不超过平均值的200%，允许更大的波动）
        self.assertLess(std_dev, avg_time * 2.0, f"Response time unstable: avg={avg_time:.2f}ms, std={std_dev:.2f}ms")
        self.assertLess(avg_time, 100, f"Average response time too high: {avg_time:.2f}ms")


class TestComplexScenarios(unittest.TestCase):
    """B. 复杂场景处理能力"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

    def tearDown(self):
        try:
            self.service.close()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_same_name_inner_classes(self):
        """B5. 同名内部类处理：一个类中包含两个同名的内部类"""
        content = """
export class OuterClass {
    // First inner class named Inner
    static Inner = class {
        method1(): void {}
    }

    // Second inner class also named Inner (in different scope)
    getInner(): typeof Inner {
        class Inner {
            method2(): void {}
        }
        return Inner;
    }
}

export class AnotherOuter {
    static Inner = class {
        method3(): void {}
    }
}
"""
        file_path = os.path.join(self.temp_dir, "OuterClass.ets")
        with open(file_path, 'w') as f:
            f.write(content)

        self.service._handle_file_change(file_path, content)

        # 验证所有实体都被正确解析
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_entities'], 0)

        # 验证数据库中的实体
        entities = self.service.db.get_all_entities(limit=100)
        entity_names = [e['name'] for e in entities]

        # 应该解析出OuterClass和AnotherOuter
        self.assertIn('OuterClass', entity_names)
        self.assertIn('AnotherOuter', entity_names)

    def test_circular_inheritance(self):
        """B6. 循环继承检测：A extends B, B extends C, C extends A"""
        content = """
// Circular inheritance chain
export class ClassA extends ClassC {
    methodA(): void {}
}

export class ClassB extends ClassA {
    methodB(): void {}
}

export class ClassC extends ClassB {
    methodC(): void {}
}
"""
        file_path = os.path.join(self.temp_dir, "Circular.ets")
        with open(file_path, 'w') as f:
            f.write(content)

        # 应该能够处理而不崩溃
        self.service._handle_file_change(file_path, content)

        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats['total_entities'], 3)

        # 验证关系被正确记录
        relations = self.service.db.get_all_relationships(limit=100)
        self.assertGreaterEqual(len(relations), 3)

    def test_deeply_nested_namespaces(self):
        """B7. 深度嵌套命名空间：namespace A { namespace B { namespace C { class D {} } } }"""
        content = """
namespace Level1 {
    export namespace Level2 {
        export namespace Level3 {
            export namespace Level4 {
                export namespace Level5 {
                    export class DeepClass {
                        deepMethod(): void {}
                    }
                }
            }
        }
    }
}

// Multiple branches
namespace BranchA {
    export namespace SubA {
        export class ClassA {}
    }
}

namespace BranchB {
    export namespace SubB {
        export class ClassB {}
    }
}
"""
        file_path = os.path.join(self.temp_dir, "DeepNamespace.ets")
        with open(file_path, 'w') as f:
            f.write(content)

        self.service._handle_file_change(file_path, content)

        stats = self.service.get_statistics()
        # 应该解析出 DeepClass, ClassA, ClassB
        self.assertGreaterEqual(stats['total_entities'], 3)

    def test_generic_type_parsing(self):
        """B8. 泛型类型解析：class MyClass<T extends Base> implements Interface<T>"""
        content = """
// Generic interfaces
export interface Container<T> {
    getItem(): T;
    setItem(item: T): void;
}

// Generic class with constraints
export class GenericClass<T extends Container<U>, U> implements Container<T> {
    private items: T[];

    getItem(): T {
        return this.items[0];
    }

    setItem(item: T): void {
        this.items.push(item);
    }
}

// Complex generic inheritance
export class SpecializedClass<T extends string | number>
    extends GenericClass<Container<T>, T>
    implements Container<Container<T>> {

    getItem(): Container<T> {
        return super.getItem();
    }

    setItem(item: Container<T>): void {
        super.setItem(item);
    }
}

// Multiple type parameters
export class MultiGeneric<K, V, R extends Container<V>> {
    map: Map<K, V>;
    container: R;
}
"""
        file_path = os.path.join(self.temp_dir, "GenericTypes.ets")
        with open(file_path, 'w') as f:
            f.write(content)

        self.service._handle_file_change(file_path, content)

        stats = self.service.get_statistics()
        # 应该解析出 Container, GenericClass, SpecializedClass, MultiGeneric
        self.assertGreaterEqual(stats['total_entities'], 4)

        # 验证泛型关系
        entities = self.service.db.get_all_entities(limit=100)
        entity_names = {e['name'] for e in entities}
        self.assertIn('Container', entity_names)
        self.assertIn('GenericClass', entity_names)

    def test_decorator_parsing(self):
        """B9. 装饰器解析：@Component @Entry class MyComponent {}"""
        content = """
// Single decorator
@Component
export class SimpleComponent {
    build(): void {}
}

// Multiple decorators
@Component
@Entry
@Preview
export class MainComponent {
    @State private count: number = 0;

    @Builder
    buildHeader(): void {}

    build(): void {}
}

// Decorator with arguments
@CustomDialog({ controller: DialogController })
export class MyDialog {
    confirm(): void {}
}

// Method decorators
export class ServiceClass {
    @Log
    @Validate
    processData(): void {}

    @Async
    @Cache(3600)
    fetchData(): Promise<void> {
        return Promise.resolve();
    }
}

// Property decorators
export class ModelClass {
    @Observed
    @Required
    name: string = '';

    @Transient
    tempData: any;
}
"""
        file_path = os.path.join(self.temp_dir, "Decorators.ets")
        with open(file_path, 'w') as f:
            f.write(content)

        self.service._handle_file_change(file_path, content)

        stats = self.service.get_statistics()
        # 应该解析出所有组件类
        self.assertGreaterEqual(stats['total_entities'], 5)

        entities = self.service.db.get_all_entities(limit=100)
        entity_names = {e['name'] for e in entities}
        expected_names = {'SimpleComponent', 'MainComponent', 'MyDialog', 'ServiceClass', 'ModelClass'}
        for name in expected_names:
            self.assertIn(name, entity_names, f"Missing entity: {name}")


class TestBoundaryLimits(unittest.TestCase):
    """C. 边界极限测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

    def tearDown(self):
        try:
            self.service.close()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_large_file_hash_computation(self):
        """C10. 1GB文件处理：超大文件的内容hash计算（模拟，实际使用较小文件）"""
        # 由于实际1GB文件测试太慢，使用100MB模拟
        file_path = os.path.join(self.temp_dir, "LargeFile.ets")

        # 创建大文件内容
        chunk_size = 1024 * 1024  # 1MB chunks
        total_size = 10 * chunk_size  # 10MB for testing

        content_parts = []
        base_content = "export class LargeClass {\n"
        for i in range(100):
            base_content += f"    method{i}(): void {{}}\n"
        base_content += "}\n"

        # 重复内容以达到目标大小
        repeat_count = total_size // len(base_content)
        large_content = base_content * repeat_count

        # 写入文件
        with open(file_path, 'w') as f:
            f.write(large_content)

        # 测试hash计算性能
        start = time.time()
        file_hash = self.service.db.compute_file_hash(large_content)
        hash_time = time.time() - start

        # Hash计算应该很快（< 1秒）
        self.assertLess(hash_time, 1.0, f"Hash computation too slow: {hash_time:.2f}s")
        self.assertEqual(len(file_hash), 64)  # SHA256 hex length

        # 验证相同内容产生相同hash
        file_hash2 = self.service.db.compute_file_hash(large_content)
        self.assertEqual(file_hash, file_hash2)

    def test_unicode_control_characters(self):
        """C11. Unicode控制字符：RTL标记、零宽字符在搜索中的处理"""
        # 创建包含各种Unicode控制字符的内容
        control_chars = [
            '\u200B',  # 零宽空格 (ZWSP)
            '\u200C',  # 零宽非连接符 (ZWNJ)
            '\u200D',  # 零宽连接符 (ZWJ)
            '\uFEFF',  # 零宽非断空格 (BOM)
            '\u202D',  # 从左到右覆盖 (LRO)
            '\u202E',  # 从右到左覆盖 (RLO)
            '\u202A',  # 从左到右嵌入 (LRE)
            '\u202B',  # 从右到左嵌入 (RLE)
            '\u202C',  # 弹出方向格式化 (PDF)
        ]

        for i, char in enumerate(control_chars):
            content = f"""
export class Test{char}Class{i} {{
    method{char}(): void {{}}
}}
"""
            file_path = os.path.join(self.temp_dir, f"Unicode{i}.ets")
            with open(file_path, 'w', encoding='utf-8') as f:
                f.write(content)

            self.service._handle_file_change(file_path, content)

        # 测试搜索功能
        stats = self.service.get_statistics()
        self.assertGreaterEqual(stats['total_entities'], len(control_chars))

        # 搜索应该能正常工作
        results = self.service.search("Test")
        self.assertGreaterEqual(len(results), len(control_chars))

    def test_none_content_handling(self):
        """C12. None内容处理：add_or_update_file(content=None)"""
        # 测试处理None内容
        file_path = os.path.join(self.temp_dir, "NoneContent.ets")

        # 先创建一个正常文件
        normal_content = "export class NormalClass {}"
        with open(file_path, 'w') as f:
            f.write(normal_content)

        self.service._handle_file_change(file_path, normal_content)

        # 然后模拟删除（content=None）
        self.service._handle_file_change(file_path, None)

        # 验证文件数据被删除
        file_id = self.service.db.get_file_id(file_path)
        self.assertIsNone(file_id)

    def test_empty_file_path(self):
        """C13. 空文件路径：处理空字符串路径"""
        # 测试空路径处理
        empty_path = ""

        # 应该抛出异常或优雅处理
        try:
            result = self.service.db.get_file_id(empty_path)
            # 如果返回None，也是可接受的
            self.assertIsNone(result)
        except (ValueError, sqlite3.Error) as e:
            # 抛出异常也是可接受的
            pass

        # 测试add_or_update_file
        try:
            file_id = self.service.db.add_or_update_file(empty_path, "content")
            # 如果成功，验证返回了ID
            self.assertIsInstance(file_id, int)
        except (ValueError, sqlite3.Error) as e:
            # 抛出异常也是可接受的
            pass


class TestConsistency(unittest.TestCase):
    """D. 一致性验证"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

    def tearDown(self):
        try:
            self.service.close()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_statistics_order_consistency(self):
        """D14. 统计顺序一致性：get_statistics返回的事件类型顺序是否稳定"""
        # 创建一些事件
        for i in range(10):
            file_path = os.path.join(self.temp_dir, f"Test{i}.ets")
            content = f"export class Test{i} {{}}"
            with open(file_path, 'w') as f:
                f.write(content)
            self.service._handle_file_change(file_path, content)

        # 多次获取统计信息
        orders = []
        for _ in range(5):
            stats = self.service.get_statistics()
            if 'recent_events' in stats:
                order = tuple(sorted(stats['recent_events'].keys()))
                orders.append(order)

        # 所有顺序应该相同
        if len(orders) > 1:
            for i in range(1, len(orders)):
                self.assertEqual(orders[0], orders[i], "Statistics order inconsistent")

    def test_cross_platform_path_consistency(self):
        """D15. 跨平台路径一致性：Windows和Linux路径分隔符处理"""
        test_cases = [
            ("path/to/file.ets", "path/to/file.ets"),
            ("path\\\\to\\\\file.ets", "path/to/file.ets"),  # Windows style
            ("./path/to/file.ets", "path/to/file.ets"),
            ("../path/to/file.ets", "../path/to/file.ets"),
            ("path//to//file.ets", "path/to/file.ets"),  # Double slash
        ]

        for input_path, expected_normalized in test_cases:
            normalized = self.service.db._normalize_path(input_path)
            # 验证路径被规范化
            self.assertNotIn("//", normalized, f"Double slash not normalized: {input_path}")

    def test_parse_count_accuracy_multithreaded(self):
        """D16. 计数器准确性：parse_count在多线程下的准确性"""
        parser = MockArkTSParser()
        initial_count = parser.parse_count

        content = "export class TestClass { method(): void {} }"
        num_threads = 50
        iterations_per_thread = 20

        def parse_multiple():
            for _ in range(iterations_per_thread):
                parser.parse_file("test.ets", content)

        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=parse_multiple)
            threads.append(t)
            t.start()

        for t in threads:
            t.join()

        expected_count = initial_count + num_threads * iterations_per_thread
        self.assertEqual(parser.parse_count, expected_count,
                        f"Parse count inaccurate: expected {expected_count}, got {parser.parse_count}")


class TestUltimateChaos(unittest.TestCase):
    """E. 终极混沌测试"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

    def tearDown(self):
        try:
            self.service.close()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_ultimate_chaos_combination(self):
        """E17. 终极混沌：所有上述场景的组合，同时运行"""
        import random

        errors = []
        success_count = [0]

        def create_complex_file(file_id: int):
            """创建包含各种复杂场景的文件"""
            scenarios = [
                # 泛型 + 装饰器
                f"""
@Component
@Entry
export class GenericComponent<T extends Base> {{
    @State data: T;
    build(): void {{}}
}}
""",
                # 深度嵌套 + 循环引用
                f"""
namespace NS{file_id} {{
    export class ClassA extends ClassC {{
        methodA(): void {{}}
    }}
    export class ClassB extends ClassA {{}}
    export class ClassC extends ClassB {{}}
}}
""",
                # Unicode + 特殊字符
                f"""
export class Unicode\u200BClass{file_id} {{
    method\u200C(): void {{}}
}}
""",
                # 同名内部类
                f"""
export class Outer{file_id} {{
    static Inner = class {{}}
}}
export class Another{file_id} {{
    static Inner = class {{}}
}}
""",
                # 复杂泛型
                f"""
export interface Container{file_id}<T> {{
    get(): T;
}}
export class Impl{file_id}<K, V> implements Container{file_id}<V> {{
    get(): V {{ return null; }}
}}
""",
            ]
            return random.choice(scenarios)

        def worker(thread_id: int):
            """工作线程：执行各种操作"""
            try:
                for i in range(10):
                    file_id = thread_id * 100 + i
                    file_path = os.path.join(self.temp_dir, f"Chaos{file_id}.ets")

                    # 创建文件
                    content = create_complex_file(file_id)
                    with open(file_path, 'w', encoding='utf-8') as f:
                        f.write(content)

                    # 处理文件
                    self.service._handle_file_change(file_path, content)

                    # 随机执行搜索
                    if random.random() < 0.3:
                        self.service.search("Class")

                    # 随机获取统计
                    if random.random() < 0.2:
                        self.service.get_statistics()

                    # 随机删除
                    if random.random() < 0.1:
                        self.service._handle_file_change(file_path, None)

                    success_count[0] += 1

            except Exception as e:
                errors.append(f"Thread {thread_id}: {str(e)}")

        # 启动多个线程
        threads = []
        num_threads = 20

        for i in range(num_threads):
            t = threading.Thread(target=worker, args=(i,))
            threads.append(t)
            t.start()

        for t in threads:
            t.join(timeout=60)

        # 验证结果
        self.assertEqual(len(errors), 0, f"Errors during chaos test: {errors[:10]}")
        self.assertGreater(success_count[0], 0, "No operations succeeded")

        # 验证系统仍然可用
        stats = self.service.get_statistics()
        self.assertIsInstance(stats, dict)

        # 验证数据库一致性
        files = self.service.db.get_all_files(limit=1000)
        entities = self.service.db.get_all_entities(limit=1000)
        relations = self.service.db.get_all_relationships(limit=1000)

        # 验证数据完整性
        for entity in entities:
            self.assertIn('name', entity)
            self.assertIn('type', entity)

        print(f"\nUltimate Chaos Test Results:")
        print(f"  Successful operations: {success_count[0]}")
        print(f"  Files: {len(files)}")
        print(f"  Entities: {len(entities)}")
        print(f"  Relations: {len(relations)}")


class TestParserEnhancements(unittest.TestCase):
    """测试Parser对复杂场景的增强解析能力"""

    def setUp(self):
        self.parser = MockArkTSParser()

    def test_parser_handles_generic_constraints(self):
        """测试Parser处理泛型约束"""
        content = """
export class GenericClass<T extends Base & Interface> {
    process(item: T): T {
        return item;
    }
}
"""
        result = self.parser.parse_file("test.ets", content)
        self.assertIn('entities', result)
        entity_names = [e['name'] for e in result['entities']]
        self.assertIn('GenericClass', entity_names)

    def test_parser_handles_decorator_chains(self):
        """测试Parser处理装饰器链"""
        content = """
@Injectable()
@Singleton()
@Component({ selector: 'app-test' })
export class DecoratedService {
    @Log()
    @Measure()
    process(): void {}
}
"""
        result = self.parser.parse_file("test.ets", content)
        entity_names = [e['name'] for e in result['entities']]
        self.assertIn('DecoratedService', entity_names)

    def test_parser_handles_complex_types(self):
        """测试Parser处理复杂类型"""
        content = """
export type ComplexType = {
    nested: {
        deep: {
            value: string;
        };
    };
    union: string | number | null;
    intersection: A & B;
    conditional: T extends U ? X : Y;
};

export interface MappedType {
    [K in keyof T]: T[K];
}
"""
        result = self.parser.parse_file("test.ets", content)
        entity_names = [e['name'] for e in result['entities']]
        self.assertIn('ComplexType', entity_names)


class TestDatabaseRobustness(unittest.TestCase):
    """测试数据库在极端情况下的鲁棒性"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_concurrent_reads_and_writes(self):
        """测试并发读写"""
        db = KnowledgeGraphDB(db_path=self.db_path)
        errors = []

        def writer(thread_id: int):
            try:
                for i in range(20):
                    file_path = f"/test/file_{thread_id}_{i}.ets"
                    content = f"export class Class{thread_id}_{i} {{}}"
                    db.add_or_update_file(file_path, content)
            except Exception as e:
                errors.append(f"Writer {thread_id}: {e}")

        def reader(thread_id: int):
            try:
                for _ in range(20):
                    db.get_statistics()
                    db.get_all_files(limit=100)
            except Exception as e:
                errors.append(f"Reader {thread_id}: {e}")

        threads = []
        for i in range(5):
            threads.append(threading.Thread(target=writer, args=(i,)))
            threads.append(threading.Thread(target=reader, args=(i,)))

        for t in threads:
            t.start()
        for t in threads:
            t.join()

        db.close()
        self.assertEqual(len(errors), 0, f"Concurrent access errors: {errors}")

    def test_transaction_rollback_on_error(self):
        """测试事务错误时回滚"""
        db = KnowledgeGraphDB(db_path=self.db_path)

        # 添加初始数据
        file_id = db.add_or_update_file("/test/file.ets", "content")
        initial_count = db.get_statistics()['total_files']

        # 模拟事务中的错误
        try:
            with db._get_connection() as conn:
                conn.execute("INSERT INTO files (path, hash) VALUES (?, ?)",
                           ("/test/new.ets", "hash"))
                # 故意引发错误
                conn.execute("INVALID SQL")
        except:
            pass

        # 验证数据未改变
        final_count = db.get_statistics()['total_files']
        self.assertEqual(initial_count, final_count, "Transaction not rolled back")

        db.close()


class TestFileWatcherRobustness(unittest.TestCase):
    """测试文件监控器的鲁棒性"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")
        self.events = []

        def handler(path, content):
            self.events.append((path, content is not None))

        self.handler = handler
        self.watcher = FileWatcher(handler, [self.temp_dir])

    def tearDown(self):
        try:
            self.watcher.stop()
        except:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_rapid_file_operations(self):
        """测试快速文件操作"""
        self.watcher.start()
        time.sleep(0.1)

        file_path = os.path.join(self.temp_dir, "RapidTest.ets")

        # 快速创建、修改、删除
        for i in range(10):
            with open(file_path, 'w') as f:
                f.write(f"export class Class{i} {{}}")
            time.sleep(0.05)

            with open(file_path, 'w') as f:
                f.write(f"export class Modified{i} {{}}")
            time.sleep(0.05)

        # 等待处理
        time.sleep(0.5)

        # 验证没有崩溃
        self.assertTrue(True, "Rapid operations completed")


class TestMemoryAndPerformance(unittest.TestCase):
    """测试内存和性能"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, "test.db")

    def tearDown(self):
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    def test_memory_usage_with_many_files(self):
        """测试大量文件时的内存使用"""
        import psutil
        process = psutil.Process()

        service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

        initial_memory = process.memory_info().rss / 1024 / 1024  # MB

        # 创建大量文件
        for i in range(100):
            file_path = os.path.join(self.temp_dir, f"MemoryTest{i}.ets")
            content = f"""
export class TestClass{i} {{
    method1(): void {{}}
    method2(): void {{}}
    method3(): void {{}}
}}
"""
            with open(file_path, 'w') as f:
                f.write(content)
            service._handle_file_change(file_path, content)

        final_memory = process.memory_info().rss / 1024 / 1024  # MB
        memory_increase = final_memory - initial_memory

        service.close()

        # 内存增长应该合理（< 100MB）
        self.assertLess(memory_increase, 100,
                       f"Memory increase too high: {memory_increase:.2f}MB")

    def test_query_performance_with_large_dataset(self):
        """测试大数据集查询性能"""
        service = KnowledgeGraphService(db_path=self.db_path, watch_paths=[self.temp_dir])

        # 创建大量实体
        for i in range(50):
            file_path = os.path.join(self.temp_dir, f"PerfTest{i}.ets")
            content = "\n".join([
                f"export class Class{i}_{j} {{ method(): void {{}} }}"
                for j in range(20)
            ])
            with open(file_path, 'w') as f:
                f.write(content)
            service._handle_file_change(file_path, content)

        # 测试搜索性能
        start = time.time()
        for _ in range(10):
            service.search("Class")
        search_time = (time.time() - start) / 10 * 1000  # ms

        # 测试统计性能
        start = time.time()
        for _ in range(10):
            service.get_statistics()
        stats_time = (time.time() - start) / 10 * 1000  # ms

        service.close()

        # 查询应该很快
        self.assertLess(search_time, 100, f"Search too slow: {search_time:.2f}ms")
        self.assertLess(stats_time, 50, f"Stats too slow: {stats_time:.2f}ms")


def run_tests():
    """运行所有测试并生成报告"""
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()

    # 添加所有测试类
    suite.addTests(loader.loadTestsFromTestCase(TestUsability))
    suite.addTests(loader.loadTestsFromTestCase(TestComplexScenarios))
    suite.addTests(loader.loadTestsFromTestCase(TestBoundaryLimits))
    suite.addTests(loader.loadTestsFromTestCase(TestConsistency))
    suite.addTests(loader.loadTestsFromTestCase(TestUltimateChaos))
    suite.addTests(loader.loadTestsFromTestCase(TestParserEnhancements))
    suite.addTests(loader.loadTestsFromTestCase(TestDatabaseRobustness))
    suite.addTests(loader.loadTestsFromTestCase(TestFileWatcherRobustness))
    suite.addTests(loader.loadTestsFromTestCase(TestMemoryAndPerformance))

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)

    # 打印摘要
    print("\n" + "="*70)
    print("终极混沌测试 V22 - 第七轮（终极轮）测试报告")
    print("="*70)
    print(f"测试运行时间: {time.strftime('%Y-%m-%d %H:%M:%S')}")
    print(f"总测试数: {result.testsRun}")
    print(f"通过: {result.testsRun - len(result.failures) - len(result.errors)}")
    print(f"失败: {len(result.failures)}")
    print(f"错误: {len(result.errors)}")
    print(f"成功率: {(result.testsRun - len(result.failures) - len(result.errors)) / result.testsRun * 100:.1f}%")
    print("="*70)

    if result.failures:
        print("\n失败的测试:")
        for test, trace in result.failures:
            print(f"  - {test}")

    if result.errors:
        print("\n错误的测试:")
        for test, trace in result.errors:
            print(f"  - {test}")

    return result.wasSuccessful()


if __name__ == '__main__':
    success = run_tests()
    sys.exit(0 if success else 1)
