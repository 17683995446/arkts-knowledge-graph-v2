"""
System Test Script V4 - Extended long-running test with concurrency
Tests edge cases and race conditions
"""
import os
import sys
import time
import random
import shutil
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_db import KnowledgeGraphDB
from mock_llm_parser import MockArkTSParser


class ConcurrentSystemTester:
    """Test system with concurrent operations"""
    
    def __init__(self, test_dir: str, db_path: str):
        self.test_dir = test_dir
        self.db_path = db_path
        self.db = KnowledgeGraphDB(db_path)
        self.parser = MockArkTSParser(simulate_errors=True, error_rate=0.01)
        self.issues_found = []
        self.stats_lock = threading.Lock()
        self.stats = {
            'files_created': 0,
            'files_modified': 0,
            'files_deleted': 0,
            'parse_errors': 0,
            'db_errors': 0,
            'race_conditions': 0,
            'entities_extracted': 0,
            'relations_extracted': 0
        }
    
    def generate_code(self, filename: str) -> str:
        """Generate code with random complexity"""
        base_name = filename.replace('.ets', '')
        class_name = ''.join([p.capitalize() for p in base_name.split('_')])
        
        # Randomly include various patterns
        patterns = []
        
        if random.random() > 0.3:
            patterns.append(f"import {{ ApiResponse }} from '../types/CommonTypes';")
        if random.random() > 0.5:
            patterns.append(f"import {{ LoggerUtility }} from '../utils/LoggerUtility';")
        
        # Interface
        if random.random() > 0.4:
            patterns.append(f"""
export interface I{class_name} {{
    id: string;
    name: string;
    value: number;
}}
""")
        
        # Class with methods
        method_count = random.randint(2, 8)
        methods = []
        for i in range(method_count):
            method_name = f"method{i}_{random.randint(100, 999)}"
            methods.append(f"""
    {method_name}(arg: string): Promise<boolean> {{
        return Promise.resolve(true);
    }}
""")
        
        patterns.append(f"""
export class {class_name}Service {{
    private data: Map<string, any> = new Map();
    
    constructor() {{}}
    
    {''.join(methods)}
    
    async process(): Promise<void> {{
        for (const [key, value] of this.data) {{
            await this.handleItem(key, value);
        }}
    }}
    
    private async handleItem(key: string, value: any): Promise<void> {{
        console.log(key, value);
    }}
}}
""")
        
        return '\n'.join(patterns)
    
    def process_file_safely(self, filepath: str):
        """Process file with error handling"""
        try:
            if not os.path.exists(filepath):
                return
            
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # Check if file changed
            unchanged, new_hash = self.db.file_exists_and_unchanged(filepath, content)
            if unchanged:
                return
            
            # Delete old data
            self.db.delete_file_data(filepath)
            
            # Parse
            parse_result = self.parser.parse_file(filepath, content)
            
            # Add file
            file_id = self.db.add_or_update_file(filepath, content)
            
            # Add entities
            entity_name_to_id = {}
            for entity in parse_result.get('entities', []):
                try:
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
                    with self.stats_lock:
                        self.stats['entities_extracted'] += 1
                except Exception as e:
                    with self.stats_lock:
                        self.issues_found.append(f"Entity error in {filepath}: {e}")
            
            # Add relationships
            for rel in parse_result.get('relationships', []):
                try:
                    source_id = entity_name_to_id.get(rel['source'])
                    target_id = entity_name_to_id.get(rel['target'])
                    
                    self.db.add_relationship(
                        source_file_id=file_id,
                        relation_type=rel['type'],
                        source_entity_id=source_id,
                        target_entity_id=target_id
                    )
                    with self.stats_lock:
                        self.stats['relations_extracted'] += 1
                except Exception as e:
                    with self.stats_lock:
                        self.issues_found.append(f"Relationship error in {filepath}: {e}")
            
            # Add imports
            for imp in parse_result.get('imports', []):
                try:
                    self.db.add_import(
                        file_id=file_id,
                        module_path=imp['module'],
                        imported_names=imp.get('names', []),
                        is_default=imp.get('is_default', False)
                    )
                except Exception as e:
                    pass  # Import errors are less critical
            
        except Exception as e:
            with self.stats_lock:
                self.issues_found.append(f"Process error {filepath}: {e}")
                self.stats['parse_errors'] += 1
    
    def create_and_process(self, filename: str) -> str:
        """Create and immediately process a file"""
        filepath = os.path.join(self.test_dir, filename)
        try:
            content = self.generate_code(filename)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            with self.stats_lock:
                self.stats['files_created'] += 1
            
            self.process_file_safely(filepath)
            return filepath
        except Exception as e:
            with self.stats_lock:
                self.issues_found.append(f"Create error {filename}: {e}")
            return None
    
    def modify_and_process(self, filepath: str):
        """Modify and re-process a file"""
        try:
            if not os.path.exists(filepath):
                return
            
            filename = os.path.basename(filepath)
            content = self.generate_code(filename)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            
            with self.stats_lock:
                self.stats['files_modified'] += 1
            
            self.process_file_safely(filepath)
        except Exception as e:
            with self.stats_lock:
                self.issues_found.append(f"Modify error {filepath}: {e}")
    
    def delete_and_cleanup(self, filepath: str):
        """Delete file and cleanup database"""
        try:
            if os.path.exists(filepath):
                self.db.delete_file_data(filepath)
                os.remove(filepath)
                with self.stats_lock:
                    self.stats['files_deleted'] += 1
        except Exception as e:
            with self.stats_lock:
                self.issues_found.append(f"Delete error {filepath}: {e}")
    
    def run_concurrent_test(self, duration_seconds: int = 60, max_workers: int = 5):
        """Run concurrent stress test"""
        print(f"Starting CONCURRENT test for {duration_seconds} seconds...")
        print(f"Max workers: {max_workers}")
        print(f"Test directory: {self.test_dir}")
        print("-" * 60)
        
        start_time = time.time()
        files = []
        counter = 0
        
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = []
            
            while time.time() - start_time < duration_seconds:
                operation = random.choices(
                    ['create', 'modify', 'delete'],
                    weights=[50, 35, 15]
                )[0]
                
                if operation == 'create':
                    counter += 1
                    filename = f"ConcurrentModule{counter}_{random.randint(1000, 9999)}.ets"
                    future = executor.submit(self.create_and_process, filename)
                    futures.append((future, 'create', filename))
                
                elif operation == 'modify' and files:
                    filepath = random.choice(files)
                    future = executor.submit(self.modify_and_process, filepath)
                    futures.append((future, 'modify', filepath))
                
                elif operation == 'delete' and len(files) > 10:
                    filepath = random.choice(files)
                    future = executor.submit(self.delete_and_cleanup, filepath)
                    futures.append((future, 'delete', filepath))
                    files.remove(filepath)
                
                # Process completed futures
                done_futures = [(f, op, path) for f, op, path in futures if f.done()]
                for future, op, path in done_futures:
                    try:
                        result = future.result()
                        if op == 'create' and result:
                            files.append(result)
                    except Exception as e:
                        with self.stats_lock:
                            self.stats['race_conditions'] += 1
                            self.issues_found.append(f"Concurrent error: {e}")
                    futures.remove((future, op, path))
                
                # Progress report
                elapsed = time.time() - start_time
                if int(elapsed) % 5 == 0 and elapsed % 1 < 0.5:
                    db_stats = self.db.get_statistics()
                    print(f"  Time: {elapsed:.0f}s, Files: {len(files)}, "
                          f"Entities: {db_stats.get('total_entities', 0)}, "
                          f"Issues: {len(self.issues_found)}")
                
                time.sleep(0.05)  # Small delay to prevent overwhelming
        
        print("-" * 60)
        print("Concurrent test completed!")
        self.print_report()
    
    def print_report(self):
        """Print test report"""
        print("\n" + "=" * 60)
        print("CONCURRENT TEST REPORT")
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
            unique_issues = list(set(self.issues_found))[:30]
            for i, issue in enumerate(unique_issues, 1):
                print(f"  {i}. {issue}")
            if len(set(self.issues_found)) > 30:
                print(f"  ... and {len(set(self.issues_found)) - 30} more unique issues")
        
        print("=" * 60)


def main():
    test_dir = '/workspace/arkts_knowledge_graph/test_complex_project/concurrent_test'
    db_path = '/workspace/arkts_knowledge_graph/test_concurrent.db'
    
    # Cleanup
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir, exist_ok=True)
    
    if os.path.exists(db_path):
        os.remove(db_path)
    
    tester = ConcurrentSystemTester(test_dir, db_path)
    tester.run_concurrent_test(duration_seconds=60, max_workers=5)


if __name__ == '__main__':
    main()
