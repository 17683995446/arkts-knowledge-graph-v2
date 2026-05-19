"""
System Test Script V2 - Long running test to discover issues
Fixed to use file-based database for persistence
"""
import os
import sys
import time
import random
import shutil
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from knowledge_graph_service import KnowledgeGraphService


class SystemTester:
    """Test the knowledge graph system with complex scenarios"""
    
    def __init__(self, test_dir: str, db_path: str):
        self.test_dir = test_dir
        self.db_path = db_path
        self.service = KnowledgeGraphService(
            db_path=db_path,
            watch_paths=[test_dir]
        )
        self.issues_found = []
        self.stats = {
            'files_created': 0,
            'files_modified': 0,
            'files_deleted': 0,
            'parse_errors': 0,
            'db_errors': 0,
            'llm_calls': 0
        }
    
    def generate_random_code(self, filename: str) -> str:
        """Generate random ArkTS code"""
        base_name = filename.replace('.ets', '')
        class_name = ''.join([p.capitalize() for p in base_name.split('_')])
        
        return f"""/**
 * {filename}
 * Auto-generated test file
 */

export interface I{class_name} {{
    id: string;
    name: string;
    value: number;
}}

export class {class_name}Service {{
    private items: Map<string, I{class_name}> = new Map();
    
    add(item: I{class_name}): void {{
        this.items.set(item.id, item);
    }}
    
    get(id: string): I{class_name} | undefined {{
        return this.items.get(id);
    }}
    
    remove(id: string): boolean {{
        return this.items.delete(id);
    }}
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
        """Run stress test"""
        print(f"Starting stress test for {duration_seconds} seconds...")
        print(f"Test directory: {self.test_dir}")
        print(f"Database: {self.db_path}")
        print("-" * 60)
        
        start_time = time.time()
        files = []
        
        # Phase 1: Create initial files
        print("Phase 1: Creating initial files...")
        for i in range(5):
            filename = f"TestModule{i}_{random.randint(1000, 9999)}.ets"
            filepath = self.create_file(filename)
            if filepath:
                files.append(filepath)
                # Process immediately
                try:
                    with open(filepath, 'r', encoding='utf-8') as f:
                        content = f.read()
                    self.service._handle_file_change(filepath, content)
                    self.stats['llm_calls'] += 1
                except Exception as e:
                    self.issues_found.append(f"Error processing initial {filepath}: {e}")
                    self.stats['parse_errors'] += 1
        
        print(f"Initial files created and processed: {len(files)}")
        
        # Phase 2: Random operations
        print("Phase 2: Random operations...")
        operations = 0
        
        while time.time() - start_time < duration_seconds:
            operation = random.choice(['create', 'modify', 'delete'])
            
            try:
                if operation == 'create':
                    filename = f"DynamicModule{operations}_{random.randint(1000, 9999)}.ets"
                    filepath = self.create_file(filename)
                    if filepath:
                        files.append(filepath)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.service._handle_file_change(filepath, content)
                        self.stats['llm_calls'] += 1
                
                elif operation == 'modify' and files:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        self.modify_file(filepath)
                        with open(filepath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        self.service._handle_file_change(filepath, content)
                        self.stats['llm_calls'] += 1
                
                elif operation == 'delete' and len(files) > 3:
                    filepath = random.choice(files)
                    if os.path.exists(filepath):
                        self.service.db.delete_file_data(filepath)
                        self.delete_file(filepath)
                        files.remove(filepath)
                
                operations += 1
                
                # Print progress
                if operations % 5 == 0:
                    elapsed = time.time() - start_time
                    print(f"  Operations: {operations}, Elapsed: {elapsed:.1f}s, "
                          f"Files: {len(files)}, Issues: {len(self.issues_found)}")
                
                time.sleep(0.5)  # Delay to avoid rate limiting
                
            except Exception as e:
                error_msg = str(e)
                if "rate" in error_msg.lower() or "limit" in error_msg.lower():
                    print(f"  Rate limit hit, waiting...")
                    time.sleep(5)
                else:
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
                if isinstance(value, (int, str, float)):
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
    db_path = '/workspace/arkts_knowledge_graph/test_stress.db'
    
    # Cleanup
    if os.path.exists(test_dir):
        shutil.rmtree(test_dir)
    os.makedirs(test_dir, exist_ok=True)
    
    if os.path.exists(db_path):
        os.remove(db_path)
    
    tester = SystemTester(test_dir, db_path)
    tester.run_stress_test(duration_seconds=60)


if __name__ == '__main__':
    main()
