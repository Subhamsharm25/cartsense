"""Command line: `cartsense serve` and `cartsense seed-demo`."""

from __future__ import annotations

import argparse


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cartsense")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the HTTP server")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    sub.add_parser("seed-demo", help="rebuild the database with demo personas and history")
    args = parser.parse_args(argv)

    if args.cmd == "seed-demo":
        from .seed import seed_demo

        stats = seed_demo()
        print(f"seeded {stats['users']} users, {stats['products']} products, {stats['orders']} orders")
        return 0

    import uvicorn

    uvicorn.run("cartsense.app:app", host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
