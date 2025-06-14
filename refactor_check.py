import os
import ast

class _DocstringAndCommentStripper(ast.NodeTransformer):
    """
    Removes docstrings from functions and classes.
    Comments are already dropped by the parser, so we just strip
    out any constant string that appears as the first statement
    in a FunctionDef or ClassDef.
    """
    def visit_FunctionDef(self, node):
        self.generic_visit(node)
        if (node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            # Drop the docstring
            node.body = node.body[1:]
        return node

    def visit_AsyncFunctionDef(self, node):
        return self.visit_FunctionDef(node)

    def visit_ClassDef(self, node):
        self.generic_visit(node)
        if (node.body
            and isinstance(node.body[0], ast.Expr)
            and isinstance(node.body[0].value, ast.Constant)
            and isinstance(node.body[0].value.value, str)
        ):
            node.body = node.body[1:]
        return node

def _normalize_ast(source: str) -> ast.AST:
    """
    Parse source into an AST, strip docstrings, and remove
    any location-specific attributes.
    """
    tree = ast.parse(source)
    tree = _DocstringAndCommentStripper().visit(tree)
    ast.fix_missing_locations(tree)
    return tree

def ast_changed(old_file_path: str, new_file_path: str) -> bool:
    """
    Returns True if the AST (ignoring docstrings/comments/whitespace)
    differs between the two files, False if they are structurally identical.
    """
    with open(old_file_path, 'r', encoding='utf-8') as f:
        old_src = f.read()
    with open(new_file_path, 'r', encoding='utf-8') as f:
        new_src = f.read()

    old_tree = _normalize_ast(old_src)
    new_tree = _normalize_ast(new_src)

    # Dump without attributes or field names to focus on structure
    old_dump = ast.dump(old_tree, include_attributes=False, annotate_fields=False)
    new_dump = ast.dump(new_tree, include_attributes=False, annotate_fields=False)

    return old_dump != new_dump


def detect_refactoring(old_file_path: str, new_file_path: str) -> bool:
    #  dynamic equivalence
    #    — assumes the main function is called `fizzbuzz`; change if needed.
    return ast_changed(old_file_path, new_file_path)
