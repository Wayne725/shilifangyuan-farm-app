"""Disposable local PostgreSQL + real HTTP checkout load; never use production data.

Run from backend with --db-url pointing at shilifangyuan_load_test (or
shilifangyuan_test). Every run owns and removes a fresh load_<uuid> schema.
Only this harness disables background integrations; product code is unchanged.
"""

from __future__ import annotations

import argparse
import asyncio
from collections import Counter
from datetime import datetime, time as datetime_time, timedelta, timezone
import json
import hashlib
import os
from pathlib import Path
import platform
import re
import resource
import socket
import subprocess
import sys
import time
from urllib.parse import urlsplit
from uuid import uuid4
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


ALLOWED_DATABASES = {"shilifangyuan_load_test", "shilifangyuan_test"}
LOOPBACK_HOSTS = {"localhost", "127.0.0.1", "::1"}
SCHEMA_PATTERN = re.compile(r"load_[0-9a-f]{32}\Z")


def validate_database_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme not in {"postgresql", "postgresql+asyncpg"}:
        raise ValueError("Only PostgreSQL is permitted")
    if parsed.hostname not in LOOPBACK_HOSTS or parsed.path[1:] not in ALLOWED_DATABASES:
        raise ValueError("Only an explicitly named disposable loopback database is permitted")
    if parsed.query or parsed.fragment or parsed.port is None:
        raise ValueError("An explicit local port and no URL query/fragment are required")
    return value.replace("postgresql://", "postgresql+asyncpg://", 1)


def validate_schema(value: str) -> str:
    if not SCHEMA_PATTERN.fullmatch(value):
        raise ValueError("A generated load_<uuid hex> schema is required")
    return value


def validate_http_target(value: str) -> str:
    parsed = urlsplit(value)
    if (parsed.scheme != "http" or parsed.hostname != "127.0.0.1"
        or parsed.port is None or parsed.username or parsed.password
        or parsed.path not in {"", "/"} or parsed.query or parsed.fragment):
        raise ValueError("Only the harness-owned http://127.0.0.1:<port> target is permitted")
    return value.rstrip("/")


def block_external_connections() -> None:
    original_connect = socket.socket.connect
    original_connect_ex = socket.socket.connect_ex
    original_getaddrinfo = socket.getaddrinfo

    def allowed(address):
        if not isinstance(address, tuple) or address[0] not in LOOPBACK_HOSTS:
            raise RuntimeError(f"Load harness blocked a non-loopback connection: {address!r}")

    def connect(sock, address):
        allowed(address)
        return original_connect(sock, address)

    def connect_ex(sock, address):
        allowed(address)
        return original_connect_ex(sock, address)

    def getaddrinfo(host, *args, **kwargs):
        if host not in LOOPBACK_HOSTS:
            raise RuntimeError(f"Load harness blocked non-loopback DNS: {host!r}")
        return original_getaddrinfo(host, *args, **kwargs)

    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo


def configure_runtime(database_url: str):
    # Build settings without inherited application secrets or any local .env file.
    from unittest.mock import patch
    import app.config as config
    with patch.dict(os.environ, {}, clear=True):
        settings = config.Settings(
            _env_file=None, environment="test", database_url=database_url,
            app_base_url="https://api.example.test", web_base_url="https://app.example.test",
            jwt_secret="isolated-load-harness-jwt-secret-only-20261031",
            reconciliation_enabled=False, printer_enabled=False, printer_worker_enabled=False,
            printer_dry_run=True, payment_provider="ecpay", invoice_provider="ecpay",
            ecpay_payment_merchant_id="3002607", ecpay_payment_hash_key="pwFHCqoQZGmho4w6",
            ecpay_payment_hash_iv="EkRm7iFT261dpevs", ecpay_payment_stage=True,
            database_pool_size=5, database_max_overflow=5, database_pool_timeout_seconds=10,
        )
    config.get_settings = lambda: settings
    return settings


def percentile(values, quantile):
    if not values:
        return None
    ordered = sorted(values)
    index = (len(ordered) - 1) * quantile
    lower = int(index)
    return round(ordered[lower] + (ordered[min(lower + 1, len(ordered) - 1)] - ordered[lower]) * (index - lower), 3)


def latency_summary(values):
    return {"count": len(values), "p50_ms": percentile(values, .5),
            "p95_ms": percentile(values, .95), "p99_ms": percentile(values, .99),
            "max_ms": round(max(values), 3) if values else None}


def serve(database_url: str, schema: str, port: int) -> None:
    import uvicorn
    settings = configure_runtime(database_url)
    block_external_connections()
    from sqlalchemy import event
    from sqlalchemy.exc import TimeoutError as PoolTimeout
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import AsyncAdaptedQueuePool
    import app.database as database
    import app.main as main

    metrics = {"waits_ms": [], "pool_timeouts": 0, "checked_out": 0,
               "max_checked_out": 0, "sql_count": 0}

    class ObservedPool(AsyncAdaptedQueuePool):
        def _do_get(self):
            started = time.perf_counter()
            try:
                return super()._do_get()
            except PoolTimeout:
                metrics["pool_timeouts"] += 1
                raise
            finally:
                metrics["waits_ms"].append((time.perf_counter() - started) * 1000)

    engine = create_async_engine(
        database_url, poolclass=ObservedPool, pool_pre_ping=True,
        pool_size=settings.database_pool_size, max_overflow=settings.database_max_overflow,
        pool_timeout=settings.database_pool_timeout_seconds, pool_recycle=300,
        connect_args={"timeout": 10, "server_settings": {"search_path": schema}},
    )
    database.SessionLocal.configure(bind=engine)

    @event.listens_for(engine.sync_engine, "checkout")
    def checkout(*_args):
        metrics["checked_out"] += 1
        metrics["max_checked_out"] = max(metrics["max_checked_out"], metrics["checked_out"])

    @event.listens_for(engine.sync_engine, "checkin")
    def checkin(*_args):
        metrics["checked_out"] -= 1

    @event.listens_for(engine.sync_engine, "before_cursor_execute")
    def before_cursor_execute(*_args):
        metrics["sql_count"] += 1

    # The real middleware otherwise schedules reconciliation even when its timer is disabled.
    # No lifespan means no demo seeding, periodic worker, printer, or external provider calls.
    main.schedule_background_reconcile = lambda *_args, **_kwargs: None
    application = main.create_app()

    @application.get("/__load/metrics", include_in_schema=False)
    async def read_metrics(reset: bool = False):
        if reset:
            metrics.update(waits_ms=[], pool_timeouts=0, max_checked_out=metrics["checked_out"], sql_count=0)
        usage = resource.getrusage(resource.RUSAGE_SELF)
        return {**metrics, "pool_acquisition": latency_summary(metrics["waits_ms"]),
                "rss_max_mb": round(usage.ru_maxrss / (1024 * 1024 if sys.platform == "darwin" else 1024), 2),
                "process_cpu_seconds": round(usage.ru_utime + usage.ru_stime, 3)}

    uvicorn.run(application, host="127.0.0.1", port=port, workers=1,
                loop="asyncio", lifespan="off", access_log=False, log_level="warning")


async def run_load(args) -> int:
    database_url = validate_database_url(args.db_url)
    schema = validate_schema(f"load_{uuid4().hex}")
    output = Path(args.output).resolve()
    output.mkdir(parents=True, exist_ok=True)
    settings = configure_runtime(database_url)
    block_external_connections()
    import httpx
    from sqlalchemy import func, select, text
    from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
    from app.auth import create_token
    from app.database import Base
    from app.models import (ExternalEvent, InventoryReservation, Meal, MealEvent, MealEventOffering, MealEventStatus,
                            Order, OrderItem, OutboxEvent, PaymentAttempt, PaymentStatus, Product, ReservationStatus,
                            SystemSetting, User, UserRole)

    admin_engine = create_async_engine(database_url, pool_size=1, max_overflow=0)
    engine = create_async_engine(database_url, pool_size=1, max_overflow=0,
                                connect_args={"server_settings": {"search_path": schema}})
    sessions = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
    child = None
    server_log = None
    records, phases, issues = [], [], []
    result = {"schema": schema, "base_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
              "tracked_tree_dirty": bool(subprocess.check_output(["git", "status", "--porcelain", "--untracked-files=no"], text=True).strip()),
              "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
              "production_readiness": "not_assessed",
              "passed_means": "All harness assertions and expected outcomes passed; known gaps remain and production capacity is not certified",
              "known_gaps": [{"id": "OPS03", "description": "General-order create has no client request idempotency; same payload can create distinct pending orders", "observed": False}],
              "database": {"host": urlsplit(database_url).hostname, "port": urlsplit(database_url).port,
                           "name": urlsplit(database_url).path[1:]},
              "runtime": {"python": sys.version, "platform": platform.platform(), "cpu_count": os.cpu_count(),
                          "server_workers": 1, "pool_size": 5, "max_overflow": 5, "pool_timeout_seconds": 10,
                          "real_tcp_http": True, "integrations": "blocked; no background workers; synthetic ECPay signatures only",
                          "limits": "native process; NOT constrained to production 0.5 CPU / 512 MB"},
              "phases": phases, "issues": issues, "requests": records}
    now = datetime.now(timezone.utc)
    taipei = ZoneInfo("Asia/Taipei")
    if now.astimezone(taipei).hour >= 20:
        raise RuntimeError("Run before 20:00 Asia/Taipei so tomorrow's legitimate meal booking window is open")
    tomorrow = now.astimezone(taipei).date() + timedelta(days=1)
    try:
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
            result["database"]["version"] = await connection.scalar(text("SELECT version()"))
        async with sessions() as session:
            buyers = [User(email=f"load-{index}@example.com", display_name=f"合成買家{index}",
                           password_hash="!load-test-no-login", email_verified_at=now) for index in range(100)]
            admin = User(email="load-admin@example.com", display_name="合成管理員", password_hash="!load-test-no-login", user_role=UserRole.ADMIN)
            meal = Meal(slug="isolated-load-meal", name="合成便當", price=100)
            session.add_all([*buyers, admin, meal,
                             SystemSetting(key="storefront_operations", value={"orders_open": True}),
                             SystemSetting(key="promotion_operations", value={})])
            await session.flush()
            tokens = [create_token(buyer, "access", timedelta(hours=1), settings) for buyer in buyers]
            await session.commit()

        async def seed_regular(label, stock):
            async with sessions() as session:
                product = Product(slug=label, name=f"合成商品-{label}", category="隔離測試", unit="份",
                                  member_price=100, nonmember_price=100, stock_quantity=stock)
                session.add(product)
                await session.commit()
                return product.id

        async def seed_meal(label, capacity):
            async with sessions() as session:
                event = MealEvent(title=f"合成場次-{label}", location="隔離測試地點", created_by_id=admin.id,
                                  status=MealEventStatus.PUBLISHED, allow_cash_payment=True, service_date=tomorrow,
                                  ordering_starts_at=now - timedelta(minutes=1),
                                  ordering_ends_at=datetime.combine(tomorrow, datetime_time.min, taipei),
                                  pickup_starts_at=datetime.combine(tomorrow, datetime_time(11), taipei),
                                  pickup_ends_at=datetime.combine(tomorrow, datetime_time(13, 30), taipei),
                                  offerings=[MealEventOffering(meal_id=meal.id, price=100, capacity=capacity)])
                session.add(event)
                await session.commit()
                return event.id, event.offerings[0].id

        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        target = validate_http_target(f"http://127.0.0.1:{port}")
        server_log = (output / "order-load-server.log").open("w")
        clean_env = {key: os.environ[key] for key in ("PATH", "PYTHONPATH", "LANG", "TMPDIR") if key in os.environ}
        clean_env["PYTHONUNBUFFERED"] = "1"
        started_server = time.perf_counter()
        child = subprocess.Popen([sys.executable, str(Path(__file__).resolve()), "--serve", "--db-url", database_url,
                                  "--schema", schema, "--port", str(port)], env=clean_env, stdout=server_log, stderr=subprocess.STDOUT)
        async with httpx.AsyncClient(base_url=target, trust_env=False, timeout=30,
                                    limits=httpx.Limits(max_connections=100, max_keepalive_connections=100)) as client:
            for _ in range(100):
                if child.poll() is not None:
                    raise RuntimeError("HTTP server exited; inspect order-load-server.log")
                try:
                    response = await client.get("/__load/metrics")
                    if response.status_code == 200:
                        break
                except httpx.ConnectError:
                    pass
                await asyncio.sleep(.1)
            else:
                raise RuntimeError("HTTP server did not become ready")
            result["server_startup_seconds"] = round(time.perf_counter() - started_server, 3)

            async def request(phase, step, path, index, payload=None, expected=(200, 201), form=None):
                started = time.perf_counter()
                record = {"phase": phase, "step": step, "index": index}
                try:
                    response = await client.post(path, json=payload, data=form,
                                                 headers={"Authorization": f"Bearer {tokens[index % len(tokens)]}",
                                                          "X-Request-ID": f"load-{schema[5:13]}-{len(records):06}-{index}"})
                    record.update(status=response.status_code, bytes=len(response.content))
                    if response.status_code not in expected:
                        record["error"] = response.text[:500]
                        raise RuntimeError(f"{phase}/{step}: unexpected {response.status_code}: {response.text[:200]}")
                    return response
                except httpx.HTTPError as exc:
                    record.update(error=type(exc).__name__, status=None)
                    raise
                finally:
                    record["duration_ms"] = round((time.perf_counter() - started) * 1000, 3)
                    records.append(record)

            async def phase(label, concurrency, count, operation):
                if count < 1:
                    raise ValueError(f"{label}: an empty phase cannot pass validation")
                await client.get("/__load/metrics?reset=true")
                semaphore = asyncio.Semaphore(concurrency)
                timings = []
                async def work(index):
                    async with semaphore:
                        started = time.perf_counter()
                        try:
                            return await operation(label, index)
                        finally:
                            timings.append((time.perf_counter() - started) * 1000)
                started = time.perf_counter()
                values = await asyncio.gather(*(work(index) for index in range(count)), return_exceptions=True)
                elapsed = time.perf_counter() - started
                failures = [{"index": index, "error": str(value)} for index, value in enumerate(values) if isinstance(value, BaseException)]
                issues.extend({"phase": label, **failure} for failure in failures)
                metrics = (await client.get("/__load/metrics")).json()
                selected = [record for record in records if record["phase"] == label]
                statuses = Counter(str(record["status"]) for record in selected)
                accepted = sum(value == 201 for value in values) if label in {"regular-last-unit", "meal-last-unit"} else count - len(failures)
                step_metrics = {}
                for step in sorted({record["step"] for record in selected}):
                    step_metrics[step] = latency_summary([record["duration_ms"] for record in selected if record["step"] == step])
                phase_result = {"name": label, "concurrency": concurrency, "workflows": count,
                                "seconds": round(elapsed, 3), "workflows_per_second": round(count / elapsed, 3),
                                "workflow_attempts_per_second": round(count / elapsed, 3),
                                "successful_workflows": accepted, "successful_workflows_per_second": round(accepted / elapsed, 3),
                                "http_requests_per_second": round(len(selected) / elapsed, 3),
                                "http_statuses": dict(statuses), "workflow_latency": latency_summary(timings),
                                "request_latency": latency_summary([record["duration_ms"] for record in selected]),
                                "step_latency": step_metrics,
                                "http_5xx": sum(1 for record in selected if record["status"] is not None and record["status"] >= 500),
                                "client_timeouts": sum(1 for record in selected if "Timeout" in record.get("error", "")),
                                "unexpected_errors": len(failures), "pool": metrics}
                phases.append(phase_result)
                print(json.dumps({key: value for key, value in phase_result.items() if key != "pool"}, ensure_ascii=False), flush=True)
                return values, phase_result

            def regular_operation(product_id, *, pay=True):
                async def operation(label, index):
                    items = [{"product_id": product_id, "quantity": 1}]
                    quoted = await request(label, "quote", "/v1/orders/quote", index, {"items": items})
                    if quoted.json()["amount_total"] != 100:
                        raise AssertionError("Quote total changed")
                    created = await request(label, "create", "/v1/orders", index,
                                            {"items": items, "contact_email": f"load-{index % 100}@example.com"}, expected=(201,))
                    order_id = created.json()["id"]
                    if pay:
                        await request(label, "reserve", f"/v1/orders/{order_id}/payment-attempts", index, expected=(201,))
                    return order_id
                return operation

            def meal_operation(event_id, offering_id):
                async def operation(label, index):
                    body = {"client_request_id": str(uuid4()), "payment_method": "cash_on_pickup",
                            "contact_email": f"load-{index % 100}@example.com",
                            "items": [{"offering_id": offering_id, "quantity": 1}]}
                    quoted = await request(label, "quote", f"/v1/meal-events/{event_id}/quote", index, body)
                    body["expected_total"] = quoted.json()["amount_total"]
                    created = await request(label, "create", f"/v1/meal-events/{event_id}/orders", index, body, expected=(201,))
                    return created.json()["id"]
                return operation

            async def regular_invariant(product_id, initial, expected_allocated, expected_orders):
                async with sessions() as session:
                    stock = await session.scalar(select(Product.stock_quantity).where(Product.id == product_id))
                    allocations = (await session.execute(select(InventoryReservation.status, func.sum(InventoryReservation.quantity)).where(
                        InventoryReservation.source_product_id == product_id).group_by(InventoryReservation.status))).all()
                    quantities = {status.value: quantity for status, quantity in allocations}
                    count = await session.scalar(select(func.count(Order.id)).join(OrderItem).where(OrderItem.source_product_id == product_id))
                    allocated = quantities.get("active", 0) + quantities.get("consumed", 0)
                    observation = {"initial": initial, "stock": stock, "reservations": quantities, "orders": count,
                                   "allocated": allocated, "conserved": stock + allocated == initial,
                                   "expected_allocated": expected_allocated, "expected_orders": expected_orders}
                    if not observation["conserved"] or stock < 0 or allocated != expected_allocated or count != expected_orders:
                        issues.append({"invariant": "regular", **observation})
                    return observation

            async def meal_invariant(offering_id, expected):
                async with sessions() as session:
                    offering = await session.get(MealEventOffering, offering_id)
                    quantity = await session.scalar(select(func.coalesce(func.sum(InventoryReservation.quantity), 0)).where(
                        InventoryReservation.source_meal_offering_id == offering_id,
                        InventoryReservation.status == ReservationStatus.ACTIVE))
                    count = await session.scalar(select(func.count(Order.id)).join(OrderItem).where(OrderItem.source_meal_offering_id == offering_id))
                    observation = {"capacity": offering.capacity, "reserved": offering.reserved_quantity,
                                   "paid": offering.paid_quantity, "active_reservations": quantity, "orders": count}
                    if offering.reserved_quantity != quantity or quantity != expected or count != expected or offering.reserved_quantity + offering.paid_quantity > offering.capacity:
                        issues.append({"invariant": "meal", **observation})
                    return observation

            warm_product = await seed_regular("warmup", 10)
            _, warm = await phase("cold-and-warmup", 1, 10, regular_operation(warm_product))
            warm["invariants"] = await regular_invariant(warm_product, 10, 10, 10)
            if issues:
                raise RuntimeError("Warmup/preflight failed; refusing to continue the load stages")
            regular_products = []
            for concurrency in (1, 5, 20, 50):
                product_id = await seed_regular(f"regular-c{concurrency}", args.per_stage)
                regular_products.append(product_id)
                _, current = await phase(f"regular-c{concurrency}", concurrency, args.per_stage, regular_operation(product_id))
                current["invariants"] = await regular_invariant(product_id, args.per_stage, args.per_stage, args.per_stage)
                event_id, offering_id = await seed_meal(f"meal-c{concurrency}", args.per_stage)
                _, current = await phase(f"meal-c{concurrency}", concurrency, args.per_stage, meal_operation(event_id, offering_id))
                current["invariants"] = await meal_invariant(offering_id, args.per_stage)

            last_product = await seed_regular("regular-last-unit", 1)
            order_ids, preparation = await phase("regular-last-prepare", 50, 50, regular_operation(last_product, pay=False))
            if any(isinstance(value, BaseException) for value in order_ids):
                raise RuntimeError("Cannot test last stock after failed preparation")
            async def last_regular(label, index):
                response = await request(label, "reserve", f"/v1/orders/{order_ids[index]}/payment-attempts", index, expected=(201, 409))
                return response.status_code
            values, current = await phase("regular-last-unit", 50, 50, last_regular)
            if Counter(values) != {201: 1, 409: 49}:
                issues.append({"invariant": "regular-last-status", "actual": [str(value) for value in values]})
            current["invariants"] = await regular_invariant(last_product, 1, 1, 50)

            event_id, offering_id = await seed_meal("meal-last-unit", 1)
            # Quote all requests before racing creation; losing 409s are intentional conflicts.
            bodies = [{"client_request_id": str(uuid4()), "payment_method": "cash_on_pickup", "expected_total": 100,
                       "contact_email": f"load-{index}@example.com", "items": [{"offering_id": offering_id, "quantity": 1}]} for index in range(50)]
            for index, body in enumerate(bodies):
                await request("meal-last-prepare", "quote", f"/v1/meal-events/{event_id}/quote", index, body)
            async def last_meal(label, index):
                response = await request(label, "create", f"/v1/meal-events/{event_id}/orders", index, bodies[index], expected=(201, 409))
                return response.status_code
            values, current = await phase("meal-last-unit", 50, 50, last_meal)
            if Counter(values) != {201: 1, 409: 49}:
                issues.append({"invariant": "meal-last-status", "actual": [str(value) for value in values]})
            current["invariants"] = await meal_invariant(offering_id, 1)

            replay_product = await seed_regular("regular-replay", 10)
            async def regular_replay(label, index):
                response = await request(label, "create", "/v1/orders", 0,
                                         {"items": [{"product_id": replay_product, "quantity": 1}], "contact_email": "load-0@example.com"}, expected=(201,))
                return response.json()["id"]
            values, current = await phase("regular-replay-known-OPS03", 4, 4, regular_replay)
            current["known_gap"] = {"same_payload_requests": 4, "distinct_orders": len(set(values)),
                                    "meaning": "General order creation has no request idempotency; duplicates are distinct pending orders"}
            result["known_gaps"][0].update(observed=True, same_payload_requests=4, distinct_orders=len(set(values)))
            current["invariants"] = await regular_invariant(replay_product, 10, 0, 4)

            replay_event, replay_offering = await seed_meal("meal-replay", 10)
            replay_body = {"client_request_id": str(uuid4()), "payment_method": "cash_on_pickup", "expected_total": 100,
                           "contact_email": "load-0@example.com", "items": [{"offering_id": replay_offering, "quantity": 1}]}
            async def meal_replay(label, index):
                response = await request(label, "create", f"/v1/meal-events/{replay_event}/orders", 0, replay_body, expected=(201,))
                return response.json()["id"]
            values, current = await phase("meal-idempotent-replay", 4, 4, meal_replay)
            current["distinct_orders"] = len(set(values))
            if len(set(values)) != 1:
                issues.append({"invariant": "meal replay duplicates"})
            current["invariants"] = await meal_invariant(replay_offering, 1)

            # Signed success and replay exercise the actual local callback endpoint, with no bank call.
            from app.integrations.ecpay import build_check_mac_value
            async with sessions() as session:
                attempts = list(await session.scalars(select(PaymentAttempt).join(Order).join(OrderItem).where(
                    OrderItem.source_product_id == regular_products[0]).order_by(PaymentAttempt.id).limit(25)))
            if len(attempts) != 25:
                raise AssertionError(f"Expected 25 real reserved attempts for callbacks; got {len(attempts)}")
            callback_forms = []
            for attempt in attempts:
                date = datetime.now(taipei).strftime("%Y/%m/%d %H:%M:%S")
                form = {"MerchantID": settings.ecpay_payment_merchant_id, "MerchantTradeNo": attempt.merchant_trade_no,
                        "TradeAmt": str(attempt.amount), "RtnCode": "1", "RtnMsg": "合成成功", "SimulatePaid": "0",
                        "PaymentDate": date, "TradeDate": date, "TradeNo": f"LOAD{attempt.id.replace('-', '')}"}
                form["CheckMacValue"] = build_check_mac_value(form, settings.ecpay_payment_hash_key, settings.ecpay_payment_hash_iv)
                callback_forms.extend([form, form])
            async def callback(label, index):
                response = await request(label, "callback", "/webhooks/ecpay/payment", index, expected=(200,), form=callback_forms[index])
                if response.text != "1|OK":
                    raise AssertionError(f"Callback acknowledgement: {response.text}")
                return response.text
            _, current = await phase("synthetic-payment-callback-replay", 20, len(callback_forms), callback)
            current["invariants"] = await regular_invariant(regular_products[0], args.per_stage, args.per_stage, args.per_stage)
            async with sessions() as session:
                paid = await session.scalar(select(func.count(Order.id)).where(Order.id.in_([attempt.order_id for attempt in attempts]), Order.payment_status == PaymentStatus.PAID))
                current["paid_orders"] = paid
                if paid != 25:
                    issues.append({"invariant": "synthetic paid count", "actual": paid})
                actual = {
                    "orders": await session.scalar(select(func.count(Order.id))),
                    "order_items": await session.scalar(select(func.count(OrderItem.id))),
                    "payment_attempts": await session.scalar(select(func.count(PaymentAttempt.id))),
                    "reservation_quantity": await session.scalar(select(func.sum(InventoryReservation.quantity))),
                    "consumed_reservation_quantity": await session.scalar(select(func.sum(InventoryReservation.quantity)).where(InventoryReservation.status == ReservationStatus.CONSUMED)),
                    "external_payment_events": await session.scalar(select(func.count(ExternalEvent.id)).where(ExternalEvent.provider == "ecpay_aio")),
                    "invoice_outbox": await session.scalar(select(func.count(OutboxEvent.id)).where(OutboxEvent.event_type == "invoice.issue_requested")),
                }
                expected = {"orders": 8 * args.per_stage + 66, "order_items": 8 * args.per_stage + 66,
                            "payment_attempts": 4 * args.per_stage + 11, "reservation_quantity": 8 * args.per_stage + 13,
                            "consumed_reservation_quantity": 25, "external_payment_events": 25, "invoice_outbox": 25}
                result["global_invariants"] = {"actual": actual, "expected": expected, "passed": actual == expected}
                if actual != expected:
                    issues.append({"invariant": "global counts or duplicate effects", "actual": actual, "expected": expected})
            result["final_server_metrics"] = (await client.get("/__load/metrics")).json()
            result["cold_first_workflow_requests"] = [record for record in records if record["phase"] == "cold-and-warmup" and record["index"] == 0]
    except BaseException as exc:
        issues.append({"fatal": type(exc).__name__, "message": str(exc)})
        raise
    finally:
        if child is not None:
            child.terminate()
            try:
                child.wait(timeout=10)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if server_log is not None:
            server_log.close()
        await engine.dispose()
        async with admin_engine.begin() as connection:
            await connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema}" CASCADE'))
        await admin_engine.dispose()
        result["schema_removed"] = True
        result["completed_at"] = datetime.now(timezone.utc).isoformat()
        result["passed"] = not issues
        result["assertions_passed"] = not issues
        result["http_summary"] = {"requests": len(records), "status_counts": dict(Counter(str(record["status"]) for record in records)),
                                  "http_5xx": sum(1 for record in records if record.get("status") is not None and record["status"] >= 500),
                                  "client_timeouts": sum(1 for record in records if "Timeout" in record.get("error", ""))}
        (output / "order-load-results.json").write_text(json.dumps(result, ensure_ascii=False, indent=2))
    return 1 if issues else 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db-url", required=True)
    parser.add_argument("--output", default="../outputs/legacy-load-audit")
    parser.add_argument("--per-stage", type=int, default=100)
    parser.add_argument("--serve", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--schema", help=argparse.SUPPRESS)
    parser.add_argument("--port", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()
    database_url = validate_database_url(args.db_url)
    if args.serve:
        validate_schema(args.schema or "")
        if args.port is None or not 1024 <= args.port <= 65535:
            parser.error("A nonprivileged local port is required")
        serve(database_url, args.schema, args.port)
        return 0
    if args.per_stage < 100 or args.per_stage > 125:
        parser.error("--per-stage must be between 100 and 125 (800–1000 core workflows)")
    return asyncio.run(run_load(args))


if __name__ == "__main__":
    raise SystemExit(main())
