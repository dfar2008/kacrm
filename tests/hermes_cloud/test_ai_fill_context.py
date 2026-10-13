from hermes_cloud.app.crm.ai_attributes import build_fill_context


def _field_meta(inputs):
    return {"field_name": "icp_level", "field_type": "picklist",
            "field_config": {"ai_fill": {"output_type": "picklist", "mission": "m",
                                         "output_spec": "o", "inputs": inputs}}}


def test_record_enabled_returns_all_business_cols():
    rec = {"accounts_id": 7, "industry": "制造", "employee_count": 600,
           "created_by": 1, "updated_at": "x", "ai_meta": None, "icp_level": "高"}
    ctx, basis = build_fill_context(
        db=None, module="accounts", record=rec,
        field_meta=_field_meta({"record": {"enabled": True},
                                "interactions": {"enabled": False}}))
    assert "industry" in ctx["fields"] and "employee_count" in ctx["fields"]
    assert "created_by" not in ctx["fields"]      # 簿记列排除
    assert "ai_meta" not in ctx["fields"]
    assert "icp_level" not in ctx["fields"]        # 目标字段本身排除
    assert "field:industry" in basis
    assert ctx["subdetails"] == []                 # db=None 时 key 存在且为空


def test_record_disabled_yields_no_fields():
    rec = {"accounts_id": 7, "industry": "制造", "employee_count": 600}
    ctx, _ = build_fill_context(
        db=None, module="accounts", record=rec,
        field_meta=_field_meta({"record": {"enabled": False},
                                "interactions": {"enabled": False}}))
    assert ctx["fields"] == {}


def test_legacy_fields_key_treated_as_enabled():
    # 旧配置:无 record 键,但有 inputs.fields(含空列表)→ 视为开启,返回全业务字段
    rec = {"accounts_id": 7, "industry": "制造", "employee_count": 600, "ai_meta": None}
    ctx, _ = build_fill_context(
        db=None, module="accounts", record=rec,
        field_meta=_field_meta({"fields": ["industry"],
                                "interactions": {"enabled": False}}))
    assert "industry" in ctx["fields"] and "employee_count" in ctx["fields"]


class _SubDb:
    def __init__(self, children, rows):
        self._children = children
        self._rows = rows
    def fetchall(self, sql, args=()):
        if "collections" in sql:
            return self._children
        return self._rows
    def fetchone(self, sql, args=()):
        return None


def test_subdetails_injected_when_record_enabled():
    db = _SubDb(
        children=[{"id": 9, "name": "orders_lines", "title": "订单行",
                   "detail_kind": "generic"}],
        rows=[{"orders_lines_id": 1, "orders_id": 7, "qty": 2}],
    )
    rec = {"orders_id": 7, "amount": 100, "ai_meta": None}
    ctx, basis = build_fill_context(
        db=db, module="orders", record=rec,
        field_meta={"field_name": "next_step", "field_type": "text",
                    "field_config": {"ai_fill": {"mode": "prompt", "mission": "m",
                        "inputs": {"record": {"enabled": True},
                                   "interactions": {"enabled": False}}}}})
    assert ctx["subdetails"] and ctx["subdetails"][0]["module"] == "orders_lines"
    assert any(b.startswith("subdetail:orders_lines") for b in basis)


def test_subdetails_absent_when_record_disabled():
    db = _SubDb(
        children=[{"id": 9, "name": "orders_lines", "title": "行",
                   "detail_kind": "generic"}],
        rows=[{"orders_lines_id": 1, "orders_id": 7, "qty": 2}],
    )
    rec = {"orders_id": 7, "amount": 100}
    ctx, _ = build_fill_context(
        db=db, module="orders", record=rec,
        field_meta={"field_name": "next_step", "field_type": "text",
                    "field_config": {"ai_fill": {"mode": "prompt", "mission": "m",
                        "inputs": {"record": {"enabled": False},
                                   "interactions": {"enabled": False}}}}})
    assert ctx["subdetails"] == []


def test_instruction_contains_subdetail_block():
    from hermes_cloud.app.crm.ai_attributes import _build_instruction
    fm = {"field_name": "next_step", "field_type": "text",
          "field_config": {"ai_fill": {"mode": "prompt", "mission": "m"}}}
    ctx = {"fields": {"amount": 100},
           "subdetails": [{"module": "orders_lines", "title": "行",
                           "rows": [{"qty": 2}]}]}
    instr = _build_instruction(fm, ctx)
    assert "[子明细]" in instr


def test_subdetail_block_truncated_with_marker():
    from hermes_cloud.app.crm.ai_attributes import _build_instruction
    fm = {"field_name": "next_step", "field_type": "text",
          "field_config": {"ai_fill": {"mode": "prompt", "mission": "m"}}}
    big = [{"module": "orders_lines", "title": "行",
            "rows": [{"memo": "x" * 200} for _ in range(100)]}]  # 远超 8000 字
    instr = _build_instruction(fm, {"fields": {}, "subdetails": big})
    assert "…(截断)" in instr


def test_validate_rejects_non_dict_record():
    import pytest
    from hermes_cloud.app.crm.ai_attributes import validate_ai_fill, AiFillError
    with pytest.raises(AiFillError):
        validate_ai_fill("text",
                         {"mode": "prompt", "mission": "m",
                          "inputs": {"record": True}},
                         module_field_names=[])


def test_validate_accepts_dict_record():
    from hermes_cloud.app.crm.ai_attributes import validate_ai_fill
    validate_ai_fill("text",
                     {"mode": "prompt", "mission": "m",
                      "inputs": {"record": {"enabled": True}}},
                     module_field_names=[])  # 不抛即通过
