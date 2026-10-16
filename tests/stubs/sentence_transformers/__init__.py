"""Stub of sentence_transformers.CrossEncoder: score = share of the question's longer words found in the document."""
import re


class CrossEncoder:
    loads = []

    def __init__(self, model, **kw):
        if model.startswith("broken/"):
            raise OSError("model not found")
        CrossEncoder.loads.append(model)
        self.model = model
        self.calls = 0

    def predict(self, pairs):
        self.calls += 1
        out = []
        for q, d in pairs:
            words = [w for w in re.findall(r"\w+", q.lower()) if len(w) > 3]
            out.append(sum(1 for w in words if w in d.lower()) / max(1, len(words)))
        return out
