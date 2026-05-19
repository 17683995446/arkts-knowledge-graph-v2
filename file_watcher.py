"""
File Watcher Module
Monitors ArkTS files for changes with debouncing
"""
import os
import time
import threading
from pathlib import Path
from typing import Callable, Set, Dict
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler, FileModifiedEvent, FileCreatedEvent, FileDeletedEvent
from config import WATCH_DELAY_SECONDS, SUPPORTED_EXTENSIONS, MIN_FILE_WORDS


class ArkTSFileHandler(FileSystemEventHandler):
    """Handle file system events for ArkTS files"""
    
    def __init__(self, callback: Callable, debounce_seconds: float = 1.0):
        self.callback = callback
        self.debounce_seconds = debounce_seconds
        self.pending_files: Dict[str, threading.Timer] = {}
        self.lock = threading.Lock()
    
    def _is_valid_file(self, file_path: str) -> bool:
        """Check if file should be tracked"""
        path = Path(file_path)
        
        # Check extension
        if path.suffix not in SUPPORTED_EXTENSIONS:
            return False
        
        # Check filename length (at least 4 words)
        filename = path.stem
        words = self._extract_words(filename)
        if len(words) < MIN_FILE_WORDS:
            return False
        
        return True
    
    def _extract_words(self, text: str) -> list:
        """Extract words from camelCase or snake_case filename"""
        import re
        words = []
        # 先按下划线分割
        parts = text.split('_')
        for part in parts:
            if not part:
                continue
            # 对每个部分进行 camelCase 拆分
            camel_words = re.findall(r'[A-Z]?[a-z]+|[A-Z]+(?=[A-Z]|$)', part)
            words.extend(camel_words)
        # 去重并过滤短词
        seen = set()
        result = []
        for w in words:
            if len(w) > 1 and w not in seen:
                seen.add(w)
                result.append(w)
        return result
    
    def _debounced_callback(self, file_path: str):
        """Execute callback after debounce period"""
        file_path = os.path.normpath(file_path)
        with self.lock:
            if file_path in self.pending_files:
                del self.pending_files[file_path]

        # [MEDIUM#13] 检查文件是否仍然存在，防止处理已删除的文件
        if not os.path.exists(file_path):
            return

        try:
            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                content = f.read()
            self.callback(file_path, content)
        except UnicodeDecodeError as e:
            print(f"[WARN] Skipping non-UTF-8 file: {file_path} ({e})")
        except Exception as e:
            print(f"[ERROR] Error reading file {file_path}: {e}")
    
    def _schedule_update(self, file_path: str):
        """Schedule a debounced update for a file"""
        if not self._is_valid_file(file_path):
            return
        
        with self.lock:
            # Cancel existing timer for this file
            if file_path in self.pending_files:
                self.pending_files[file_path].cancel()
            
            # Create new timer
            timer = threading.Timer(
                self.debounce_seconds,
                self._debounced_callback,
                args=[file_path]
            )
            self.pending_files[file_path] = timer
            timer.start()
    
    def on_modified(self, event):
        if isinstance(event, FileModifiedEvent) and not event.is_directory:
            self._schedule_update(os.path.normpath(event.src_path))

    def on_created(self, event):
        if isinstance(event, FileCreatedEvent) and not event.is_directory:
            self._schedule_update(os.path.normpath(event.src_path))

    def on_deleted(self, event):
        if isinstance(event, FileDeletedEvent) and not event.is_directory:
            file_path = os.path.normpath(event.src_path)
            # [MEDIUM#18] 删除事件不检查 _is_valid_file，因为文件可能已被重命名
            # 只检查扩展名是否为支持的类型
            if Path(file_path).suffix in SUPPORTED_EXTENSIONS:
                try:
                    self.callback(file_path, None)  # content=None 表示删除
                except Exception as e:
                    print(f"Error handling file deletion {file_path}: {e}")


class FileWatcher:
    """Watch directories for ArkTS file changes"""
    
    def __init__(self, callback: Callable, watch_paths: list = None):
        self.callback = callback
        self.watch_paths = watch_paths or [os.getcwd()]
        self.observer = Observer()
        self.handler = ArkTSFileHandler(callback, WATCH_DELAY_SECONDS)
        self.is_running = False
    
    def start(self):
        """Start watching files"""
        if self.is_running:
            return
        
        for path in self.watch_paths:
            if os.path.exists(path):
                self.observer.schedule(self.handler, path, recursive=True)
                print(f"Watching: {path}")
        
        self.observer.start()
        self.is_running = True
        print(f"File watcher started (debounce: {WATCH_DELAY_SECONDS}s)")
    
    def stop(self):
        """Stop watching files"""
        if not self.is_running:
            return
        
        # 取消所有 pending 的 timers
        with self.handler.lock:
            for file_path, timer in list(self.handler.pending_files.items()):
                timer.cancel()
            self.handler.pending_files.clear()
        
        self.observer.stop()
        self.observer.join()
        self.is_running = False
        print("File watcher stopped")
    
    def scan_existing_files(self) -> list:
        """Scan for existing ArkTS files in watch paths"""
        files = []
        
        for watch_path in self.watch_paths:
            if not os.path.exists(watch_path):
                continue
            
            for root, _, filenames in os.walk(watch_path):
                for filename in filenames:
                    file_path = os.path.normpath(os.path.join(root, filename))
                    if self.handler._is_valid_file(file_path):
                        try:
                            with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
                                content = f.read()
                            files.append({
                                'path': file_path,
                                'content': content
                            })
                        except Exception as e:
                            print(f"Error reading {file_path}: {e}")
        
        return files
