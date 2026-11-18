"""Read a test function's source to show its input data and expected result
before it runs (Console → Testing → Test cases).

Expected result = every `assert ...` and `case.check(name, expected, ...)`
in the test, with parametrize values filled in:
    case.check("slug", expected, ...)   with expected="palakkad-bp"
    -> {"check": "slug", "expected": "'palakkad-bp'"}
Input data = request bodies (client.post(url, {...})) and model rows the test
creates (Model.objects.create(...)).
"""
import ast
import copy
import inspect
import sys
import textwrap
from functools import lru_cache

MAX_ITEMS = 12
MAX_TEXT = 240
HTTP = {"get", "post", "put", "patch", "delete"}
# Parametrize argument names that hold the expected outcome, not input.
EXPECT_PARAMS = {"expected", "expect", "valid", "ok", "result", "message", "status", "expected_status",
                 "expected_code", "status_code", "should_pass", "allowed"}


def _clip(text):
    text = " ".join(text.split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


def _source(func):
    try:
        return inspect.getsource(func)
    except (OSError, TypeError):
        pass
    # Test modules rewritten by pytest's assert rewriting have no loader source:
    # read the block straight from the file.
    func = inspect.unwrap(func)
    code = getattr(func, "__code__", None)
    if code is None:
        return None
    # Cached .pyc files can carry an old path in co_filename: prefer the module's file.
    module = sys.modules.get(getattr(func, "__module__", "") or "")
    path = getattr(module, "__file__", None) or code.co_filename
    try:
        with open(path, encoding="utf-8") as fh:
            lines = fh.readlines()
    except OSError:
        return None
    start = code.co_firstlineno - 1
    while start > 0 and lines[start - 1].lstrip().startswith("@"):
        start -= 1
    return "".join(inspect.getblock(lines[start:]))


@lru_cache(maxsize=4096)
def _tree(func):
    try:
        src = _source(func)
        return ast.parse(textwrap.dedent(src)) if src else None
    except (OSError, TypeError, SyntaxError, IndentationError, IndexError):
        return None


class _Fill(ast.NodeTransformer):
    """Put parametrize values in place of their names."""

    def __init__(self, params):
        self.params = params

    def visit_Name(self, node):
        if node.id in self.params:
            v = self.params[node.id]
            if v is None or isinstance(v, (str, int, float, bool)):
                return ast.copy_location(ast.Constant(v), node)
        return node


def _src(node, params):
    """Readable source of an expression; plain strings without quotes, f-strings filled in."""
    value = _const(node, params)
    if isinstance(value, str):
        return _clip(value)
    if isinstance(node, ast.JoinedStr):
        return _clip(_fstring(node, params))
    try:
        return _clip(ast.unparse(_Fill(params).visit(copy.deepcopy(node))))
    except Exception:  # noqa: BLE001
        return ""


def _fstring(node, params):
    parts = []
    for v in node.values:
        if isinstance(v, ast.Constant):
            parts.append(str(v.value))
        elif isinstance(v, ast.FormattedValue):
            value = _const(v.value, params)
            parts.append(str(value) if value is not None else "{" + ast.unparse(v.value) + "}")
    return "".join(parts)


def _const(node, params):
    """Literal value of a node if it is one (after filling params), else None."""
    if isinstance(node, ast.JoinedStr):
        return None
    try:
        return ast.literal_eval(_Fill(params).visit(copy.deepcopy(node)))
    except Exception:  # noqa: BLE001
        return None


def analyse(func, params=None):
    """{"expected": [{"check", "expected"}], "inputs": {label: text}} for a test function."""
    # Module constants (BASE = "/api/v1/...") read like params; params win.
    names = {k: v for k, v in getattr(func, "__globals__", {}).items()
             if k.isupper() and isinstance(v, (str, int, float, bool))}
    params = {**names, **dict(params or {})}
    tree = _tree(func)
    out = {"expected": [], "inputs": {}}
    if tree is None:
        return out
    expected, inputs = out["expected"], out["inputs"]
    for node in ast.walk(tree):
        if len(expected) >= MAX_ITEMS and len(inputs) >= MAX_ITEMS:
            break
        if isinstance(node, ast.Assert) and len(expected) < MAX_ITEMS:
            expected.append({"line": node.lineno, "check": "assert", "expected": _src(node.test, params)})
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
            attr = node.func.attr
            if attr == "check" and len(node.args) >= 2 and len(expected) < MAX_ITEMS:
                name = _const(node.args[0], params)
                label = name if isinstance(name, str) else _src(node.args[0], params)
                expected.append({"line": node.lineno, "check": label, "expected": _src(node.args[1], params)})
            elif attr in HTTP and node.args and len(inputs) < MAX_ITEMS:
                url = _const(node.args[0], params)
                url = url if isinstance(url, str) else _src(node.args[0], params)
                body = node.args[1] if len(node.args) > 1 else next(
                    (k.value for k in node.keywords if k.arg in ("data", "json")), None)
                key = f"{attr.upper()} {url}"
                if key not in inputs:
                    inputs[key] = _src(body, params) if body is not None else "(no body)"
            elif attr in ("create", "create_user", "create_superuser", "get_or_create") and (
                    node.keywords and len(inputs) < MAX_ITEMS):
                owner = node.func.value
                model = owner.value.id if (isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name)) \
                    else (owner.id if isinstance(owner, ast.Name) else "")
                key = f"new {model or 'row'}"
                n = 2
                while key in inputs:
                    key, n = f"new {model or 'row'} ({n})", n + 1
                inputs[key] = _clip(", ".join(f"{k.arg}={_src(k.value, params)}" for k in node.keywords if k.arg))
    expected.sort(key=lambda e: e["line"])
    return out


def split_params(params):
    """Parametrize values -> (input data, expected values)."""
    data, exp = {}, {}
    for k, v in (params or {}).items():
        if k.startswith("_"):
            continue
        (exp if k in EXPECT_PARAMS else data)[k] = v
    return data, exp
