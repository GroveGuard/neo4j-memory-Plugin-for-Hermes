class SentenceTransformersProvider:
    def __init__(self, model, dimensions=None, **kw): self.model = model; self.dimensions = dimensions
    async def embed(self, texts): return [[float(len(t) % 7)] * self.dimensions for t in texts]
