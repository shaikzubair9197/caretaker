import pytest
from unittest.mock import patch


@pytest.fixture(autouse=True)
def force_ollama_provider():
    with patch("services.llm_service.LLM_PROVIDER", "ollama"):
        yield
