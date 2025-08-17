import os
import pytest


@pytest.fixture(autouse=True, scope="session")
def set_mattermost_env_defaults():
    os.environ.setdefault("MATTERMOST_API_TOKEN", "5x7rr788c7gwdnkdr9imb49ffo")
    os.environ.setdefault("MATTERMOST_API_URL", "http://localhost:8065/api/v4/users/me")
