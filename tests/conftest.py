import os

import pytest
from unittest.mock import patch

os.environ.setdefault("AZURE_OPENAI_API_KEY", "test-dummy-key")
os.environ.setdefault("AZURE_DEPLOYMENT", "test-deployment")
os.environ.setdefault("OPENAI_API_VERSION", "2024-08-01-preview")
os.environ.setdefault("OPENAI_ENDPOINT", "https://test-resource.openai.azure.com")

@pytest.fixture(autouse=True)
def force_azure_provider():
    with patch("services.llm_service.LLM_PROVIDER", "azure_openai"):
        yield
