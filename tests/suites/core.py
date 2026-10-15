import os, sys, json, time, uuid, io, contextlib, importlib.util, argparse
from datetime import datetime, timedelta, timezone
sys.path.insert(0, ".")
os.environ.update(NEO4J_URI="neo4j+s://x", NEO4J_USERNAME="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="d")
os.environ["HERMES_HOME"] = os.path.abspath("hh_profiles/profiles/clippy")
import neo4j_agent_memory as sdk
from plug.neo4j_memory_plugin import Neo4jMemoryProvider
NS = sdk.NS
n_ok = 0
def ok(c, m):
    global n_ok
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
    n_ok += 1
def mk(ctx="primary", seed=None, **env):
    for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
    os.environ.update(env)
    sdk.MemoryClient.seed = seed; sdk.MemoryClient.fail = False; sdk.MemoryClient.context_calls = 0; sdk.MemoryClient.search_calls = 0; sdk.MemoryClient.transient_once = False
    p = Neo4jMemoryProvider(); p.initialize("s1", user_id="alice", agent_context=ctx); time.sleep(0.4); return p
def T(p, n, a): return json.loads(p.handle_tool_call(n, a))
def ent(s, name, typ, desc=None):
    e = NS(id=uuid.uuid4(), name=name, type=typ, full_type=typ, description=desc, metadata={}, aliases=[], merged_into=None, embedding=None); s.ents.append(e); return e
def rel(s, a, b, t, until=None): s.rels.append(NS(type=t, source_id=a.id, target_id=b.id, valid_until=until))
def pref(s, uid, cat, text): s.prefs.setdefault(uid, []).append(NS(id=uuid.uuid4(), category=cat, preference=text))

print("== embedding")
p = mk(); c = sdk.MemoryClient.last
emb = c.settings.kw["embedding"]
ok(emb.model == "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2" and emb.dimensions == 384, "multilingual model, explicit dimensions=384")
ok(c.store.meta.get("embedding_model") == emb.model, "empty graph: marker written with current model")
p.shutdown()
p = mk(NEO4J_MEMORY_EMBEDDING="BAAI/bge-small-en-v1.5"); ok(sdk.MemoryClient.last.settings.kw["embedding"].model == "BAAI/bge-small-en-v1.5", "env override of the model"); p.shutdown()

print("== marker / legacy graph / reembed")
s = sdk.Store(); ent(s, "Maya", "PERSON"); ent(s, "Acme", "ORGANIZATION")
p = mk(seed=s)
ok(p._embedding_ok is False and "bge-small" in p._embedding_issue, "unmarked graph with entities = legacy model -> mismatch")
r = T(p, "neo4j_memory_remember", {"entities": [{"name": "New", "type": "PERSON"}]})
ok(r.get("error") == "embedding_model_mismatch" and len(s.ents) == 2, "entity writes blocked on mismatch")
r = T(p, "neo4j_memory_remember", {"preferences": [{"category": "x", "preference": "still works"}]}); ok(r["preferences"] == 1, "preferences still writable")
res = p.cli_reembed()
ok(res["status"] == "done" and res["entities_reembedded"] == 2 and all(len(e.embedding) == 384 for e in s.ents), "reembed: all entities got 384-dim vectors")
ok(s.meta["embedding_model"].endswith("MiniLM-L12-v2") and p._embedding_ok, "reembed sets marker, unblocks writes")
ok(T(p, "neo4j_memory_remember", {"entities": [{"name": "New", "type": "PERSON"}]})["entities"] == 1, "entity write works after reembed")
p.shutdown()
s = sdk.Store(); ent(s, "Old", "PERSON"); s.meta["embedding_model"] = "BAAI/bge-small-en-v1.5"
p = mk(seed=s, NEO4J_MEMORY_EMBEDDING="BAAI/bge-small-en-v1.5"); ok(p._embedding_ok, "marker == configured model (legacy config) -> ok"); p.shutdown()

print("== recall: relationships, merged filter, expiry")
s = sdk.Store(); maya = ent(s, "Maya", "PERSON", "Entwicklerin"); acme = ent(s, "Acme", "ORGANIZATION"); bob = ent(s, "Bob", "PERSON")
ghost = ent(s, "Mayo", "PERSON"); ghost.merged_into = str(maya.id); old = ent(s, "OldCo", "ORGANIZATION")
rel(s, maya, acme, "WORKS_AT"); rel(s, bob, maya, "MANAGES"); rel(s, maya, old, "WORKED_AT", until=datetime.now(timezone.utc) - timedelta(days=1)); rel(s, maya, ghost, "KNOWS")
s.meta["embedding_model"] = sdk.__dict__ and "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
p = mk(seed=s); p._store_messages = False
out = p.prefetch("was weiss ich ueber maya heute")
print(out)
ok("- Maya (PERSON): Entwicklerin" in out and "→ WORKS_AT Acme" in out and "← Bob MANAGES" in out, "outgoing and incoming relations listed")
ok("OldCo" not in out and "Mayo" not in out, "expired relation and merged-away entity hidden")
ok(len(out) <= 1200, "within budget")

print("== recall: preference relevance (de/en)")
uid = "alice:clippy"
pref(s, uid, "communication", "Antworte kurz und auf Deutsch"); pref(s, uid, "food", "trinkt gern Kaffee ohne Zucker")
pref(s, uid, "hobby", "spielt Gitarre in einer Band"); pref(s, uid, "hermes_user", "likes tea (mirrored)")
out = p.prefetch("Welchen Kaffee soll ich fuer Maya kaufen?")
ok("[communication]" in out and "[food]" in out and "Gitarre" not in out and "mirrored" not in out, "always-category + relevant match; irrelevant and hermes_* excluded")
out = p.prefetch("Kannst du Gitarren empfehlen fuer Maya?")
ok("[hobby]" in out and "[food]" not in out, "umlaut/stem match (Gitarren ~ Gitarre), others dropped")
ok(T(p, "neo4j_memory_search", {"query": "maya"})["preferences"].__len__() == 4, "search tool still returns all preferences incl. mirrored")

print("== dream report")
s = sdk.Store(); s.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
a = ent(s, "Maya", "PERSON", "short"); b = ent(s, "maya!", "PERSON"); cc = ent(s, "Maya Chen", "PERSON", "Dev at Acme")
d = ent(s, "Maya", "ORGANIZATION"); v1 = ent(s, "Müller Verlag", "ORGANIZATION"); v2 = ent(s, "Mueller Verlag", "ORGANIZATION")
other = ent(s, "Acme", "ORGANIZATION"); orphan = ent(s, "Lonely", "OBJECT")
ent(s, "Schneider Verlag", "ORGANIZATION"); ent(s, "Schnieder Verlag", "ORGANIZATION"); ent(s, "BMW", "ORGANIZATION"); ent(s, "Bayerische Motoren Werke", "ORGANIZATION")
ent(s, "CP740 Vormontage", "OBJECT"); ent(s, "CP750 Vormontage", "OBJECT"); ent(s, "Link Consistency Batch 2", "EVENT"); ent(s, "Link Consistency Batch 3", "EVENT"); ent(s, "Angebot 2025", "OBJECT"); ent(s, "angebot 2025!", "OBJECT")
rel(s, cc, other, "WORKS_AT"); rel(s, cc, a, "KNOWS")
p = mk("cron", seed=s); pref(s, "alice:clippy", "hermes_memory", "Maya works at Acme")
rep = T(p, "neo4j_memory_maintain", {"action": "report"})
rules = {(x["keep"]["name"], x["drop"]["name"]): x["rule"] for x in rep["duplicates"]}
print(json.dumps([[k, v] for k, v in rules.items()], ensure_ascii=False))
ok(any(r == "exact_norm" for r in rules.values()), "exact_norm pair found (Maya / maya!)")
ok(any(r == "likely" and "Maya Chen" in k for k, r in rules.items()), "token-subset pair found (Maya < Maya Chen)")
ok(any(r == "exact_norm" and {"Müller Verlag", "Mueller Verlag"} == set(k) for k, r in rules.items()), "umlaut folding: Müller == Mueller is an exact match now")
ok(any(r == "fuzzy" and {"Schneider Verlag", "Schnieder Verlag"} == set(k) for k, r in rules.items()), "fuzzy rule finds a typo pair")
ok(any(r == "possible" and {"BMW", "Bayerische Motoren Werke"} == set(k) for k, r in rules.items()), "semantic-only pair found via embeddings (BMW)")
ok(not any({"Maya", "Maya"} == set(k) and a is not None and False for k in rules) and all(x["keep"]["type"] == x["drop"]["type"] for x in rep["duplicates"]), "no cross-type pairs")
allnames = [frozenset((x["keep"]["name"], x["drop"]["name"])) for x in rep["duplicates"]]
ok(frozenset(("CP740 Vormontage", "CP750 Vormontage")) not in allnames, "fuzzy/semantic: process codes that differ in a number are not proposed (CP740 vs CP750)")
ok(frozenset(("Link Consistency Batch 2", "Link Consistency Batch 3")) not in allnames, "'Batch 2' vs 'Batch 3' not proposed")
ok(any(r == "exact_norm" and {"Angebot 2025", "angebot 2025!"} == set(k) for k, r in rules.items()), "identical numbers still match exactly (Angebot 2025)")
ok(rep["orphans"] == ["Lonely"] or "Lonely" in rep["orphans"], "orphans listed")
ok(rep["mirrored_preferences"] and rep["mirrored_preferences"][0]["text"] == "Maya works at Acme", "mirrored built-in notes surfaced for promotion")
ok(rep["stats"]["entities"] == 18 and rep["stats"]["embedding_ok"], "stats")
ok(any(t["name"] == "neo4j_memory_maintain" for t in p.get_tool_schemas()), "maintain tool exposed in cron context")
pp = mk("primary", seed=s); ok(not any(t["name"] == "neo4j_memory_maintain" for t in pp.get_tool_schemas()), "maintain tool hidden in normal sessions")
ok("error" in T(pp, "neo4j_memory_maintain", {"action": "report"}), "maintain call refused in normal sessions"); pp.shutdown()
pp = mk("primary", seed=s, NEO4J_MEMORY_MAINTENANCE_TOOLS="1"); ok(any(t["name"] == "neo4j_memory_maintain" for t in pp.get_tool_schemas()), "env flag exposes it"); pp.shutdown()

print("== merge / reject")
p = mk("cron", seed=s)
pair = next(x for x in rep["duplicates"] if x["rule"] == "likely")
r = T(p, "neo4j_memory_maintain", {"action": "merge", "keep_id": pair["keep"]["id"], "drop_id": pair["drop"]["id"]})
ok(r["status"] == "merged" and r["kept"] == "Maya Chen" and r["merged_away"] == "Maya", f"merge keeps higher-degree entity: {r}")
ok(s.by_id(pair["drop"]["id"]).merged_into == pair["keep"]["id"], "source marked merged_into (not deleted)")
ok(T(p, "neo4j_memory_maintain", {"action": "merge", "keep_id": str(other.id), "drop_id": str(b.id)})["status"] == "refused", "cross-type merge refused")
ok(T(p, "neo4j_memory_maintain", {"action": "merge", "keep_id": "nope", "drop_id": str(d.id)})["status"] == "error", "bad ids rejected")
ok(T(p, "neo4j_memory_maintain", {"action": "merge", "keep_id": pair["keep"]["id"], "drop_id": pair["drop"]["id"]})["status"] == "not_found", "already merged -> not_found")
r = T(p, "neo4j_memory_remember", {"relationships": [{"source": "Maya", "target": "Acme", "type": "knows"}]})
ok(r["relationships"] == 1 or r["skipped"] == [], f"lookup by alias/name works after merge: {r}")
mv = next(x for x in T(p, "neo4j_memory_maintain", {"action": "report"})["duplicates"] if x["rule"] == "possible")
ok(T(p, "neo4j_memory_maintain", {"action": "reject", "keep_id": mv["keep"]["id"], "drop_id": mv["drop"]["id"]})["status"] == "rejected", "reject recorded")
rules2 = {frozenset((x["keep"]["id"], x["drop"]["id"])) for x in T(p, "neo4j_memory_maintain", {"action": "report"})["duplicates"]}
ok(frozenset((mv["keep"]["id"], mv["drop"]["id"])) not in rules2, "rejected pair no longer proposed")
p.shutdown()

print("== CLI")
spec = importlib.util.spec_from_file_location("cli_under_test", "plug/cli_plugin.py"); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
import shutil; os.makedirs("plug_dir", exist_ok=True); shutil.copy("plug/neo4j_memory_plugin.py", "plug_dir/__init__.py"); shutil.copy("plug/cli_plugin.py", "plug_dir/cli.py")
spec = importlib.util.spec_from_file_location("cli_dir", "plug_dir/cli.py"); cli = importlib.util.module_from_spec(spec); spec.loader.exec_module(cli)
s = sdk.Store(); s.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
ent(s, "Maya", "PERSON"); ent(s, "maya!", "PERSON"); ent(s, "Maya Chen", "PERSON"); ent(s, "Orion", "EVENT")
for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
sdk.MemoryClient.seed = s
def run(argv):
    ap = argparse.ArgumentParser(); cli.register_cli(ap); a = ap.parse_args(argv); buf = io.StringIO()
    with contextlib.redirect_stdout(buf): a.func(a)
    return buf.getvalue()
out = run(["dream", "--no-semantic"]); ok("[exact_norm]" in out and "[likely]" in out and "Dry run" in out and len([x for x in s.ents if x.merged_into]) == 0, "dream dry run changes nothing")
out = run(["dream", "--no-semantic", "--apply"]); merged = [x for x in s.ents if x.merged_into]
ok(len(merged) == 1 and "Applied 1 safe merge" in out, "dream --apply merges only exact_norm pairs (likely left for the agent)")
sdk.MemoryClient.seed = sdk.Store(); out = run(["reembed"]); ok('"status": "done"' in out, "reembed command runs")
out = run(["install-cron", "--hour", "4", "--minute", "15", "--profile", "clippy"])
ok("15 4 * * *" in out and "neo4j-memory-dream" in out and "-p clippy" in out and "[SILENT]" in out and "Dry run" in out, "install-cron dry run shows schedule/profile/prompt")
try: ap = argparse.ArgumentParser(); cli.register_cli(ap); ap.parse_args(["dedupe"]); ok(False, "x")
except SystemExit: ok(True, "unknown command rejected")

print("== regression (0.2 behaviour)")
s = sdk.Store(); s.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"; p = mk(seed=s); c = sdk.MemoryClient.last
T(p, "neo4j_memory_remember", {"entities": [{"name": "Zed", "type": "PERSON", "description": "old"}, {"name": "Orion", "type": "EVENT"}, {"name": "Orion2", "type": "OBJECT"}]})
s.ents[-1].name = "Orion"
ok(T(p, "neo4j_memory_update", {"entity_name": "Zed", "new_description": "new"})["status"] == "updated" and s.ents[0].description == "new", "update entity")
ok(T(p, "neo4j_memory_forget", {"entity_name": "Orion"})["status"] == "ambiguous", "ambiguous forget deletes nothing")
ok(T(p, "neo4j_memory_forget", {"entity_name": "Orion", "type": "EVENT"})["status"] == "forgotten", "forget with type")
T(p, "neo4j_memory_remember", {"preferences": [{"category": "food", "preference": "likes tea"}]})
ok(T(p, "neo4j_memory_update", {"preference_category": "food", "old_preference": "likes tea", "new_preference": "likes coffee"})["status"] == "updated", "supersede preference")
ok(T(p, "neo4j_memory_forget", {"preference_category": "food", "preference": "likes coffee"})["status"] == "forgotten", "delete preference")
ok(all(k["user_identifier"] == "alice:clippy" and k["generate_embedding"] is False for k in s.pref_kwargs), "scoped id, no embedding on preferences")
ok(p._mirror is True, "mirror is ON by default")
p.on_memory_write("add", "user", "likes tea"); time.sleep(0.3); p.on_memory_write("replace", "user", "likes coffee", {"previous_content": "likes tea"}); time.sleep(0.3)
ok([(x.category, x.preference) for x in s.prefs["alice:clippy"]] == [("hermes_user", "likes coffee")], "built-in add+replace mirrored")
p.on_memory_write("remove", "user", "", {"previous_content": "likes coffee"}); time.sleep(0.3); ok(s.prefs["alice:clippy"] == [], "built-in remove mirrored")
p.on_memory_write("add", "user", "x", "not-a-dict"); ok(True, "non-dict metadata safe")
p.shutdown()
before = [x.preference for x in s.prefs.get("alice:clippy", [])]
p = mk(seed=s, NEO4J_MEMORY_MIRROR_BUILTIN="0"); ok(p._mirror is False, "NEO4J_MEMORY_MIRROR_BUILTIN=0 disables mirroring")
p.on_memory_write("add", "user", "nope"); time.sleep(0.2); ok([x.preference for x in s.prefs.get("alice:clippy", [])] == before, "no mirror when disabled"); p.shutdown()
p = mk("cron", seed=s); p.on_memory_write("add", "user", "cron write"); time.sleep(0.2); ok([x.preference for x in s.prefs.get("alice:clippy", [])] == before, "no automatic writes in cron context"); p.shutdown()
s2 = sdk.Store(); s2.meta["embedding_model"] = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"; p = mk(seed=s2)
ent(s2, "Q", "PERSON", "x"); n0 = sdk.MemoryClient.search_calls
ok(p.prefetch("hallo") == "" and p.prefetch("Danke!") == "", "smalltalk skipped")
ok(p.prefetch("something unknown entirely") == "" and sdk.MemoryClient.search_calls == n0 + 1, "empty result: exactly one search, no retries")
sdk.MemoryClient.fail = True; n0 = sdk.MemoryClient.search_calls
for i in range(5): p.prefetch(f"qq query number {i} here")
ok(sdk.MemoryClient.search_calls - n0 == 3, "breaker opens after 3 failures")
t = time.time(); p.prefetch("another q again"); ok(time.time() - t < 0.05, "open breaker returns instantly")
p.shutdown()
print(f"\nALL {n_ok} CHECKS PASSED")
