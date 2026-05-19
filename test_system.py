"""
System Test Script - Long running test to discover issues
"""
import os
import sys
import time
import random
import string
import threading
from pathlib import Path

# Add project to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_service import KnowledgeGraphService


class SystemTester:
    """Test the knowledge graph system with complex scenarios"""
    
    def __init__(self, test_dir: str):
        self.test_dir = test_dir
        self.service = KnowledgeGraphService(
            db_path=':memory:',
            watch_paths=[test_dir]
        )
        self.issues_found = []
        self.stats = {
            'files_created': 0,
            'files_modified': 0,
            'files_deleted': 0,
            'parse_errors': 0,
            'db_errors': 0
        }
    
    def generate_random_code(self, filename: str) -> str:
        """Generate random ArkTS code with various complexity"""
        complexity = random.choice(['simple', 'medium', 'complex'])
        
        if complexity == 'simple':
            return self._generate_simple_code(filename)
        elif complexity == 'medium':
            return self._generate_medium_code(filename)
        else:
            return self._generate_complex_code(filename)
    
    def _generate_simple_code(self, filename: str) -> str:
        """Generate simple code"""
        class_name = ''.join([p.capitalize() for p in filename.replace('.ets', '').split('_')])
        return f"""/**
 * {filename}
 * Auto-generated test file
 */

export class {class_name} {{
    private value: number;
    
    constructor(value: number) {{
        this.value = value;
    }}
    
    getValue(): number {{
        return this.value;
    }}
    
    setValue(value: number): void {{
        this.value = value;
    }}
}}
"""
    
    def _generate_medium_code(self, filename: str) -> str:
        """Generate medium complexity code with imports and interfaces"""
        base_name = filename.replace('.ets', '')
        imports = []
        
        # Random imports
        if random.random() > 0.5:
            imports.append("import { BaseEntity } from '../types/CommonTypes';")
        if random.random() > 0.5:
            imports.append("import { LoggerUtility } from '../utils/LoggerUtility';")
        
        interface_name = f"I{base_name.capitalize()}"
        class_name = f"{base_name.capitalize()}Service"
        
        return f"""/**
 * {filename}
 * Auto-generated medium complexity test file
 */

{chr(10).join(imports)}

/**
 * Interface for {base_name}
 */
export interface {interface_name} {{
    id: string;
    name: string;
    createdAt: number;
}}

/**
 * Service for managing {base_name}
 */
export class {class_name} {{
    private items: Map<string, {interface_name}> = new Map();
    private logger?: LoggerUtility;
    
    constructor(logger?: LoggerUtility) {{
        this.logger = logger;
    }}
    
    /**
     * Add item
     */
    add(item: {interface_name}): void {{
        this.items.set(item.id, item);
        this.logger?.info(`Added ${{item.id}}`);
    }}
    
    /**
     * Get by ID
     */
    get(id: string): {interface_name} | undefined {{
        return this.items.get(id);
    }}
    
    /**
     * Get all items
     */
    getAll(): {interface_name}[] {{
        return Array.from(this.items.values());
    }}
    
    /**
     * Remove item
     */
    remove(id: string): boolean {{
        const existed = this.items.delete(id);
        if (existed) {{
            this.logger?.info(`Removed ${{id}}`);
        }}
        return existed;
    }}
    
    /**
     * Update item
     */
    update(id: string, updates: Partial<{interface_name}>): boolean {{
        const item = this.items.get(id);
        if (!item) return false;
        
        Object.assign(item, updates);
        this.logger?.info(`Updated ${{id}}`);
        return true;
    }}
}}
"""
    
    def _generate_complex_code(self, filename: str) -> str:
        """Generate complex code with multiple classes and relationships"""
        base_name = filename.replace('.ets', '')
        
        return f"""/**
 * {filename}
 * Auto-generated complex test file
 */

import {{ ApiResponse, PaginatedResponse, PaginationParams }} from '../types/CommonTypes';
import {{ LoggerUtility }} from '../utils/LoggerUtility';
import {{ EncryptionUtility }} from '../utils/EncryptionUtility';

/**
 * Entity interface
 */
export interface {base_name.capitalize()}Entity {{
    id: string;
    name: string;
    description?: string;
    metadata: Record<string, any>;
    createdAt: number;
    updatedAt: number;
    version: number;
}}

/**
 * Configuration options
 */
export interface {base_name.capitalize()}Config {{
    enabled: boolean;
    maxItems: number;
    timeout: number;
    retryCount: number;
    fallbackEnabled: boolean;
}}

/**
 * Main service class
 */
export class {base_name.capitalize()}ManagerService {{
    private entities: Map<string, {base_name.capitalize()}Entity> = new Map();
    private config: {base_name.capitalize()}Config;
    private logger: LoggerUtility;
    private encryption: EncryptionUtility;
    private eventHandlers: Map<string, Function[]> = new Map();
    
    constructor(
        config: Partial<{base_name.capitalize()}Config>,
        logger: LoggerUtility,
        encryption: EncryptionUtility
    ) {{
        this.config = {{
            enabled: true,
            maxItems: 1000,
            timeout: 5000,
            retryCount: 3,
            fallbackEnabled: false,
            ...config
        }};
        this.logger = logger;
        this.encryption = encryption;
    }}
    
    /**
     * Create new entity
     */
    async create(data: Omit<{base_name.capitalize()}Entity, 'id' | 'createdAt' | 'updatedAt' | 'version'>): Promise<ApiResponse<{base_name.capitalize()}Entity>> {{
        try {{
            if (!this.config.enabled) {{
                return {{ success: false, error: {{ code: 'DISABLED', message: 'Service disabled' }} }};
            }}
            
            if (this.entities.size >= this.config.maxItems) {{
                return {{ success: false, error: {{ code: 'LIMIT_REACHED', message: 'Max items reached' }} }};
            }}
            
            const id = this.encryption.generateRandomId();
            const now = Date.now();
            
            const entity: {base_name.capitalize()}Entity = {{
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
        }} catch (error) {{
            this.logger.error(`Create failed: ${{error.message}}`);
            return {{ success: false, error: {{ code: 'ERROR', message: error.message }} }};
        }}
    }}
    
    /**
     * Get paginated list
     */
    async list(params: PaginationParams): Promise<PaginatedResponse<{base_name.capitalize()}Entity>> {{
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
    
    /**
     * Get by ID
     */
    async getById(id: string): Promise<ApiResponse<{base_name.capitalize()}Entity>> {{
        const entity = this.entities.get(id);
        if (!entity) {{
            return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
        }}
        return {{ success: true, data: entity }};
    }}
    
    /**
     * Update entity
     */
    async update(id: string, updates: Partial<Omit<{base_name.capitalize()}Entity, 'id' | 'createdAt'>>): Promise<ApiResponse<{base_name.capitalize()}Entity>> {{
        const entity = this.entities.get(id);
        if (!entity) {{
            return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
        }}
        
        Object.assign(entity, updates, {{ updatedAt: Date.now(), version: entity.version + 1 }});
        this.emit('updated', entity);
        
        return {{ success: true, data: entity }};
    }}
    
    /**
     * Delete entity
     */
    async delete(id: string): Promise<ApiResponse<void>> {{
        const existed = this.entities.delete(id);
        if (existed) {{
            this.emit('deleted', {{ id }});
            return {{ success: true }};
        }}
        return {{ success: false, error: {{ code: 'NOT_FOUND', message: 'Entity not found' }} }};
    }}
    
    /**
     * Subscribe to events
     */
    on(event: string, handler: Function): void {{
        if (!this.eventHandlers.has(event)) {{
            this.eventHandlers.set(event, []);
        }}
        this.eventHandlers.get(event)!.push(handler);
    }}
    
    /**
     * Emit event
     */
    private emit(event: string, data: any): void {{
        const handlers = this.eventHandlers.get(event);
        if (handlers) {{
            handlers.forEach(h => {{
                try {{
                    h(data);
                }} catch (e) {{
                    this.logger.error(`Event handler error: ${{e.message}}`);
                }}
            }});
        }}
    }}
    
    /**
     * Get statistics
     */
    getStats(): {{ count: number; config: {base_name.capitalize()}Config }} {{
        return {{
            count: this.entities.size,
            config: this.config
        }};
    }}
}}

/**
 * Factory function
 */
export function create{base_name.capitalize()}Manager(
    config: Partial<{base_name.capitalize()}Config>,
    logger: LoggerUtility,
    encryption: EncryptionUtility
): {base_name.capitalize()}ManagerService {{
    return new {base_name.capitalize()}ManagerService(config, logger, encryption);
}}
"""
    
    def create_file(self, filename: str) -> str:
        """Create a new test file"""
        filepath = os.path.join(self.test_dir, filename)
        content = self.generate_random_code(filename)
        
        try:
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            self.stats['files_created'] += 1
            return filepath
        except Exception as e:
            self.issues_found.append(f"Failed to create file {filename}: {e}")
            return None
    
    def modify_file(self, filepath: str) -> bool:
        """Modify an existing file"""
        try:
            filename = os.path.basename(filepath)
            content = self.generate_random_code(filename)
            
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
            os.remove(filepath)
            self.stats['files_deleted'] += 1
            return True
        except Exception as e:
            self.issues_found.append(f"Failed to delete file {filepath}: {e}")
            return False
    
    def run_stress_test(self, duration_seconds: int = 60):
        """Run stress test for specified duration"""
        print(f"Starting stress test for {duration_seconds} seconds...")
        print(f"Test directory: {self.test_dir}")
        print("-" * 60)
        
        start_time = time.time()
        files = []
        
        # Phase 1: Create initial files
        print("Phase 1: Creating initial files...")
        for i in range(10):
            filename = f"TestModule{i}_{random.randint(1000, 9999)}.ets"
            filepath = self.create_file(filename)
            if filepath:
                files.append(filepath)
        
        # Process initial files
        for filepath in files:
            try:
                with open(filepath, 'r', encoding='utf-8') as f:
                    content = f.read()
                self.service._handle_file_change(filepath, content)
            except Exception as e:
                self.issues_found.append(f"Error processing {filepath}: {e}")
                self.stats['parse_errors'] += 1
        
        # Phase 2: Random operations
        print("Phase 2: Random operations...")
        operations = 0
        
        while time.time() - start_time < duration_seconds:
            operation = random.choice(['create', 'modify', 'modify', 'delete', 'create'])
            
            try:
                if operation == 'create':
                    filename = f"DynamicModule{operations}_{random.randint(1000, 9999)}.ets"
                    filepath = self.create_file(filename)
                    if filepath:
                        files.append(filepath)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.service._handle_file_change(filepath, content)
                
                elif operation == 'modify' and files:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        self.modify_file(filepath)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.service._handle_file_change(filepath, content)
                
                elif operation == 'delete' and len(files) > 5:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        self.service.db.delete_file_data(filepath)
                        self.delete_file(filepath)
                        files.remove(filepath)
                
                operations += 1
                
                # Print progress every 10 operations
                if operations % 10 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Operations: {operations}, Elapsed: {elapsed:.1f}s, "
                          f"Files: {len(files)}, Issues: {len(self.issues_found)}")
                
                # Small delay to prevent overwhelming
                time.sleep(0.1)
                
            except Exception as e:
                self.issues_found.append(f"Operation error: {e}")
                self.stats['db_errors'] += 1
        
        print("-" * 60)
        print("Stress test completed!")
        self.print_report()
    
    def print_report(self):
        """Print test report"""
        print("\n" + "=" * 60)
        print("TEST REPORT")
        print("=" * 60)
        
        print("\nStatistics:")
        for key, value in self.stats.items():
            print(f"  {key}: {value}")
        
        # Get DB stats
        try:
            db_stats = self.service.get_statistics()
            print("\nDatabase Statistics:")
            for key, value in db_stats.items():
                if isinstance(value, (int, str)):
                    print(f"  {key}: {value}")
        except Exception as e:
            print(f"\nError getting DB stats: {e}")
        
        print(f"\nIssues Found: {len(self.issues_found)}")
        if self.issues_found:
            print("\nDetailed Issues:")
            for i, issue in enumerate(self.issues_found[:20], 1):
                print(f"  {i}. {issue}")
            if len(self.issues_found) > 20:
                print(f"  ... and {len(self.issues_found) - 20} more issues")
        
        print("=" * 60)


def main():
    test_dir = '/workspace/arkts_knowledge_graph/test_complex_project/stress_test'
    os.makedirs(test_dir, exist_ok=True)
    
    tester = SystemTester(test_dir)
    tester.run_stress_test(duration_seconds=30)


if __name__ == '__main__':
    main()
