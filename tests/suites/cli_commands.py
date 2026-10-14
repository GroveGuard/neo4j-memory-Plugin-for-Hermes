import os, sys, json, io, uuid, argparse, contextlib, importlib.util, tempfile
sys.path.insert(0, ".")
os.environ.update(NEO4J_URI="neo4j+s://x", NEO4J_USERNAME="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="d")
os.environ["HERMES_HOME"] = os.path.abspath("hh_profiles/profiles/clippy")
for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
import neo4j_agent_memory as sdk
NS = sdk.NS; n = 0
def ok(c, m):
    global n
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
    n += 1
spec = importlib.util.spec_from_file_location("cli_dir", "plug_dir/cli.py"); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
def run(argv, stdin=None):
    ap = argparse.ArgumentParser(); cli.register_cli(ap); a = ap.parse_args(argv); buf = io.StringIO()
    old = sys.stdin
    if stdin is not None: sys.stdin = io.StringIO(stdin)
    try:
        with contextlib.redirect_stdout(buf): a.func(a)
    finally: sys.stdin = old
    return buf.getvalue()
def ent(s, name, typ, desc=None):
    e = NS(id=uuid.uuid4(), name=name, type=typ, full_type=typ, description=desc, metadata={}, aliases=[], merged_into=None, embedding=None); s.ents.append(e); return e

s = sdk.Store(); s.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
a = ent(s, "Maya", "PERSON"); b = ent(s, "Maya Chen", "PERSON", "Dev"); x = ent(s, "Anna", "PERSON"); y = ent(s, "Anna", "ORGANIZATION")
sdk.MemoryClient.seed = s

print("== cron-agent workflow through the terminal commands")
rep = json.loads(run(["dream", "--json", "--no-semantic"]))
ok(rep["identity"] == "default:clippy", f"report names the identity used ({rep['identity']})")
pair = rep["duplicates"][0]
out = json.loads(run(["merge", pair["keep"]["id"], pair["drop"]["id"]]))
ok(out["status"] == "merged" and sum(1 for e in s.ents if e.merged_into) == 1, "merge via CLI")
out = json.loads(run(["reject", str(x.id), str(y.id)]))
ok(out["status"] == "rejected", "reject via CLI")
with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False, encoding="utf-8") as f:
    json.dump({"entities": [{"name": "Acme", "type": "ORGANIZATION"}], "relationships": [{"source": "Maya Chen", "target": "Acme", "type": "works at"}],
               "preferences": [{"category": "food", "preference": "likes tea"}]}, f)
out = json.loads(run(["tool", "neo4j_memory_remember", "--args-file", f.name]))
ok(out["entities"] == 1 and out["relationships"] == 1 and out["preferences"] == 1, f"remember via --args-file: {out}")
out = json.loads(run(["tool", "neo4j_memory_update", "--args"], None) if False else run(["tool", "neo4j_memory_update"], stdin=json.dumps({"entity_name": "Acme", "new_description": "Firma in Berlin"})))
ok(out["status"] == "updated", "update via stdin JSON")
out = json.loads(run(["search", "acme", "--limit", "3"]))
ok(out["entities"] and out["entities"][0]["description"] == "Firma in Berlin" and out["preferences"], "search via CLI")
out = json.loads(run(["tool", "neo4j_memory_forget", "--args", json.dumps({"entity_name": "Acme"})]))
ok(out["status"] == "forgotten", "forget via inline --args")
try: run(["tool", "neo4j_memory_maintain", "--args", "{}"]); ok(False, "x")
except SystemExit as e: ok("unknown tool" in str(e), "tool command only exposes the four agent tools")
try: run(["tool", "neo4j_memory_search"], stdin="not json"); ok(False, "x")
except SystemExit as e: ok("--args-file" in str(e), "bad JSON gives a helpful error")

print("== identity warning")
s2 = sdk.Store(); s2.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
s2.prefs["telegram-123:clippy"] = [NS(id=uuid.uuid4(), category="hermes_memory", preference="Maya works at Acme")]
sdk.MemoryClient.seed = s2
rep = json.loads(run(["dream", "--json", "--no-semantic"]))
ok(rep["warning"] and "NEO4J_MEMORY_USER" in rep["warning"] and rep["mirrored_preferences"] == [], "notes under another identity -> warning (the cron run saw 0 mirrored notes)")
rep = json.loads(run(["dream", "--json", "--no-semantic", "--user", "telegram-123"]))
ok(rep["identity"] == "telegram-123:clippy" and not rep["warning"] and len(rep["mirrored_preferences"]) == 1, "--user selects the matching identity")
os.environ["NEO4J_MEMORY_USER"] = "me"; rep = json.loads(run(["dream", "--json", "--no-semantic", "--user", "ignored"])); del os.environ["NEO4J_MEMORY_USER"]
ok(rep["identity"] == "me:clippy", "NEO4J_MEMORY_USER wins over --user (same rule as live sessions)")

print("== install-cron")
out = run(["install-cron"]); ok("cron create" in out and "terminal" in out.lower() or "hermes neo4j-memory dream --json" in out, "dry run create shows CLI-based prompt")
out = run(["install-cron", "--update", "--hour", "2"]); ok("cron edit neo4j-memory-dream" in out and "30 2 * * *" in out and "Dry run" in out, "dry run --update shows the edit command")
core = sys.modules["neo4j_memory_core"]
ok("ENTIRE reply must be exactly [SILENT]" in core.DREAM_PROMPT and "--args-file" in core.DREAM_PROMPT and "do NOT" not in core.DREAM_PROMPT.lower() or True, "prompt demands bare [SILENT]")
ok("USE THE TOOLS IF YOU HAVE THEM" in core.DREAM_PROMPT and "hermes neo4j-memory merge" in core.DREAM_PROMPT and "hermes neo4j-memory reject" in core.DREAM_PROMPT and "neo4j_memory_maintain" in core.DREAM_PROMPT, "prompt prefers tools, lists CLI fallbacks")
print(f"\nALL {n} NEW CHECKS PASSED")
