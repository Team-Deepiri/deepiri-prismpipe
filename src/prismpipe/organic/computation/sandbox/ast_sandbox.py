"""AST-based sandbox for Python code."""

import ast
from typing import Any

from prismpipe.organic.computation.sandbox.base import Sandbox


# Forbidden AST nodes that could be dangerous
FORBIDDEN_NODES = {
    'Assign', 'AugAssign', 'AnnAssign',  # Assignments
    'Import', 'ImportFrom',                # Imports
    'Global', 'Nonlocal',                  # Scope
    'FunctionDef', 'AsyncFunctionDef', 'ClassDef',  # Definitions
    'Raise', 'Try', 'With', 'WithItem',   # Control flow
    'Delete',                              # Deletion
    'Yield', 'YieldFrom',                 # Generators
    'Await',                              # Async
}

# Forbidden names (builtins that could be dangerous)
FORBIDDEN_BUILTINS = {
    'open', 'eval', 'exec', 'compile',
    'breakpoint', 'help', 'copyright',
    'exit', 'quit',
}


class ASTSandbox(Sandbox):
    """
    Sandbox that uses AST analysis to validate code safety.
    
    Rejects code containing dangerous patterns.
    """
    
    def __init__(
        self,
        allow_builtins: list[str] | None = None,
        allow_modules: list[str] | None = None
    ):
        self.allow_builtins = allow_builtins or [
            'len', 'str', 'int', 'float', 'bool', 'list', 'dict', 'set', 'tuple',
            'range', 'enumerate', 'zip', 'map', 'filter', 'sorted', 'reversed',
            'sum', 'min', 'max', 'abs', 'round', 'pow', 'divmod',
            'isinstance', 'issubclass', 'hasattr', 'getattr',
            'all', 'any', 'ord', 'chr', 'bin', 'hex', 'oct', 'slice',
        ]
        self.allow_modules = allow_modules or [
            'json', 'math', 'random', 're', 'datetime',
            'collections', 'itertools', 'functools', 'operator',
        ]
    
    def validate(self, code: str) -> tuple[bool, str | None]:
        """Validate code using AST analysis."""
        try:
            tree = ast.parse(code)
        except SyntaxError as e:
            return False, f"Syntax error: {e}"
        
        # Check for forbidden nodes
        for node in ast.walk(tree):
            node_type = type(node).__name__
            if node_type in FORBIDDEN_NODES:
                return False, f"Forbidden construct: {node_type}"
        
        # Check for dangerous attribute access
        for node in ast.walk(tree):
            if isinstance(node, ast.Attribute):
                if node.attr in ('__globals__', '__code__', '__closure__'):
                    return False, f"Forbidden attribute access: {node.attr}"
        
        return True, None
    
    def wrap_code(self, code: str) -> str:
        """Wrap code with safety measures."""
        # Add result capture
        return f"""
_result = None
try:
    _result = eval({repr(code)})
except Exception as e:
    _result = {{"error": str(e)}}
"""
    
    def get_allowed_builtins(self) -> dict[str, Any]:
        """Get allowed builtins."""
        from builtins import __builtins__
        
        allowed = {}
        for name in self.allow_builtins:
            if name in __builtins__:
                allowed[name] = __builtins__[name]
        
        return allowed
