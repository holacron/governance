"""Pytest config: register custom markers."""



def pytest_configure(config):
    config.addinivalue_line(
        "markers", "live: tests that call the real LLM (Z.ai) and Postgres; "
        "need ZAI_API_KEY + DATABASE_URL"
    )
