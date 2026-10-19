import os, sys, json, time, uuid, io, types, argparse, contextlib, importlib.util, shutil, subprocess, tempfile
from pathlib import Path
sys.path.insert(0, ".")
HOME = os.path.abspath("hh_v9/profiles/clippy"); shutil.rmtree("hh_v9", ignore_errors=True); os.makedirs(HOME)
os.environ.update(NEO4J_URI="neo4j+s://launch", NEO4J_USERNAME="launch-user", NEO4J_PASSWORD="launch-pw", NEO4J_DATABASE="launch-db", HERMES_HOME=HOME)
import neo4j_agent_memory as sdk
NS = sdk.NS; n = 0
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
def ok(c, m):
    global n
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
    n += 1
def pref_env():
    for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]

print("== the SDK call that was wrong (real signature, strict stub)")
import plug.neo4j_memory_plugin as core
pref_env(); st = sdk.Store(); st.meta["embedding_model"] = MODEL; sdk.MemoryClient.seed = st; sdk.MemoryClient.fail = False
os.environ["NEO4J_MEMORY_STORE_MESSAGES"] = "1"
p = core.Neo4jMemoryProvider(); p.initialize("s1", user_id="alice"); time.sleep(0.5)
p.sync_turn("Hallo Welt", "Hallo zurueck"); time.sleep(0.6)
out = p.prefetch("Was haben wir vorhin besprochen")
ok("Recent messages:" in out and "- user: Hallo Welt" in out, "raw-message recall calls MemoryClient.get_context with max_items (the old `limit=` raised TypeError)")
ok(p._fail_count == 0, "no failure was recorded for the recall call")
import inspect
sig = inspect.signature(sdk.MemoryClient.get_context)
ok("limit" not in sig.parameters and "max_items" in sig.parameters, "the stub now has the real signature, so a wrong keyword fails the test suite")
p.shutdown(); pref_env()

print("== credentials are read only through Hermes' resolver")
def scope_stub():
    m = types.ModuleType("agent.secret_scope"); m.MULTIPLEX = False; m.SCOPE = None
    def get_secret(name):
        if m.SCOPE is not None: return m.SCOPE.get(name)
        if m.MULTIPLEX: raise RuntimeError(f"get_secret({name!r}) called with no profile secret scope active")
        return os.environ.get(name)           # single-profile process: Hermes' own fallback
    m.get_secret = get_secret; return m
count = [0]
def load(path, stub):
    count[0] += 1
    saved = sys.modules.get("agent.secret_scope")
    if stub is None: sys.modules.pop("agent.secret_scope", None)
    else: sys.modules["agent.secret_scope"] = stub
    try:
        spec = importlib.util.spec_from_file_location(f"under_test_{count[0]}", path)
        mod = importlib.util.module_from_spec(spec); spec.loader.exec_module(mod)
    finally:
        sys.modules.pop("agent.secret_scope", None)
        if saved is not None: sys.modules["agent.secret_scope"] = saved
    return mod
m0 = load("plug/neo4j_memory_plugin.py", None)
ok(m0._env("NEO4J_PASSWORD") == "launch-pw" and m0.Neo4jMemoryProvider().is_available(), "standalone (no agent.secret_scope): environment fallback")
stub = scope_stub(); m1 = load("plug/neo4j_memory_plugin.py", stub)
ok(m1._env("NEO4J_PASSWORD") == "launch-pw" and m1.Neo4jMemoryProvider().is_available(), "single-profile Hermes: get_secret falls back to the process environment, plugin works as before")
stub.MULTIPLEX = True
ok(m1._env("NEO4J_PASSWORD") == "" and m1._env("NEO4J_URI") == "" and not m1.Neo4jMemoryProvider().is_available(), "multiplexed gateway without a scope: fail closed, the launch profile's credentials are NOT used")
stub.SCOPE = {"NEO4J_PASSWORD": "pw-b", "NEO4J_URI": "neo4j+s://profile-b", "NEO4J_USERNAME": "user-b", "NEO4J_DATABASE": "db-b"}
sdk.MemoryClient.seed = sdk.Store()
p = m1.Neo4jMemoryProvider(); p.initialize("s1", user_id="alice"); time.sleep(0.4)
cred = p._build_settings().kw["neo4j"]
ok(cred == {"uri": "neo4j+s://profile-b", "username": "user-b", "password": "pw-b", "database": "db-b"}, "inside a profile scope the plugin uses that profile's values, not the launch environment")
stub.SCOPE = None
ok(p._build_settings().kw["neo4j"]["password"] == "pw-b", "values were cached at initialize(): the event-loop thread needs no scope later"); p.shutdown()
# the CLI follows the same rule (it imports agent.secret_scope when called, so the stub must be present during the call)
cli_mod = load("plug_dir/cli.py", None)
def setting(name, stub_module):
    saved = sys.modules.get("agent.secret_scope")
    if stub_module is None: sys.modules.pop("agent.secret_scope", None)
    else: sys.modules["agent.secret_scope"] = stub_module
    try: return cli_mod._setting(name)
    finally:
        sys.modules.pop("agent.secret_scope", None)
        if saved is not None: sys.modules["agent.secret_scope"] = saved
stub.SCOPE = None; stub.MULTIPLEX = False
ok(setting("NEO4J_PASSWORD", None) == "launch-pw", "CLI standalone (no Hermes): environment fallback")
ok(setting("NEO4J_PASSWORD", stub) == "launch-pw", "CLI single-profile: via get_secret (its own environment fallback)")
stub.MULTIPLEX = True
ok(setting("NEO4J_PASSWORD", stub) == "" and setting("NEO4J_URI", stub) == "", "CLI under multiplexing without a scope: fails closed, no os.environ fallback")
stub.SCOPE = {"NEO4J_PASSWORD": "pw-b"}
ok(setting("NEO4J_PASSWORD", stub) == "pw-b", "CLI inside a scope: the profile's value")

print("== cli.py loads the provider without touching the module table")
before = set(sys.modules)
spec = importlib.util.spec_from_file_location("cli_dir", "plug_dir/cli.py"); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
mod = cli._core()
ok(hasattr(mod, "Neo4jMemoryProvider") and "neo4j_memory_core" not in sys.modules, "fallback path (no package): provider loaded, nothing registered in sys.modules")
os.makedirs("pkgtest", exist_ok=True); shutil.copy("plug_dir/__init__.py", "pkgtest/__init__.py"); shutil.copy("plug_dir/cli.py", "pkgtest/cli.py")
import pkgtest.cli as pc
ok(pc._core() is sys.modules["pkgtest"] and "neo4j_memory_core" not in sys.modules, "inside a package (how Hermes loads it): the already-imported package module is reused")
def run(argv, stdin=None):
    ap = argparse.ArgumentParser(); cli.register_cli(ap); a = ap.parse_args(argv); b = io.StringIO(); old = sys.stdin
    if stdin is not None: sys.stdin = stdin
    try:
        with contextlib.redirect_stdout(b): a.func(a)
    finally: sys.stdin = old
    return b.getvalue()
class Tty(io.StringIO):
    def isatty(self): return True
try: run(["tool", "neo4j_memory_search"], stdin=Tty("")); ok(False, "x")
except SystemExit as e: ok("--args-file" in str(e), "the tool command never waits for a person: a terminal stdin fails cleanly")

print("== catalog lint")
import importlib
lint_spec = importlib.util.spec_from_file_location("lint_catalog", "lint_catalog.py"); lint = importlib.util.module_from_spec(lint_spec); lint_spec.loader.exec_module(lint)
tmp = Path(tempfile.mkdtemp()); shutil.copy("plug_dir/__init__.py", tmp / "__init__.py"); shutil.copy("plug_dir/cli.py", tmp / "cli.py")
errors, warnings = lint.lint(tmp)
ok(errors == [] and len(warnings) == 2, f"current sources: no errors, two disclosed warnings {[w.split(':')[0] + ':' + w.split(':')[2][:28] for w in warnings]}")
shutil.copy("fixtures/cli_0_6_0_before_catalog_fixes.py.txt", tmp / "cli.py")
errors, _ = lint.lint(tmp)
ok(any("sys.modules" in e for e in errors) and any("__import__" in e for e in errors), f"the release 0.6.0 cli.py is rejected by the lint (sys.modules write, __import__): {len(errors)} errors")
bad = tmp / "cli.py"; bad.write_text(bad.read_text() + "\nimport base64\nsetattr(os, 'x', 1)\nos.environ['A'] = 'b'\nsubprocess.run('ls', shell=True)\nopen('.env')\n")
errors, _ = lint.lint(tmp)
ok(all(any(k in e for e in errors) for k in ("base64", "setattr", "os.environ[...]", "shell=True", "credential file")), "synthetic violations are all caught")
(tmp / "payload.pyc").write_bytes(b"\x00\x00"); (tmp / "tool.bin").write_bytes(b"\x7fELF....")
errors, _ = lint.lint(tmp); ok(any("payload.pyc" in e for e in errors) and any("tool.bin" in e for e in errors), "bytecode and binary files are caught (rule 17)")
print(f"\nALL {n} CATALOG/SDK-CONTRACT CHECKS PASSED")
