"""Test input data as FORMS (Console → Testing), read from the test source.

For a test like

    @pytest.fixture
    def continent(db):
        return Continent.objects.create(name="Asia")

    def test_state_api(auth_client, continent, country):
        auth_client.post("/api/v1/states/", {"name": "Kerala", "country_id": country.unique_id}, format="json")

it returns one entry per form the test fills in:

    {"form": "State", "module": "Common masters", "source": "POST /api/v1/states/", "main": True,
     "fields": [{"name": "name", "label": "Name", "value": "Kerala", "given": True, "required": True}, ...]}
    {"form": "Continent", "source": "fixture continent", "main": False, "fields": [... "Asia" ...]}

The model behind a form is found from the class name in Model.objects.create(...)
/ Model(...), or for a request body from the URL's DRF view (queryset /
serializer model). Fields are the model's form fields in order, labelled the
way the IWMS forms label them; fields the test does not fill are marked
given=False. Values pointing at another fixture (company_id=company.unique_id)
are shown as that record ("Test Company (company)").
"""
import ast
import copy
import re
from functools import lru_cache

from .source_info import _Fill, _const, _tree

MAX_FORMS = 8
MAX_TEXT = 160
CREATE_CALLS = {"create", "get_or_create", "update_or_create", "create_user", "create_superuser"}
HTTP = {"post", "put", "patch"}
HIDDEN = {"is_deleted", "delete_reason", "created_by_id", "updated_by_id", "created_at", "updated_at",
          "deleted_at", "deleted_by_id", "previous_password", "fcm_token", "id", "unique_id"}
SPECIAL_LABELS = {"is_active": "Status", "mob_code": "Mobile Code", "doj": "Date of Joining"}


def _label(name):
    if name in SPECIAL_LABELS:
        return SPECIAL_LABELS[name]
    base = re.sub(r"_ids$", "s", name)
    base = re.sub(r"_id$", "", base)
    return base.replace("_", " ").strip().title() or name


def _clip(text):
    text = " ".join(str(text).split())
    return text if len(text) <= MAX_TEXT else text[:MAX_TEXT] + "…"


# ---------------------------------------------------------------- models

@lru_cache(maxsize=1)
def _models_by_name():
    from django.apps import apps

    out = {}
    for m in apps.get_models():
        out.setdefault(m.__name__, m)
        out.setdefault(m.__name__.lower(), m)
    return out


def _model(name):
    if not name:
        return None
    by = _models_by_name()
    return by.get(name) or by.get(name.lower())


def _module_of(model):
    """app.models.superadmin.common_masters.continent -> 'Common masters'."""
    parts = model.__module__.split(".")
    if parts[:2] == ["app", "models"]:
        parts = parts[2:]
    parts = [p for p in parts[:-1] if p not in ("superadmin", "core_modules")] or parts[:1]
    return " › ".join(p.replace("_", " ").capitalize() for p in parts[-2:])


def _form_label(model):
    verbose = str(getattr(model._meta, "verbose_name", "") or "")
    # Django's default verbose_name is the class name split on capitals.
    return verbose.title() if verbose and verbose.lower() != model.__name__.lower() else \
        re.sub(r"(?<!^)(?=[A-Z])", " ", model.__name__).replace("_", " ").strip()


def _form_fields(model):
    out = []
    for f in model._meta.concrete_fields:
        if f.primary_key or f.name in HIDDEN or f.attname in HIDDEN:
            continue
        verbose = str(getattr(f, "verbose_name", "") or "")
        explicit = verbose and verbose != f.name.replace("_", " ")
        has_default = f.has_default()
        default = f.get_default() if has_default and not callable(f.default) else None
        out.append({
            "name": f.name, "attname": f.attname,
            "label": verbose.title() if explicit else _label(f.name),
            "type": f.get_internal_type(),
            "required": not f.null and not f.blank and not has_default,
            "default": default if isinstance(default, (str, int, float, bool)) else None,
        })
    return out


@lru_cache(maxsize=1024)
def _model_for_url(url):
    """The model behind an API URL (DRF view queryset / serializer)."""
    from django.urls import Resolver404, resolve

    match = None
    # /api/t/<company>/... is rewritten to /api/v1/... by TenantMiddleware.
    url = re.sub(r"^/api/t/[^/]+/", "/api/v1/", url)
    for filler in ("x", "1"):
        path = re.sub(r"\{[^}]*\}", filler, url.split("?")[0])
        if not path.startswith("/"):
            return None
        try:
            match = resolve(path)
            break
        except (Resolver404, Exception):  # noqa: BLE001
            continue
    if match is None:
        return None
    cls = getattr(match.func, "cls", None) or getattr(match.func, "view_class", None)
    if cls is None:
        return None
    qs = getattr(cls, "queryset", None)
    if qs is not None and hasattr(qs, "model"):
        return qs.model
    ser = getattr(cls, "serializer_class", None)
    meta = getattr(ser, "Meta", None)
    return getattr(meta, "model", None)


# ---------------------------------------------------------------- reading source

def _calls(func, params):
    """(kind, model_name_or_url, {field: ast node}, line) for each form-like call."""
    tree = _tree(func)
    if tree is None:
        return []
    # Module constants (BASE = "/api/v1/continents/") read like params.
    params = {**{k: v for k, v in getattr(func, "__globals__", {}).items()
                 if k.isupper() and isinstance(v, (str, int, float, bool))}, **params}
    dict_vars, url_vars = {}, {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name):
                    if isinstance(node.value, ast.Dict):
                        dict_vars[target.id] = node.value
                    elif isinstance(node.value, (ast.JoinedStr, ast.Constant, ast.BinOp)):
                        url_vars[target.id] = node.value
    out = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        if isinstance(fn, ast.Attribute) and fn.attr in CREATE_CALLS and node.keywords:
            owner = fn.value   # Model.objects
            name = owner.value.id if isinstance(owner, ast.Attribute) and isinstance(owner.value, ast.Name) else (
                owner.id if isinstance(owner, ast.Name) else None)
            fields = _kw_fields(node.keywords, dict_vars)
            if fn.attr in ("create_user", "create_superuser"):
                name = name if _model(name) else "User"
                if node.args:
                    fields.setdefault("username", node.args[0])
            out.append(("create", name, fields, node.lineno, params))
        elif isinstance(fn, ast.Name) and _model(fn.id) is not None and fn.id[:1].isupper() and node.keywords:
            out.append(("create", fn.id, _kw_fields(node.keywords, dict_vars), node.lineno, params))
        elif isinstance(fn, ast.Attribute) and fn.attr in HTTP and node.args:
            body = node.args[1] if len(node.args) > 1 else next(
                (k.value for k in node.keywords if k.arg in ("data", "json")), None)
            if isinstance(body, ast.Name):
                body = dict_vars.get(body.id, body)
            if not isinstance(body, ast.Dict):
                continue
            arg = node.args[0]
            if isinstance(arg, ast.Name) and arg.id in url_vars and arg.id not in params:
                arg = url_vars[arg.id]
            url = _url(arg, params)
            fields = _dict_fields(body, dict_vars)
            # Company workspace records: {"data": {...}} holds the form fields.
            if set(fields) == {"data"} and isinstance(fields["data"], ast.Dict):
                fields = _dict_fields(fields["data"], dict_vars)
            out.append((fn.attr.upper(), url, fields, node.lineno, params))
    out.sort(key=lambda c: c[3])
    return out


def _kw_fields(keywords, dict_vars):
    fields = {}
    for k in keywords:
        if k.arg:
            fields[k.arg] = k.value
        elif isinstance(k.value, ast.Name) and k.value.id in dict_vars:      # **payload
            fields.update(_dict_fields(dict_vars[k.value.id], dict_vars))
        elif isinstance(k.value, ast.Dict):
            fields.update(_dict_fields(k.value, dict_vars))
    return fields


def _dict_fields(node, dict_vars):
    fields = {}
    for key, value in zip(node.keys, node.values):
        if key is None:                                                      # {**base, ...}
            if isinstance(value, ast.Name) and value.id in dict_vars:
                fields.update(_dict_fields(dict_vars[value.id], dict_vars))
            elif isinstance(value, ast.Dict):
                fields.update(_dict_fields(value, dict_vars))
        elif isinstance(key, ast.Constant) and isinstance(key.value, str):
            fields[key.value] = value
    return fields


def _url(node, params):
    value = _const(node, params)
    if isinstance(value, str):
        return value
    if isinstance(node, ast.JoinedStr):
        parts = []
        for v in node.values:
            if isinstance(v, ast.Constant):
                parts.append(str(v.value))
            else:
                c = _const(v.value, params)
                parts.append(str(c) if c is not None else "{" + ast.unparse(v.value) + "}")
        return "".join(parts)
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        return _url(node.left, params) + _url(node.right, params)
    try:
        return "{" + ast.unparse(node) + "}"
    except Exception:  # noqa: BLE001
        return "?"


def _value(node, params, records):
    """Readable value: literals as-is; fixture.unique_id -> 'Asia (continent)'."""
    c = _const(node, params)
    if c is not None or (isinstance(node, ast.Constant) and node.value is None):
        return c if isinstance(c, (str, int, float, bool)) or c is None else _clip(repr(c))
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id in records:
        rec = records[node.value.id]
        if node.attr in ("unique_id", "pk", "id"):
            return f"→ {rec} ({node.value.id})" if rec else f"→ {node.value.id}"
        return f"{node.value.id}.{node.attr}"
    if isinstance(node, ast.Name) and node.id in records:
        return f"→ {records[node.id] or node.id} ({node.id})"
    try:
        return _clip(ast.unparse(_Fill(params).visit(copy.deepcopy(node))))
    except Exception:  # noqa: BLE001
        return "?"


def _display(fields, params):
    """A record's short name: its first text field ('Asia', 'Test Company')."""
    for name, node in fields.items():
        c = _const(node, params)
        if isinstance(c, str) and c and not name.endswith("_id") and name not in ("password", "email"):
            return _clip(c)[:40]
    return ""


# ---------------------------------------------------------------- forms

GENERIC_SEGMENTS = {"api", "v1", "t", "ws", "super", "records", "modules", "projects", "desktop", "mobile"}


def _url_form_name(url):
    """'/api/t/{c.slug}/ws/projects/{pid}/modules/assets/records/' -> 'Assets'."""
    parts = [p for p in url.split("?")[0].strip("/").split("/")
             if p and "{" not in p and p not in GENERIC_SEGMENTS]
    if not parts:
        return "Request"
    return parts[-1].replace("-", " ").replace("_", " ").strip().capitalize()


def _form(kind, target, fields, params, records, source, main):
    model = _model(target) if kind == "create" else _model_for_url(target)
    given = {k: _value(v, params, records) for k, v in fields.items()}
    if model is None:
        name = target if kind == "create" else _url_form_name(target)
        return {"form": name or "Record", "model": None, "module": "", "source": source, "main": main,
                "fields": [{"name": k, "label": _label(k), "value": v, "given": True, "required": False}
                           for k, v in given.items()]}
    rows, used = [], set()
    for f in _form_fields(model):
        key = f["name"] if f["name"] in given else (f["attname"] if f["attname"] in given else None)
        if key is not None:
            used.add(key)
            rows.append({"name": f["name"], "label": f["label"], "value": given[key], "given": True,
                         "required": f["required"], "type": f["type"]})
        else:
            rows.append({"name": f["name"], "label": f["label"], "value": f["default"], "given": False,
                         "required": f["required"], "type": f["type"]})
    # Keys sent that are not model columns (write-only serializer fields, password ...).
    for k, v in given.items():
        if k not in used:
            rows.append({"name": k, "label": _label(k), "value": "••••••" if "password" in k else v,
                         "given": True, "required": False, "extra": True})
    return {"form": _form_label(model), "model": f"{model._meta.app_label}.{model.__name__}",
            "module": _module_of(model), "source": source, "main": main, "fields": rows}


def build_inputs(item, params=None):
    """Forms the test fills in: from the test body first, then its fixtures."""
    params = dict(params or {})
    func = getattr(item, "function", None)
    info = getattr(item, "_fixtureinfo", None)
    defs = getattr(info, "name2fixturedefs", {}) or {}
    direct = set(getattr(info, "argnames", ()) or ())
    order = [n for n in (getattr(item, "fixturenames", ()) or ()) if n in defs]

    fixture_calls = {}
    for name in order:
        fdef = defs[name][-1]
        ffunc = getattr(fdef, "func", None)
        if ffunc is None or "site-packages" in (getattr(ffunc, "__code__", None) and ffunc.__code__.co_filename or ""):
            continue
        calls = [c for c in _calls(ffunc, params) if c[0] == "create"]
        if calls:
            fixture_calls[name] = calls
    # Short names of fixture records so references read as "Asia (continent)".
    records = {name: _display(calls[-1][2], calls[-1][4]) for name, calls in fixture_calls.items()}
    for name in order:
        records.setdefault(name, "")

    forms = []
    for kind, target, fields, _line, fparams in (_calls(func, params) if func else []):
        source = "Created in the test" if kind == "create" else f"{kind} {target}"
        forms.append(_form(kind, target, fields, fparams, records, source, True))
    # Fixtures the test asks for by name are the subject; the rest support them.
    body_forms = bool(forms)
    for name in sorted(fixture_calls, key=lambda n: (n not in direct, order.index(n))):
        for kind, target, fields, _line, fparams in fixture_calls[name]:
            forms.append(_form(kind, target, fields, fparams, records, f"Prepared by fixture '{name}'",
                               name in direct and not body_forms))
    if forms and not any(f["main"] for f in forms):
        forms[0]["main"] = True
    return forms[:MAX_FORMS]
