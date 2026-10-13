from hermes_cloud.app import model_roles


def _pj(providers, **extra):
    """构造 providers_json：providers 是 {id: {enabled, models:[{id}]}}。"""
    return {"providers": providers, **extra}


ZEN = {"opencode-zen": {"enabled": True, "models": [
    {"id": "deepseek-v4-flash-free"}, {"id": "glm-5.1"}]}}


def test_aux_auto_uses_deepseek_flash_when_present():
    out = model_roles.resolve_auxiliary(_pj(ZEN))
    for task in ("compression", "curator", "title_generation"):
        assert out[task]["provider"] == "custom:opencode-zen"
        assert out[task]["model"] == "deepseek-v4-flash-free"
        assert out[task]["source"] == "auto"


def test_aux_auto_without_flash_is_provider_default_sentinel():
    pj = _pj({"anthropic": {"enabled": True, "models": [{"id": "claude-opus-4-8"}]}})
    out = model_roles.resolve_auxiliary(pj)
    assert out["compression"] == {"provider": None, "model": None, "source": "provider_default"}


def test_aux_unified_override_wins():
    pj = _pj(ZEN, auxiliary={"unified": "opencode-zen::glm-5.1"})
    out = model_roles.resolve_auxiliary(pj)
    assert out["curator"]["model"] == "glm-5.1"
    assert out["curator"]["provider"] == "custom:opencode-zen"
    assert out["curator"]["source"] == "unified"


def test_aux_per_task_override_beats_unified():
    pj = _pj(ZEN, auxiliary={
        "unified": "opencode-zen::glm-5.1",
        "overrides": {"compression": "opencode-zen::deepseek-v4-flash-free"},
    })
    out = model_roles.resolve_auxiliary(pj)
    assert out["compression"]["model"] == "deepseek-v4-flash-free"
    assert out["compression"]["source"] == "custom"
    assert out["curator"]["model"] == "glm-5.1"  # 仍跟随 unified


def test_aux_override_to_disabled_provider_falls_back_to_auto():
    pj = _pj(ZEN, auxiliary={"unified": "openai::gpt-5.5"})  # openai 未启用
    out = model_roles.resolve_auxiliary(pj)
    assert out["title_generation"]["model"] == "deepseek-v4-flash-free"
    assert out["title_generation"]["source"] == "auto"


TWO = {
    "minimax": {"enabled": True, "models": [{"id": "MiniMax-M3"}]},
    "opencode-zen": {"enabled": True, "models": [{"id": "glm-5.1"}]},
}


def test_moa_auto_picks_two_and_aggregator_prefers_m3():
    out = model_roles.resolve_moa(_pj(TWO))
    models = {r["model"] for r in out["reference_models"]}
    assert models == {"MiniMax-M3", "glm-5.1"}
    assert out["aggregator"]["model"] == "MiniMax-M3"
    assert out["reference_models"][0]["provider"] == "custom:minimax"  # M3 优选最高排第一


def test_moa_none_when_fewer_than_two_models():
    pj = _pj({"minimax": {"enabled": True, "models": [{"id": "MiniMax-M3"}]}})
    assert model_roles.resolve_moa(pj) is None


def test_moa_disabled_returns_none():
    pj = _pj(TWO, moa={"enabled": False})
    assert model_roles.resolve_moa(pj) is None


def test_moa_missing_section_defaults_enabled():
    assert model_roles.resolve_moa(_pj(TWO)) is not None


def test_moa_explicit_members_and_aggregator_win():
    pj = _pj(TWO, moa={
        "reference_models": ["opencode-zen::glm-5.1", "minimax::MiniMax-M3"],
        "aggregator": "opencode-zen::glm-5.1",
    })
    out = model_roles.resolve_moa(pj)
    assert out["reference_models"][0]["model"] == "glm-5.1"  # 保用户顺序
    assert out["aggregator"]["model"] == "glm-5.1"


def test_moa_explicit_members_with_dead_ref_fall_back_to_auto():
    pj = _pj(TWO, moa={"reference_models": ["openai::gpt-5.5"]})  # 未启用，剩<2
    out = model_roles.resolve_moa(pj)
    models = {r["model"] for r in out["reference_models"]}
    assert models == {"MiniMax-M3", "glm-5.1"}  # 回落自动


def test_apply_preserves_compression_timeout():
    existing = {"auxiliary": {"compression": {"timeout": 300}}}
    model_roles.apply_to_config(existing, _pj(ZEN))
    assert existing["auxiliary"]["compression"]["timeout"] == 300  # 未被冲掉
    assert existing["auxiliary"]["compression"]["model"] == "deepseek-v4-flash-free"


def test_apply_provider_default_clears_managed_keys_keeps_others():
    existing = {"auxiliary": {"compression": {"timeout": 300, "provider": "custom:x", "model": "old"}}}
    pj = _pj({"anthropic": {"enabled": True, "models": [{"id": "claude-opus-4-8"}]}})
    model_roles.apply_to_config(existing, pj)
    slot = existing["auxiliary"]["compression"]
    assert slot == {"timeout": 300}  # provider/model 被清，timeout 保留


def test_apply_writes_and_removes_moa_deep_council():
    existing = {}
    model_roles.apply_to_config(existing, _pj(TWO))
    assert "deep-council" in existing["moa"]["presets"]

    existing2 = {"moa": {"presets": {"deep-council": {"x": 1}, "other": {"y": 2}}}}
    model_roles.apply_to_config(existing2, _pj(TWO, moa={"enabled": False}))
    assert "deep-council" not in existing2["moa"]["presets"]
    assert "other" in existing2["moa"]["presets"]  # 别的 preset 不动


def test_derived_block_shape_matches_resolvers():
    """GET 端点该拼的 derived 结构 == resolve_* 输出(用同一函数保证一致)。"""
    pj = _pj(TWO)
    derived = {
        "auxiliary": model_roles.resolve_auxiliary(pj),
        "moa": model_roles.resolve_moa(pj),
    }
    assert set(derived["auxiliary"]) == set(model_roles.AUX_TASKS)
    assert derived["moa"]["aggregator"]["model"] == "MiniMax-M3"
