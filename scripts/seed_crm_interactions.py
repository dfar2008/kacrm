"""Seed realistic interactions for the 70 batch-created CRM records.

For each record with ``memory_entity_id IS NOT NULL`` in the four CRM modules,
emit 3–5 varied Chinese business-flavoured interactions (email/call/meeting/
note/message). Then trigger ``ingest_batch`` so facts get extracted and show
up in the MemoryPanel.
"""
from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone

from hermes.crm.db import db_session
from hermes_cloud.app.interactions.emitter import emit


# ---------------------------------------------------------------------------
# Content templates — intentionally varied so ingestion has distinctive facts
# ---------------------------------------------------------------------------

ACCOUNT_TEMPLATES = [
    ("email", "首次询价", "{name} 希望了解我们的企业版报价，预计预算 ¥{budget}万，决策周期约 {weeks} 周。联系人邮箱 {email}。"),
    ("call", "电话跟进", "与 {name} 采购负责人通话 18 分钟。对方已立项，希望月底前完成 POC，关注点：数据隔离、私有化部署、SLA。"),
    ("meeting", "现场拜访", "到访 {name} 总部。CTO 参与。核心诉求：替换现有 CRM + 与 ERP 打通。建议下一步提供定制化方案书。"),
    ("note", "内部备忘", "{name} 属于重点客户，年合同预计 ¥{amount}万，已纳入 Q2 重点攻单名单，owner 已同步。"),
    ("email", "方案确认", "已将方案书发送至 {name}，包含实施周期 12 周、培训 3 天、驻场支持 1 月。等待对方采购委员会反馈。"),
]

CONTACT_TEMPLATES = [
    ("message", "微信沟通", "{name} 微信咨询产品路线图，关心 AI Copilot 上线时间。已回复预计下季度 GA。"),
    ("call", "电话确认", "与 {name} 通话，确认其部门今年预算仍有空间，倾向 SaaS 年付模式。"),
    ("note", "关系维护", "{name} 节后回来会议密集，建议周三前约视频 demo。个人偏好：简洁的 dashboard、少打扰。"),
    ("email", "资料发送", "已向 {name} 发送产品白皮书 + 竞品对比表 + 客户案例 3 篇。"),
    ("meeting", "线下见面", "咖啡会谈 45 分钟。{name} 分享其团队痛点：报表繁琐、跨部门数据割裂。我方强调自动化工作流优势。"),
]

LEAD_TEMPLATES = [
    ("email", "初次接触", "{name} 通过官网表单提交线索，行业信息已核实。已发送欢迎邮件 + 预约 demo 链接。"),
    ("call", "电话筛选", "电话打通。{name} 正在调研 3 家供应商，预算 ¥{budget}万，决策人为 IT 总监，决策周期 6 周。"),
    ("note", "资质评估", "BANT 打分 {score}/20。预算充足、需求明确，但权力人尚未接触。下一步尝试约 IT 总监。"),
    ("message", "简短跟进", "{name} 微信回复已收到资料，下周内部评审后给答复。"),
]

POTENTIAL_TEMPLATES = [
    ("meeting", "方案讨论", "与客户召开 90 分钟方案会议。{name} 商机推进至方案阶段，核心卖点：实施周期短、ROI 6 个月内可见。"),
    ("email", "正式报价", "{name} 商机报价单已发出，总金额 ¥{amount}万，含 3 年订阅 + 实施 + 一年维保。"),
    ("call", "决策节奏", "与客户决策人通话。{name} 预计 {weeks} 周内给出采购决定。关注合同条款：数据归属、退出机制。"),
    ("note", "竞争情报", "竞争对手 Salesforce 也在角逐 {name}。我方差异化：本地化服务、中文支持、实施更快。"),
    ("meeting", "合同预审", "{name} 法务提出三项修改意见。已同步我方法务评估，预计两个工作日反馈。"),
]


def _render(template: str, *, name: str) -> str:
    return template.format(
        name=name,
        budget=random.choice([30, 50, 80, 120, 180, 240]),
        amount=random.choice([50, 80, 150, 220, 300, 420]),
        weeks=random.choice([2, 3, 4, 6, 8]),
        score=random.choice([12, 14, 15, 17, 18]),
        email=f"contact{random.randint(1,99)}@{random.choice(['acme','demo','corp','group'])}.cn",
    )


def _emit_for(module: str, rec_id: int, rec_name: str, templates):
    """Emit 3–5 interactions for a single record."""
    now = datetime.now(tz=timezone.utc)
    picks = random.sample(templates, k=min(len(templates), random.randint(3, min(5, len(templates)))))
    for i, (kind, subject, body_tmpl) in enumerate(picks):
        text = _render(body_tmpl, name=rec_name)
        occurred = now - timedelta(days=random.randint(0, 21), hours=random.randint(0, 23))
        content = {"subject": subject, "text": text, "summary": text[:80]}
        if kind == "email":
            content.update({"from": f"sales@hermes.local", "to": [f"{rec_name}@client.cn"]})
        emit(
            kind=kind,
            subjects=[{"module": module, "id": rec_id}],
            actors=[{"type": "user", "id": 1}],
            content=content,
            source="seed:batch-demo",
            occurred_at=occurred,
            provenance={"privacy_level": "internal", "confidence": 0.9},
        )


def main():
    random.seed(42)
    plan = [
        ("accounts",   "accounts_id",   "accounts_name",   ACCOUNT_TEMPLATES),
        ("contacts",   "contacts_id",   "contacts_name",   CONTACT_TEMPLATES),
        ("leads",      "leads_id",      "leads_name",      LEAD_TEMPLATES),
        ("potentials", "potentials_id", "potentials_name", POTENTIAL_TEMPLATES),
    ]
    total = 0
    with db_session() as db:
        for tbl, idc, namec, templates in plan:
            with db.cursor() as c:
                c.execute(
                    f"SELECT {idc} AS id, {namec} AS name FROM {tbl} "
                    f"WHERE memory_entity_id IS NOT NULL AND deleted = false "
                    f"ORDER BY {idc} DESC LIMIT 25"
                )
                rows = c.fetchall()
            print(f"[{tbl}] seeding interactions for {len(rows)} records")
            for r in rows:
                before = total
                _emit_for(tbl, r["id"], r["name"], templates)
                total += 1  # count records seeded
    print(f"\n✓ Seeded interactions for {total} records.")


if __name__ == "__main__":
    main()
