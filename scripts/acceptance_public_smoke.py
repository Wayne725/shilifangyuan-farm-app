"""Bounded, read-only public checks. Never submits payment, invoice, or mail jobs."""

import argparse
import asyncio
import json
import time
from urllib.parse import urljoin, urlsplit

import httpx


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--web", required=True)
    parser.add_argument("--api", required=True)
    args = parser.parse_args()
    semaphore = asyncio.Semaphore(3)
    results = []
    payloads = {}
    async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
        async def probe(base, path, expected=200, image=False):
            async with semaphore:
                started = time.monotonic()
                try:
                    response = await client.get(base.rstrip("/") + path)
                    content_type = response.headers.get("content-type", "")
                    passed = response.status_code == expected
                    if image:
                        passed = passed and content_type.startswith("image/")
                    row = {
                        "host": urlsplit(base).netloc,
                        "path": path,
                        "status": response.status_code,
                        "expected": expected,
                        "passed": passed,
                        "milliseconds": round((time.monotonic() - started) * 1000),
                        "bytes": len(response.content),
                        "content_type": content_type,
                        "headers": {
                            name: response.headers.get(name)
                            for name in (
                                "strict-transport-security", "content-security-policy",
                                "x-content-type-options", "x-frame-options", "cache-control",
                            )
                        },
                    }
                    if content_type.startswith("application/json") and response.status_code == 200:
                        payloads[(base, path)] = response.json()
                except httpx.HTTPError as error:
                    row = {"host": urlsplit(base).netloc, "path": path,
                           "passed": False, "error": type(error).__name__}
                results.append(row)

        public_paths = ["/health", "/ready", "/v1/products", "/v1/group-campaigns",
                        "/v1/meal-events", "/v1/pickup-locations", "/v1/shipping-rates"]
        await asyncio.gather(*(probe(args.api, path) for path in public_paths))
        await asyncio.gather(*(
            probe(args.web, path) for path in ("/", "/shop", "/register", "/orders", "/v1/products")
        ))
        await asyncio.gather(*(
            probe(args.api, path, 401)
            for path in ("/v1/auth/me", "/v1/orders", "/v1/admin/suppliers",
                         "/v1/admin/finance/tax-ledger")
        ))
        images = set()
        for path in ("/v1/products", "/v1/meal-events"):
            rows = payloads.get((args.api, path), [])
            if isinstance(rows, list):
                for row in rows:
                    asset = row.get("image_url") or ""
                    if asset.startswith("/assets/"):
                        asset = asset.removeprefix("/assets")
                    if asset.startswith("/") and not asset.startswith("//"):
                        images.add(asset)
        await asyncio.gather(*(probe(args.web, path, image=True) for path in sorted(images)))
        response = await client.options(
            urljoin(args.api, "/v1/orders"),
            headers={"Origin": "https://untrusted.example", "Access-Control-Request-Method": "POST"},
        )
        results.append({"path": "CORS untrusted origin", "status": response.status_code,
                        "passed": response.headers.get("access-control-allow-origin") is None})
    failed = sum(not row["passed"] for row in results)
    print(json.dumps({"checks": len(results), "failed": failed,
                      "results": results}, ensure_ascii=False, indent=2))
    if failed:
        raise SystemExit(1)


if __name__ == "__main__":
    asyncio.run(main())
