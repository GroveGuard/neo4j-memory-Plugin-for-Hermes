class _Rec:
    def __init__(self, d): self._d = d
    def data(self): return self._d
    def __getitem__(self, k): return self._d[k]
class _Driver:
    calls = []
    def __init__(self, uri, auth): self.uri, self.auth = uri, auth
    async def __aenter__(self): return self
    async def __aexit__(self, *a): return False
    async def verify_connectivity(self): pass
    async def execute_query(self, cypher, **kw):
        _Driver.calls.append((cypher, kw))
        if "RETURN count(n) AS nodes" in cypher: rows = [{"nodes": 42}]
        elif "UNWIND labels(n)" in cypher: rows = [{"label": "Entity", "n": 30}, {"label": "Preference", "n": 12}]
        elif "HermesMemoryMeta" in cypher: rows = [{"v": "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2"}]
        elif "SHOW INDEXES" in cypher: rows = [{"name": "hermes_entity_fulltext", "state": "ONLINE"}]
        else: raise AssertionError("unexpected: " + cypher)
        return [_Rec(r) for r in rows], None, None
class AsyncGraphDatabase:
    @staticmethod
    def driver(uri, auth=None): return _Driver(uri, auth)
