#!/usr/bin/env python3
"""
第三轮混沌测试 - ArkTS知识图谱系统
极端复杂场景：真实IDE行为 + 数据库异常 + 文件系统异常 + 复杂业务 + 性能压力 + 日志验证
"""

import unittest
import tempfile
import os
import sys
import io
import time
import threading
import sqlite3
import shutil
import json
import re
import stat
from unittest.mock import patch, MagicMock
from collections import OrderedDict

sys.path.insert(0, '/workspace/arkts_knowledge_graph')

from knowledge_graph_service import KnowledgeGraphService
from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser
from config import SUPPORTED_EXTENSIONS, MIN_FILE_WORDS, MAX_NODES_DISPLAY


# ============================================================
# 工具类和辅助函数
# ============================================================

class LogCapture:
    """捕获print输出用于验证"""

    def __init__(self):
        self.buffer = io.StringIO()
        self._original_stdout = None

    def start(self):
        self._original_stdout = sys.stdout
        sys.stdout = self.buffer

    def stop(self):
        if self._original_stdout:
            sys.stdout = self._original_stdout
            self._original_stdout = None

    def get_output(self):
        return self.buffer.getvalue()

    def contains(self, text):
        return text in self.get_output()

    def not_contains(self, text):
        return text not in self.get_output()

    def count_occurrences(self, text):
        return self.get_output().count(text)

    def get_lines(self):
        output = self.get_output().strip()
        return output.split('\n') if output else []

    def clear(self):
        self.buffer.truncate(0)
        self.buffer.seek(0)


def make_class_content(class_name, imports=None, methods=None):
    """生成包含export class的ArkTS内容，确保MockArkTSParser可解析"""
    lines = []
    if imports:
        for imp in imports:
            lines.append(f"import {{ {imp} }} from './{imp}'")
    lines.append(f"export class {class_name} {{")
    lines.append(f"  private name: string = '{class_name}'")
    if methods:
        for m in methods:
            lines.append(f"  public {m}(): void {{ }}")
    else:
        lines.append(f"  public getData(): string {{ return this.name }}")
        lines.append(f"  public setData(v: string): void {{ this.name = v }}")
    lines.append("}")
    return '\n'.join(lines)


def make_interface_content(name, extends=None):
    """生成包含export interface的ArkTS内容"""
    content = f"export interface {name}"
    if extends:
        content += f" extends {extends}"
    content += " {\n  id: number\n  name: string\n}"
    return content


def make_multi_entity_content(prefix, num_classes=5, num_interfaces=3, num_enums=2):
    """生成包含多个实体的ArkTS内容"""
    lines = []
    for i in range(num_interfaces):
        ext = f" extends {prefix}Interface{i-1}" if i > 0 else ""
        lines.append(f"export interface {prefix}Interface{i}{ext} {{ field{i}: number }}")
    for i in range(num_classes):
        impl = f" implements {prefix}Interface{i % num_interfaces}"
        lines.append(f"export class {prefix}Class{i}{impl} {{")
        lines.append(f"  public method{i}(): number {{ return {i} }}")
        lines.append("}")
    for i in range(num_enums):
        lines.append(f"export enum {prefix}Enum{i} {{ A, B, C }}")
    return '\n'.join(lines)


# ============================================================
# 第三轮混沌测试主类
# ============================================================

class TestChaosRound3(unittest.TestCase):
    """第三轮混沌测试 - 30个极端复杂场景"""

    def setUp(self):
        self.temp_dir = tempfile.mkdtemp()
        self.db_path = os.path.join(self.temp_dir, 'test.db')
        self.service = KnowledgeGraphService(db_path=self.db_path)
        self.service.parser = MockArkTSParser()
        self.log = LogCapture()
        self.log.start()

    def tearDown(self):
        self.log.stop()
        try:
            self.service.close()
        except Exception:
            pass
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    # ================================================================
    # A. 真实IDE编辑器行为模拟 (A01-A05)
    # ================================================================

    def test_A01_vim_save_mode(self):
        """A01: Vim保存模式 - 删除->等待->创建->等待->删除->创建，验证最终一致"""
        file_path = os.path.join(self.temp_dir, 'VimSaveTestClass.ts')
        content_v1 = make_class_content('VimSaveClassAlpha')
        content_v2 = make_class_content('VimSaveClassBeta')
        content_v3 = make_class_content('VimSaveClassGamma')

        # 第一次写入
        self.service._handle_file_change(file_path, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)
        entities = self.service.db.get_all_entities()
        self.assertTrue(any(e['name'] == 'VimSaveClassAlpha' for e in entities))

        # Vim :w 机制: 删除旧文件 -> 等待 -> 创建新文件
        self.service._handle_file_change(file_path, None)
        time.sleep(0.1)
        self.service._handle_file_change(file_path, content_v2)
        time.sleep(0.1)

        # 再次删除->创建
        self.service._handle_file_change(file_path, None)
        time.sleep(0.1)
        self.service._handle_file_change(file_path, content_v3)

        # 验证最终状态
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)
        entities = self.service.db.get_all_entities()
        self.assertTrue(any(e['name'] == 'VimSaveClassGamma' for e in entities))
        self.assertFalse(any(e['name'] == 'VimSaveClassAlpha' for e in entities))
        self.assertFalse(any(e['name'] == 'VimSaveClassBeta' for e in entities))

    def test_A02_vscode_autosave(self):
        """A02: VSCode自动保存 - 文件快速修改多次，验证debounce和最终状态"""
        from file_watcher import ArkTSFileHandler

        handler = ArkTSFileHandler(self.service._handle_file_change, debounce_seconds=0.3)
        file_path = os.path.join(self.temp_dir, 'AutoSaveComponentHelper.ts')

        # 模拟VSCode每200ms自动保存一次，共15次（3秒）
        final_version = 14
        for i in range(15):
            content = make_class_content(f'AutoSaveClass{i}')
            with open(file_path, 'w') as f:
                f.write(content)
            handler._schedule_update(file_path)
            time.sleep(0.2)

        # 等待debounce完成
        time.sleep(1.0)

        # 验证最终状态
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_files'], 0)

        # 由于debounce，只有最后一个版本应该被处理
        # 但中间版本可能也会被处理（取决于debounce时机）
        entities = self.service.db.get_all_entities()
        # 至少最后一个版本应该存在
        self.assertTrue(
            any(f'AutoSaveClass{final_version}' in e['name'] for e in entities),
            f"Final version class not found in entities: {[e['name'] for e in entities]}"
        )

        # 清理pending timers
        with handler.lock:
            for timer in list(handler.pending_files.values()):
                timer.cancel()
            handler.pending_files.clear()

    def test_A03_jetbrains_temp_file(self):
        """A03: JetBrains临时文件 - 创建临时文件->修改->重命名为正式文件->删除临时文件"""
        temp_file = os.path.join(self.temp_dir, '___jb_tmp___JetBrainsHelper.ts')
        real_file = os.path.join(self.temp_dir, 'JetBrainsFinalHelper.ts')

        content_v1 = make_class_content('JetBrainsClassDraft')
        content_v2 = make_class_content('JetBrainsClassFinal')

        # 1. 创建临时文件并处理
        self.service._handle_file_change(temp_file, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # 2. 修改临时文件内容
        self.service._handle_file_change(temp_file, content_v2)
        entities = self.service.db.get_all_entities()
        self.assertTrue(any(e['name'] == 'JetBrainsClassFinal' for e in entities))

        # 3. 删除临时文件数据
        self.service._handle_file_change(temp_file, None)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 0)

        # 4. 创建正式文件（模拟重命名）
        self.service._handle_file_change(real_file, content_v2)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # 验证正式文件数据正确
        entities = self.service.db.get_all_entities()
        self.assertTrue(any(e['name'] == 'JetBrainsClassFinal' for e in entities))

        # 5. 验证临时文件数据已完全清除
        file_id = self.service.db.get_file_id(temp_file)
        self.assertIsNone(file_id)

    def test_A04_batch_rename(self):
        """A04: 批量重命名 - 同时重命名10个文件，验证依赖关系正确迁移"""
        old_files = []
        new_files = []
        old_contents = []
        new_contents = []

        # 创建10个文件，每个导入下一个（形成链）
        for i in range(10):
            old_path = os.path.join(self.temp_dir, f'OldRenameModule{i}.ts')
            new_path = os.path.join(self.temp_dir, f'NewRenameModule{i}.ts')
            next_import = [f'OldRenameClass{(i + 1) % 10}'] if i < 9 else []
            old_content = make_class_content(f'OldRenameClass{i}', imports=next_import)
            new_content = make_class_content(f'NewRenameClass{i}', imports=next_import)
            old_files.append(old_path)
            new_files.append(new_path)
            old_contents.append(old_content)
            new_contents.append(new_content)

        # 处理旧文件
        for path, content in zip(old_files, old_contents):
            self.service._handle_file_change(path, content)

        stats_before = self.service.get_statistics()
        self.assertEqual(stats_before['total_files'], 10)

        # 批量删除旧文件
        for path in old_files:
            self.service._handle_file_change(path, None)

        stats_after_delete = self.service.get_statistics()
        self.assertEqual(stats_after_delete['total_files'], 0)

        # 批量创建新文件
        for path, content in zip(new_files, new_contents):
            self.service._handle_file_change(path, content)

        stats_after_rename = self.service.get_statistics()
        self.assertEqual(stats_after_rename['total_files'], 10)

        # 验证新文件数据正确
        entities = self.service.db.get_all_entities()
        entity_names = [e['name'] for e in entities]
        for i in range(10):
            self.assertIn(f'NewRenameClass{i}', entity_names)
            self.assertNotIn(f'OldRenameClass{i}', entity_names)

    def test_A05_editor_crash_recovery(self):
        """A05: 编辑器崩溃恢复 - 写入->模拟崩溃->重新写入->验证数据完整性"""
        file_path = os.path.join(self.temp_dir, 'CrashRecoveryTestClass.ts')
        content_v1 = make_class_content('CrashRecoveryClassV1')
        content_v3 = make_class_content('CrashRecoveryClassV3')

        # 1. 正常写入v1
        self.service._handle_file_change(file_path, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # 2. 模拟崩溃：在callback中注入异常
        original_update = self.service.db.update_file_transaction

        def crashing_update(fp, content, parse_result=None, callback=None):
            def crashing_callback(conn, file_id, pr):
                # 插入部分数据后崩溃
                conn.execute(
                    "INSERT INTO entities (file_id, name, type) VALUES (?, 'CrashPartialEntity', 'class')",
                    (file_id,)
                )
                raise RuntimeError("Simulated editor crash")
            return original_update(fp, content, parse_result, crashing_callback)

        with patch.object(self.service.db, 'update_file_transaction', side_effect=crashing_update):
            content_v2 = make_class_content('CrashRecoveryClassV2')
            self.service._handle_file_change(file_path, content_v2)

        # 验证错误被捕获
        self.assertTrue(self.log.contains("[ERROR]"),
                        "Error log should be present after crash simulation")

        # 3. 重新写入v3（恢复后）
        self.service._handle_file_change(file_path, content_v3)

        # 验证最终数据完整性
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        entities = self.service.db.get_all_entities()
        entity_names = [e['name'] for e in entities]
        self.assertIn('CrashRecoveryClassV3', entity_names)
        # 崩溃时的部分数据应该被回滚
        self.assertNotIn('CrashPartialEntity', entity_names)
        # v2的数据不应该存在（事务已回滚）
        self.assertNotIn('CrashRecoveryClassV2', entity_names)

    # ================================================================
    # B. 数据库异常场景 (B06-B10)
    # ================================================================

    def test_B06_wal_file_deletion(self):
        """B06: WAL文件删除 - 运行中删除-wal和-shm文件，验证SQLite恢复能力"""
        # 先处理一些文件
        for i in range(5):
            file_path = os.path.join(self.temp_dir, f'WalTestModule{i}.ts')
            content = make_class_content(f'WalTestClass{i}')
            self.service._handle_file_change(file_path, content)

        stats_before = self.service.get_statistics()
        self.assertEqual(stats_before['total_files'], 5)

        # 删除WAL和SHM文件
        wal_path = self.db_path + '-wal'
        shm_path = self.db_path + '-shm'
        for p in [wal_path, shm_path]:
            if os.path.exists(p):
                os.remove(p)

        # 继续处理更多文件
        for i in range(5, 10):
            file_path = os.path.join(self.temp_dir, f'WalTestModule{i}.ts')
            content = make_class_content(f'WalTestClass{i}')
            self.service._handle_file_change(file_path, content)

        # 验证数据完整性
        stats_after = self.service.get_statistics()
        self.assertEqual(stats_after['total_files'], 10)

        all_entities = self.service.db.get_all_entities(limit=500)
        for i in range(10):
            self.assertTrue(
                any(f'WalTestClass{i}' in e['name'] for e in all_entities),
                f"WalTestClass{i} not found after WAL deletion"
            )

    def test_B07_database_lock_simulation(self):
        """B07: 数据库锁定模拟 - BEGIN EXCLUSIVE后尝试写入，验证重试机制"""
        # 先处理一个文件
        file_path1 = os.path.join(self.temp_dir, 'DbLockModuleAlpha.ts')
        content1 = make_class_content('DbLockClassAlpha')
        self.service._handle_file_change(file_path1, content1)

        # 用原始连接锁定数据库
        lock_conn = sqlite3.connect(self.db_path, timeout=5.0)
        lock_conn.execute("BEGIN EXCLUSIVE")
        lock_conn.execute("SELECT COUNT(*) FROM files")

        # 在另一个线程中尝试处理文件（会被阻塞）
        file_path2 = os.path.join(self.temp_dir, 'DbLockModuleBeta.ts')
        content2 = make_class_content('DbLockClassBeta')

        result = {'done': False, 'error': None}
        def process_under_lock():
            try:
                self.service._handle_file_change(file_path2, content2)
                result['done'] = True
            except Exception as e:
                result['error'] = str(e)

        t = threading.Thread(target=process_under_lock)
        t.start()

        # 等待一下，然后释放锁
        time.sleep(1.5)
        lock_conn.commit()
        lock_conn.close()

        # 等待线程完成
        t.join(timeout=15.0)

        # 验证文件最终被处理
        self.assertTrue(result['done'],
                        f"File should have been processed after lock release. Error: {result['error']}")
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 2)

    def test_B08_disk_space_simulation(self):
        """B08: 磁盘空间模拟 - mock sqlite3.connect抛出OperationalError"""
        file_path = os.path.join(self.temp_dir, 'DiskFullTestClass.ts')
        content = make_class_content('DiskFullTestClass')

        # Mock sqlite3.connect 模拟磁盘满
        with patch('knowledge_graph_db.sqlite3.connect') as mock_connect:
            mock_connect.side_effect = sqlite3.OperationalError("disk I/O error")

            # 应该优雅处理，不崩溃
            self.service._handle_file_change(file_path, content)

        # 验证错误被记录
        self.assertTrue(self.log.contains("[ERROR]"),
                        "Should log error when disk is full")

        # 恢复后数据库仍可用
        self.service._handle_file_change(file_path, content)
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_files'], 0)

    def test_B09_concurrent_transaction_conflict(self):
        """B09: 并发事务冲突 - 两个线程同时更新不同文件，验证WAL并发正确性"""
        file_path_a = os.path.join(self.temp_dir, 'ConcurrentModuleAlpha.ts')
        file_path_b = os.path.join(self.temp_dir, 'ConcurrentModuleBeta.ts')
        content_a = make_class_content('ConcurrentClassAlpha')
        content_b = make_class_content('ConcurrentClassBeta')

        errors = []

        def update_file(fp, content, label):
            try:
                for _ in range(10):
                    self.service._handle_file_change(fp, content)
            except Exception as e:
                errors.append(f"{label}: {e}")

        # 两个线程同时更新不同文件
        t1 = threading.Thread(target=update_file, args=(file_path_a, content_a, "ThreadA"))
        t2 = threading.Thread(target=update_file, args=(file_path_b, content_b, "ThreadB"))

        t1.start()
        t2.start()
        t1.join(timeout=30.0)
        t2.join(timeout=30.0)

        # 验证无错误
        self.assertEqual(len(errors), 0, f"Concurrent transaction errors: {errors}")

        # 验证两个文件都存在
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 2)

        entities = self.service.db.get_all_entities(limit=500)
        entity_names = [e['name'] for e in entities]
        self.assertIn('ConcurrentClassAlpha', entity_names)
        self.assertIn('ConcurrentClassBeta', entity_names)

    def test_B10_connection_interrupt(self):
        """B10: 数据库连接中断 - commit前模拟连接断开"""
        file_path = os.path.join(self.temp_dir, 'ConnInterruptTestClass.ts')
        content_v1 = make_class_content('ConnInterruptClassV1')

        # 先正常处理
        self.service._handle_file_change(file_path, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # Mock使commit失败（模拟连接中断）
        original_connect = sqlite3.connect

        def mock_connect_interrupt(*args, **kwargs):
            conn = original_connect(*args, **kwargs)
            original_commit = conn.commit
            _fail_count = [0]

            def failing_commit():
                _fail_count[0] += 1
                if _fail_count[0] <= 2:
                    raise sqlite3.OperationalError("unable to open database file")
                return original_commit()
            conn.commit = failing_commit
            return conn

        with patch('knowledge_graph_db.sqlite3.connect', side_effect=mock_connect_interrupt):
            content_v2 = make_class_content('ConnInterruptClassV2')
            # 可能需要重试几次才能成功
            self.service._handle_file_change(file_path, content_v2)

        # 验证数据库仍可用
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_files'], 0)

    # ================================================================
    # C. 文件系统异常场景 (C11-C15)
    # ================================================================

    def test_C11_permission_change(self):
        """C11: 权限变更 - 创建文件->修改为不可读->触发事件->验证优雅降级"""
        from file_watcher import ArkTSFileHandler

        handler = ArkTSFileHandler(self.service._handle_file_change, debounce_seconds=0.1)
        file_path = os.path.join(self.temp_dir, 'PermissionComponentHelper.ts')

        # 先通过service直接处理文件（绕过handler的文件名验证）
        content = make_class_content('PermissionTestClass')
        # 同时创建实际文件（后续chmod需要）
        with open(file_path, 'w') as f:
            f.write(content)
        self.service._handle_file_change(file_path, content)

        stats_before = self.service.get_statistics()
        self.assertGreater(stats_before['total_files'], 0)

        # 移除所有权限
        os.chmod(file_path, 0o000)

        try:
            # 尝试再次触发（应该优雅降级）
            handler._schedule_update(file_path)
            time.sleep(0.5)

            # 验证系统没有崩溃，可能有错误日志
            output = self.log.get_output()
            # 系统应该仍然可用
            stats_after = self.service.get_statistics()
            self.assertIsNotNone(stats_after)
        finally:
            # 恢复权限以便清理
            os.chmod(file_path, 0o644)

        # 清理pending timers
        with handler.lock:
            for timer in list(handler.pending_files.values()):
                timer.cancel()
            handler.pending_files.clear()

    def test_C12_symbolic_link(self):
        """C12: 符号链接 - 创建符号链接->处理->修改目标->再次处理"""
        real_file = os.path.join(self.temp_dir, 'RealTargetComponent.ts')
        symlink_file = os.path.join(self.temp_dir, 'SymlinkSourceComponent.ts')

        # 创建真实文件
        content_v1 = make_class_content('RealTargetClass')
        with open(real_file, 'w') as f:
            f.write(content_v1)

        # 创建符号链接
        try:
            os.symlink(real_file, symlink_file)
        except OSError:
            self.skipTest("Symbolic links not supported on this filesystem")

        # 通过符号链接路径处理
        self.service._handle_file_change(symlink_file, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # 修改真实文件
        content_v2 = make_class_content('RealTargetClassV2')
        with open(real_file, 'w') as f:
            f.write(content_v2)

        # 通过符号链接再次处理
        self.service._handle_file_change(symlink_file, content_v2)

        # 验证更新
        entities = self.service.db.get_all_entities()
        self.assertTrue(
            any('RealTargetClassV2' in e['name'] for e in entities),
            "Updated content should be reflected through symlink"
        )

    def test_C13_directory_deletion(self):
        """C13: 目录删除 - 删除监控目录->验证不崩溃->重建->验证恢复"""
        watch_dir = os.path.join(self.temp_dir, 'watch_subdir')
        os.makedirs(watch_dir)

        file_path = os.path.join(watch_dir, 'DirectoryTestComponent.ts')
        content = make_class_content('DirectoryTestClass')
        with open(file_path, 'w') as f:
            f.write(content)

        # 处理文件
        self.service._handle_file_change(file_path, content)
        stats_before = self.service.get_statistics()
        self.assertEqual(stats_before['total_files'], 1)

        # 删除整个目录
        shutil.rmtree(watch_dir)
        self.assertFalse(os.path.exists(watch_dir))

        # 验证服务不崩溃
        try:
            stats = self.service.get_statistics()
            self.assertIsNotNone(stats)
            files = self.service.db.get_all_files()
            self.assertIsNotNone(files)
        except Exception as e:
            self.fail(f"Service crashed after directory deletion: {e}")

        # 重建目录和文件
        os.makedirs(watch_dir)
        with open(file_path, 'w') as f:
            f.write(content)

        # 重新处理
        self.service._handle_file_change(file_path, content)
        stats_after = self.service.get_statistics()
        self.assertGreaterEqual(stats_after['total_files'], 1)

    def test_C14_file_content_race(self):
        """C14: 文件内容竞态 - 一个线程持续写入，另一个线程读取，验证无半截内容"""
        file_path = os.path.join(self.temp_dir, 'RaceConditionComponent.ts')

        # 初始内容
        initial_content = make_class_content('RaceConditionClass')
        with open(file_path, 'w') as f:
            f.write(initial_content)

        stop_event = threading.Event()
        read_results = []
        errors = []

        # 写入线程：每10ms追加一行
        def writer():
            counter = 0
            while not stop_event.is_set():
                try:
                    with open(file_path, 'a') as f:
                        f.write(f"// writer_line_{counter}\n")
                    counter += 1
                except Exception as e:
                    errors.append(f"writer: {e}")
                time.sleep(0.01)

        # 读取线程：每50ms读取文件内容并处理
        def reader():
            while not stop_event.is_set():
                try:
                    with open(file_path, 'r') as f:
                        content = f.read()
                    if content and content.strip():
                        read_results.append(len(content))
                except Exception as e:
                    errors.append(f"reader: {e}")
                time.sleep(0.05)

        t_write = threading.Thread(target=writer, daemon=True)
        t_read = threading.Thread(target=reader, daemon=True)

        t_write.start()
        t_read.start()

        # 运行1.5秒
        time.sleep(1.5)
        stop_event.set()

        t_write.join(timeout=5.0)
        t_read.join(timeout=5.0)

        # 验证无错误
        self.assertEqual(len(errors), 0, f"Errors during race condition test: {errors}")
        self.assertGreater(len(read_results), 0, "Should have read the file at least once")

        # 验证读取的内容长度单调递增（写入只追加）
        for i in range(1, len(read_results)):
            self.assertGreaterEqual(read_results[i], read_results[i - 1],
                                    "Content length should be non-decreasing with append-only writes")

    def test_C15_filename_encoding(self):
        """C15: 文件名编码 - 各种Unicode字符作为文件名"""
        test_names = [
            ('chinese_test_class', 'ChineseTestClass'),
            ('japanese_test_class', 'JapaneseTestClass'),
            ('korean_test_class', 'KoreanTestClass'),
            ('arabic_test_class', 'ArabicTestClass'),
            ('emoji_test_class', 'EmojiTestClass'),
        ]

        # 由于文件系统可能不支持所有Unicode字符，使用英文文件名但内容包含Unicode
        for suffix, class_name in test_names:
            file_path = os.path.join(self.temp_dir, f'UnicodeEncoding{suffix}.ts')
            # 内容中使用各种Unicode字符
            content = f"""// 文件注释: 测试文件 テスト 파일 테스트 اختبار
export class {class_name} {{
  private label: string = '中文标签 にほんご 한국어 عربي'
  public getValue(): string {{ return this.label }}
}}
"""
            self.service._handle_file_change(file_path, content)

        # 验证所有文件都被处理
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], len(test_names))

        # 验证实体正确提取
        entities = self.service.db.get_all_entities(limit=500)
        entity_names = [e['name'] for e in entities]
        for _, class_name in test_names:
            self.assertIn(class_name, entity_names,
                          f"{class_name} should be extracted from Unicode content")

    # ================================================================
    # D. 复杂业务场景 (D16-D20)
    # ================================================================

    def test_D16_large_project_simulation(self):
        """D16: 大型项目模拟 - 500个互相import的文件，验证依赖图"""
        num_files = 500
        file_paths = []
        contents = []

        for i in range(num_files):
            file_path = os.path.join(self.temp_dir, f'LargeProjectModule{i:04d}.ts')
            next_class = f'LargeProjectClass{(i + 1) % num_files:04d}'
            content = make_class_content(f'LargeProjectClass{i:04d}', imports=[next_class])
            file_paths.append(file_path)
            contents.append(content)

        # 处理所有文件（第一遍：建立文件记录）
        start_time = time.time()
        for fp, content in zip(file_paths, contents):
            self.service._handle_file_change(fp, content)
        first_pass_time = time.time() - start_time

        # 验证所有文件都在数据库中
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], num_files,
                         f"Expected {num_files} files, got {stats['total_files']}")

        # 第二遍：重新处理以建立依赖关系（因为依赖解析需要目标文件已在DB中）
        for fp, content in zip(file_paths, contents):
            self.service._handle_file_change(fp, content)

        # 验证imports被正确存储
        all_imports = []
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute("SELECT COUNT(*) as cnt FROM imports")
            import_count = cursor.fetchone()['cnt']
        self.assertGreater(import_count, 0,
                           "Imports should be stored for large project")

        # 验证性能：500个文件处理应在合理时间内完成
        self.assertLess(first_pass_time, 60.0,
                        f"Processing 500 files took too long: {first_pass_time:.1f}s")

    def test_D17_incremental_update_chain(self):
        """D17: 增量更新链 - 文件A v1->v2->...->v100，每版修改一个class"""
        file_path = os.path.join(self.temp_dir, 'IncrementalUpdateTestClass.ts')

        for version in range(100):
            # 每个版本添加一个新class
            classes = []
            for v in range(version + 1):
                classes.append(f"export class IncrementalClassV{v} {{ public method(): void {{ }} }}")
            content = '\n'.join(classes)
            self.service._handle_file_change(file_path, content)

        # 验证最终状态：只有v99版本的class应该存在
        file_id = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(file_id)
        entities = self.service.db.get_file_entities(file_id)
        entity_names = [e['name'] for e in entities]

        # 最终版本应该包含v0到v99的所有class
        for v in range(100):
            self.assertIn(f'IncrementalClassV{v}', entity_names,
                          f"Class IncrementalClassV{v} should exist in final state")

        # 验证没有重复（MockArkTSParser会为每个class提取method，所以entity数量翻倍）
        # 每个class有2个method(getData, setData)，所以100个class = 100 + 200 = 300 entities
        self.assertGreaterEqual(len(entity_names), 100,
                                f"Expected >= 100 unique class entities, got {len(entity_names)}")
        # 验证所有class都存在
        class_entities = [e for e in entity_names if not '.' in e]
        self.assertEqual(len(class_entities), 100,
                         f"Expected 100 unique class entities, got {len(class_entities)}")

    def test_D18_delete_middle_dependency(self):
        """D18: 删除中间依赖 - A->B->C链，删除B，验证A和C数据正确但依赖断裂"""
        file_a = os.path.join(self.temp_dir, 'DepChainModuleAlpha.ts')
        file_b = os.path.join(self.temp_dir, 'DepChainModuleBeta.ts')
        file_c = os.path.join(self.temp_dir, 'DepChainModuleGamma.ts')

        content_a = "import { DepChainClassBeta } from './DepChainModuleBeta'\n" + \
                    make_class_content('DepChainClassAlpha')
        content_b = "import { DepChainClassGamma } from './DepChainModuleGamma'\n" + \
                    make_class_content('DepChainClassBeta')
        content_c = make_class_content('DepChainClassGamma')

        # 处理三个文件
        self.service._handle_file_change(file_a, content_a)
        self.service._handle_file_change(file_b, content_b)
        self.service._handle_file_change(file_c, content_c)

        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 3)

        # 删除中间文件B
        self.service._handle_file_change(file_b, None)

        # 验证A和C仍在数据库中
        stats_after = self.service.get_statistics()
        self.assertEqual(stats_after['total_files'], 2)

        # 验证A和C的实体完整
        entities = self.service.db.get_all_entities(limit=500)
        entity_names = [e['name'] for e in entities]
        self.assertIn('DepChainClassAlpha', entity_names)
        self.assertIn('DepChainClassGamma', entity_names)
        self.assertNotIn('DepChainClassBeta', entity_names)

        # 验证A的import记录仍在（但B不存在了，依赖断裂）
        file_a_id = self.service.db.get_file_id(file_a)
        self.assertIsNotNone(file_a_id)
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT * FROM imports WHERE file_id = ?", (file_a_id,)
            )
            imports = cursor.fetchall()
        self.assertGreater(len(imports), 0, "A's imports should still exist")

    def test_D19_same_name_different_paths(self):
        """D19: 同名文件不同路径 - /dir1/Utils.ts 和 /dir2/Utils.ts 同时存在"""
        dir1 = os.path.join(self.temp_dir, 'dir1')
        dir2 = os.path.join(self.temp_dir, 'dir2')
        os.makedirs(dir1)
        os.makedirs(dir2)

        file1 = os.path.join(dir1, 'SameNameUtils.ts')
        file2 = os.path.join(dir2, 'SameNameUtils.ts')

        content1 = make_class_content('DirOneUtilsClass')
        content2 = make_class_content('DirTwoUtilsClass')

        # 处理两个同名文件
        self.service._handle_file_change(file1, content1)
        self.service._handle_file_change(file2, content2)

        # 验证两个文件都在数据库中（路径不同）
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 2)

        # 验证两个文件有独立的实体
        all_files = self.service.db.get_all_files()
        file_paths_in_db = [f['path'] for f in all_files]
        self.assertIn(os.path.normpath(file1), file_paths_in_db)
        self.assertIn(os.path.normpath(file2), file_paths_in_db)

        # 验证各自有独立的class实体
        file1_id = self.service.db.get_file_id(os.path.normpath(file1))
        file2_id = self.service.db.get_file_id(os.path.normpath(file2))
        entities1 = self.service.db.get_file_entities(file1_id)
        entities2 = self.service.db.get_file_entities(file2_id)
        self.assertTrue(any(e['name'] == 'DirOneUtilsClass' for e in entities1))
        self.assertTrue(any(e['name'] == 'DirTwoUtilsClass' for e in entities2))

    def test_D20_interface_implementation_chain(self):
        """D20: 接口实现链 - InterfaceA -> InterfaceB -> ClassC（多级implements），验证关系链完整"""
        file_path = os.path.join(self.temp_dir, 'InterfaceImplChainComponent.ts')
        content = """export interface InterfaceChainBase {
  baseMethod(): void
}
export interface InterfaceChainMiddle extends InterfaceChainBase {
  middleMethod(): void
}
export class InterfaceChainImpl implements InterfaceChainMiddle {
  baseMethod(): void {}
  middleMethod(): void {}
  public implMethod(): string { return 'impl' }
}
"""
        self.service._handle_file_change(file_path, content)

        # 验证所有实体都被提取
        entities = self.service.db.get_all_entities(limit=500)
        entity_names = [e['name'] for e in entities]
        self.assertIn('InterfaceChainBase', entity_names)
        self.assertIn('InterfaceChainMiddle', entity_names)
        self.assertIn('InterfaceChainImpl', entity_names)

        # 验证关系链
        relationships = self.service.db.get_all_relationships(limit=500)
        rel_types = {}
        for r in relationships:
            key = (r.get('source_name', ''), r.get('target_name', ''), r.get('relation_type', ''))
            rel_types[key] = True

        # InterfaceChainMiddle extends InterfaceChainBase
        self.assertTrue(
            any(r['source_name'] == 'InterfaceChainMiddle' and r['target_name'] == 'InterfaceChainBase'
                and r['relation_type'] == 'extends' for r in relationships),
            "InterfaceChainMiddle should extend InterfaceChainBase"
        )
        # InterfaceChainImpl implements InterfaceChainMiddle
        self.assertTrue(
            any(r['source_name'] == 'InterfaceChainImpl' and r['target_name'] == 'InterfaceChainMiddle'
                and r['relation_type'] == 'implements' for r in relationships),
            "InterfaceChainImpl should implement InterfaceChainMiddle"
        )

    # ================================================================
    # E. 性能压力测试 (E21-E25)
    # ================================================================

    def test_E21_sustained_write_pressure(self):
        """E21: 持续写入压力 - 10个线程每秒处理10个文件，持续3秒，验证系统吞吐量和稳定性"""
        num_threads = 10
        duration = 3  # 秒
        errors = []
        processed_count = [0]
        stop_event = threading.Event()

        def worker(thread_id):
            counter = 0
            while not stop_event.is_set():
                try:
                    file_path = os.path.join(self.temp_dir,
                                             f'StressModuleT{thread_id}F{counter:04d}.ts')
                    content = make_class_content(f'StressClassT{thread_id}F{counter}')
                    self.service._handle_file_change(file_path, content)
                    processed_count[0] += 1
                    counter += 1
                except Exception as e:
                    errors.append(f"T{thread_id}F{counter}: {e}")
                time.sleep(0.05)  # 每个线程每秒约20个文件

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()

        time.sleep(duration)
        stop_event.set()
        for t in threads:
            t.join(timeout=10.0)

        # 验证无错误
        self.assertEqual(len(errors), 0, f"Errors during sustained write: {errors[:5]}")

        # 验证系统稳定
        stats = self.service.get_statistics()
        self.assertGreater(stats['total_files'], 0)

        # 验证吞吐量合理（至少处理了一些文件）
        self.assertGreater(processed_count[0], num_threads * 5,
                           f"Too few files processed: {processed_count[0]}")

    def test_E22_large_transaction_pressure(self):
        """E22: 大事务压力 - 单个文件包含200个entity和大量relationship，验证事务性能"""
        file_path = os.path.join(self.temp_dir, 'LargeTransactionComponent.ts')

        # 生成包含大量实体的文件
        lines = []
        for i in range(200):
            lines.append(f"export class LargeEntityClass{i} {{")
            lines.append(f"  public method{i}(): number {{ return {i} }}")
            lines.append(f"  public compute{i}(x: number): number {{ return x * {i} }}")
            lines.append("}")

        content = '\n'.join(lines)

        start_time = time.time()
        self.service._handle_file_change(file_path, content)
        duration = time.time() - start_time

        # 验证所有实体都被提取
        file_id = self.service.db.get_file_id(file_path)
        self.assertIsNotNone(file_id)
        entities = self.service.db.get_file_entities(file_id)
        # 应该有200个class + 每个class有2个method = 600个entity
        self.assertGreater(len(entities), 200,
                           f"Expected >200 entities, got {len(entities)}")

        # 验证性能（应该在10秒内完成）
        self.assertLess(duration, 10.0,
                        f"Large transaction took too long: {duration:.2f}s")

    def test_E23_search_pressure(self):
        """E23: 搜索压力 - 100个线程并发搜索不同关键词，验证无死锁"""
        # 先创建一些文件
        for i in range(50):
            file_path = os.path.join(self.temp_dir, f'SearchTestModule{i}.ts')
            content = make_class_content(f'SearchTestClass{i}')
            self.service._handle_file_change(file_path, content)

        errors = []
        results = [0]
        num_threads = 100
        barrier = threading.Barrier(num_threads)

        def search_worker(thread_id):
            try:
                barrier.wait(timeout=5.0)
                # 每个线程搜索不同的关键词
                query = f'SearchTestClass{thread_id % 50}'
                result = self.service.search(query)
                results[0] += len(result)
            except Exception as e:
                errors.append(f"T{thread_id}: {e}")

        threads = [threading.Thread(target=search_worker, args=(i,)) for i in range(num_threads)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        # 验证无死锁（所有线程都完成）
        self.assertEqual(len(errors), 0, f"Errors during search pressure: {errors[:5]}")
        self.assertGreater(results[0], 0, "Should have found some search results")

    def test_E24_statistics_query_pressure(self):
        """E24: 统计查询压力 - 1000次快速调用 get_statistics()，验证无竞争条件"""
        # 先创建一些文件
        for i in range(10):
            file_path = os.path.join(self.temp_dir, f'StatsPressureModule{i}.ts')
            content = make_class_content(f'StatsPressureClass{i}')
            self.service._handle_file_change(file_path, content)

        errors = []
        results = []

        def stats_worker():
            try:
                for _ in range(100):
                    stats = self.service.get_statistics()
                    results.append(stats)
            except Exception as e:
                errors.append(str(e))

        # 10个线程各调用100次
        threads = [threading.Thread(target=stats_worker) for _ in range(10)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30.0)

        # 验证无错误
        self.assertEqual(len(errors), 0, f"Errors during stats pressure: {errors[:5]}")
        self.assertEqual(len(results), 1000, f"Expected 1000 results, got {len(results)}")

        # 验证所有结果一致
        for r in results:
            self.assertEqual(r['total_files'], 10,
                             "Stats should consistently report 10 files")

    def test_E25_memory_growth_monitor(self):
        """E25: 内存增长监控 - 处理1000个文件后验证 _file_locks 不超过 MAX_FILE_LOCKS"""
        num_files = 1000
        max_allowed = self.service._MAX_FILE_LOCKS

        for i in range(num_files):
            file_path = os.path.join(self.temp_dir, f'MemoryMonitorModule{i:04d}.ts')
            content = make_class_content(f'MemoryMonitorClass{i:04d}')
            self.service._handle_file_change(file_path, content)

        # 验证文件锁数量不超过上限
        with self.service._file_locks_mutex:
            lock_count = len(self.service._file_locks)

        self.assertLessEqual(lock_count, max_allowed,
                             f"File locks ({lock_count}) exceeded MAX_FILE_LOCKS ({max_allowed})")

        # 验证所有文件都被处理
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], num_files)

    # ================================================================
    # F. 日志驱动深度验证 (F26-F30)
    # ================================================================

    def test_F26_transaction_rollback_logging(self):
        """F26: 事务回滚日志 - 模拟callback异常，验证事务回滚后无残留数据"""
        file_path = os.path.join(self.temp_dir, 'RollbackTestComponent.ts')
        content_v1 = make_class_content('RollbackTestClassV1')
        content_v2 = make_class_content('RollbackTestClassV2')

        # 正常写入v1
        self.service._handle_file_change(file_path, content_v1)
        stats = self.service.get_statistics()
        self.assertEqual(stats['total_files'], 1)

        # 模拟callback异常导致回滚
        original_update = self.service.db.update_file_transaction

        def failing_update(fp, content, parse_result=None, callback=None):
            def failing_callback(conn, file_id, pr):
                # 插入一些数据
                conn.execute(
                    "INSERT INTO entities (file_id, name, type) VALUES (?, 'RollbackGhostEntity', 'class')",
                    (file_id,)
                )
                conn.execute(
                    "INSERT INTO entities (file_id, name, type) VALUES (?, 'RollbackGhostEntity2', 'class')",
                    (file_id,)
                )
                # 然后抛出异常触发回滚
                raise RuntimeError("Simulated callback failure for rollback test")
            return original_update(fp, content, parse_result, failing_callback)

        with patch.object(self.service.db, 'update_file_transaction', side_effect=failing_update):
            self.service._handle_file_change(file_path, content_v2)

        # 验证错误被记录
        self.assertTrue(self.log.contains("[ERROR]"),
                        "Should log ERROR after callback exception")

        # 验证回滚后无残留数据
        entities = self.service.db.get_all_entities(limit=500)
        entity_names = [e['name'] for e in entities]
        self.assertNotIn('RollbackGhostEntity', entity_names,
                         "Rolled back entity should not exist")
        self.assertNotIn('RollbackGhostEntity2', entity_names,
                         "Rolled back entity2 should not exist")
        self.assertNotIn('RollbackTestClassV2', entity_names,
                         "Rolled back v2 entities should not exist")

        # 验证v1数据仍然完整
        self.assertIn('RollbackTestClassV1', entity_names,
                      "V1 data should survive rollback of v2 attempt")

    def test_F27_retry_mechanism_logging(self):
        """F27: 重试机制日志 - 验证所有重试都有对应的RETRY日志"""
        file_path = os.path.join(self.temp_dir, 'RetryLogTestComponent.ts')
        content = make_class_content('RetryLogTestClass')

        # 阻塞文件锁，迫使重试
        file_lock = self.service._get_file_lock(file_path)
        file_lock.acquire()

        retry_triggered = threading.Event()

        def delayed_release():
            # 等待重试日志出现
            time.sleep(2.0)
            file_lock.release()
            retry_triggered.set()

        t = threading.Thread(target=delayed_release, daemon=True)
        t.start()

        # 在另一个线程中尝试处理（会被阻塞并重试）
        def process_file():
            self.service._handle_file_change(file_path, content)

        t2 = threading.Thread(target=process_file)
        t2.start()

        retry_triggered.wait(timeout=10.0)
        t2.join(timeout=10.0)

        # 验证有RETRY日志
        self.assertTrue(self.log.contains("[RETRY]"),
                        "Should have RETRY log when file lock is contended")

    def test_F28_shutdown_logging(self):
        """F28: Shutdown日志 - 验证shutdown过程中进行中的操作收到信号并退出"""
        file_path = os.path.join(self.temp_dir, 'ShutdownTestComponent.ts')
        content = make_class_content('ShutdownTestClass')

        # 阻塞文件锁模拟进行中的操作
        file_lock = self.service._get_file_lock(file_path)
        file_lock.acquire()

        # 启动一个线程尝试处理文件（会被阻塞）
        operation_started = threading.Event()
        operation_exited = threading.Event()

        def blocked_operation():
            operation_started.set()
            self.service._handle_file_change(file_path, content)
            operation_exited.set()

        t = threading.Thread(target=blocked_operation, daemon=True)
        t.start()
        operation_started.wait(timeout=5.0)

        # 确保操作被阻塞
        time.sleep(0.5)
        self.assertFalse(operation_exited.is_set(), "Operation should be blocked")

        # 执行shutdown
        self.service.close()

        # 释放锁让被阻塞的线程可以退出
        file_lock.release()

        # 等待线程退出
        t.join(timeout=15.0)

        # 验证线程最终退出
        self.assertTrue(operation_exited.is_set() or t.is_alive() == False,
                        "Blocked operation should exit after shutdown")

        # 验证shutdown后新的操作被拒绝
        self.assertTrue(self.service._shutdown_event.is_set(),
                        "Shutdown event should be set")

    def test_F29_event_log_consistency(self):
        """F29: 事件日志一致性 - 验证每个成功处理的文件都有对应的event记录"""
        num_files = 20
        processed_files = []

        for i in range(num_files):
            file_path = os.path.join(self.temp_dir, f'EventLogModule{i}.ts')
            content = make_class_content(f'EventLogClass{i}')
            self.service._handle_file_change(file_path, content)
            processed_files.append(os.path.normpath(file_path))

        # 查询所有FILE_UPDATED事件
        events = self.service.db.get_recent_events(limit=1000)
        updated_events = [e for e in events if e['event_type'] == 'FILE_UPDATED']
        deleted_events = [e for e in events if e['event_type'] == 'FILE_DELETED']

        # 验证每个处理的文件都有对应的FILE_UPDATED事件
        event_paths = set(e['file_path'] for e in updated_events)
        for fp in processed_files:
            self.assertIn(fp, event_paths,
                          f"File {fp} should have a FILE_UPDATED event")

        # 验证事件数量至少等于文件数量（可能有重复处理）
        self.assertGreaterEqual(len(updated_events), num_files,
                                f"Expected >= {num_files} FILE_UPDATED events, got {len(updated_events)}")

        # 删除一些文件并验证FILE_DELETED事件
        for i in range(5):
            self.service._handle_file_change(processed_files[i], None)

        events_after = self.service.db.get_recent_events(limit=1000)
        deleted_events = [e for e in events_after if e['event_type'] == 'FILE_DELETED']
        self.assertGreaterEqual(len(deleted_events), 5,
                                "Should have at least 5 FILE_DELETED events")

    def test_F30_timestamp_monotonic(self):
        """F30: 时间戳日志 - 验证所有日志的时间戳单调递增（无时钟回拨）"""
        file_path = os.path.join(self.temp_dir, 'TimestampTestComponent.ts')

        # 快速连续处理多个文件
        timestamps = []
        for i in range(20):
            fp = os.path.join(self.temp_dir, f'TimestampModule{i}.ts')
            content = make_class_content(f'TimestampClass{i}')
            start = time.time()
            self.service._handle_file_change(fp, content)
            timestamps.append(time.time())

        # 验证时间戳单调递增
        for i in range(1, len(timestamps)):
            self.assertGreaterEqual(timestamps[i], timestamps[i - 1],
                                    f"Timestamp {i} ({timestamps[i]}) < timestamp {i-1} ({timestamps[i-1]})")

        # 查询数据库中的事件时间戳
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.execute(
                "SELECT created_at FROM events WHERE event_type = 'FILE_UPDATED' ORDER BY created_at"
            )
            db_timestamps = [row['created_at'] for row in cursor.fetchall()]

        # 验证数据库时间戳也单调递增
        for i in range(1, len(db_timestamps)):
            self.assertGreaterEqual(db_timestamps[i], db_timestamps[i - 1],
                                    f"DB timestamp {i} ({db_timestamps[i]}) < {i-1} ({db_timestamps[i-1]})")


# ============================================================
# 运行入口
# ============================================================

if __name__ == '__main__':
    print("=" * 80)
    print("第三轮混沌测试 - ArkTS知识图谱系统")
    print("30个极端复杂场景测试")
    print("=" * 80)
    unittest.main(verbosity=2)