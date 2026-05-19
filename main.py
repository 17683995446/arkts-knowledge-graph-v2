"""
ArkTS Knowledge Graph - Main Entry Point
Dynamic incremental code knowledge graph system
"""
import os
import sys
import argparse
import time
import signal
from threading import Thread

from config import DATABASE_PATH, WEB_HOST, WEB_PORT
from knowledge_graph_service import KnowledgeGraphService
from web_server import run_server


class KnowledgeGraphSystem:
    """Main system orchestrator"""
    
    def __init__(self, watch_paths: list = None, db_path: str = None, host: str = None, port: int = None):
        self.watch_paths = watch_paths or [os.getcwd()]
        self.db_path = db_path or DATABASE_PATH
        self.host = host or WEB_HOST
        self.port = port or WEB_PORT
        self.service = None
        self.running = False
    
    def start(self):
        """Start the knowledge graph system"""
        print("=" * 60)
        print("🧠 ArkTS Knowledge Graph System")
        print("=" * 60)
        print(f"📁 Database: {self.db_path}")
        print(f"👀 Watch paths: {self.watch_paths}")
        print(f"🌐 Web UI: http://{self.host}:{self.port}")
        print("=" * 60)
        
        # Initialize service
        self.service = KnowledgeGraphService(
            db_path=self.db_path,
            watch_paths=self.watch_paths
        )
        
        # Perform initial scan
        self.service.initial_scan()
        
        # Start file watcher
        self.service.start()
        self.running = True
        
        # Setup signal handlers
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)
        
        # Start web server in main thread
        try:
            run_server(self.service, self.host, self.port)
        except KeyboardInterrupt:
            self.stop()
    
    def stop(self):
        """Stop the system gracefully"""
        print("\n🛑 Shutting down...")
        self.running = False

        if self.service:
            self.service.close()  # [HIGH#8] 调用 close() 而非 stop()，确保 DB 正确关闭

        print("✅ System stopped")
    
    def _signal_handler(self, signum, frame):
        """Handle shutdown signals"""
        self.stop()
        sys.exit(0)


def main():
    parser = argparse.ArgumentParser(
        description='ArkTS Knowledge Graph - Dynamic incremental code analysis'
    )
    parser.add_argument(
        '--watch', '-w',
        nargs='+',
        help='Directories to watch for ArkTS files'
    )
    parser.add_argument(
        '--db', '-d',
        default='knowledge_graph.db',
        help='Path to SQLite database file'
    )
    parser.add_argument(
        '--host',
        default=WEB_HOST,
        help='Web server host'
    )
    parser.add_argument(
        '--port', '-p',
        type=int,
        default=WEB_PORT,
        help='Web server port'
    )
    
    args = parser.parse_args()
    
    # Determine watch paths
    watch_paths = args.watch
    if not watch_paths:
        # Default to current directory
        watch_paths = [os.getcwd()]
    
    # Make paths absolute
    watch_paths = [os.path.abspath(p) for p in watch_paths]
    
    # Create and start system
    system = KnowledgeGraphSystem(
        watch_paths=watch_paths,
        db_path=args.db,
        host=args.host,
        port=args.port
    )
    
    system.start()


if __name__ == '__main__':
    main()
