#!/usr/bin/env python3
"""Zero-dependency HTTP server for `storefront/` + local SQLite `/api/commerce/v1/*`.

Binds to `0.0.0.0` with permissive host/origin handling so browser live previews
(e.g. `https://{port}-{sandboxId}.e2b.app`) work out of the box without calling
localhost from browser JavaScript.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import secrets
import sqlite3
import sys
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from engine.commerce.cod_checkout import (  # noqa: E402
    CheckoutError,
    CheckoutLineInput,
    CheckoutRequest,
    load_store_catalog,
    prepare_checkout,
)
from engine.commerce.orders import ALLOWED as ALLOWED_TRANSITIONS  # noqa: E402

STOREFRONT_DIR = ROOT / "storefront"
SCHEMA_SQL_PATH = ROOT / "commerce-migrations" / "0001_commerce_spine.sql"


def _utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class LocalCommerceStore:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA_SQL_PATH.read_text(encoding="utf-8"))
        self.preview_token = "preview-" + secrets.token_urlsafe(24)
        self.catalogs_raw: dict[str, dict[str, Any]] = {}
        for p in sorted((ROOT / "config" / "stores").glob("*.json")):
            data = json.loads(p.read_text(encoding="utf-8"))
            self.catalogs_raw[data["store_id"]] = data

    def create_order(self, body: dict[str, Any]) -> tuple[int, dict[str, Any]]:
        if str(body.get("website") or "").strip():
            return 400, {"ok": False, "error": "invalid_submission"}

        store_id = str(body.get("store_id") or "raawaa").strip().lower()
        if store_id not in self.catalogs_raw:
            return 400, {"ok": False, "error": f"متجر غير معروف: {store_id}"}

        catalog = load_store_catalog(store_id, root=ROOT)
        raw_lines = body.get("lines") or []
        if not isinstance(raw_lines, list) or not raw_lines:
            return 400, {"ok": False, "error": "يرجى اختيار منتج واحد على الأقل."}

        try:
            lines = [
                CheckoutLineInput(sku=str(item.get("sku", "")), quantity=int(item.get("quantity", 1)))
                for item in raw_lines
            ]
            scent_raw = str(body.get("scent") or "").strip()
            scent_label = scent_raw
            for sc in self.catalogs_raw[store_id].get("scents", []):
                if sc["id"] == scent_raw:
                    scent_label = sc["label_ar"]
                    break
            req = CheckoutRequest(
                customer_name=str(body.get("customer_name") or ""),
                customer_phone=str(body.get("customer_phone") or ""),
                zone_id=str(body.get("zone_id") or catalog.primary_zone_id),
                lines=lines,
                address_detail=str(body.get("address_detail") or ""),
                payment_method=str(body.get("payment_method") or "cod"),
                wallet_transfer_ref=str(body.get("wallet_transfer_ref") or ""),
                notes=str(body.get("notes") or ""),
                scent=scent_label,
            )
            prepared = prepare_checkout(catalog, req)
        except (CheckoutError, ValueError, TypeError) as exc:
            return 400, {"ok": False, "error": str(exc)}

        cur = self.conn.execute(
            "SELECT order_ref, state, created_at FROM commerce_orders WHERE idempotency_key = ?",
            (prepared.idempotency_key,),
        )
        existing = cur.fetchone()
        if existing:
            return 200, {
                "ok": True,
                "duplicate": True,
                "served_by": "local-preview-sqlite",
                "store_id": prepared.store_id,
                "order_ref": existing["order_ref"],
                "state": existing["state"],
                "subtotal": prepared.subtotal.major,
                "discount": prepared.discount.major,
                "delivery_fee": prepared.delivery_fee.major,
                "total": prepared.total.major,
                "currency": prepared.total.currency,
                "rto_risk": {
                    "level": prepared.rto_risk.level,
                    "score": prepared.rto_risk.score,
                    "reasons": prepared.rto_risk.reasons,
                    "required_gate": prepared.rto_risk.required_gate,
                },
                "whatsapp_url": prepared.whatsapp_url,
                "inspection_policy_ar": prepared.inspection_policy_ar,
                "created_at": existing["created_at"],
            }

        raw_cat = self.catalogs_raw[store_id]
        cost_map = {i["sku"]: float(i.get("unit_cost_estimate", 0)) for i in raw_cat.get("items", [])}
        unit_cost_cents = sum(
            round(cost_map.get(line["sku"], 0.0) * 100) * int(line["quantity"])
            for line in prepared.lines
        )
        phone_hash = hashlib.sha256(prepared.customer_phone.encode("utf-8")).hexdigest()

        with self.conn:
            self.conn.execute(
                """
                INSERT INTO commerce_orders (
                  store_id, order_ref, idempotency_key, state,
                  customer_name, customer_phone, customer_phone_hash,
                  zone_id, zone_label_ar, address_detail,
                  payment_method, wallet_transfer_ref, scent, notes,
                  lines_json, currency,
                  subtotal_cents, discount_cents, delivery_cents, total_cents,
                  unit_cost_cents, courier_cost_cents, refusal_cost_cents, spoilage_bps,
                  rto_level, rto_score, rto_gate, rto_reasons_json,
                  whatsapp_confirmed, created_at, updated_at
                ) VALUES (?, ?, ?, 'requested', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 200, 100, 10000, ?, ?, ?, ?, 0, ?, ?)
                """,
                (
                    prepared.store_id,
                    prepared.order_ref,
                    prepared.idempotency_key,
                    prepared.customer_name,
                    prepared.customer_phone,
                    phone_hash,
                    prepared.zone_id,
                    prepared.zone_label_ar,
                    prepared.address_detail,
                    prepared.payment_method,
                    prepared.wallet_transfer_ref,
                    prepared.scent,
                    prepared.notes,
                    json.dumps(prepared.lines, ensure_ascii=False),
                    prepared.total.currency,
                    prepared.subtotal.minor,
                    prepared.discount.minor,
                    prepared.delivery_fee.minor,
                    prepared.total.minor,
                    unit_cost_cents,
                    prepared.rto_risk.level,
                    prepared.rto_risk.score,
                    prepared.rto_risk.required_gate,
                    json.dumps(prepared.rto_risk.reasons),
                    prepared.created_at,
                    prepared.created_at,
                ),
            )
            self.conn.execute(
                """
                INSERT INTO commerce_order_events (order_ref, from_state, to_state, actor, reason, created_at)
                VALUES (?, NULL, 'requested', 'customer', 'created', ?)
                """,
                (prepared.order_ref, prepared.created_at),
            )

        return 201, {
            "ok": True,
            "duplicate": False,
            "served_by": "local-preview-sqlite",
            "store_id": prepared.store_id,
            "order_ref": prepared.order_ref,
            "state": "requested",
            "subtotal": prepared.subtotal.major,
            "discount": prepared.discount.major,
            "delivery_fee": prepared.delivery_fee.major,
            "total": prepared.total.major,
            "currency": prepared.total.currency,
            "rto_risk": {
                "level": prepared.rto_risk.level,
                "score": prepared.rto_risk.score,
                "reasons": prepared.rto_risk.reasons,
                "required_gate": prepared.rto_risk.required_gate,
            },
            "whatsapp_url": prepared.whatsapp_url,
            "inspection_policy_ar": prepared.inspection_policy_ar,
            "created_at": prepared.created_at,
        }

    def list_orders(self, store_id: str = "all", state: str = "all", limit: int = 100) -> dict[str, Any]:
        sql = "SELECT * FROM commerce_orders WHERE 1=1"
        params: list[Any] = []
        if store_id and store_id != "all":
            sql += " AND store_id = ?"
            params.append(store_id)
        if state and state != "all":
            sql += " AND state = ?"
            params.append(state)
        sql += " ORDER BY created_at DESC, id DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(sql, params).fetchall()
        return {
            "ok": True,
            "served_by": "local-preview-sqlite",
            "summary": self._summary(rows),
            "orders": [self._format_row(r) for r in rows],
        }

    def transition_order(self, order_ref: str, next_state: str, reason: str = "") -> tuple[int, dict[str, Any]]:
        row = self.conn.execute(
            "SELECT * FROM commerce_orders WHERE order_ref = ?", (order_ref,)
        ).fetchone()
        if not row:
            return 404, {"ok": False, "error": "order_not_found"}
        cur_state = row["state"]
        if next_state not in ALLOWED_TRANSITIONS.get(cur_state, set()):
            return 409, {"ok": False, "error": f"لا يمكن الانتقال من ({cur_state}) إلى ({next_state})."}
        if next_state in {"refused", "cancelled"} and not reason.strip():
            return 400, {"ok": False, "error": "يرجى تحديد سبب الرفض أو الإلغاء."}
        now = _utc_iso()
        wa_conf = 1 if next_state == "confirmed" else int(row["whatsapp_confirmed"])
        with self.conn:
            self.conn.execute(
                "UPDATE commerce_orders SET state = ?, whatsapp_confirmed = ?, updated_at = ? WHERE order_ref = ?",
                (next_state, wa_conf, now, order_ref),
            )
            self.conn.execute(
                "INSERT INTO commerce_order_events (order_ref, from_state, to_state, actor, reason, created_at) VALUES (?, ?, ?, 'owner', ?, ?)",
                (order_ref, cur_state, next_state, reason or next_state, now),
            )
        updated = self.conn.execute(
            "SELECT * FROM commerce_orders WHERE order_ref = ?", (order_ref,)
        ).fetchone()
        return 200, {"ok": True, "order": self._format_row(updated)}

    def _format_row(self, row: sqlite3.Row) -> dict[str, Any]:
        total_usd = round(int(row["total_cents"]) / 100, 2)
        phone = str(row["customer_phone"])
        brand_ar = self.catalogs_raw.get(row["store_id"], {}).get("brand_ar", row["store_id"])
        from urllib.parse import quote
        reply_msg = (
            f"مرحبًا {row['customer_name']}، بخصوص طلبك #{row['order_ref']} من {brand_ar} "
            f"(${total_usd:.2f}) — نؤكد تجهيز طلبك وإرساله إلى {row['zone_label_ar']}."
        )
        return {
            "store_id": row["store_id"],
            "order_ref": row["order_ref"],
            "state": row["state"],
            "customer_name": row["customer_name"],
            "customer_phone": phone,
            "zone_id": row["zone_id"],
            "zone_label_ar": row["zone_label_ar"],
            "address_detail": row["address_detail"],
            "payment_method": row["payment_method"],
            "wallet_transfer_ref": row["wallet_transfer_ref"],
            "scent": row["scent"],
            "notes": row["notes"],
            "lines": json.loads(row["lines_json"] or "[]"),
            "currency": row["currency"],
            "subtotal": round(int(row["subtotal_cents"]) / 100, 2),
            "discount": round(int(row["discount_cents"]) / 100, 2),
            "delivery_fee": round(int(row["delivery_cents"]) / 100, 2),
            "total": total_usd,
            "whatsapp_confirmed": bool(row["whatsapp_confirmed"]),
            "rto_risk": {
                "level": row["rto_level"],
                "score": int(row["rto_score"]),
                "required_gate": row["rto_gate"],
                "reasons": json.loads(row["rto_reasons_json"] or "[]"),
            },
            "whatsapp_owner_reply_url": f"https://wa.me/963{phone.lstrip('0')}?text={quote(reply_msg)}",
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _summary(self, rows: list[sqlite3.Row]) -> dict[str, Any]:
        by_state = {s: 0 for s in ("requested", "confirmed", "prepared", "shipped", "delivered", "refused", "cancelled")}
        rev_cents = net_cents = loss_cents = pipe_cents = 0
        for r in rows:
            st = r["state"]
            by_state[st] = by_state.get(st, 0) + 1
            if st == "delivered":
                rev_cents += int(r["total_cents"])
                net_cents += int(r["total_cents"]) - int(r["unit_cost_cents"]) - int(r["courier_cost_cents"])
            elif st == "refused":
                spoiled = round(int(r["unit_cost_cents"]) * int(r["spoilage_bps"]) / 10000)
                loss_cents += int(r["refusal_cost_cents"]) + spoiled
            elif st in {"requested", "confirmed", "prepared", "shipped"}:
                pipe_cents += int(r["total_cents"])
        resolved = by_state["delivered"] + by_state["refused"]
        return {
            "total_orders": len(rows),
            "by_state": by_state,
            "pipeline_value_usd": round(pipe_cents / 100, 2),
            "delivered_revenue_usd": round(rev_cents / 100, 2),
            "delivered_contribution_usd": round(net_cents / 100, 2),
            "refused_loss_usd": round(loss_cents / 100, 2),
            "net_contribution_after_refusals_usd": round((net_cents - loss_cents) / 100, 2),
            "refusal_rate_pct": round(by_state["refused"] / resolved * 100, 1) if resolved else None,
        }


def make_handler(store: LocalCommerceStore):
    class PreviewHandler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            sys.stdout.write("[preview] " + (fmt % args) + "\n")
            sys.stdout.flush()

        def _send_json(self, status: int, data: dict[str, Any]) -> None:
            raw = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, OPTIONS")
            self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
            self.end_headers()
            self.wfile.write(raw)

        def _read_json(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length") or 0)
            if length <= 0 or length > 16384:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _authorized(self) -> bool:
            auth = self.headers.get("Authorization") or ""
            return auth.startswith("Bearer ") and len(auth[7:].strip()) >= 8

        def do_OPTIONS(self) -> None:
            self._send_json(200, {"ok": True})

        def do_GET(self) -> None:
            parsed = urlparse(self.path)
            path = unquote(parsed.path)

            if path == "/api/commerce/v1/health":
                self._send_json(200, {
                    "ok": True,
                    "service": "local-preview-sqlite",
                    "stores": list(store.catalogs_raw.keys()),
                })
                return
            if path == "/api/commerce/v1/preview-session":
                self._send_json(200, {"ok": True, "preview_token": store.preview_token})
                return
            if path.startswith("/api/commerce/v1/catalog/"):
                sid = path.rsplit("/", 1)[-1]
                cat = store.catalogs_raw.get(sid)
                if not cat:
                    self._send_json(404, {"ok": False, "error": "unknown_store"})
                else:
                    self._send_json(200, {"ok": True, "catalog": cat})
                return
            if path == "/api/commerce/v1/owner/orders":
                if not self._authorized():
                    self._send_json(401, {"ok": False, "error": "unauthorized"})
                    return
                qs = parse_qs(parsed.query)
                sid = (qs.get("store_id") or ["all"])[0]
                st = (qs.get("state") or ["all"])[0]
                self._send_json(200, store.list_orders(sid, st))
                return

            # Static files under storefront/
            rel = path.lstrip("/")
            if not rel or path.endswith("/"):
                rel = (rel.rstrip("/") + "/index.html").lstrip("/")
            target = (STOREFRONT_DIR / rel).resolve()
            if not str(target).startswith(str(STOREFRONT_DIR.resolve())) or not target.is_file():
                self.send_error(HTTPStatus.NOT_FOUND, "Not Found")
                return
            ctype, _ = mimetypes.guess_type(str(target))
            ctype = ctype or "application/octet-stream"
            if ctype.startswith(("text/", "application/javascript", "application/json")):
                ctype += "; charset=utf-8"
            data = target.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-cache")
            self.end_headers()
            self.wfile.write(data)

        def do_POST(self) -> None:
            parsed = urlparse(self.path)
            if parsed.path == "/api/commerce/v1/orders":
                status, payload = store.create_order(self._read_json())
                self._send_json(status, payload)
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

        def do_PATCH(self) -> None:
            parsed = urlparse(self.path)
            prefix = "/api/commerce/v1/owner/orders/"
            if parsed.path.startswith(prefix):
                if not self._authorized():
                    self._send_json(401, {"ok": False, "error": "unauthorized"})
                    return
                ref = parsed.path[len(prefix):].strip("/")
                body = self._read_json()
                status, payload = store.transition_order(
                    ref, str(body.get("state") or ""), str(body.get("reason") or "")
                )
                self._send_json(status, payload)
                return
            self._send_json(404, {"ok": False, "error": "not_found"})

    return PreviewHandler


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8788)
    parser.add_argument("--db", default=":memory:")
    args = parser.parse_args()

    store = LocalCommerceStore(args.db)
    server = ThreadingHTTPServer((args.host, args.port), make_handler(store))
    print(f"Storefront & Owner Preview listening on http://{args.host}:{args.port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
