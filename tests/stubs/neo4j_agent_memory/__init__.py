import asyncio, difflib, uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

SYN = [{"bmw", "bayerische motoren werke"}]

class ServiceUnavailable(Exception):
    pass

class MemorySettings:
    def __init__(self, **kw): self.kw = kw

@dataclass
class MemoryContext:
    messages: list = field(default_factory=list)
    entities: list = field(default_factory=list)
    preferences: list = field(default_factory=list)
    reasoning_traces: list = field(default_factory=list)
    def to_prompt(self): return "SHOULD NOT BE USED"

class Store:
    def __init__(self):
        self.ents = []; self.rels = []; self.prefs = {}; self.meta = {}; self.notsame = set(); self.pref_kwargs = []
        self.fulltext_created = False; self.fulltext_fail = False
    def by_id(self, i): return next((e for e in self.ents if str(e.id) == str(i)), None)
    def degree(self, e): return sum(1 for r in self.rels if str(e.id) in (str(r.source_id), str(r.target_id)))

class _Graph:
    def __init__(self, s): self.s = s; self.writes = []
    async def execute_read(self, q, p=None):
        s, p = self.s, p or {}
        if "db.index.fulltext.queryNodes" in q:
            if not s.fulltext_created: raise RuntimeError("There is no such fulltext schema index")
            words = [w.lower() for w in p["q"].split(" OR ")]
            rows = []
            for e in s.ents:
                if e.merged_into is not None: continue
                hay = " ".join([e.name, e.description or "", *e.aliases]).lower()
                score = sum(1 for w in words if w in hay)
                if score: rows.append(dict(id=str(e.id), name=e.name, type=e.type, description=e.description, labels=["Entity", e.type], score=score))
            return sorted(rows, key=lambda r: -r["score"])[:p["limit"]]
        if "/*TEST_ROWS*/" in q:
            return [{"e": {"name": f"n{i}", "embedding": [0.1] * 384, "description": "d" * 500, "aliases": []}} for i in range(60)]
        if "CALL db.labels()" in q: return [{"v": ["Entity", "Preference", "User"]}]
        if "CALL db.relationshipTypes()" in q: return [{"v": ["RELATED_TO", "HAS_PREFERENCE"]}]
        if "e.type AS type, count(*) AS n" in q:
            out = {}
            for e in s.ents:
                if e.merged_into is None: out[e.type] = out.get(e.type, 0) + 1
            return [dict(type=k, n=v) for k, v in out.items()]
        if "UNWIND keys(r)" in q: return [{"v": ["relation_type", "confidence"]}] if s.rels else [{"v": []}]
        if "properties(r) AS props" in q:
            return [dict(source=s.by_id(r.source_id).name, props={"relation_type": r.type, "confidence": 0.9, "embedding": [0.1] * 384}, target=s.by_id(r.target_id).name) for r in s.rels[:3]]
        if "AS rel" in q:
            out = {}
            for r in s.rels:
                k = (s.by_id(r.source_id).type, r.type, s.by_id(r.target_id).type); out[k] = out.get(k, 0) + 1
            return [dict(source_type=a, rel=b, target_type=c, n=n) for (a, b, c), n in out.items()]
        if "UNWIND keys(e)" in q: return [{"v": ["id", "name", "type", "description"]}]
        if "STARTS WITH 'hermes_'" in q:
            return [{"n": sum(1 for u in s.prefs.values() for x in u if x.category.startswith("hermes_"))}]
        if "RETURN m.value AS value" in q:
            return [{"value": s.meta["embedding_model"]}] if "embedding_model" in s.meta else []
        if "RETURN count(e) AS n" in q: return [{"n": len(s.ents)}]
        if "RETURN count(r) AS n" in q: return [{"n": len(s.rels)}]
        if "AS name, e.type AS type\"" in q.replace("\n"," ") or ("$ids" in q and "e.type AS type" in q and "e.name AS name" in q):
            return [dict(id=str(e.id), name=e.name, type=e.type) for e in s.ents if str(e.id) in p["ids"] and e.merged_into is None]
        if "datetime(e.created_at)" in q:
            from datetime import datetime as _dt
            from datetime import timezone as _tz
            def _p(x):
                if not x: return None
                d = _dt.fromisoformat(x.replace("Z", "+00:00"))
                return d if d.tzinfo else d.replace(tzinfo=_tz.utc)
            lo, hi = _p(p["lo"]), _p(p["hi"]); out = []
            for e in s.ents:
                if str(e.id) not in p["ids"]: continue
                c = getattr(e, "created_at", None)
                if c is None: continue
                c = _p(c)
                if lo and c < lo: continue
                if hi and c > hi: continue
                out.append(dict(id=str(e.id)))
            return out
        if "$ids" in q: return [dict(id=str(e.id)) for e in s.ents if str(e.id) in p["ids"] and e.merged_into is None]
        if "coalesce(e.canonical_name" in q:
            n = p["n"].lower()
            return [dict(id=str(e.id), type=e.type) for e in s.ents if e.merged_into is None and (e.name.lower()==n or any(a.lower()==n for a in e.aliases) or any(k.lower()==n for k in getattr(e, "surface_keys", [])))][:1]
        if "labels(e) AS labels" in q:
            return [dict(id=str(e.id), name=e.name, type=e.type, description=e.description, labels=["Entity", e.type])
                    for e in s.ents if e.merged_into is None and e.name.lower()==p["name"].lower()]
        if "RETURN e.description AS description" in q:
            return [dict(description=e.description) for e in s.ents if str(e.id)==p["id"]]
        if "RETURN e.name AS name" in q and "$id" in q and "e.id > " not in q:
            return [dict(name=e.name) for e in s.ents if str(e.id)==p["id"]]
        if "COUNT { (e)--() }" in q:
            return [dict(id=str(e.id), name=e.name, type=e.type, description=e.description, degree=s.degree(e)) for e in s.ents if e.merged_into is None]
        if "NOT_SAME_AS" in q: return [dict(a=a, b=b) for a, b in s.notsame]
        if "e.merged_into IS NOT NULL AS merged" in q:
            return [dict(merged=s.by_id(p["id"]).merged_into is not None)]
        if "e.id > $after" in q:
            rows = sorted([e for e in s.ents if str(e.id) > p["after"]], key=lambda e: str(e.id))[:p["limit"]]
            return [dict(id=str(e.id), text=e.name) for e in rows]
        raise AssertionError("unexpected read: " + q)
    async def execute_write(self, q, p=None):
        s, p = self.s, p or {}; self.writes.append(q)
        if "CREATE FULLTEXT INDEX" in q:
            if s.fulltext_fail: raise RuntimeError("fulltext not supported")
            s.fulltext_created = True
        elif "SET e.hermes_origin" in q:
            e = s.by_id(p["id"]); e.hermes_origin = p["o"]; e.hermes_session = p["s"]
        elif "MERGE (m:HermesMemoryMeta" in q: s.meta["embedding_model"] = p["v"]
        elif "SET e.description" in q: s.by_id(p["id"]).description = p["d"]
        elif "DETACH DELETE e" in q: s.ents = [e for e in s.ents if str(e.id) != p["id"]]
        elif "DETACH DELETE p" in q:
            for u in s.prefs: s.prefs[u] = [x for x in s.prefs[u] if str(x.id) != p["id"]]
        elif "NOT_SAME_AS" in q: s.notsame.add(frozenset((p["a"], p["b"])))
        elif "SET e.embedding" in q:
            for row in p["rows"]: s.by_id(row["id"]).embedding = row["vector"]
        else: raise AssertionError("unexpected write: " + q)

class _LT:
    def __init__(self, s): self.s = s
    async def search_entities(self, q, entity_types=None, limit=10, threshold=0.7):
        MemoryClient.search_calls += 1
        if MemoryClient.transient_once:
            MemoryClient.transient_once = False; raise ServiceUnavailable("Connection reset")
        if MemoryClient.fail: raise RuntimeError("db down")
        out = []
        for e in self.s.ents:
            if entity_types and e.type not in entity_types: continue
            sim = max(difflib.SequenceMatcher(None, q.lower(), e.name.lower()).ratio(), 0.95 if e.name.lower() in q.lower() else 0 , 0.95 if any(q.lower() in pr and e.name.lower() in pr and q.lower() != e.name.lower() for pr in SYN) else 0)
            if sim >= threshold: out.append(NS(**{**e.__dict__, "metadata": {"similarity": round(sim, 3)}}))
        return out[:limit]
    async def get_entity_by_name(self, name):
        return next((e for e in self.s.ents if e.name.lower()==name.lower()), None)
    async def add_entity(self, name, typ, **kw):
        ex = next((e for e in self.s.ents if e.name==name and e.type==typ), None)
        if ex: return ex, None
        e = NS(id=uuid.uuid4(), name=name, type=typ, full_type=typ, description=kw.get("description"), metadata={}, aliases=[], merged_into=None, embedding=None)
        self.s.ents.append(e); return e, None
    async def add_relationship(self, a, b, t, **kw):
        ida = getattr(a, "id", a); idb = getattr(b, "id", b)
        assert self.s.by_id(ida) and self.s.by_id(idb), "NotFound"
        r = NS(type=t, source_id=ida, target_id=idb, valid_until=None); self.s.rels.append(r); return r
    async def get_related_entities(self, entity, depth=1):
        eid = str(getattr(entity, "id", entity)); out = []
        for r in self.s.rels:
            if str(r.source_id) == eid: out.append((self.s.by_id(r.target_id), r))
            elif str(r.target_id) == eid: out.append((self.s.by_id(r.source_id), r))
        return out
    async def add_preference(self, cat, preference, **kw):
        self.s.pref_kwargs.append(kw)
        p = NS(id=uuid.uuid4(), category=cat, preference=preference); self.s.prefs.setdefault(kw["user_identifier"], []).append(p); return p
    async def get_preferences_for(self, uid, **kw): return list(self.s.prefs.get(uid, []))
    async def supersede_preference(self, old, new):
        for u in self.s.prefs: self.s.prefs[u] = [x for x in self.s.prefs[u] if x.id != old]
    async def merge_duplicate_entities(self, source_id, target_id):
        src, tgt = self.s.by_id(source_id), self.s.by_id(target_id)
        if not src or not tgt: return None
        tgt.aliases.append(src.name); src.merged_into = str(tgt.id)
        for r in list(self.s.rels):
            if str(r.source_id) == str(src.id): self.s.rels.append(NS(type=r.type, source_id=tgt.id, target_id=r.target_id, valid_until=r.valid_until))
            if str(r.target_id) == str(src.id): self.s.rels.append(NS(type=r.type, source_id=r.source_id, target_id=tgt.id, valid_until=r.valid_until))
        return src, tgt
    async def find_potential_duplicates(self, limit=100): return []
    async def review_duplicate(self, a, b, confirm): return True
class _ST:
    def __init__(self): self.msgs = []
    async def add_message(self, sid, role, text, **kw): self.msgs.append((sid, role, text, kw))

class MemoryClient:
    last = None; fail = False; context_calls = 0; seed = None; search_calls = 0; transient_once = False
    def __init__(self, settings):
        self.settings = settings; self.store = MemoryClient.seed or Store(); self.long_term = _LT(self.store)
        self.short_term = _ST(); self.graph = _Graph(self.store); MemoryClient.last = self
    async def __aenter__(self): await asyncio.sleep(0.1); return self
    async def __aexit__(self, *a): self.closed = True
    async def get_context(self, q, session_id=None, **kw):
        MemoryClient.context_calls += 1
        if MemoryClient.fail: raise RuntimeError("db down")
        self.ctx_kwargs = kw
        words = [w for w in q.lower().split() if len(w) > 2]
        ents = [e for e in self.store.ents if any(w.strip("?!.,") in e.name.lower() for w in words)]   # merged ones included, like the real SDK
        return MemoryContext(entities=ents[:kw.get("limit", 10)])
