"""
Knowledge Graph Database Manager
Handles all SQLite operations for the ArkTS knowledge graph
"""
import sqlite3
import os
import json
import hashlib
import time
from typing import List, Dict, Optional, Tuple
from contextlib import contextmanager
from database_schema import SCHEMA_SQL


class KnowledgeGraphDB:
    """SQLite database manager for code knowledge graph with concurrency support"""

    @staticmethod
    def _normalize_path(file_path: str) -> str:
        """规范化文件路径，消除 ../ 和 ./ 等相对路径差异"""
        return os.path.normpath(file_path)

    def __init__(self, db_path: str = "knowledge_graph.db", timeout: float = 30.0):
        self.db_path = db_path
        self.timeout = timeout
        self._init_database()
    
    @contextmanager
    def _get_connection(self):
        """Context manager for database connections with retry on lock"""
        conn = None
        max_retries = 3
        
        for attempt in range(max_retries):
            try:
                conn = sqlite3.connect(
                    self.db_path,
                    timeout=self.timeout,
                    check_same_thread=False
                )
                conn.row_factory = sqlite3.Row
                conn.execute("PRAGMA busy_timeout=30000")
                conn.execute("PRAGMA foreign_keys=ON")
                break
            except sqlite3.OperationalError as e:
                # [R3-09] PRAGMA失败时关闭已创建的连接，防止泄漏
                if conn is not None:
                    try:
                        conn.close()
                    except Exception:
                        pass
                    conn = None
                if attempt < max_retries - 1:
                    time.sleep(0.1 * (attempt + 1))
                else:
                    raise
        
        try:
            yield conn
            conn.commit()
        except Exception as e:
            if conn is not None:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()
    
    def _init_database(self):
        """Initialize database with schema"""
        with self._get_connection() as conn:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA busy_timeout=30000")
            conn.execute("PRAGMA foreign_keys=ON")
            conn.executescript(SCHEMA_SQL)
    
    def compute_file_hash(self, content: str) -> str:
        """Compute SHA256 hash of file content (returns hex string for consistent storage)"""
        return hashlib.sha256(content.encode('utf-8')).hexdigest()
    
    def file_exists_and_unchanged(self, file_path: str, content: str) -> Tuple[bool, Optional[str]]:
        """Check if file exists with same hash"""
        file_path = self._normalize_path(file_path)
        new_hash = self.compute_file_hash(content)
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT hash FROM files WHERE path = ?",
                (file_path,)
            )
            row = cursor.fetchone()
            
            if row is None:
                return False, new_hash
            
            stored_hash = row['hash']
            return stored_hash == new_hash, new_hash
    
    def add_or_update_file(self, file_path: str, content: str) -> int:
        """Add new file or update existing file"""
        file_path = self._normalize_path(file_path)
        file_hash = self.compute_file_hash(content)
        
        with self._get_connection() as conn:
            # Try to update existing
            cursor = conn.execute(
                """UPDATE files SET hash = ?, modified = strftime('%s', 'now'), 
                   updated_at = CURRENT_TIMESTAMP WHERE path = ? RETURNING id""",
                (file_hash, file_path)
            )
            row = cursor.fetchone()
            
            if row:
                return row['id']
            
            # Insert new
            cursor = conn.execute(
                """INSERT INTO files (path, hash, modified) 
                   VALUES (?, ?, strftime('%s', 'now')) RETURNING id""",
                (file_path, file_hash)
            )
            return cursor.fetchone()['id']
    
    def delete_file_data(self, file_path: str) -> bool:
        """Delete all data associated with a file (cascade delete handles related tables)"""
        file_path = self._normalize_path(file_path)
        with self._get_connection() as conn:
            cursor = conn.execute("DELETE FROM files WHERE path = ?", (file_path,))
            return cursor.rowcount > 0
    
    def get_file_id(self, file_path: str) -> Optional[int]:
        """Get file ID by path"""
        file_path = self._normalize_path(file_path)
        with self._get_connection() as conn:
            cursor = conn.execute(
                "SELECT id FROM files WHERE path = ?",
                (file_path,)
            )
            row = cursor.fetchone()
            return row['id'] if row else None
    
    def add_entity(self, file_id: int, name: str, entity_type: str,
                   signature: str = None, docstring: str = None,
                   start_line: int = None, end_line: int = None) -> int:
        """Add a code entity"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO entities 
                   (file_id, name, type, signature, docstring, start_line, end_line)
                   VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                (file_id, name, entity_type, signature, docstring, start_line, end_line)
            )
            return cursor.fetchone()['id']
    
    def add_entities_batch(self, file_id: int, entities: List[Dict]) -> List[int]:
        """Add multiple entities in a single transaction"""
        if not entities:
            return []

        entity_ids = []
        with self._get_connection() as conn:
            for entity in entities:
                cursor = conn.execute(
                    """INSERT INTO entities
                       (file_id, name, type, signature, docstring, start_line, end_line)
                       VALUES (?, ?, ?, ?, ?, ?, ?) RETURNING id""",
                    (file_id, entity['name'], entity['type'],
                     entity.get('signature'), entity.get('docstring'),
                     entity.get('start_line'), entity.get('end_line'))
                )
                entity_ids.append(cursor.fetchone()['id'])
        return entity_ids
    
    def add_relationships_batch(self, source_file_id: int, relationships: List[Dict],
                                 entity_name_to_id: Dict[str, int],
                                 target_file_id: Optional[int] = None) -> int:
        """Add multiple relationships in a single transaction (optimized)"""
        if not relationships:
            return 0
        
        count = 0
        with self._get_connection() as conn:
            for rel in relationships:
                # 先尝试精确匹配，再尝试复合键前缀匹配
                source_id = entity_name_to_id.get(rel['source'])
                if source_id is None:
                    for key, eid in entity_name_to_id.items():
                        if key == rel['source'] or key.startswith(f"{rel['source']}_"):
                            source_id = eid
                            break
                target_id = entity_name_to_id.get(rel['target'])
                if target_id is None:
                    for key, eid in entity_name_to_id.items():
                        if key == rel['target'] or key.startswith(f"{rel['target']}_"):
                            target_id = eid
                            break
                
                if source_id is None or target_id is None:
                    continue
                
                conn.execute(
                    """INSERT INTO relationships 
                       (source_file_id, source_entity_id, target_entity_id, target_file_id, relation_type)
                       VALUES (?, ?, ?, ?, ?)""",
                    (source_file_id, source_id, target_id, target_file_id, rel['type'])
                )
                count += 1
        return count
    
    def add_relationship(self, source_file_id: int, relation_type: str,
                        source_entity_id: int = None, target_entity_id: int = None,
                        target_file_id: int = None) -> int:
        """Add a relationship between entities"""
        # [R3-12] 验证必要参数，防止NULL entity_id产生无法清理的垃圾数据
        if source_entity_id is None or target_entity_id is None:
            raise ValueError("source_entity_id and target_entity_id must not be None")
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO relationships
                   (source_file_id, source_entity_id, target_entity_id, target_file_id, relation_type)
                   VALUES (?, ?, ?, ?, ?) RETURNING id""",
                (source_file_id, source_entity_id, target_entity_id, target_file_id, relation_type)
            )
            return cursor.fetchone()['id']
    
    def add_import(self, file_id: int, module_path: str,
                   imported_names: List[str] = None, is_default: bool = False) -> int:
        """Add an import statement"""
        names_json = json.dumps(imported_names) if imported_names else None
        
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT INTO imports (file_id, module_path, imported_names, is_default_import)
                   VALUES (?, ?, ?, ?) RETURNING id""",
                (file_id, module_path, names_json, is_default)
            )
            return cursor.fetchone()['id']
    
    def add_file_dependency(self, source_file_id: int, target_file_id: int) -> Optional[int]:
        """Add a file-level dependency"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """INSERT OR IGNORE INTO file_dependencies (source_file_id, target_file_id)
                   VALUES (?, ?) RETURNING id""",
                (source_file_id, target_file_id)
            )
            result = cursor.fetchone()
            return result['id'] if result else None
    
    def log_event(self, event_type: str, file_path: str = None,
                  details: str = None, duration_ms: int = None):
        """Log a system event"""
        with self._get_connection() as conn:
            conn.execute(
                """INSERT INTO events (event_type, file_path, details, duration_ms)
                   VALUES (?, ?, ?, ?)""",
                (event_type, file_path, details, duration_ms)
            )
    
    def get_all_files(self, limit: int = 5000) -> List[Dict]:
        """Get all tracked files"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT id, path, modified, created_at, updated_at FROM files ORDER BY path LIMIT ?""",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_file_entities(self, file_id: int) -> List[Dict]:
        """Get all entities for a file"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT id, name, type, signature, start_line, end_line 
                   FROM entities WHERE file_id = ? ORDER BY start_line""",
                (file_id,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_entities(self, limit: int = 1000) -> List[Dict]:
        """Get all entities with file info"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT e.id, e.name, e.type, e.signature, e.start_line, e.end_line,
                          f.path as file_path, f.id as file_id
                   FROM entities e JOIN files f ON e.file_id = f.id
                   LIMIT ?""",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_all_relationships(self, limit: int = 2000) -> List[Dict]:
        """Get all relationships with entity and file info"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT r.id, r.relation_type,
                          r.source_entity_id, se.name as source_name,
                          r.target_entity_id, te.name as target_name,
                          sf.path as source_file, tf.path as target_file
                   FROM relationships r
                   LEFT JOIN entities se ON r.source_entity_id = se.id
                   LEFT JOIN entities te ON r.target_entity_id = te.id
                   JOIN files sf ON r.source_file_id = sf.id
                   LEFT JOIN files tf ON r.target_file_id = tf.id
                   LIMIT ?""",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_file_dependencies(self, limit: int = 5000) -> List[Dict]:
        """Get all file dependencies"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT fd.id, sf.path as source_file, tf.path as target_file
                   FROM file_dependencies fd
                   JOIN files sf ON fd.source_file_id = sf.id
                   JOIN files tf ON fd.target_file_id = tf.id LIMIT ?""",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_statistics(self) -> Dict:
        """Get database statistics"""
        with self._get_connection() as conn:
            stats = {}
            
            cursor = conn.execute("SELECT COUNT(*) as count FROM files")
            stats['total_files'] = cursor.fetchone()['count']
            
            cursor = conn.execute("SELECT COUNT(*) as count FROM entities")
            stats['total_entities'] = cursor.fetchone()['count']
            
            cursor = conn.execute("SELECT COUNT(*) as count FROM relationships")
            stats['total_relationships'] = cursor.fetchone()['count']
            
            cursor = conn.execute("SELECT COUNT(*) as count FROM imports")
            stats['total_imports'] = cursor.fetchone()['count']
            
            cursor = conn.execute("""
                SELECT type, COUNT(*) as count FROM entities GROUP BY type
            """)
            stats['entities_by_type'] = {row['type']: row['count'] for row in cursor.fetchall()}
            
            cursor = conn.execute("""
                SELECT relation_type, COUNT(*) as count FROM relationships GROUP BY relation_type
            """)
            stats['relations_by_type'] = {row['relation_type']: row['count'] for row in cursor.fetchall()}
            
            cursor = conn.execute("""
                SELECT event_type, COUNT(*) as count FROM events 
                WHERE created_at > datetime('now', '-1 day')
                GROUP BY event_type
            """)
            stats['recent_events'] = {row['event_type']: row['count'] for row in cursor.fetchall()}
            
            return stats
    
    def get_recent_events(self, limit: int = 50) -> List[Dict]:
        """Get recent system events"""
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT event_type, file_path, details, duration_ms, created_at
                   FROM events ORDER BY created_at DESC LIMIT ?""",
                (limit,)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def search_entities(self, query: str, limit: int = 20) -> List[Dict]:
        """Search entities by name"""
        # 防止空查询返回全部结果
        if not query or not query.strip():
            return []
        escaped = query.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_')
        with self._get_connection() as conn:
            cursor = conn.execute(
                """SELECT e.id, e.name, e.type, e.signature, f.path as file_path
                   FROM entities e JOIN files f ON e.file_id = f.id
                   WHERE e.name LIKE ? ESCAPE '\\' ORDER BY e.name LIMIT ?""",
                (f'%{escaped}%', limit)
            )
            return [dict(row) for row in cursor.fetchall()]
    
    def get_entity_details(self, entity_id: int) -> Optional[Dict]:
        """Get detailed information about an entity including relationships"""
        with self._get_connection() as conn:
            # Get entity info
            cursor = conn.execute(
                """SELECT e.*, f.path as file_path FROM entities e
                   JOIN files f ON e.file_id = f.id WHERE e.id = ?""",
                (entity_id,)
            )
            entity = cursor.fetchone()
            
            if not entity:
                return None
            
            result = dict(entity)
            
            # Get outgoing relationships
            cursor = conn.execute(
                """SELECT r.*, te.name as target_name, tf.path as target_file
                   FROM relationships r
                   LEFT JOIN entities te ON r.target_entity_id = te.id
                   LEFT JOIN files tf ON r.target_file_id = tf.id
                   WHERE r.source_entity_id = ?""",
                (entity_id,)
            )
            result['outgoing_relations'] = [dict(row) for row in cursor.fetchall()]
            
            # Get incoming relationships
            cursor = conn.execute(
                """SELECT r.*, se.name as source_name, sf.path as source_file
                   FROM relationships r
                   LEFT JOIN entities se ON r.source_entity_id = se.id
                   LEFT JOIN files sf ON r.source_file_id = sf.id
                   WHERE r.target_entity_id = ?""",
                (entity_id,)
            )
            result['incoming_relations'] = [dict(row) for row in cursor.fetchall()]
            
            return result
    
    def close(self):
        """Close database resources and clean up"""
        # [MEDIUM#24] 清理过期事件（保留最近7天）
        try:
            with self._get_connection() as conn:
                conn.execute("DELETE FROM events WHERE created_at < datetime('now', '-7 days')")
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        except Exception as e:
            print(f"[WARN] WAL checkpoint failed during close: {e}")
    
    def update_file_transaction(self, file_path: str, content: str,
                                 parse_result=None, callback=None) -> bool:
        """Execute delete + insert in a single connection for atomicity.
        
        [CRITICAL#2] 将 hash 检查合并到事务内，消除 TOCTOU 竞态条件。
        Returns True if file was actually updated, False if unchanged.
        """
        file_path = self._normalize_path(file_path)
        file_hash = self.compute_file_hash(content)
        with self._get_connection() as conn:
            # [CRITICAL#2] 在同一事务中检查 hash，避免竞态
            cursor = conn.execute("SELECT hash FROM files WHERE path = ?", (file_path,))
            row = cursor.fetchone()
            if row:
                stored_hash = row['hash']
                if stored_hash == file_hash:
                    return False  # 文件未变化，跳过

            # 先删除关联数据（entities/relationships/imports 通过 CASCADE 自动删除）
            conn.execute("DELETE FROM files WHERE path = ?", (file_path,))

            # Add/update the file (DELETE已保证唯一性，使用INSERT即可)
            cursor = conn.execute(
                """INSERT INTO files (path, hash, modified)
                   VALUES (?, ?, strftime('%s', 'now')) RETURNING id""",
                (file_path, file_hash)
            )
            file_id = cursor.fetchone()['id']

            # Execute callback if provided
            if callback:
                callback(conn, file_id, parse_result)
            return True
