"""
System Test Script V3 - Using Mock Parser for comprehensive testing
"""
import os
import sys
import time
import random
import shutil
import threading
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser
from file_watcher import FileWatcher


class SystemTester:
    """Comprehensive system tester"""
    
    def __init__(self, test_dir: str, db_path: str):
        self.test_dir = test_dir
        self.db_path = db_path
        self.db = KnowledgeGraphDB(db_path)
        self.parser = MockArkTSParser(simulate_errors=True, error_rate=0.02)
        self.issues_found = []
        self.stats = {
            'files_created': 0,
            'files_modified': 0,
            'files_deleted': 0,
            'parse_errors': 0,
            'db_errors': 0,
            'entities_extracted': 0,
            'relations_extracted': 0
        }
        self.lock = threading.Lock()
    
    def generate_complex_code(self, filename: str, complexity: str = 'medium') -> str:
        """Generate complex ArkTS code with various patterns"""
        base_name = filename.replace('.ets', '')
        class_name = ''.join([p.capitalize() for p in base_name.split('_')])
        
        if complexity == 'simple':
            return f"""export class {class_name} {{
    private value: number;
    constructor(value: number) {{ this.value = value; }}
    getValue(): number {{ return this.value; }}
}}"""
        
        elif complexity == 'medium':
            return f"""import {{ LoggerUtility }} from '../utils/LoggerUtility';
import {{ ApiResponse }} from '../types/CommonTypes';

export interface I{class_name} {{
    id: string;
    name: string;
}}

export class {class_name}Service {{
    private items: Map<string, I{class_name}> = new Map();
    private logger: LoggerUtility;
    
    constructor(logger: LoggerUtility) {{
        this.logger = logger;
    }}
    
    async create(data: Omit<I{class_name}, 'id'>): Promise<ApiResponse<I{class_name}>> {{
        const id = Math.random().toString(36);
        const item = {{ ...data, id }};
        this.items.set(id, item);
        this.logger.info(`Created ${{id}}`);
        return {{ success: true, data: item }};
    }}
    
    get(id: string): I{class_name} | undefined {{
        return this.items.get(id);
    }}
    
    update(id: string, updates: Partial<I{class_name}>): boolean {{
        const item = this.items.get(id);
        if (!item) return false;
        Object.assign(item, updates);
        return true;
    }}
    
    delete(id: string): boolean {{
        return this.items.delete(id);
    }}
}}"""
        
        else:  # complex
            return f"""import {{ ApiResponse, PaginatedResponse, PaginationParams }} from '../types/CommonTypes';
import {{ LoggerUtility }} from '../utils/LoggerUtility';
import {{ EncryptionUtility }} from '../utils/EncryptionUtility';
import {{ EventEmitter }} from '../utils/EventEmitter';

export interface {class_name}Entity {{
    id: string;
    name: string;
    description?: string;
    metadata: Record<string, any>;
    createdAt: number;
    updatedAt: number;
    version: number;
}}

export interface {class_name}Config {{
    enabled: boolean;
    maxItems: number;
    timeout: number;
    retryCount: number;
}}

export class {class_name}Manager extends EventEmitter {{
    private entities: Map<string, {class_name}Entity> = new Map();
    private config: {class_name}Config;
    private logger: LoggerUtility;
    private encryption: EncryptionUtility;
    
    constructor(config: Partial<{class_name}Config>, logger: LoggerUtility, encryption: EncryptionUtility) {{
        super();
        this.config = {{
            enabled: true,
            maxItems: 1000,
            timeout: 5000,
            retryCount: 3,
            ...config
        }};
        this.logger = logger;
        this.encryption = encryption;
    }}
    
    async create(data: Omit<{class_name}Entity, 'id' | 'createdAt' | 'updatedAt' | 'version'>): Promise<ApiResponse<{class_name}Entity>> {{
        if (!this.config.enabled) {{
            return {{ success: false, error: {{ code: 'DISABLED', message: 'Service disabled' }} }};
        }}
        
        if (this.entities.size >= this.config.maxItems) {{
            return {{ success: false, error: {{ code: 'LIMIT_REACHED', message: 'Max items reached' }} }};
        }}
        
        const id = this.encryption.generateRandomId();
        const now = Date.now();
        
        const entity: {class_name}Entity = {{
            id,
            ...data,
            createdAt: now,
            updatedAt: now,
            version: 1
        }};
        
        this.entities.set(id, entity);
        this.emit('created', entity);
        this.logger.info(`Entity created: ${{id}}`);
        
        return {{ success: true, data: entity }};
    }}
    
    async list(params: PaginationParams): Promise<PaginatedResponse<{class_name}Entity>> {{
        const items = Array.from(this.entities.values());
        const total = items.length;
        const offset = (params.page - 1) * params.pageSize;
        const data = items.slice(offset, offset + params.pageSize);
        
        return {{
            data,
            total,
            page: params.page,
            pageSize: params.pageSize,
            hasMore: offset + data.length < total
        }};
    }}
    
    async getById(id: string): Promise<ApiResponse<{class_name}Entity>> {{
        const entity = this.entities.get(id);
        if (!entity) {{
            return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
        }}
        return {{ success: true, data: entity }};
    }}
    
    async update(id: string, updates: Partial<Omit<{class_name}Entity, 'id' | 'createdAt'>>): Promise<ApiResponse<{class_name}Entity>> {{
        const entity = this.entities.get(id);
        if (!entity) {{
            return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
        }}
        
        Object.assign(entity, updates, {{ updatedAt: Date.now(), version: entity.version + 1 }});
        this.emit('updated', entity);
        
        return {{ success: true, data: entity }};
    }}
    
    async delete(id: string): Promise<ApiResponse<void>> {{
        const existed = this.entities.delete(id);
        if (existed) {{
            this.emit('deleted', {{ id }});
            return {{ success: true }};
        }}
        return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
    }}
    
    getStats(): {{ count: number; config: {class_name}Config }} {{
        return {{
            count: this.entities.size,
            config: this.config
        }};
    }}
}}"""
    
    def process_file(self, filepath: str, content: str):
        """Process a file through the system"""
        try:
            # Check if file changed
            unchanged, new_hash = self.db.file_exists_and_unchanged(filepath, content)
            if unchanged:
                return
            
            # Delete old data
            self.db.delete_file_data(filepath)
            
            # Parse with mock parser
            parse_result = self.parser.parse_file(filepath, content)
            
            # Add file
            file_id = self.db.add_or_update_file(filepath, content)
            
            # Add entities
            entity_name_to_id = {}
            for entity in parse_result.get('entities', []):
                entity_id = self.db.add_entity(
                    file_id=file_id,
                    name=entity['name'],
                    entity_type=entity['type'],
                    signature=entity.get('signature'),
                    docstring=entity.get('docstring'),
                    start_line=entity.get('start_line'),
                    end_line=entity.get('end_line')
                )
                entity_name_to_id[entity['name']] = entity_id
                self.stats['entities_extracted'] += 1
            
            # Add relationships
            for rel in parse_result.get('relationships', []):
                source_id = entity_name_to_id.get(rel['source'])
                target_id = entity_name_to_id.get(rel['target'])
                
                self.db.add_relationship(
                    source_file_id=file_id,
                    relation_type=rel['type'],
                    source_entity_id=source_id,
                    target_entity_id=target_id
                )
                self.stats['relations_extracted'] += 1
            
            # Add imports
            for imp in parse_result.get('imports', []):
                self.db.add_import(
                    file_id=file_id,
                    module_path=imp['module'],
                    imported_names=imp.get('names', []),
                    is_default=imp.get('is_default', False)
                )
            
            # Log event
            self.db.log_event(
                event_type='FILE_UPDATED',
                file_path=filepath,
                details=f"Entities: {len(parse_result.get('entities', []))}, Relations: {len(parse_result.get('relationships', []))}",
                duration_ms=random.randint(10, 100)
            )
            
        except Exception as e:
            with self.lock:
                self.issues_found.append(f"Error processing {filepath}: {e}")
                self.stats['parse_errors'] += 1
    
    def create_file(self, filename: str, complexity: str = 'medium') -> str:
        """Create a new test file"""
        filepath = os.path.join(self.test_dir, filename)
        content = self.generate_complex_code(filename, complexity)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            self.stats['files_created'] += 1
            return filepath
        except Exception as e:
            self.issues_found.append(f"Failed to create file {filename}: {e}")
            return None
    
    def modify_file(self, filepath: str, complexity: str = 'medium') -> bool:
        """Modify an existing file"""
        try:
            filename = os.path.basename(filepath)
            content = self.generate_complex_code(filename, complexity)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            self.stats['files_modified'] += 1
            return True
        except Exception as e:
            self.issues_found.append(f"Failed to modify file {filepath}: {e}")
            return False
    
    def delete_file(self, filepath: str) -> bool:
        """Delete a file"""
        try:
            self.db.delete_file_data(filepath)
            os.remove(filepath)
            self.stats['files_deleted'] += 1
            return True
        except Exception as e:
            self.issues_found.append(f"Failed to delete file {filepath}: {e}")
            return False
    
    def run_comprehensive_test(self, duration_seconds: int = 60):
        """Run comprehensive stress test"""
        print(f"Starting comprehensive test for {duration_seconds} seconds...")
        print(f"Test directory: {self.test_dir}")
        print(f"Database: {self.db_path}")
        print("-" * 60)
        
        start_time = time.time()
        files = []
        
        # Phase 1: Create initial complex files
        print("Phase 1: Creating initial complex files...")
        complexities = ['simple', 'medium', 'complex', 'medium', 'complex']
        for i, complexity in enumerate(complexities):
            filename = f"TestModule{i}_{random.randint(1000, 9999)}.ets"
            filepath = self.create_file(filename, complexity)
            if filepath:
                files.append(filepath)
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.process_file(filepath, content)
        
        print(f"Initial files created and processed: {len(files)}")
        
        # Phase 2: Random operations with varying complexity
        print("Phase 2: Random operations...")
        operations = 0
        
        while time.time() - start_time < duration_seconds:
            operation = random.choices(
                ['create', 'modify', 'delete', 'batch_create'],
                weights=[30, 40, 15, 15]
            )[0]
            
            try:
                if operation == 'create':
                    complexity = random.choice(['simple', 'medium', 'complex'])
                    filename = f"DynamicModule{operations}_{random.randint(1000, 9999)}.ets"
                    filepath = self.create_file(filename, complexity)
                    if filepath:
                        files.append(filepath)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.process_file(filepath, content)
                
                elif operation == 'modify' and files:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        complexity = random.choice(['simple', 'medium', 'complex'])
                        self.modify_file(filepath, complexity)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.process_file(filepath, content)
                
                elif operation == 'delete' and len(files) > 3:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        self.delete_file(filepath)
                        files.remove(filepath)
                
                elif operation == 'batch_create':
                    # Create multiple files at once
                    for _ in range(random.randint(2, 4)):
                        filename = f"BatchModule{operations}_{random.randint(1000, 9999)}.ets"
                        filepath = self.create_file(filename, 'medium')
                        if filepath:
                            files.append(filepath)
                            with open(filepath, 'r', encoding='utf-8') as f:
                                content = f.read()
                            self.process_file(filepath, content)
                
                operations += 1
                
                # Print progress
                if operations % 10 == 0:
                    elapsed = time.time() - start_time
                    db_stats = self.db.get_statistics()
                    print(f"  Ops: {operations}, Time: {elapsed:.1f}s, "
                          f"Files: {len(files)}, Entities: {db_stats.get('total_entities', 0)}, "
                          f"Issues: {len(self.issues_found)}")
                
                time.sleep(0.1)
                
            except Exception as e:
                self.issues_found.append(f"Operation error: {e}")
                self.stats['db_errors'] += 1
        
        print("-" * 60)
        print("Comprehensive test completed!")
        self.print_report()
    
    def print_report(self):
        """Print test report"""
        print("\n" + "=" * 60)
        print("COMPREHENSIVE TEST REPORT")
        print("=" * 60)
        
        print("\nOperation Statistics:")
        for key, value in self.stats.items():
            print(f"  {key}: {value}")
        
        # Get DB stats
        try:
            db_stats = self.db.get_statistics()
            print("\nDatabase Statistics:")
            for key, value in db_stats.items():
                if isinstance(value, (int, str, float, bool)):
                    print(f"  {key}: {value}")
                elif isinstance(value, dict):
                    print(f"  {key}:")
                    for k, v in value.items():
                        print(f"    {k}: {v}")
        except Exception as e:
            print(f"\nError getting DB stats: {e}")
        
        print(f"\nTotal Issues Found: {len(self.issues_found)}")
        if self.issues_found:
            print("\nDetailed Issues:")
            for i, issue in enumerate(self.issues_found[:30], 1):
                print(f"  {i}. {issue}")
            if len(self.issues_found) > 30:
                print(f"  ... and {len(self.issues_found) - 30} more issues")
        
        print("=" * 60)


def main():
    test_dir = '/workspace/arkts_knowledge_graph/test_complex_project/stress_test'
    db_path = '/workspace/arkts_knowledge_graph/test_comprehensive.db'
    
    # Cleanup
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir, exist_ok=True)
    
    if os.path.exists(db_path):
        os.remove(db_path)
    
    tester = SystemTester(test_dir, db_path)
    tester.run_comprehensive_test(duration_seconds=60)


if __name__ == '__main__':
    main()
