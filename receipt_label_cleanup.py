import ast
import os

class CallGraphVisitor(ast.NodeVisitor):
    def __init__(self, filename, max_depth=3):
        self.calls = set()
        self.filename = filename
        self.current_func = None
        self.current_depth = 0
        self.max_depth = max_depth

    def visit_FunctionDef(self, node):
        previous_func = self.current_func
        previous_depth = self.current_depth

        self.current_depth += 1
        if self.current_depth <= self.max_depth:
            self.current_func = f"{self.filename}.{node.name}"
            self.generic_visit(node)
        self.current_depth -= 1
        self.current_func = previous_func

    def visit_Call(self, node):
        if self.current_func and isinstance(node.func, ast.Name):
            self.calls.add((self.current_func, node.func.id))
        elif self.current_func and isinstance(node.func, ast.Attribute):
            self.calls.add((self.current_func, node.func.attr))
        self.generic_visit(node)

def parse_file(filepath, max_depth=3):
    with open(filepath, 'r') as file:
        source = file.read()
    tree = ast.parse(source, filepath)
    visitor = CallGraphVisitor(filepath, max_depth=max_depth)
    visitor.visit(tree)
    return visitor.calls

def walk_codebase(directory, max_depth=3):
    all_calls = set()
    for root, dirs, files in os.walk(directory):
        for file in files:
            if file.endswith('.py'):
                filepath = os.path.join(root, file)
                calls = parse_file(filepath, max_depth=max_depth)
                all_calls.update(calls)
    return all_calls

def generate_dot(calls, output_file='call_graph.dot'):
    with open(output_file, 'w') as f:
        f.write('digraph callgraph {\n')
        for caller, callee in calls:
            caller_label = caller.replace('"', '\\"')
            callee_label = callee.replace('"', '\\"')
            f.write(f'    "{caller_label}" -> "{callee_label}";\n')
        f.write('}\n')

calls = walk_codebase('/Users/tnorlund/GitHub/example/receipt_label', max_depth=1)
generate_dot(calls)
print(calls)