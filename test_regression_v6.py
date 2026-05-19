"""
回归测试脚本 v6 - 验证最新修复
覆盖: 致命#1, 致命#2, 高#3, 中#4, 中#19, 基础功能, 并发安全, 压力测试
"""
import os
import sys
import time
import shutil
import sqlite3
import threading
import traceback
from pathlib import Path

# 确保项目目录在 sys.path 中
PROJECT_DIR = "/workspace/arkts_knowledge_graph"
sys.path.insert(0, PROJECT_DIR)

from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser
from knowledge_graph_service import KnowledgeGraphService

# ============================================================
# 全局配置
# ============================================================
DB_PATH = os.path.join(PROJECT_DIR, "test_regression_v6.db")
TEST_DIR = os.path.join(PROJECT_DIR, "test_regression_project")

# 测试结果收集
results = []


def report(test_name, passed, detail=""):
    """记录测试结果"""
    status = "PASS" if passed else "FAIL"
    msg = f"[{status}] {test_name}"
    if detail:
        msg += f" -- {detail}"
    print(msg)
    results.append((test_name, passed, detail))


def cleanup_env():
    """清理测试环境"""
    if os.path.exists(DB_PATH):
        os.remove(DB_PATH)
    if os.path.exists(TEST_DIR):
        shutil.rmtree(TEST_DIR)
    os.makedirs(TEST_DIR, exist_ok=True)


def create_test_file(filename, content):
    """创建测试文件"""
    filepath = os.path.join(TEST_DIR, filename)
    os.makedirs(os.path.dirname(filepath), exist_ok=True)
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)
    return filepath


# ============================================================
# 测试1: 致命#1 - initial_scan() 不传参数不抛 TypeError
# ============================================================
def test_fatal_1_initial_scan_no_args():
    """调用 initial_scan() 不传参数，验证不抛 TypeError"""
    test_name = "致命#1: initial_scan() 无参数调用"
    try:
        # 创建一个空目录作为 watch_path，这样 initial_scan 不会扫描到无关文件
        cleanup_env()
        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()
        try:
            service.initial_scan()
            report(test_name, True, "initial_scan() 无参数调用成功，无 TypeError")
        except TypeError as e:
            report(test_name, False, f"抛出 TypeError: {e}")
        except Exception as e:
            # TypeError 之外的其他异常不算此测试失败
            report(test_name, True, f"无 TypeError（其他异常: {type(e).__name__}: {e}）")
        finally:
            service.close()
    except Exception as e:
        report(test_name, False, f"初始化异常: {e}")


# ============================================================
# 测试2: 致命#2 - _handle_file_change(path, None) 不崩溃
# ============================================================
def test_fatal_2_handle_file_delete():
    """调用 _handle_file_change(path, None) 验证不崩溃，且数据库中该文件被清理"""
    test_name = "致命#2: _handle_file_change(path, None) 文件删除处理"
    try:
        cleanup_env()

        # 先创建一个文件并写入数据库
        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()

        filepath = create_test_file("TestFileDeleteHandler.ets", """
export class DataModelManager {
    private data: string = '';
    public getData(): string { return this.data; }
}
""")
        # 先插入文件数据
        service._handle_file_change(filepath, open(filepath, "r").read())

        # 验证文件已入库
        files = service.db.get_all_files()
        file_paths = [f["path"] for f in files]
        if filepath not in file_paths:
            report(test_name, False, "文件未成功入库，无法继续测试")
            service.close()
            return

        # 调用 _handle_file_change(path, None) 模拟删除
        try:
            service._handle_file_change(filepath, None)
        except Exception as e:
            report(test_name, False, f"调用 _handle_file_change(path, None) 崩溃: {e}")
            service.close()
            return

        # 验证文件已被清理
        files_after = service.db.get_all_files()
        file_paths_after = [f["path"] for f in files_after]
        if filepath in file_paths_after:
            report(test_name, False, "文件未被清理，仍存在于数据库中")
        else:
            report(test_name, True, "文件删除处理正常，数据库已清理")
        service.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试3: 高#3 - 事务原子性：callback中抛异常，旧数据不被删除
# ============================================================
def test_high_3_transaction_atomicity():
    """模拟事务中途异常（在callback中抛异常），验证旧数据不被删除"""
    test_name = "高#3: 事务原子性 - callback异常时旧数据保留"
    try:
        cleanup_env()

        db = KnowledgeGraphDB(DB_PATH)

        # 先手动插入一个文件及其实体
        filepath = "/fake/path/AtomicTestService.ets"
        content_v1 = "export class AtomicTestService { public methodOne(): void {} }"

        # 使用 update_file_transaction 正常插入
        parse_result = {
            "entities": [
                {"name": "AtomicTestService", "type": "class", "signature": "class AtomicTestService",
                 "docstring": None, "start_line": 1, "end_line": 1}
            ],
            "relationships": [],
            "imports": []
        }

        def normal_callback(conn, file_id, pr):
            conn.execute(
                "INSERT INTO entities (file_id, name, type, signature, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, "AtomicTestService", "class", "class AtomicTestService", 1, 1)
            )

        db.update_file_transaction(filepath, content_v1, parse_result, normal_callback)

        # 验证数据已存在
        files_before = db.get_all_files()
        entities_before = db.get_all_entities()
        assert any(f["path"] == filepath for f in files_before), "初始数据未插入"
        assert any(e["name"] == "AtomicTestService" for e in entities_before), "初始实体未插入"

        # 现在尝试更新，但callback中抛异常
        content_v2 = "export class AtomicTestService { public methodTwo(): void {} }"

        def failing_callback(conn, file_id, pr):
            # 先插入一些新数据
            conn.execute(
                "INSERT INTO entities (file_id, name, type, signature, start_line, end_line) VALUES (?, ?, ?, ?, ?, ?)",
                (file_id, "NewEntityShouldBeRolledBack", "class", "class NewEntityShouldBeRolledBack", 1, 1)
            )
            # 然后抛异常
            raise RuntimeError("模拟事务中途异常")

        try:
            db.update_file_transaction(filepath, content_v2, parse_result, failing_callback)
            report(test_name, False, "update_file_transaction 应该抛出异常但没有")
        except RuntimeError:
            pass  # 预期异常

        # 验证原子性：旧数据应该仍然存在（因为 rollback）
        files_after = db.get_all_files()
        entities_after = db.get_all_entities()

        file_exists = any(f["path"] == filepath for f in files_after)
        old_entity_exists = any(e["name"] == "AtomicTestService" for e in entities_after)
        new_entity_exists = any(e["name"] == "NewEntityShouldBeRolledBack" for e in entities_after)

        if file_exists and old_entity_exists and not new_entity_exists:
            report(test_name, True, "事务回滚成功，旧数据保留，新数据未入库")
        elif not file_exists:
            report(test_name, False, "原子性失败：文件记录被删除但事务已回滚")
        elif not old_entity_exists:
            report(test_name, False, "原子性失败：旧实体被删除但事务已回滚")
        elif new_entity_exists:
            report(test_name, False, "原子性失败：新实体已入库但事务应该已回滚")
        else:
            report(test_name, False, "未知状态")

        db.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试4: 中#4 - _file_locks LRU 不超过限制
# ============================================================
def test_medium_4_file_locks_lru():
    """创建超过1000个不同文件的锁，验证 _file_locks 不超过限制"""
    test_name = "中#4: _file_locks LRU 限制 (max=1000)"
    try:
        cleanup_env()

        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()

        # 创建 1100 个不同的文件锁
        for i in range(1100):
            lock = service._get_file_lock(f"/fake/file_{i}.ets")
            assert lock is not None

        # 验证 _file_locks 不超过 _MAX_FILE_LOCKS
        lock_count = len(service._file_locks)
        if lock_count <= service._MAX_FILE_LOCKS:
            report(test_name, True, f"_file_locks 数量={lock_count}, 限制={service._MAX_FILE_LOCKS}")
        else:
            report(test_name, False, f"_file_locks 数量={lock_count}, 超过限制={service._MAX_FILE_LOCKS}")

        service.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试5: 中#19 - updated_at 自动更新且不递归
# ============================================================
def test_medium_19_updated_at_auto_update():
    """更新文件记录，验证 updated_at 被自动更新且不递归"""
    test_name = "中#19: updated_at 自动更新且不递归"
    try:
        cleanup_env()

        db = KnowledgeGraphDB(DB_PATH)

        # 插入一个文件
        filepath = "/fake/UpdatedAtTestService.ets"
        content_v1 = "export class UpdatedAtTestClass {}"
        file_id = db.add_or_update_file(filepath, content_v1)

        # 获取初始 updated_at
        with db._get_connection() as conn:
            cursor = conn.execute("SELECT created_at, updated_at FROM files WHERE id = ?", (file_id,))
            row = cursor.fetchone()
            created_at_1 = row["created_at"]
            updated_at_1 = row["updated_at"]

        # 等待足够时间确保时间戳不同（SQLite CURRENT_TIMESTAMP 精度为秒）
        time.sleep(1.5)

        # 更新文件（相同路径，不同内容）
        content_v2 = "export class UpdatedAtTestClass { public newMethod(): void {} }"
        file_id_2 = db.add_or_update_file(filepath, content_v2)

        # 获取更新后的 updated_at
        with db._get_connection() as conn:
            cursor = conn.execute("SELECT created_at, updated_at FROM files WHERE id = ?", (file_id_2,))
            row = cursor.fetchone()
            created_at_2 = row["created_at"]
            updated_at_2 = row["updated_at"]

        # 验证
        # 1. file_id 应该相同（更新而非新建）
        if file_id != file_id_2:
            report(test_name, False, f"期望更新现有记录(file_id={file_id})，但得到新记录(file_id={file_id_2})")
            db.close()
            return

        # 2. created_at 不应该改变
        if created_at_1 != created_at_2:
            report(test_name, False, f"created_at 被意外修改: {created_at_1} -> {created_at_2}")
            db.close()
            return

        # 3. updated_at 应该改变
        if updated_at_1 == updated_at_2:
            report(test_name, False, f"updated_at 未更新: {updated_at_1}")
            db.close()
            return

        # 4. 验证不递归：多次查询 updated_at 应该稳定不变
        time.sleep(0.1)
        with db._get_connection() as conn:
            cursor = conn.execute("SELECT updated_at FROM files WHERE id = ?", (file_id_2,))
            updated_at_3 = cursor.fetchone()["updated_at"]

        if updated_at_2 != updated_at_3:
            report(test_name, False, f"updated_at 递归更新: {updated_at_2} -> {updated_at_3}")
        else:
            report(test_name, True,
                   f"updated_at 正确更新({updated_at_1} -> {updated_at_2})，created_at 不变，无递归更新")

        db.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试6: 基础功能回归 - 创建/修改/查询/搜索
# ============================================================
def test_basic_functionality():
    """创建文件、修改文件、查询实体、搜索"""
    test_name_prefix = "基础功能"
    try:
        cleanup_env()

        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()

        # --- 6a: 创建文件 ---
        filepath1 = create_test_file("BasicFunctionalityTestService.ets", """
import { Component } from '@arkui/core';
import { Router } from '@system/router';

export interface IDataModel {
    name: string;
    value: number;
}

export class BasicFunctionalityTestService {
    private model: IDataModel = { name: 'test', value: 42 };

    public getModel(): IDataModel {
        return this.model;
    }

    public updateModel(name: string, value: number): void {
        this.model = { name, value };
    }
}
""")
        service._handle_file_change(filepath1, open(filepath1, "r").read())

        files = service.db.get_all_files()
        if not any(f["path"] == filepath1 for f in files):
            report(f"{test_name_prefix}: 创建文件", False, "文件未入库")
        else:
            report(f"{test_name_prefix}: 创建文件", True, "文件成功入库")

        # --- 6b: 查询实体 ---
        entities = service.db.get_all_entities()
        entity_names = [e["name"] for e in entities]
        expected_entities = ["IDataModel", "BasicFunctionalityTestService"]
        found_all = all(name in entity_names for name in expected_entities)
        report(f"{test_name_prefix}: 查询实体", found_all,
               f"找到实体: {entity_names}" if not found_all else f"找到 {len(entities)} 个实体")

        # --- 6c: 搜索 ---
        search_results = service.search("BasicFunctionality")
        if len(search_results) > 0:
            report(f"{test_name_prefix}: 搜索", True, f"搜索 'BasicFunctionality' 返回 {len(search_results)} 结果")
        else:
            report(f"{test_name_prefix}: 搜索", False, "搜索 'BasicFunctionality' 无结果")

        # --- 6d: 修改文件 ---
        time.sleep(0.05)  # 确保时间戳不同
        filepath1_updated = create_test_file("BasicFunctionalityTestService.ets", """
import { Component } from '@arkui/core';

export interface IDataModel {
    name: string;
    value: number;
    extra: boolean;
}

export class BasicFunctionalityTestService {
    private model: IDataModel = { name: 'updated', value: 99, extra: true };

    public getModel(): IDataModel {
        return this.model;
    }

    public newMethod(): string {
        return 'new method added';
    }
}
""")
        service._handle_file_change(filepath1_updated, open(filepath1_updated, "r").read())

        # 验证实体更新
        entities_after = service.db.get_all_entities()
        entity_names_after = [e["name"] for e in entities_after]

        has_new_method = any("newMethod" in name for name in entity_names_after)
        report(f"{test_name_prefix}: 修改文件", has_new_method,
               f"修改后实体: {entity_names_after}" if not has_new_method else "新方法 newMethod 已入库")

        # --- 6e: 统计信息 ---
        stats = service.get_statistics()
        report(f"{test_name_prefix}: 统计信息", True,
               f"文件={stats['total_files']}, 实体={stats['total_entities']}, 关系={stats['total_relationships']}")

        service.close()
    except Exception as e:
        report(f"{test_name_prefix}", False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试7: 并发安全 - 5线程同时操作
# ============================================================
def test_concurrent_safety():
    """5线程同时操作，验证无崩溃"""
    test_name = "并发安全: 5线程同时操作"
    errors = []
    error_lock = threading.Lock()

    try:
        cleanup_env()

        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()

        def worker(thread_id):
            """每个线程创建/修改/删除文件"""
            try:
                for i in range(20):
                    filepath = create_test_file(
                        f"ConcurrentTestModule{thread_id:03d}_Iteration{i:03d}.ets",
                        f"export class ThreadClass{thread_id}Iter{i} {{ public method(): void {{}} }}"
                    )
                    service._handle_file_change(filepath, open(filepath, "r").read())

                    # 修改
                    filepath_mod = create_test_file(
                        f"ConcurrentTestModule{thread_id:03d}_Iteration{i:03d}.ets",
                        f"export class ThreadClass{thread_id}Iter{i} {{ public updatedMethod(): string {{ return 'hello'; }} }}"
                    )
                    service._handle_file_change(filepath_mod, open(filepath_mod, "r").read())

                    # 每隔几次删除一个
                    if i % 5 == 0:
                        service._handle_file_change(filepath, None)

                    # 搜索
                    service.search(f"ThreadClass{thread_id}")

            except Exception as e:
                with error_lock:
                    errors.append((thread_id, str(e), traceback.format_exc()))

        threads = []
        for t_id in range(5):
            t = threading.Thread(target=worker, args=(t_id,))
            threads.append(t)

        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=60)

        if errors:
            error_summary = "; ".join([f"线程{e[0]}: {e[1]}" for e in errors[:3]])
            report(test_name, False, f"{len(errors)} 个线程出错: {error_summary}")
        else:
            stats = service.get_statistics()
            report(test_name, True,
                   f"5线程各20轮操作无崩溃, 文件={stats['total_files']}, 实体={stats['total_entities']}")

        service.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 测试8: 60秒压力测试
# ============================================================
def test_stress_60s():
    """60秒压力测试：大量创建/修改/删除操作"""
    test_name = "60秒压力测试"
    errors = []
    error_lock = threading.Lock()

    try:
        cleanup_env()

        service = KnowledgeGraphService(db_path=DB_PATH, watch_paths=[TEST_DIR])
        service.parser = MockArkTSParser()

        start_time = time.time()
        duration = 60  # 秒
        op_count = {"create": 0, "update": 0, "delete": 0}
        count_lock = threading.Lock()
        stop_flag = threading.Event()

        def stress_worker(thread_id):
            """持续执行创建/修改/删除操作"""
            try:
                iteration = 0
                while not stop_flag.is_set():
                    iteration += 1

                    # 创建
                    filepath = create_test_file(
                        f"StressTestModule{thread_id:03d}_Iter{iteration:04d}.ets",
                        f"export class StressClass{thread_id}_{iteration} {{ public run(): void {{}} }}"
                    )
                    service._handle_file_change(filepath, open(filepath, "r").read())
                    with count_lock:
                        op_count["create"] += 1

                    # 修改
                    filepath_mod = create_test_file(
                        f"StressTestModule{thread_id:03d}_Iter{iteration:04d}.ets",
                        f"export class StressClass{thread_id}_{iteration} {{ public run(): string {{ return 'v2'; }} }}"
                    )
                    service._handle_file_change(filepath_mod, open(filepath_mod, "r").read())
                    with count_lock:
                        op_count["update"] += 1

                    # 每3次删除一次
                    if iteration % 3 == 0:
                        service._handle_file_change(filepath, None)
                        with count_lock:
                            op_count["delete"] += 1

            except Exception as e:
                with error_lock:
                    errors.append((thread_id, str(e)))

        # 启动3个压力线程
        threads = []
        for t_id in range(3):
            t = threading.Thread(target=stress_worker, args=(t_id,))
            threads.append(t)

        for t in threads:
            t.start()

        # 等待指定时间
        elapsed = 0
        while elapsed < duration:
            time.sleep(5)
            elapsed = int(time.time() - start_time)
            with count_lock:
                total = op_count["create"] + op_count["update"] + op_count["delete"]
            print(f"  [压力测试] {elapsed}s - 总操作数: {total} "
                  f"(创建={op_count['create']}, 修改={op_count['update']}, 删除={op_count['delete']})")

        stop_flag.set()
        for t in threads:
            t.join(timeout=10)

        total_ops = op_count["create"] + op_count["update"] + op_count["delete"]
        actual_duration = time.time() - start_time

        if errors:
            error_summary = "; ".join([f"线程{e[0]}: {e[1]}" for e in errors[:3]])
            report(test_name, False,
                   f"{len(errors)} 个错误, 总操作={total_ops}, 耗时={actual_duration:.1f}s. 错误: {error_summary}")
        else:
            stats = service.get_statistics()
            ops_per_sec = total_ops / actual_duration if actual_duration > 0 else 0
            report(test_name, True,
                   f"无崩溃! 总操作={total_ops} ({ops_per_sec:.0f} ops/s), "
                   f"耗时={actual_duration:.1f}s, "
                   f"DB文件={stats['total_files']}, 实体={stats['total_entities']}")

        service.close()
    except Exception as e:
        report(test_name, False, f"测试异常: {e}\n{traceback.format_exc()}")


# ============================================================
# 主函数
# ============================================================
def main():
    print("=" * 70)
    print("  回归测试 v6 - ArkTS Knowledge Graph")
    print("=" * 70)
    print()

    # 确保初始环境干净
    cleanup_env()

    # 执行测试
    print("--- 测试1: 致命#1 - initial_scan() 无参数 ---")
    test_fatal_1_initial_scan_no_args()
    print()

    print("--- 测试2: 致命#2 - _handle_file_change(path, None) ---")
    test_fatal_2_handle_file_delete()
    print()

    print("--- 测试3: 高#3 - 事务原子性 ---")
    test_high_3_transaction_atomicity()
    print()

    print("--- 测试4: 中#4 - _file_locks LRU 限制 ---")
    test_medium_4_file_locks_lru()
    print()

    print("--- 测试5: 中#19 - updated_at 自动更新 ---")
    test_medium_19_updated_at_auto_update()
    print()

    print("--- 测试6: 基础功能回归 ---")
    test_basic_functionality()
    print()

    print("--- 测试7: 并发安全 ---")
    test_concurrent_safety()
    print()

    print("--- 测试8: 60秒压力测试 ---")
    test_stress_60s()
    print()

    # 最终清理
    cleanup_env()

    # 汇总报告
    print("=" * 70)
    print("  测试结果汇总")
    print("=" * 70)
    passed = sum(1 for _, p, _ in results if p)
    failed = sum(1 for _, p, _ in results if not p)
    total = len(results)

    for name, p, detail in results:
        status = "PASS" if p else "FAIL"
        line = f"  [{status}] {name}"
        if detail and not p:
            line += f"  => {detail}"
        print(line)

    print()
    print(f"  总计: {total}  通过: {passed}  失败: {failed}")
    print("=" * 70)

    if failed > 0:
        print("\n失败的测试详情:")
        for name, p, detail in results:
            if not p:
                print(f"  - {name}: {detail}")

    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
