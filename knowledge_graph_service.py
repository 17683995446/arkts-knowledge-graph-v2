"""
Knowledge Graph Service
Core service that orchestrates file watching, parsing, and database updates
"""
import json
import os
import time
import threading
from collections import OrderedDict
from typing import Dict, List
from knowledge_graph_db import KnowledgeGraphDB
from llm_parser import ArkTSParser
from file_watcher import FileWatcher


def _normalize_path(file_path: str) -> str:
    """规范化文件路径，消除 ../ 和 ./ 等相对路径差异"""
    return os.path.normpath(file_path)


class KnowledgeGraphService:
    """Main service for incremental knowledge graph updates"""

    def __init__(self, db_path: str = "knowledge_graph.db", watch_paths: list = None):
        self.db = KnowledgeGraphDB(db_path)
        self.parser = ArkTSParser()
        self.watcher = FileWatcher(self._handle_file_change, watch_paths)
        self.stats = {
            'files_processed': 0,
            'entities_extracted': 0,
            'relationships_extracted': 0,
            'last_update_time': None
        }
        self._lock = threading.Lock()
        self._file_locks = OrderedDict()  # 文件级锁（LRU）
        self._MAX_FILE_LOCKS = 1000
        self._file_locks_mutex = threading.Lock()  # 保护 _file_locks 字典
        self.on_update_callback = None  # 外部回调（用于WebSocket推送）

        # [CRITICAL#1] shutdown 信号，防止 close() 时工作线程使用已关闭的 DB
        self._shutdown_event = threading.Event()
        # [MEDIUM#17] 防止 close() 重复调用
        self._closed = False

    def _get_file_lock(self, file_path: str):
        """获取文件级锁，确保同一文件不会并发处理（LRU清理）"""
        with self._file_locks_mutex:
            if file_path in self._file_locks:
                # 移到末尾（最近使用）
                self._file_locks.move_to_end(file_path)
            else:
                self._file_locks[file_path] = threading.Lock()
                # [HIGH#7] LRU清理：只清理未被任何线程等待的锁
                if len(self._file_locks) > self._MAX_FILE_LOCKS:
                    # 从最旧的开始检查，跳过被持有的锁
                    keys_to_remove = []
                    for key in list(self._file_locks.keys()):
                        if len(self._file_locks) - len(keys_to_remove) <= self._MAX_FILE_LOCKS:
                            break
                        lock = self._file_locks[key]
                        if not lock.locked():
                            keys_to_remove.append(key)
                    for key in keys_to_remove:
                        del self._file_locks[key]
            return self._file_locks[file_path]

    _MAX_RETRY_ATTEMPTS = 5
    _RETRY_DELAY_BASE = 0.3  # 秒，指数退避基数

    def _handle_file_change(self, file_path: str, content):
        """Handle a file change event - 线程安全，带重试机制"""
        # [CRITICAL#1] 如果系统正在关闭，拒绝新请求
        if self._shutdown_event.is_set():
            return

        file_path = _normalize_path(file_path)
        # 处理文件删除事件
        if content is None:
            self._handle_file_deleted(file_path)
            return

        file_lock = self._get_file_lock(file_path)

        # 带指数退避的重试机制，避免静默丢弃更新
        for attempt in range(self._MAX_RETRY_ATTEMPTS):
            # [CRITICAL#1] 每次重试前检查 shutdown
            if self._shutdown_event.is_set():
                return
            if file_lock.acquire(blocking=False):
                try:
                    self._handle_file_change_inner(file_path, content)
                except Exception as e:
                    print(f"[ERROR] Failed to process {file_path}: {e}")
                finally:
                    file_lock.release()
                return
            else:
                delay = self._RETRY_DELAY_BASE * (2 ** attempt)
                print(f"[RETRY] {file_path} is locked, retry {attempt+1}/{self._MAX_RETRY_ATTEMPTS} in {delay:.1f}s")
                # 使用 Event.wait 替代 time.sleep，可以立即响应 shutdown
                if self._shutdown_event.wait(timeout=delay):
                    return  # 收到 shutdown 信号，退出

        # 所有重试均失败，使用阻塞式获取作为最后手段
        print(f"[WAIT] {file_path} still locked after retries, waiting...")
        file_lock.acquire(blocking=True)
        try:
            if not self._shutdown_event.is_set():
                self._handle_file_change_inner(file_path, content)
        except Exception as e:
            print(f"[ERROR] Failed to process {file_path} after waiting: {e}")
        finally:
            file_lock.release()

    def _handle_file_deleted(self, file_path: str):
        """处理文件删除事件 - [CRITICAL#2] 使用文件级锁保护"""
        # [CRITICAL#2] 删除操作也需要文件级锁，防止与更新操作竞态
        file_lock = self._get_file_lock(file_path)

        for attempt in range(self._MAX_RETRY_ATTEMPTS):
            if self._shutdown_event.is_set():
                return
            if file_lock.acquire(blocking=False):
                try:
                    self._handle_file_deleted_inner(file_path)
                except Exception as e:
                    print(f"[ERROR] Failed to delete file data for {file_path}: {e}")
                finally:
                    file_lock.release()
                return
            else:
                delay = self._RETRY_DELAY_BASE * (2 ** attempt)
                if self._shutdown_event.wait(timeout=delay):
                    return

        # 最后手段：阻塞等待
        file_lock.acquire(blocking=True)
        try:
            if not self._shutdown_event.is_set():
                self._handle_file_deleted_inner(file_path)
        except Exception as e:
            print(f"[ERROR] Failed to delete file data for {file_path}: {e}")
        finally:
            file_lock.release()

    def _handle_file_deleted_inner(self, file_path: str):
        """删除文件数据的实际逻辑"""
        deleted = self.db.delete_file_data(file_path)
        if deleted:
            self.db.log_event(event_type='FILE_DELETED', file_path=file_path)
            print(f"[DELETE] Removed: {file_path}")
            with self._lock:
                self.stats['files_processed'] += 1
                # [HIGH#5] 删除操作也更新 last_update_time
                self.stats['last_update_time'] = time.time()
            # [HIGH#4] on_update_callback 使用daemon线程在锁外调用
            if self.on_update_callback:
                callback = self.on_update_callback
                stats = self.get_statistics()
                threading.Thread(target=callback, args=(file_path, stats), daemon=True).start()

    def _handle_file_change_inner(self, file_path: str, content: str):
        """实际处理逻辑 - 原子事务"""
        start_time = time.time()

        # [MEDIUM#7] 跳过空内容文件
        if not content or not content.strip():
            return

        # 1. 检查文件是否真正改变
        unchanged, new_hash = self.db.file_exists_and_unchanged(file_path, content)
        if unchanged:
            return

        print(f"[UPDATE] Processing: {file_path}")

        # 2. 先解析（不删除旧数据）
        parse_result = self.parser.parse_file(file_path, content)
        # [MEDIUM#20] 防御性检查 parse_result
        if not parse_result:
            parse_result = {"entities": [], "relationships": [], "imports": []}
        entities = parse_result.get('entities', [])
        relationships = parse_result.get('relationships', [])

        # 3. 在单个事务中完成 delete + insert（原子操作）
        def transaction_callback(conn, file_id, parse_result):
            """在事务内执行所有插入操作"""
            entity_name_to_id = {}

            for entity in entities:
                cursor = conn.execute(
                    """INSERT INTO entities
                       (file_id, name, type, signature, docstring, start_line, end_line)
                       VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                    (file_id, entity['name'], entity['type'],
                     entity.get('signature'), entity.get('docstring'),
                     entity.get('start_line'), entity.get('end_line'))
                )
                composite_key = f"{entity['name']}_{entity.get('start_line', '')}"
                entity_name_to_id[composite_key] = cursor.fetchone()['id']

            # 插入关系
            for rel in relationships:
                source_id = entity_name_to_id.get(rel['source'])
                if source_id is None:
                    for key, eid in entity_name_to_id.items():
                        if key.startswith(f"{rel['source']}_"):
                            source_id = eid
                            break
                target_id = entity_name_to_id.get(rel['target'])
                if target_id is None:
                    for key, eid in entity_name_to_id.items():
                        if key.startswith(f"{rel['target']}_"):
                            target_id = eid
                            break
                if source_id is None or target_id is None:
                    continue
                conn.execute(
                    """INSERT INTO relationships
                       (source_file_id, source_entity_id, target_entity_id, target_file_id, relation_type)
                       VALUES (?, ?, ?, ?, ?)""",
                    (file_id, source_id, target_id, file_id, rel['type'])
                )

            # 插入导入
            for imp in parse_result.get('imports', []):
                names_json = json.dumps(imp.get('names', [])) if imp.get('names') else None
                conn.execute(
                    """INSERT INTO imports (file_id, module_path, imported_names, is_default_import)
                       VALUES (?, ?, ?, ?)""",
                    (file_id, imp['module'], names_json, imp.get('is_default', False))
                )

        self.db.update_file_transaction(file_path, content, parse_result, transaction_callback)

        # 4. 记录事件和更新统计
        duration_ms = int((time.time() - start_time) * 1000)
        self.db.log_event(
            event_type='FILE_UPDATED',
            file_path=file_path,
            details=f"Entities: {len(entities)}, Relations: {len(relationships)}",
            duration_ms=duration_ms
        )

        with self._lock:
            self.stats['files_processed'] += 1
            self.stats['entities_extracted'] += len(entities)
            self.stats['relationships_extracted'] += len(relationships)
            self.stats['last_update_time'] = time.time()

        print(f"[DONE] {file_path} in {duration_ms}ms - {len(entities)} entities, {len(relationships)} relations")

        # [HIGH#4] on_update_callback 在文件锁释放后由调用方触发
        # 此处仍在锁内，但 _handle_file_change 的 finally 块会先释放锁再 return
        # 为了确保安全，将回调延迟到锁外执行
        if self.on_update_callback:
            callback = self.on_update_callback
            stats = self.get_statistics()
            # 使用后台线程调用回调，避免阻塞文件锁
            threading.Thread(target=callback, args=(file_path, stats), daemon=True).start()

    def initial_scan(self):
        """Perform initial scan of all files"""
        print("[INIT] Starting initial file scan...")
        start_time = time.time()

        files = self.watcher.scan_existing_files()
        print(f"[INIT] Found {len(files)} ArkTS files")

        processed = 0
        for file_info in files:
            if self._shutdown_event.is_set():
                print("[INIT] Scan interrupted by shutdown signal")
                break
            self._handle_file_change(file_info['path'], file_info['content'])
            processed += 1

        duration = int(time.time() - start_time)
        print(f"[INIT] Initial scan completed in {duration}s")

        self.db.log_event(
            event_type='INITIAL_SCAN_COMPLETE',
            details=f"Scanned {len(files)} files, processed {processed}",
            duration_ms=duration * 1000
        )

    def start(self):
        """Start the service"""
        self._shutdown_event.clear()
        self.watcher.start()

    def stop(self):
        """Stop the service"""
        self._shutdown_event.set()  # 通知所有工作线程停止
        self.watcher.stop()

    def close(self):
        """清理资源 - [CRITICAL#1] 线程安全关闭"""
        # [MEDIUM#15] 使用锁保护幂等检查，防止并发close
        with self._lock:
            if self._closed:
                return
            self._closed = True

        # 1. 先设置 shutdown 信号，阻止新任务
        self._shutdown_event.set()

        # 2. 停止 watcher（取消 pending timers，不再产生新事件）
        self.watcher.stop()

        # 3. 禁用回调，防止后台线程继续访问数据库
        self.on_update_callback = None

        # 4. 等待正在处理的文件锁释放（给正在执行的工作线程一些时间完成）
        # 检查是否有锁被持有
        wait_start = time.time()
        max_wait = 10  # 最多等待10秒
        while time.time() - wait_start < max_wait:
            any_locked = False
            with self._file_locks_mutex:
                for lock in self._file_locks.values():
                    if lock.locked():
                        any_locked = True
                        break
            if not any_locked:
                break
            time.sleep(0.2)

        # 5. 给后台 daemon 线程一些时间完成
        time.sleep(0.5)

        # 6. 清理文件锁
        self._file_locks.clear()

        # 7. 最后关闭数据库（此时所有工作线程应该已完成）
        self.db.close()

    def get_statistics(self) -> Dict:
        """Get current statistics"""
        db_stats = self.db.get_statistics()
        with self._lock:
            return {
                **db_stats,
                **self.stats
            }

    def search(self, query: str) -> List[Dict]:
        """Search the knowledge graph"""
        return self.db.search_entities(query)

    def get_graph_data(self) -> Dict:
        """Get data for visualization"""
        return {
            'entities': self.db.get_all_entities(limit=500),
            'relationships': self.db.get_all_relationships(limit=1000),
            'files': self.db.get_all_files(),
            'dependencies': self.db.get_file_dependencies()
        }
