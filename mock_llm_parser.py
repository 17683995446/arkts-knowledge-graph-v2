"""
Mock LLM Parser for testing without API calls
Simulates parsing results for testing system robustness
"""
import re
import random
from typing import List, Dict


class MockArkTSParser:
    """Mock parser that simulates LLM responses without API calls"""
    
    def __init__(self, simulate_errors: bool = False, error_rate: float = 0.05):
        self.simulate_errors = simulate_errors
        self.error_rate = error_rate
        self.parse_count = 0
    
    def parse_file(self, file_path: str, content: str) -> Dict:
        """Parse file using regex patterns to extract entities"""
        self.parse_count += 1
        
        # Simulate occasional errors
        if self.simulate_errors and random.random() < self.error_rate:
            raise Exception("Simulated LLM error")
        
        entities = []
        relationships = []
        imports = []
        
        # Extract imports - 分别处理默认导入和命名导入
        # 命名导入: import { Foo, Bar } from 'module'
        named_import_pattern = r"import\s+\{([^}]+)\}\s+from\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(named_import_pattern, content):
            imported_names = [n.strip() for n in match.group(1).split(',')]
            module_path = match.group(2)
            imports.append({
                "module": module_path,
                "names": imported_names,
                "is_default": False
            })

        # 默认导入: import Foo from 'module'
        default_import_pattern = r"import\s+(\w+)\s+from\s+['\"]([^'\"]+)['\"]"
        for match in re.finditer(default_import_pattern, content):
            # 跳过已匹配的命名导入
            if '{' in content[match.start():match.start() + 50]:
                continue
            module_path = match.group(2)
            imports.append({
                "module": module_path,
                "names": [],
                "is_default": True
            })
        
        # Extract interfaces
        interface_pattern = r"export\s+interface\s+(\w+)(?:\s+extends\s+(\w+))?"
        for match in re.finditer(interface_pattern, content):
            interface_name = match.group(1)
            parent = match.group(2)
            
            # Find interface body
            start_pos = match.end()
            brace_count = 0
            end_pos = start_pos
            for i, char in enumerate(content[start_pos:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = start_pos + i + 1
                        break
            
            interface_body = content[start_pos:end_pos]
            
            entities.append({
                "name": interface_name,
                "type": "interface",
                "signature": f"interface {interface_name}",
                "docstring": None,
                "start_line": content[:match.start()].count('\n') + 1,
                "end_line": content[:end_pos].count('\n') + 1
            })
            
            # Add extends relationship
            if parent:
                relationships.append({
                    "source": interface_name,
                    "target": parent,
                    "type": "extends"
                })
        
        # Extract classes
        class_pattern = r"export\s+class\s+(\w+)(?:\s+extends\s+(\w+))?(?:\s+implements\s+([\w,\s]+))?"
        for match in re.finditer(class_pattern, content):
            class_name = match.group(1)
            parent_class = match.group(2)
            implements = match.group(3)
            
            # Find class body
            start_pos = match.end()
            brace_count = 0
            end_pos = start_pos
            for i, char in enumerate(content[start_pos:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = start_pos + i + 1
                        break
            
            class_body = content[start_pos:end_pos]
            
            entities.append({
                "name": class_name,
                "type": "class",
                "signature": f"class {class_name}",
                "docstring": None,
                "start_line": content[:match.start()].count('\n') + 1,
                "end_line": content[:end_pos].count('\n') + 1
            })
            
            # Add extends relationship
            if parent_class:
                relationships.append({
                    "source": class_name,
                    "target": parent_class,
                    "type": "extends"
                })
            
            # Add implements relationships
            if implements:
                for interface in implements.split(','):
                    relationships.append({
                        "source": class_name,
                        "target": interface.strip(),
                        "type": "implements"
                    })
            
            # Extract methods within class
            method_pattern = r"(?:async\s+)?(\w+)\s*\([^)]*\)\s*:\s*(\w+|\{[^}]*\}|\w+<[^>]+>)"
            for method_match in re.finditer(method_pattern, class_body):
                method_name = method_match.group(1)
                if method_name not in ['constructor', 'if', 'for', 'while', 'switch',
                                       'catch', 'return', 'new', 'throw', 'else', 'do',
                                       'function', 'class', 'interface', 'enum', 'type',
                                       'import', 'export', 'from', 'const', 'let', 'var']:
                    entities.append({
                        "name": f"{class_name}.{method_name}",
                        "type": "function",
                        "signature": f"{method_name}(): {method_match.group(2)}",
                        "docstring": None,
                        "start_line": content[:match.start() + method_match.start()].count('\n') + 1,
                        "end_line": content[:match.start() + method_match.end()].count('\n') + 1
                    })
                    
                    relationships.append({
                        "source": class_name,
                        "target": f"{class_name}.{method_name}",
                        "type": "contains"
                    })
        
        # Extract enums
        enum_pattern = r"export\s+enum\s+(\w+)"
        for match in re.finditer(enum_pattern, content):
            enum_name = match.group(1)
            # [MEDIUM-5] 使用花括号匹配计算准确的结束位置
            start_pos = match.end()
            brace_count = 0
            end_pos = start_pos
            for i, char in enumerate(content[start_pos:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = start_pos + i + 1
                        break
            if brace_count != 0:
                end_pos = match.end()  # fallback
            entities.append({
                "name": enum_name,
                "type": "enum",
                "signature": f"enum {enum_name}",
                "docstring": None,
                "start_line": content[:match.start()].count('\n') + 1,
                "end_line": content[:end_pos].count('\n') + 1
            })
        
        # Extract type aliases
        type_pattern = r"export\s+type\s+(\w+)"
        for match in re.finditer(type_pattern, content):
            type_name = match.group(1)
            entities.append({
                "name": type_name,
                "type": "type",
                "signature": f"type {type_name}",
                "docstring": None,
                "start_line": content[:match.start()].count('\n') + 1,
                "end_line": content[:match.end()].count('\n') + 1
            })
        
        # Extract functions
        func_pattern = r"export\s+(?:async\s+)?function\s+(\w+)"
        for match in re.finditer(func_pattern, content):
            func_name = match.group(1)
            # [MEDIUM-5] 使用花括号匹配计算准确的结束位置
            start_pos = match.end()
            brace_count = 0
            end_pos = start_pos
            for i, char in enumerate(content[start_pos:]):
                if char == '{':
                    brace_count += 1
                elif char == '}':
                    brace_count -= 1
                    if brace_count == 0:
                        end_pos = start_pos + i + 1
                        break
            if brace_count != 0:
                end_pos = match.end()  # fallback
            entities.append({
                "name": func_name,
                "type": "function",
                "signature": f"function {func_name}()",
                "docstring": None,
                "start_line": content[:match.start()].count('\n') + 1,
                "end_line": content[:end_pos].count('\n') + 1
            })
        
        return {
            "entities": entities,
            "relationships": relationships,
            "imports": imports
        }
    
    def batch_parse(self, files: List[Dict]) -> List[Dict]:
        """Parse multiple files"""
        results = []
        for file_info in files:
            result = self.parse_file(file_info['path'], file_info['content'])
            result['file_path'] = file_info['path']
            results.append(result)
        return results
