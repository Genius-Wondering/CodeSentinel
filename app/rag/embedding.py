from langchain_openai import OpenAIEmbeddings

from app.config import config


def get_embedding_model():
    # FIX: pass api_key via the canonical parameter name; `openai_api_key` is deprecated
    # FIX: removed `or None` — an empty string already triggers the SDK's own env-var fallback
    return OpenAIEmbeddings(api_key=config.OPENAI_API_KEY or None)
