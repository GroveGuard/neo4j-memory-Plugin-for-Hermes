from .adapters.sentence_transformers import SentenceTransformersProvider
def from_provider(model, kind="embedding"): return SentenceTransformersProvider(model, dimensions=384)
