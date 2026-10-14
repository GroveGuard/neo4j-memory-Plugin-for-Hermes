import os, sys, json, time, uuid
sys.path.insert(0, ".")
os.environ.update(NEO4J_URI="neo4j+s://x", NEO4J_USERNAME="u", NEO4J_PASSWORD="p", NEO4J_DATABASE="d")
os.environ["HERMES_HOME"] = os.path.abspath("hh_v6/profiles/clippy"); os.makedirs(os.environ["HERMES_HOME"], exist_ok=True)
import neo4j_agent_memory as sdk
from plug.neo4j_memory_plugin import Neo4jMemoryProvider
NS = sdk.NS; n = 0
def ok(c, m):
    global n
    print(("PASS " if c else "FAIL ") + m)
    if not c: sys.exit(1)
    n += 1
MODEL = "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"
def mk(ctx="primary", seed=None, **env):
    for k in [k for k in os.environ if k.startswith("NEO4J_MEMORY_")]: del os.environ[k]
    os.environ.update(env)
    seed = seed or sdk.Store(); seed.meta.setdefault("embedding_model", MODEL)
    sdk.MemoryClient.seed = seed; sdk.MemoryClient.fail = False; sdk.MemoryClient.search_calls = 0; sdk.MemoryClient.transient_once = False
    p = Neo4jMemoryProvider(); p.initialize("s1", user_id="alice", agent_context=ctx); time.sleep(0.4); return p
def T(p, name, args): return json.loads(p.handle_tool_call(name, args))
def ent(s, name, typ, desc=None):
    e = NS(id=uuid.uuid4(), name=name, type=typ, full_type=typ, description=desc, metadata={}, aliases=[], merged_into=None, embedding=None); s.ents.append(e); return e

print("== hybrid retrieval")
s = sdk.Store()
maya = ent(s, "Maya", "PERSON", "Entwicklerin bei Acme, Spezialgebiet Rust und Kubernetes")
acme = ent(s, "Acme", "ORGANIZATION", "Softwarefirma in Berlin")
ent(s, "Orion", "EVENT", "Konferenz zu Kubernetes im Herbst")
p = mk(seed=s)
ok(s.fulltext_created and p._fulltext_ok, "full-text index created on connect (IF NOT EXISTS)")
r = T(p, "neo4j_memory_search", {"query": "wer kennt sich mit Kubernetes aus"})
names = {e["name"]: e["match"] for e in r["entities"]}
print(names)
ok("Maya" in names and "Orion" in names and all(m == "text" for m in names.values()), "description-only match found by keywords (vector search alone would miss it)")
r = T(p, "neo4j_memory_search", {"query": "Was ist mit Maya und Kubernetes?"})
ok({e["name"]: e["match"] for e in r["entities"]}.get("Maya") == "vector+text", "entity found by both channels is tagged vector+text and ranked first")
ok(r["entities"][0]["name"] == "Maya", "RRF puts the double hit first")
r = T(p, "neo4j_memory_search", {"query": "Kubernetes", "type": "EVENT"})
ok([e["name"] for e in r["entities"]] == ["Orion"], "type pre-filter applies to the text channel too")
r = T(p, "neo4j_memory_search", {"query": "Quantenphysik Vorlesung"})
ok(r["entities"] == [] and "Do not guess" in r["note"], "empty result carries an explicit 'do not guess' note")
ghost = ent(s, "Mayo", "PERSON", "Kubernetes Duplikat"); ghost.merged_into = str(maya.id)
r = T(p, "neo4j_memory_search", {"query": "Kubernetes"})
ok("Mayo" not in [e["name"] for e in r["entities"]], "merged-away entity hidden in the text channel")
out = p.prefetch("wer kennt sich mit Kubernetes aus heute")
print(out)
ok("- Maya (PERSON)" in out and "Orion" in out, "recall uses the hybrid result")
p.shutdown()
s2 = sdk.Store(); s2.fulltext_fail = True; ent(s2, "Maya", "PERSON", "x")
p = mk(seed=s2); ok(not p._fulltext_ok, "index creation failure is tolerated")
r = T(p, "neo4j_memory_search", {"query": "Maya bitte"}); ok([e["match"] for e in r["entities"]] == ["vector"], "falls back to vector-only")
p.shutdown()
p = mk(seed=sdk.Store(), NEO4J_MEMORY_HYBRID="0"); ok(not p._hybrid and not p._fulltext_ok, "NEO4J_MEMORY_HYBRID=0 disables it"); p.shutdown()

print("== relationship vocabulary")
s = sdk.Store(); p = mk(seed=s)
T(p, "neo4j_memory_remember", {"entities": [{"name": "Maya", "type": "PERSON"}, {"name": "Acme", "type": "ORGANIZATION"}, {"name": "Berlin", "type": "LOCATION"}]})
r = T(p, "neo4j_memory_remember", {"relationships": [
    {"source": "Maya", "target": "Acme", "type": "employed by"},        # alias -> WORKS_AT
    {"source": "Acme", "target": "Maya", "type": "works at"},           # wrong direction -> swapped
    {"source": "Acme", "target": "Berlin", "type": "Located-In"},      # normalized spelling
    {"source": "Maya", "target": "Berlin", "type": "inspired by"}]})   # free type allowed by default
ok(r["relationships"] == 4 and not r["skipped"], f"all four stored: {r['adjusted']}")
rels = {(a, b, t) for a, b, t in [(s.by_id(x.source_id).name, s.by_id(x.target_id).name, x.type) for x in s.rels]}
ok(("Maya", "Acme", "WORKS_AT") in rels and ("Acme", "Berlin", "LOCATED_IN") in rels and ("Maya", "Berlin", "INSPIRED_BY") in rels, f"normalized types stored: {sorted(rels)}")
ok(sum(1 for x in rels if x[2] == "WORKS_AT") == 1 and any("direction corrected" in a for a in r["adjusted"]) and any("EMPLOYED_BY -> WORKS_AT" in a for a in r["adjusted"]), "alias mapped and direction corrected (both reported)")
sch = [t for t in p.get_tool_schemas() if t["name"] == "neo4j_memory_remember"][0]["description"]
ok("WORKS_AT" in sch and "LOCATED_IN" in sch, "tool description lists the vocabulary"); ok("WORKS_AT" in p.system_prompt_block(), "system prompt lists the vocabulary")
p.shutdown()
home = os.environ["HERMES_HOME"]; open(os.path.join(home, "neo4j-memory-schema.json"), "w").write(json.dumps(
    {"relationship_types": {"WORKS_AT": "x", "FRIEND_OF": "y"}, "patterns": [["PERSON", "FRIEND_OF", "PERSON"], ["PERSON", "WORKS_AT", "ORGANIZATION"]], "aliases": {"BUDDY_OF": "FRIEND_OF"},
     "additional_relationship_types": False, "additional_patterns": False}))
s = sdk.Store(); p = mk(seed=s)
T(p, "neo4j_memory_remember", {"entities": [{"name": "Maya", "type": "PERSON"}, {"name": "Bob", "type": "PERSON"}, {"name": "Acme", "type": "ORGANIZATION"}]})
r = T(p, "neo4j_memory_remember", {"relationships": [
    {"source": "Maya", "target": "Bob", "type": "buddy of"}, {"source": "Maya", "target": "Acme", "type": "owns"},
    {"source": "Maya", "target": "Acme", "type": "friend of"}, {"source": "Maya", "target": "Acme", "type": "works at"}]})
ok(r["relationships"] == 2 and len(r["skipped"]) == 2 and any("not in the vocabulary" in x for x in r["skipped"]) and any("not an allowed pattern" in x for x in r["skipped"]), f"strict user schema: custom alias ok, unknown type and bad pattern rejected: {r['skipped']}")
p.shutdown(); os.remove(os.path.join(home, "neo4j-memory-schema.json"))
open(os.path.join(home, "neo4j-memory-schema.json"), "w").write("{not json")
p = mk(); ok("WORKS_AT" in p._schema["relationship_types"], "broken schema file falls back to defaults"); p.shutdown(); os.remove(os.path.join(home, "neo4j-memory-schema.json"))

print("== SDK keys in lookups")
s = sdk.Store(); mc = ent(s, "Maya Chen", "PERSON"); mc.surface_keys = ["mayachen", "m. chen"]; ent(s, "Acme", "ORGANIZATION"); p = mk(seed=s)
r = T(p, "neo4j_memory_remember", {"relationships": [{"source": "m. chen", "target": "Acme", "type": "works at"}]})
ok(r["relationships"] == 1 and not r["skipped"], "entity found through the SDK's surface_keys"); p.shutdown()

print("== provenance")
s = sdk.Store(); p = mk("cron", seed=s)
T(p, "neo4j_memory_remember", {"entities": [{"name": "Zed", "type": "PERSON"}]})
z = s.ents[0]; ok(z.hermes_origin == "dream" and z.hermes_session == "s1", "entities created in cron runs are marked origin=dream with the session id"); p.shutdown()
p = mk(); T(p, "neo4j_memory_remember", {"entities": [{"name": "Yan", "type": "PERSON"}]}); ok(sdk.MemoryClient.last.store.ents[0].hermes_origin == "tool", "normal sessions: origin=tool"); p.shutdown()

print("== read-only query tool")
p = mk(); ok(not any(t["name"] == "neo4j_memory_query" for t in p.get_tool_schemas()), "hidden in normal sessions")
ok("error" in T(p, "neo4j_memory_query", {}), "refused in normal sessions"); p.shutdown()
p = mk(NEO4J_MEMORY_QUERY_TOOL="1"); ok(any(t["name"] == "neo4j_memory_query" for t in p.get_tool_schemas()) and "neo4j_memory_query" in p.system_prompt_block(), "env flag exposes it and mentions it in the prompt"); p.shutdown()
s = sdk.Store(); ent(s, "Maya", "PERSON"); p = mk("cron", seed=s)
ok(any(t["name"] == "neo4j_memory_query" for t in p.get_tool_schemas()), "always available in cron")
ent(s, "Acme", "ORGANIZATION"); T(p, "neo4j_memory_remember", {"relationships": [{"source": "Maya", "target": "Acme", "type": "works at"}]})
sc = T(p, "neo4j_memory_query", {})
ok(sc["status"] == "schema" and "Entity" in sc["labels"] and sc["entity_types"] and sc["relationship_patterns"] and "merged_into" in sc["notes"] and sc["examples"], "no argument -> schema summary with examples")
ok(sc["relationship_properties"] == ["relation_type", "confidence"] and sc["relationship_name_expression"] == "r.relation_type", "discovers where the specific relation name is stored")
ok(sc["relationship_patterns"][0]["rel"] == "WORKS_AT" and "r.relation_type" in sc["examples"][1] and "relation_type" in sc["notes"], "patterns and examples use the real property, not type(r)")
ok(sc["relationship_sample"] and sc["relationship_sample"][0]["props"]["relation_type"] == "WORKS_AT" and "embedding" not in json.dumps(sc["relationship_sample"]), "sample edge shown, vectors stripped")
for bad in ["CREATE (n:X)", "MATCH (n) SET n.x = 1", "MATCH (n) DETACH DELETE n", "CALL apoc.cypher.doIt('x', {})", "CALL dbms.listConfig()", "LOAD CSV FROM 'x' AS r RETURN r", "MERGE (n:X)", "MATCH (n) REMOVE n.x", "match (n) foreach (x in [1] | create (:Y))"]:
    ok(T(p, "neo4j_memory_query", {"cypher": bad})["status"] == "refused", "refused: " + bad[:34])
ok(T(p, "neo4j_memory_query", {"cypher": "CALL db.labels() YIELD label RETURN label"})["status"] != "refused", "db.labels allowed")
r = T(p, "neo4j_memory_query", {"cypher": "MATCH (e:Entity) RETURN e /*TEST_ROWS*/"})
ok(r["status"] == "ok" and r["row_count"] <= 50 and r["truncated"] is True and "embedding" not in json.dumps(r), f"row cap + embeddings stripped (rows={r['row_count']})")
ok(len(json.dumps(r)) <= 7500 and all(len(row["e"]["description"]) <= 300 for row in r["rows"]), "size bounded, long strings clipped")
r = T(p, "neo4j_memory_query", {"cypher": "MATCH (e:Entity) RETURN e /*TEST_ROWS*/", "max_rows": 3}); ok(r["row_count"] == 3, "max_rows respected")
p.shutdown()

print("== transient errors / reconnect")
s = sdk.Store(); ent(s, "Maya", "PERSON", "x"); p = mk(seed=s); c1 = sdk.MemoryClient.last
sdk.MemoryClient.transient_once = True
r = T(p, "neo4j_memory_search", {"query": "Maya bitte"})
ok(r.get("entities") and "error" not in r, "tool call retried once after ServiceUnavailable and succeeded")
time.sleep(0.4); ok(getattr(c1, "closed", False) and sdk.MemoryClient.last is not c1, "broken client was closed and a new connection was made")
p.prefetch("Maya Chen bitte heute")    # make sure the cache is cold and the connection is up
c2 = sdk.MemoryClient.last; sdk.MemoryClient.transient_once = True
out = p.prefetch("Maya Chen noch einmal bitte")
ok(out == "", "prefetch swallows a transient error (returns empty text, does not raise)")
time.sleep(0.8)
ok(sdk.MemoryClient.last is not c2 and p._client is not None, "reconnect starts immediately after the transient error")
ok("Maya" in p.prefetch("Was weiss ich ueber Maya jetzt"), "recall works again on the new connection")
p.shutdown()
print(f"\nALL {n} FEATURE CHECKS PASSED")
