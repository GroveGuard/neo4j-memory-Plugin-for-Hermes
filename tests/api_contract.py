"""SDK contract check: does the installed (or a given wheel's) neo4j-agent-memory offer everything the plugin calls?

    python tests/api_contract.py                 # checks the installed package (run it in Hermes' Python)
    python tests/api_contract.py path/to.whl ... # checks wheels (no install, no imports: the source is parsed)

It parses the SDK source with `ast`, so it needs no heavy imports. It verifies the existence of the methods, the
parameter names the plugin passes and the MemorySettings fields. It does NOT run the SDK or test behaviour.
"""
import ast
import importlib.util
import sys
import zipfile
from pathlib import Path

METHODS = {   # class -> method -> parameters the plugin passes by keyword (or positionally by name)
    "MemoryClient": {"get_context": ["query", "session_id", "max_items", "include_short_term", "include_long_term", "include_reasoning"],
                     "__aenter__": [], "__aexit__": []},
    "LongTermMemory": {
        "search_entities": ["query", "entity_types", "limit", "threshold"],
        "get_entity_by_name": [],
        "add_entity": ["name", "entity_type", "description", "resolve", "deduplicate"],
        "add_relationship": [],
        "get_related_entities": ["depth"],
        "add_preference": ["category", "preference", "user_identifier", "generate_embedding", "metadata"],
        "get_preferences_for": [],
        "supersede_preference": [],
        "merge_duplicate_entities": [],
        "find_potential_duplicates": [],
        "review_duplicate": ["confirm"],
    },
    "ShortTermMemory": {"add_message": ["session_id", "role", "content", "user_identifier", "extract_entities"]},
}
ORDER = {}   # (class, method) -> ordered positional parameter names (without self)
SETTINGS_FIELDS = ["backend", "neo4j", "embedding", "llm", "extraction", "resolution", "geocoding", "enrichment"]


def load_sources(arg):
    if arg:
        z = zipfile.ZipFile(arg)
        return {n: z.read(n).decode("utf-8", "ignore") for n in z.namelist() if n.endswith(".py")}
    spec = importlib.util.find_spec("neo4j_agent_memory")
    if spec is None or not spec.submodule_search_locations:
        raise SystemExit("neo4j_agent_memory is not installed in this Python")
    root = Path(list(spec.submodule_search_locations)[0])
    return {str(p.relative_to(root.parent)): p.read_text(encoding="utf-8", errors="ignore") for p in root.rglob("*.py")}


def check_callsites(plugin_source, classes):
    """Every call the plugin makes on the SDK: keyword names and positional count must fit the real signature."""
    receivers = {"long_term": "LongTermMemory", "short_term": "ShortTermMemory"}
    problems = []
    for node in ast.walk(ast.parse(plugin_source)):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
            continue
        method, recv = node.func.attr, node.func.value
        cls = None
        if isinstance(recv, ast.Attribute) and recv.attr in receivers:
            cls = receivers[recv.attr]
        elif method == "get_context" and isinstance(recv, ast.Name) and recv.id == "client":
            cls = "MemoryClient"
        if not cls or cls not in classes or method not in classes[cls]:
            continue
        have = classes[cls][method]
        order = ORDER.get((cls, method), [])
        bad = [k.arg for k in node.keywords if k.arg and k.arg not in have and "**" not in have]
        if bad:
            problems.append(f"line {node.lineno}: {cls}.{method}() called with unknown keyword(s) {bad}")
        if len(node.args) > len(order) and "**" not in have and not any(isinstance(a, ast.Starred) for a in node.args):
            problems.append(f"line {node.lineno}: {cls}.{method}() called with {len(node.args)} positional arguments, the SDK takes {len(order)}")
    return problems


def check(sources, plugin_source=None):
    classes, funcs, graph_like, property_names = {}, {}, set(), set()
    for name, text in sources.items():
        try:
            tree = ast.parse(text)
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef):
                methods = {}
                for fn in node.body:
                    if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        a = fn.args
                        methods[fn.name] = ({x.arg for x in a.posonlyargs + a.args + a.kwonlyargs} | ({"**"} if a.kwarg else set()))
                        ORDER[(node.name, fn.name)] = [x.arg for x in (a.posonlyargs + a.args)][1:]
                        if any(isinstance(d, ast.Name) and d.id == "property" for d in fn.decorator_list):
                            property_names.add((node.name, fn.name))
                # prefer the real implementation (the largest class with that name, not the NAMS proxy with **kwargs)
                if node.name not in classes or len(methods) > len(classes[node.name]):
                    classes[node.name] = methods
                if {"execute_read", "execute_write"} <= set(methods):
                    graph_like.add(node.name)
            elif isinstance(node, ast.FunctionDef) and node.name == "from_provider":
                funcs["from_provider"] = True
        if "class SentenceTransformersProvider" in text:
            funcs["SentenceTransformersProvider"] = True
    problems = []
    for cls, ms in METHODS.items():
        if cls not in classes:
            problems.append(f"class {cls} not found"); continue
        for m, params in ms.items():
            have = classes[cls].get(m)
            if have is None:
                problems.append(f"{cls}.{m} missing"); continue
            missing = [p for p in params if p not in have and "**" not in have]
            if missing:
                problems.append(f"{cls}.{m}: parameter(s) {missing} missing")
    if ("MemoryClient", "graph") not in property_names and "graph" not in classes.get("MemoryClient", {}):
        problems.append("MemoryClient.graph (raw Cypher accessor) missing")
    if not graph_like:
        problems.append("no graph client with execute_read/execute_write found")
    for f in ("from_provider", "SentenceTransformersProvider"):
        if f not in funcs:
            problems.append(f"{f} missing")
    settings = "\n".join(t for t in sources.values() if "class MemorySettings" in t)
    for fld in SETTINGS_FIELDS:
        if f"{fld}:" not in settings.split("class MemorySettings", 1)[-1][:6000]:
            problems.append(f"MemorySettings.{fld} missing")
    if plugin_source:
        problems += check_callsites(plugin_source, classes)
    return problems


if __name__ == "__main__":
    targets = sys.argv[1:] or [None]
    plugin = Path(__file__).resolve().parent.parent / "__init__.py"
    plugin_source = plugin.read_text(encoding="utf-8") if plugin.exists() else None
    bad = 0
    for t in targets:
        problems = check(load_sources(t), plugin_source)
        label = Path(t).name if t else "installed neo4j-agent-memory"
        print(f"{'ok  ' if not problems else 'FAIL'} {label}" + ("" if not problems else ": " + "; ".join(problems)))
        bad += bool(problems)
    sys.exit(1 if bad else 0)
