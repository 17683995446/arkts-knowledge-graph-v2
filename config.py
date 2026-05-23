"""
Configuration Module - ArkTS Knowledge Graph System
"""
import os

# LLM Configuration
LLM_API_KEY = os.environ.get('LLM_API_KEY', '')
LLM_BASE_URL = "https://api.siliconflow.cn/v1"
LLM_MODEL = "Qwen/Qwen2.5-Coder-32B-Instruct"

# File Monitoring Configuration
WATCH_DELAY_SECONDS = 1.0  # Wait 1 second after last change before updating
SUPPORTED_EXTENSIONS = {'.ets', '.ts'}  # ArkTS file extensions
MIN_FILE_WORDS = 4  # Minimum 4 words in file name
MAX_FILE_SIZE = 10 * 1024 * 1024  # [HIGH-4] 最大文件大小 10MB，防止OOM

# Database Configuration
DATABASE_PATH = "knowledge_graph.db"

# Web Server Configuration
WEB_HOST = "0.0.0.0"
WEB_PORT = 5000
WEB_DEBUG = os.environ.get('WEB_DEBUG', 'false').lower() == 'true'

# Paths Configuration
PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
TEMPLATES_DIR = os.path.join(PROJECT_ROOT, "templates")
STATIC_DIR = os.path.join(PROJECT_ROOT, "static")

# Visualization Configuration
MAX_NODES_DISPLAY = 200
GRAPH_LAYOUT = "force-directed"
