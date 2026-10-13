"""Seed the commerce-chain CRM modules: products / tasks / quotes /
salesorders / receivables / payments.

Context
-------
When we ported kacrm's schema we brought the table definitions over but
never seeded the commerce-chain rows — only leads/accounts/contacts/
potentials + interactions got populated. That left six modules showing
"0 records" in the CRM nav, which both looks broken and starves the
record-insight scanners (nothing to scan).

Design choices
--------------
* **Anchor to real potentials.** Every row connects to an existing
  potential (and its account / contact). No free-floating commerce data.
* **Distribute states deliberately.** Dates, statuses and amounts are
  spread so scanners actually fire:
  - quotes: one expired, one expiring-soon, the rest healthy.
  - tasks:  a third overdue, a third due this week, the rest future.
  - payments: deliberately leave some receivables unpaid.
* **Idempotent.** Re-running soft-deletes rows we own
  (``created_by = SEED_USER``, ``description LIKE '%commerce-seed%'``)
  before reinserting. Safe to run on a database that already has the
  scripted commerce data.
* **Money is jsonb.** Legacy tenants used ``{"CNY": 150000}`` — we
  follow that shape so the insight scanners' ``_amount_from_jsonb``
  helper finds values.

Usage
-----
    uv run python -m scripts.seed_crm_commerce
"""
from __future__ import annotations

import json
import random
from datetime import date, timedelta
from typing import Any

from hermes.crm.db import db_session

SEED_USER = 1
SEED_TAG = "commerce-seed"        # appears in description / remark fields


def _money(n: float | int) -> str:
    """Store monetary amounts as *scalar* jsonb numbers.

    ``module_fields`` declares the amount columns with ``field_type=number``,
    which drives ListQuery to run ``CAST(col AS NUMERIC)``. PostgreSQL can
    cast a jsonb scalar number to numeric, but not a jsonb object — storing
    ``{"CNY": 500000}`` here would blow up the list-summary query with
    "cannot cast jsonb object to type numeric". The insight scanners'
    ``_amount_from_jsonb`` helper already accepts scalars, so this shape
    works on both read paths.
    """
    return json.dumps(int(n))


def _clean(db) -> None:
    """Soft-delete previously seeded rows so reruns stay idempotent.

    Order matters: delete line items before their parent quotes/orders so
    that the "commerce-seed" filter in description/remark still matches
    (once a parent is soft-deleted we don't want its line items drifting
    as orphans). Line-item tables have no ``deleted`` column — they're
    hard-cleaned via their parent FK.
    """
    # Hard-delete line items whose parent is a commerce-seed row.
    db.execute(
        """
        DELETE FROM quotes_products
         WHERE quotes_id IN (
            SELECT quotes_id FROM quotes
             WHERE created_by = %s
               AND COALESCE(adjustment_remark, '') LIKE %s
         )
        """,
        (SEED_USER, f"%{SEED_TAG}%"),
    )
    db.execute(
        """
        DELETE FROM salesorders_products
         WHERE salesorders_id IN (
            SELECT salesorders_id FROM salesorders
             WHERE created_by = %s
               AND COALESCE(description, '') LIKE %s
         )
        """,
        (SEED_USER, f"%{SEED_TAG}%"),
    )

    tables = [
        ("payments",    "payments_id",    "remark"),
        ("receivables", "receivables_id", "receivables_name"),
        ("salesorders", "salesorders_id", "description"),
        ("quotes",      "quotes_id",      "adjustment_remark"),
        ("tasks",       "tasks_id",       "description"),
        ("products",    "products_id",    "description"),
    ]
    for tbl, pk, col in tables:
        db.execute(
            f"UPDATE {tbl} SET deleted = true WHERE created_by = %s "
            f"AND COALESCE({col}, '') LIKE %s",
            (SEED_USER, f"%{SEED_TAG}%"),
        )


PRODUCTS = [
    ("P-AD-001",  "数字广告投放 · 企业版", "service", 180_000,
     "面向 KA 客户的全渠道广告投放代运营，含策略 / 素材 / 投放 / 复盘。"),
    ("P-MKT-001", "MarTech SaaS · 年费",   "saas",    240_000,
     "营销自动化套件，含 CDP / 自动化工作流 / 报表。按坐席年付。"),
    ("P-SCM-001", "私域代运营 · 季度包",   "service",  90_000,
     "企业微信 + 小程序运营，含内容 / 活动 / 客服流转。"),
    ("P-LIV-001", "直播电商陪跑 · 3 月",   "service", 150_000,
     "主播 + 投流 + 选品陪跑一站式，KPI 绑定 GMV。"),
    ("P-CRM-001", "CRM 实施 · 标准版",     "impl",    320_000,
     "含需求梳理 / 定制开发 / 数据迁移 / 培训 / 一年维保。"),
    ("P-AI-001",  "AI Copilot 企业订阅",   "saas",    360_000,
     "Hermes Copilot 企业版，不限坐席，含定制 agent / 私有部署支持。"),
]


TASK_TEMPLATES = [
    ("给 {who} 发送方案 v2",          "high",   -3),  # overdue 3d
    ("安排与 {who} 的技术对齐会",     "medium",  1),
    ("回复 {who} 的商务条款修改意见", "high",   -1),
    ("{who} 回款跟进电话",            "medium",  2),
    ("准备 {who} 的竞品对比材料",     "low",     5),
    ("与 {who} 法务对合同终稿",       "high",    0),  # due today
]


def _seed_products(db) -> list[dict]:
    """Insert catalog products. Return list of dicts with id / name / price
    so downstream line-item inserts can snapshot the catalog into
    quotes_products and salesorders_products (standard commerce pattern —
    line items keep their own copy so historical quotes don't mutate if
    the catalog later changes)."""
    out: list[dict] = []
    for productno, name, catalog, price, desc in PRODUCTS:
        row = db.fetchone(
            """
            INSERT INTO products
                (products_name, productno, catalog, price, reference_price,
                 status, description, stock, safe_stock,
                 created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s,
                    'active', %s || ' [commerce-seed]', 100, 10,
                    %s, %s)
            RETURNING products_id
            """,
            (name, productno, catalog, price, price, desc,
             SEED_USER, SEED_USER),
        )
        out.append({
            "id": row["products_id"],
            "productno": productno,
            "name": name,
            "price": price,
        })
    return out


# Potential-name → product keyword mapping. The match is lowercase substring
# on the potential name; first hit wins, falling through to the default
# "CRM 实施 · 标准版" bundle for generic implementation deals.
_PRODUCT_KEYWORDS: list[tuple[str, str]] = [
    ("数字广告",          "数字广告投放"),
    ("saas",              "MarTech SaaS"),
    ("martech",           "MarTech SaaS"),
    ("copilot",           "AI Copilot"),
    ("ai",                "AI Copilot"),
    ("数据分析",          "AI Copilot"),
    ("直播",              "直播电商陪跑"),
    ("内容",              "私域代运营"),
    ("私域",              "私域代运营"),
]


def _pick_product(potential_name: str, products: list[dict]) -> dict:
    """Pick the best-matching product for a potential. Falls back to the
    generic CRM implementation bundle when no keyword matches."""
    low = (potential_name or "").lower()
    for kw, prod_substr in _PRODUCT_KEYWORDS:
        if kw in low:
            for p in products:
                if prod_substr in p["name"]:
                    return p
    # default: CRM 实施 · 标准版 (generic impl)
    for p in products:
        if "CRM 实施" in p["name"]:
            return p
    return products[0]


def _seed_tasks(db, potentials: list[dict]) -> None:
    today = date.today()
    for p in potentials:
        # Active potentials (not won / lost) get 1-2 tasks each.
        if (p["milestone"] or "").lower() in {"won", "lost", "成交", "丢单"}:
            continue
        picks = random.sample(TASK_TEMPLATES, k=2)
        for name_tmpl, priority, offset_days in picks:
            due = today + timedelta(days=offset_days)
            status = "completed" if offset_days < -14 else "pending"
            db.execute(
                """
                INSERT INTO tasks
                    (tasks_name, description, status, priority,
                     startdate, enddate, accounts_id, contacts_id,
                     created_by, updated_by)
                VALUES (%s, %s || ' [commerce-seed]', %s, %s,
                        %s, %s, %s, %s, %s, %s)
                """,
                (
                    name_tmpl.format(who=p["account_name"]),
                    f"关联商机『{p['name']}』",
                    status, priority,
                    today - timedelta(days=2), due,
                    p["accounts_id"], p["contacts_id"],
                    SEED_USER, SEED_USER,
                ),
            )


def _seed_quotes(db, potentials: list[dict], products: list[dict]) -> list[dict]:
    """Return list of {quote_id, potential, amount, product} for downstream linking."""
    today = date.today()
    out: list[dict] = []

    # Drive validity_end distribution: one expired, one expiring, rest healthy.
    expiry_plan = [-8, 3, 20, 45, 60, 90]  # days from today
    random.shuffle(expiry_plan)
    plan_iter = iter(expiry_plan)

    proposal_stages = {"proposal_sent", "negotiation", "方案", "谈判", "口头确认"}
    for p in potentials:
        milestone = (p["milestone"] or "").lower()
        if milestone not in proposal_stages and milestone not in {"won", "成交"}:
            continue

        amount = int(p["amount"] or 0) or 200_000
        try:
            offset = next(plan_iter)
        except StopIteration:
            offset = random.choice([30, 60, -5])
        validity = today + timedelta(days=offset)
        status = "accepted" if milestone in {"won", "成交"} else "sent"
        quote_date = today - timedelta(days=random.randint(3, 20))

        product = _pick_product(p["name"], products)

        row = db.fetchone(
            """
            INSERT INTO quotes
                (quotes_name, accounts_id, contacts_id, potentials_id,
                 status, quotedate, validity_end,
                 total_quantity, total_with_tax, final_amount,
                 adjustment_remark, created_by, updated_by)
            VALUES (%s, %s, %s, %s,
                    %s, %s, %s,
                    1, %s::jsonb, %s::jsonb,
                    '[commerce-seed]', %s, %s)
            RETURNING quotes_id
            """,
            (
                f"{p['name']} 报价单",
                p["accounts_id"], p["contacts_id"], p["potentials_id"],
                status, quote_date, validity,
                _money(amount), _money(amount),
                SEED_USER, SEED_USER,
            ),
        )
        quote_id = row["quotes_id"]

        # Attach one line item. In a real quote a rep might add multiple
        # SKUs, but for demo fidelity one product per quote keeps the
        # amount math tidy (total == subtotal == potential.amount).
        db.execute(
            """
            INSERT INTO quotes_products
                (quotes_id, products_id, productno, products_name,
                 model, specifications, quantity, tax_rate,
                 price, subtotal, total)
            VALUES (%s, %s, %s, %s, '', '', 1, 0,
                    %s::jsonb, %s::jsonb, %s::jsonb)
            """,
            (
                quote_id, product["id"], product["productno"], product["name"],
                _money(amount), _money(amount), _money(amount),
            ),
        )

        out.append({
            "quote_id": quote_id,
            "potential": p,
            "amount": amount,
            "status": status,
            "product": product,
        })
    return out


def _seed_salesorders(db, quotes: list[dict]) -> list[dict]:
    today = date.today()
    out: list[dict] = []
    for q in quotes:
        if q["status"] != "accepted":
            continue
        p = q["potential"]
        product = q["product"]   # inherit from the accepted quote
        row = db.fetchone(
            """
            INSERT INTO salesorders
                (salesorders_name, status, ordertype, description,
                 orderdate, accounts_id, contacts_id, quotes_id,
                 potentials_id, total_quantity,
                 total_with_tax, final_amount,
                 created_by, updated_by)
            VALUES (%s, 'confirmed', 'standard',
                    '[commerce-seed] 由报价转化',
                    %s, %s, %s, %s, %s, 1,
                    %s::jsonb, %s::jsonb,
                    %s, %s)
            RETURNING salesorders_id
            """,
            (
                f"{p['name']} 销售订单",
                today - timedelta(days=random.randint(2, 10)),
                p["accounts_id"], p["contacts_id"], q["quote_id"],
                p["potentials_id"],
                _money(q["amount"]), _money(q["amount"]),
                SEED_USER, SEED_USER,
            ),
        )
        order_id = row["salesorders_id"]

        # Carry the same line item across into the order.
        db.execute(
            """
            INSERT INTO salesorders_products
                (salesorders_id, products_id, productno, products_name,
                 model, specifications, quantity, tax_rate,
                 price, subtotal, total)
            VALUES (%s, %s, %s, %s, '', '', 1, 0,
                    %s::jsonb, %s::jsonb, %s::jsonb)
            """,
            (
                order_id, product["id"], product["productno"], product["name"],
                _money(q["amount"]), _money(q["amount"]), _money(q["amount"]),
            ),
        )

        out.append({
            "order_id": order_id,
            "potential": p,
            "amount": q["amount"],
        })
    return out


def _seed_receivables(db, orders: list[dict]) -> list[dict]:
    today = date.today()
    out: list[dict] = []
    for o in orders:
        # Split 50% prepay (due past) + 50% acceptance (due future).
        half = o["amount"] // 2
        p = o["potential"]
        for i, (ratio, status, offset) in enumerate([
            (half, "paid",    -30),     # prepay, already paid
            (o["amount"] - half, "unpaid", 15),  # balance, upcoming
        ]):
            row = db.fetchone(
                """
                INSERT INTO receivables
                    (receivables_name, accounts_id, contacts_id,
                     salesorders_id, dueamount, actualamount, actualdate,
                     duedate, receivablesstatus, balance,
                     created_by, updated_by)
                VALUES (%s, %s, %s, %s,
                        %s::jsonb, %s::jsonb, %s,
                        %s, %s, %s::jsonb,
                        %s, %s)
                RETURNING receivables_id
                """,
                (
                    f"{p['name']} · {'首款' if i == 0 else '尾款'} [commerce-seed]",
                    p["accounts_id"], p["contacts_id"], o["order_id"],
                    _money(ratio),
                    _money(ratio if status == "paid" else 0),
                    (today + timedelta(days=offset)) if status == "paid" else None,
                    today + timedelta(days=offset),
                    status,
                    _money(0 if status == "paid" else ratio),
                    SEED_USER, SEED_USER,
                ),
            )
            out.append({
                "receivable_id": row["receivables_id"],
                "amount": ratio,
                "status": status,
            })
    return out


def _seed_payments(db, receivables: list[dict]) -> None:
    today = date.today()
    # Keep paydate inside the current month so the daily-feed
    # "本月已收" stat shows seeded data instead of zero.
    month_start = today.replace(day=1)
    max_offset = max(0, (today - month_start).days)
    for r in receivables:
        if r["status"] != "paid":
            continue
        db.execute(
            """
            INSERT INTO payments
                (payments_name, paydate, payamount, remark,
                 receivables_id, created_by, updated_by)
            VALUES (%s, %s, %s::jsonb, %s, %s, %s, %s)
            """,
            (
                f"收款 #{r['receivable_id']}",
                today - timedelta(days=random.randint(0, max_offset)),
                _money(r["amount"]),
                "[commerce-seed] 银行到账已核对",
                r["receivable_id"], SEED_USER, SEED_USER,
            ),
        )


def _load_potentials(db) -> list[dict]:
    return db.fetchall(
        """
        SELECT p.potentials_id, p.potentials_name AS name,
               p.milestone, p.amount, p.accounts_id, p.contacts_id,
               a.accounts_name AS account_name
          FROM potentials p
          LEFT JOIN accounts a ON a.accounts_id = p.accounts_id
         WHERE p.deleted = false
         ORDER BY p.potentials_id
        """
    )


def main() -> None:
    random.seed(20260418)
    with db_session() as db:
        print("⟳ cleaning previous commerce-seed rows …")
        _clean(db)

        potentials = _load_potentials(db)
        print(f"→ {len(potentials)} potentials to anchor against")

        products = _seed_products(db)
        print(f"✓ products: {len(products)}")

        _seed_tasks(db, potentials)
        tasks_n = db.fetchone(
            "SELECT count(*) AS n FROM tasks WHERE deleted=false AND created_by=%s",
            (SEED_USER,),
        )["n"]
        print(f"✓ tasks: {tasks_n}")

        quotes = _seed_quotes(db, potentials, products)
        qp_n = db.fetchone("SELECT count(*) AS n FROM quotes_products")["n"]
        print(f"✓ quotes: {len(quotes)}  (line items: {qp_n})")

        orders = _seed_salesorders(db, quotes)
        sp_n = db.fetchone("SELECT count(*) AS n FROM salesorders_products")["n"]
        print(f"✓ salesorders: {len(orders)}  (line items: {sp_n})")

        receivables = _seed_receivables(db, orders)
        print(f"✓ receivables: {len(receivables)}")

        _seed_payments(db, receivables)
        pays_n = db.fetchone(
            "SELECT count(*) AS n FROM payments WHERE deleted=false AND created_by=%s",
            (SEED_USER,),
        )["n"]
        print(f"✓ payments: {pays_n}")

    print("\n✅ commerce seed complete")


if __name__ == "__main__":
    main()
