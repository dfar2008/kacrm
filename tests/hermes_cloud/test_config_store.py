from hermes_cloud.app.config_store import (
    InMemoryConfigStore,
    SqlAlchemyConfigStore,
)


def test_inmemory_config_store_round_trip():
    store = InMemoryConfigStore()
    store.set_user_model(
        user_id="user-1",
        model="tenant-model",
        api_key="provider-key",
        base_url="https://provider.example/v1",
    )
    store.set_user_toolsets(user_id="user-1", toolsets=["web", "file"])

    model_config = store.get_user_model("user-1")
    tool_config = store.get_user_toolsets("user-1")

    assert model_config is not None
    assert model_config.model == "tenant-model"
    assert model_config.api_key == "provider-key"
    assert model_config.base_url == "https://provider.example/v1"
    assert tool_config is not None
    assert tool_config.toolsets == ["web", "file"]


def test_sqlalchemy_config_store_round_trip(tmp_path):
    db_path = tmp_path / "config.db"
    store = SqlAlchemyConfigStore(f"sqlite+pysqlite:///{db_path}")
    store.set_user_model(
        user_id="user-1",
        model="tenant-model",
        api_key="provider-key",
        base_url="https://provider.example/v1",
    )
    store.set_user_toolsets(user_id="user-1", toolsets=["web", "file"])

    model_config = store.get_user_model("user-1")
    tool_config = store.get_user_toolsets("user-1")

    assert model_config is not None
    assert model_config.model == "tenant-model"
    assert model_config.api_key == "provider-key"
    assert model_config.base_url == "https://provider.example/v1"
    assert tool_config is not None
    assert tool_config.toolsets == ["web", "file"]
