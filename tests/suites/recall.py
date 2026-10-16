import os, sys, json, time, uuid, io, inspect, argparse, contextlib, importlib.util, shutil, threading
sys.path.insert(0, ".")
HOME = os.path.abspath("hh_v8/profiles/clippy"); shutil.rmtree("hh_v8", ignore_errors=True); os.makedirs(HOME)
os.environ.update(NEO4J_URI="neo4j+s://x", NEO4J_USERNAME="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="d", HERMES_HOME=HOME)
import neo4j_agent_memory as sdk
import agent.memory_provider as hermes_stub
import plug.neo4j_memory_plugin as core
from plug.neo4j_memory_plugin import Neo4jMemoryProvider
NS = sdk.NS; n = 0
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
def ok(c, m):
    global n
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
    n += 1
def mk(ctx="primary", seed=None, **env):
    for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
    os.environ.update(env)
    seed = seed or sdk.Store(); seed.meta.setdefault("embedding_model", MODEL)
    sdk.MemoryClient.seed = seed; sdk.MemoryClient.fail = False; sdk.MemoryClient.search_calls = 0; sdk.MemoryClient.transient_once = False
    p = Neo4jMemoryProvider(); p.initialize("s1", user_id="alice", agent_context=ctx); time.sleep(0.5); return p
def T(p, name, args): return json.loads(p.handle_tool_call(name, args))
def ent(s, name, typ, desc=None):
    e = NS(id=uuid.uuid4(), name=name, type=typ, full_type=typ, description=desc, metadata={}, aliases=[], merged_into=None, embedding=None); s.ents.append(e); return e
def pref(s, uid, cat, text): s.prefs.setdefault(uid, []).append(NS(id=uuid.uuid4(), category=cat, preference=text))
def cfg(text=None):
    path = os.path.join(HOME, "config.yaml")
    if text is None:
        if os.path.exists(path): os.remove(path)
    else: open(path, "w").write(text)
def entity_lines(out): return [l for l in out.splitlines() if l.startswith("- ")]

print("== budget levels (Hindsight recall_budget)")
s = sdk.Store()
for nm in ["Alpha", "Beta", "Gamma", "Delta", "Epsilon", "Zeta", "Eta", "Theta", "Iota"]: ent(s, nm, "OBJECT", "x")
q = "Alpha Beta Gamma Delta Epsilon Zeta Eta Theta Iota zusammen betrachten bitte"
for level, count, chars in (("low", 3, 700), ("mid", 5, 1200), ("high", 8, 2400)):
    p = mk(seed=s, NEO4J_MEMORY_RECALL_BUDGET=level); out = p.prefetch(q)
    ok(len(entity_lines(out)) == count and p._max_chars == chars, f"{level}: {count} entities, {chars} chars"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_RECALL_BUDGET="low", NEO4J_MEMORY_MAX_CHARS="300"); ok(p._max_chars == 300, "NEO4J_MEMORY_MAX_CHARS overrides the budget's character cap"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_RECALL_BUDGET="nonsense"); ok(p._budget["entities"] == 5, "unknown budget falls back to mid"); p.shutdown()

print("== relevance floors")
s = sdk.Store()
ent(s, "Plattform", "OBJECT", "Kubernetes Cluster Betrieb Handbuch")
ent(s, "Buero", "OBJECT", "Betrieb im Buero und Empfang")
p = mk(seed=s); out = p.prefetch("Kubernetes Cluster Betrieb")
ok("Plattform" in out and "Buero" not in out, "recall: a keyword hit far weaker than the best one is dropped (1 of 3 words vs 3 of 3)")
r = T(p, "neo4j_memory_search", {"query": "Kubernetes Cluster Betrieb"})
ok({e["name"] for e in r["entities"]} == {"Plattform", "Buero"}, "the explicit search tool does NOT apply the floors"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_MIN_TEXT_SCORE="0"); ok("Buero" in p.prefetch("Kubernetes Cluster Betrieb"), "NEO4J_MEMORY_MIN_TEXT_SCORE=0 switches the keyword floor off"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_MIN_TEXT_SCORE="kaputt"); ok("Buero" not in p.prefetch("Kubernetes Cluster Betrieb"), "an invalid value falls back to the default"); p.shutdown()
s = sdk.Store(); ent(s, "Maya", "PERSON", "Entwicklerin")
p = mk(seed=s, NEO4J_MEMORY_HYBRID="0"); ok("Maya" in p.prefetch("Was weiss ich ueber Maya bitte"), "vector channel finds the entity by default"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_HYBRID="0", NEO4J_MEMORY_MIN_SIMILARITY="0.99"); ok(p.prefetch("Was weiss ich ueber Maya bitte") == "", "NEO4J_MEMORY_MIN_SIMILARITY is passed to the vector search (nothing is injected)"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_HYBRID="0", NEO4J_MEMORY_MIN_SIMILARITY="abc"); ok("Maya" in p.prefetch("Was weiss ich ueber Maya bitte"), "an invalid similarity value is ignored"); p.shutdown()
p = mk(seed=sdk.Store()); ok(p.prefetch("Voellig unbekanntes Thema ohne Treffer") == "", "an unrelated question injects nothing"); p.shutdown()

print("== optional reranker")
s = sdk.Store()
ent(s, "Plattform", "OBJECT", "Kubernetes Cluster Betrieb Handbuch"); ent(s, "Buero", "OBJECT", "Betrieb im Buero und Empfang")
import sentence_transformers as sst
p = mk(seed=s, NEO4J_MEMORY_RERANKER="stub/cross", NEO4J_MEMORY_MIN_TEXT_SCORE="0", NEO4J_MEMORY_RERANK_MIN="0.5")
ok(p._reranker is not None and "stub/cross" in sst.CrossEncoder.loads, "the model is loaded in the background at connect time")
res = p.cli_recall("Kubernetes Cluster Betrieb"); cands = {c["name"]: c for c in res["candidates"]}; print(json.dumps(res["candidates"], ensure_ascii=False))
ok(cands["Plattform"]["kept"] and cands["Plattform"]["rerank"] == 1.0, "relevant candidate passes the reranker")
ok(not cands["Buero"]["kept"] and "reranker score 0.33 is below the floor 0.50" in cands["Buero"]["reason"], "weak candidate is dropped with the reason")
out = p.prefetch("Kubernetes Cluster Betrieb"); ok("Plattform" in out and "Buero" not in out, "recall uses the reranker floor")
r = T(p, "neo4j_memory_search", {"query": "Kubernetes Cluster Betrieb"}); ok([e["name"] for e in r["entities"]] == ["Plattform", "Buero"], "search tool: ordered by the reranker but not cut by the floor"); p.shutdown()
p = mk(seed=s, NEO4J_MEMORY_RERANKER="broken/model", NEO4J_MEMORY_MIN_TEXT_SCORE="0")
ok(p._reranker is None and "Plattform" in p.prefetch("Kubernetes Cluster Betrieb"), "a model that cannot be loaded is skipped; recall keeps working")
ok("NOT loaded" in p.cli_recall("Kubernetes")["settings"]["reranker"], "the diagnostics say that the reranker is not loaded"); p.shutdown()
p = mk(seed=s); ok(p._reranker is None and p.cli_recall("Kubernetes")["settings"]["reranker"] == "off", "off by default"); p.shutdown()

print("== background-only recall (recall_sync=0)")
s = sdk.Store(); ent(s, "Maya", "PERSON", "Entwicklerin")
p = mk(seed=s, NEO4J_MEMORY_RECALL_SYNC="0"); c0 = sdk.MemoryClient.search_calls
ok(p.prefetch("Was weiss ich ueber Maya bitte") == "" and sdk.MemoryClient.search_calls == c0, "cold cache: no waiting, no search")
p.queue_prefetch("Was weiss ich ueber Maya bitte"); time.sleep(0.7)
ok("Maya" in p.prefetch("Ganz andere naechste Frage hier"), "the recall prepared after the previous turn is used for the next one"); p.shutdown()
p = mk(seed=s); c0 = sdk.MemoryClient.search_calls; p.prefetch("Was weiss ich ueber Maya bitte"); ok(sdk.MemoryClient.search_calls > c0, "default: synchronous recall for the current message"); p.shutdown()

print("== digest switch and categories")
cfg("memory:\n  memory_enabled: false\n  user_profile_enabled: true\n")
dpath = os.path.join(HOME, "neo4j-memory-digest.md"); open(dpath, "w", encoding="utf-8").write("- [communication] kurz\n")
p = mk(); ok("## Core memory" in p.system_prompt_block(), "auto: MEMORY.md off -> digest in the prompt"); p.shutdown()
p = mk(NEO4J_MEMORY_DIGEST="0"); ok("## Core memory" not in p.system_prompt_block(), "NEO4J_MEMORY_DIGEST=0 turns it off"); p.shutdown()
cfg("memory:\n  memory_enabled: true\n  user_profile_enabled: true\n")
p = mk(); ok("## Core memory" not in p.system_prompt_block(), "auto: MEMORY.md on -> no digest"); p.shutdown()
p = mk(NEO4J_MEMORY_DIGEST="1"); ok("## Core memory" in p.system_prompt_block(), "NEO4J_MEMORY_DIGEST=1 forces it on"); p.shutdown()
cfg("memory:\n  memory_enabled: false\n  user_profile_enabled: false\n")
s = sdk.Store(); pref(s, "alice:clippy", "communication", "Antworte kurz"); pref(s, "alice:clippy", "style", "Ohne Floskeln"); pref(s, "alice:clippy", "environment", "Windows 11, Hermes Desktop"); pref(s, "alice:clippy", "execution_style", "Ohne Pausen arbeiten")
p = mk("cron", seed=s, NEO4J_MEMORY_DIGEST_CATEGORIES="communication, style")
r = T(p, "neo4j_memory_maintain", {"action": "digest"}); d = open(dpath, encoding="utf-8").read()
ok("[communication]" in d and "[style]" in d and "environment" not in d and "execution_style" not in d and r["targets"][0]["dropped_excluded"] == 2, "NEO4J_MEMORY_DIGEST_CATEGORIES keeps only behaviour categories in the digest"); p.shutdown()
os.remove(dpath); cfg(None)

print("== recall diagnostics (CLI)")
s = sdk.Store()
ent(s, "Plattform", "OBJECT", "Kubernetes Cluster Betrieb Handbuch"); ent(s, "Buero", "OBJECT", "Betrieb im Buero und Empfang")
sdk.MemoryClient.seed = s; s.meta["embedding_model"] = MODEL
for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
spec = importlib.util.spec_from_file_location("cli_dir", "plug_dir/cli.py"); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
def run(argv):
    ap = argparse.ArgumentParser(); cli.register_cli(ap); a = ap.parse_args(argv); b = io.StringIO()
    with contextlib.redirect_stdout(b): a.func(a)
    return b.getvalue()
out = run(["recall", "Kubernetes Cluster Betrieb"]); print(out)
ok("budget=mid" in out and "KEEP  Plattform" in out and "drop  Buero" in out and "keyword score 1.0 is below 40% of the best keyword hit (3.0)" in out and "Injected text" in out, "hermes neo4j-memory recall explains every candidate and shows the injected text")
out = run(["recall", "Voellig unbekanntes Thema"]); ok("nothing is injected for this question" in out, "recall says so when nothing would be injected")

print("== conformance with the Hermes memory-provider guide")
P = Neo4jMemoryProvider
def params(fn): return [(k, v.kind.name) for k, v in inspect.signature(fn).parameters.items()]
ok(params(P.initialize)[:2] == [("self", "POSITIONAL_OR_KEYWORD"), ("session_id", "POSITIONAL_OR_KEYWORD")] and params(P.initialize)[-1][1] == "VAR_KEYWORD", "initialize(session_id, **kwargs)")
ok([k for k, _ in params(P.prefetch)] == ["self", "query", "session_id"] and params(P.prefetch)[-1][1] == "KEYWORD_ONLY", "prefetch(query, *, session_id)")
ok([k for k, _ in params(P.queue_prefetch)] == ["self", "query", "session_id"], "queue_prefetch(query, *, session_id)")
ok([k for k, _ in params(P.sync_turn)] == ["self", "user_content", "assistant_content", "session_id", "messages"], "sync_turn(user, assistant, *, session_id, messages)")
ok([k for k, _ in params(P.on_memory_write)] == ["self", "action", "target", "content", "metadata"], "on_memory_write(action, target, content, metadata=None)")
ok([k for k, _ in params(P.handle_tool_call)] == ["self", "tool_name", "args", "kwargs"], "handle_tool_call(tool_name, args, **kwargs)")
ok([k for k, _ in params(P.save_config)] == ["self", "values", "hermes_home"], "save_config(values, hermes_home)")
ok(all(hasattr(P, m) for m in ("name", "is_available", "get_tool_schemas", "get_config_schema", "system_prompt_block", "shutdown")), "all required members exist")
src = open("plug/neo4j_memory_plugin.py", encoding="utf-8").read()
ok("threading.Thread(" not in src, "no bare threading.Thread in the plugin (only spawn_context_thread)")
hermes_stub.SPAWNED.clear()
s = sdk.Store(); ent(s, "Maya", "PERSON", "x"); p = mk(seed=s, NEO4J_MEMORY_STORE_MESSAGES="1")
p.sync_turn("hallo", "hi"); p.on_memory_write("add", "user", "likes tea"); p.queue_prefetch("Was weiss ich ueber Maya bitte"); time.sleep(0.6)
ok({"neo4j-memory-loop", "neo4j-memory-sync", "neo4j-memory-mirror", "neo4j-memory-prefetch"} <= set(hermes_stub.SPAWNED), f"every background thread goes through spawn_context_thread: {sorted(set(hermes_stub.SPAWNED))}")
p.shutdown()
os.environ.pop("NEO4J_MEMORY_STORE_MESSAGES", None)
# remove without previous_content must not delete anything
s = sdk.Store(); p = mk(seed=s); p.on_memory_write("add", "user", "likes tea"); time.sleep(0.4)
p.on_memory_write("remove", "user", "likes tea", {}); time.sleep(0.4)
ok([x.preference for x in s.prefs["alice:clippy"]] == ["likes tea"], "built-in remove without previous_content is not mirrored (no destructive guess)")
p.on_memory_write("remove", "user", "", {"previous_content": "likes tea"}); time.sleep(0.4)
ok(s.prefs["alice:clippy"] == [], "with previous_content it is mirrored"); p.shutdown()
# credentials: resolved once in initialize(), from the hermes_home kwarg
p = mk(seed=sdk.Store()); first = p._build_settings().kw["neo4j"]["password"]; os.environ["NEO4J_PASSWORD"] = "changed-later"
ok(p._build_settings().kw["neo4j"]["password"] == first == "p", "credentials are cached at initialize (the loop thread does not re-read a per-turn scope)"); os.environ["NEO4J_PASSWORD"] = "p"; p.shutdown()
other = os.path.abspath("hh_v8/other_profile"); os.makedirs(other)
open(os.path.join(other, "neo4j-memory.json"), "w").write(json.dumps({"uri": "neo4j+s://from-sidecar", "username": "su", "database": "sdb"}))
for k in ("NEO4J_URI", "NEO4J_USERNAME", "NEO4J_DATABASE"): os.environ.pop(k, None)
p = Neo4jMemoryProvider(); sdk.MemoryClient.seed = sdk.Store(); p.initialize("s1", user_id="alice", hermes_home=other); time.sleep(0.4)
cfgd = p._build_settings().kw["neo4j"]
ok(cfgd["uri"] == "neo4j+s://from-sidecar" and cfgd["username"] == "su" and cfgd["database"] == "sdb", "the sidecar is read from the hermes_home kwarg, not from a global location (profile isolation)"); p.shutdown()
import yaml
manifest = yaml.safe_load(open("plugin.yaml", encoding="utf-8"))
deps = " ".join(manifest["python_dependencies"])
ok(manifest["name"] == "neo4j-memory" and "neo4j-agent-memory" in deps and ">=0.7.0,<0.8" in deps, "plugin.yaml pins the SDK to a reviewed range")
ok(set(manifest["hooks"]) == {"prefetch", "queue_prefetch", "sync_turn", "on_memory_write", "shutdown"} and all(hasattr(P, h) for h in manifest["hooks"]), "plugin.yaml hooks match the implemented methods")
print(f"\nALL {n} RECALL/CONFORMANCE CHECKS PASSED")
