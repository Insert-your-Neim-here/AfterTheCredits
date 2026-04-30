from sentence_transformers import SentenceTransformer

MODEL_NAME = "sentence-transformers/all-MiniLM-L6-v2"
_model = None


def get_embedding_model():
    global _model
    if _model is None:
        _model = SentenceTransformer(MODEL_NAME)
    return _model

def compute_embedding(text: str) -> list[float]:
    model = get_embedding_model()
    vec = model.encode([text], normalize_embeddings=True)[0]
    return vec.tolist()



