"""
LLM-based ArkTS Code Parser
Uses Silicon Flow API to parse ArkTS/TypeScript code and extract knowledge graph data
"""
import json
import time
import requests
from typing import List, Dict, Optional
from config import LLM_API_KEY, LLM_BASE_URL, LLM_MODEL


class ArkTSParser:
    """Parse ArkTS code using LLM to extract entities and relationships"""
    
    def __init__(self, max_retries: int = 3, retry_delay: float = 1.0):
        self.api_key = LLM_API_KEY
        self.base_url = LLM_BASE_URL
        self.model = LLM_MODEL
        self.max_retries = max_retries
        self.retry_delay = retry_delay
    
    def _call_llm(self, prompt: str) -> Optional[str]:
        """Call Silicon Flow LLM API with retry logic"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a code analysis expert. Extract structured information from ArkTS/TypeScript code. Always respond with valid JSON only."
                },
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "temperature": 0.1,
            "max_tokens": 4000
        }
        
        last_error = None
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    f"{self.base_url}/chat/completions",
                    headers=headers,
                    json=payload,
                    timeout=60
                )
                response.raise_for_status()
                result = response.json()
                return result['choices'][0]['message']['content']
            except requests.exceptions.RequestException as e:
                last_error = e
                error_msg = str(e).lower()
                
                # Don't retry on auth errors
                if '403' in str(e) or '401' in str(e):
                    print(f"LLM API auth error (not retrying): {e}")
                    return None
                
                # Don't retry on client errors (4xx)
                if '400' in str(e) or '422' in str(e):
                    print(f"LLM API client error (not retrying): {e}")
                    return None
                
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)  # Exponential backoff
                    print(f"LLM API error (attempt {attempt + 1}/{self.max_retries}): {e}")
                    print(f"Retrying in {wait_time:.1f}s...")
                    time.sleep(wait_time)
                else:
                    print(f"LLM API error (final attempt): {e}")
            except Exception as e:
                last_error = e
                if attempt < self.max_retries - 1:
                    wait_time = self.retry_delay * (2 ** attempt)
                    print(f"Unexpected error (attempt {attempt + 1}/{self.max_retries}): {e}")
                    time.sleep(wait_time)
                else:
                    print(f"Unexpected error (final attempt): {e}")
        
        return None
    
    def parse_file(self, file_path: str, content: str) -> Dict:
        """Parse a single ArkTS file and extract knowledge graph data"""
        
        # Skip empty files
        if not content or not content.strip():
            return {"entities": [], "relationships": [], "imports": []}
        
        # Skip files that are too large (LLM token limit)
        max_chars = 15000  # Approximate limit
        if len(content) > max_chars:
            print(f"File too large, truncating: {file_path} ({len(content)} chars)")
            content = content[:max_chars] + "\n// ... truncated"
        
        prompt = f"""Analyze this ArkTS/TypeScript file and extract all code entities and their relationships.

File path: {file_path}

Code content:
```typescript
{content}
```

Extract the following information in JSON format:

{{
    "entities": [
        {{
            "name": "entity name",
            "type": "function|class|interface|enum|type|variable|decorator|component",
            "signature": "function signature or type definition",
            "docstring": "documentation comment if any",
            "start_line": line number where entity starts,
            "end_line": line number where entity ends
        }}
    ],
    "relationships": [
        {{
            "source": "source entity name",
            "target": "target entity name",
            "type": "calls|extends|implements|imports|references|uses"
        }}
    ],
    "imports": [
        {{
            "module": "module path",
            "names": ["imported name 1", "imported name 2"],
            "is_default": false
        }}
    ]
}}

Important:
1. Include ALL functions, classes, interfaces, enums, types, and decorated components
2. Capture function calls between entities in the same file
3. Capture inheritance (extends/implements) relationships
4. List all import statements with their imported names
5. Use exact line numbers from the code
6. Return ONLY valid JSON, no markdown formatting or explanations"""

        response = self._call_llm(prompt)
        
        if not response:
            return {"entities": [], "relationships": [], "imports": []}
        
        # Clean up response - remove markdown code blocks if present
        response = response.strip()
        if response.startswith("```json"):
            response = response[7:]
        if response.startswith("```"):
            response = response[3:]
        if response.endswith("```"):
            response = response[:-3]
        response = response.strip()
        
        # Try to extract JSON if wrapped in other text
        if not response.startswith('{'):
            json_start = response.find('{')
            json_end = response.rfind('}')
            if json_start != -1 and json_end != -1:
                response = response[json_start:json_end+1]
        
        try:
            parsed = json.loads(response)
            return {
                "entities": parsed.get("entities", []),
                "relationships": parsed.get("relationships", []),
                "imports": parsed.get("imports", [])
            }
        except json.JSONDecodeError as e:
            print(f"Failed to parse LLM response as JSON: {e}")
            print(f"Response preview: {response[:500]}...")
            return {"entities": [], "relationships": [], "imports": []}
    
    def batch_parse(self, files: List[Dict]) -> List[Dict]:
        """Parse multiple files"""
        results = []
        for file_info in files:
            result = self.parse_file(file_info['path'], file_info['content'])
            result['file_path'] = file_info['path']
            results.append(result)
        return results
