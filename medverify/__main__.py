"""Command line: `python -m medverify serve|tick|seed|create-admin`."""

from __future__ import annotations

import argparse
import contextlib
import getpass
import logging


def main() -> None:
    parser = argparse.ArgumentParser(prog="medverify")
    sub = parser.add_subparsers(dest="cmd", required=True)
    serve = sub.add_parser("serve", help="run the web app and API")
    serve.add_argument("--host", default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8000)
    serve.add_argument("--demo", action="store_true", help="seed demo data")
    sub.add_parser("tick", help="run background jobs once (for cron)")
    sub.add_parser("seed", help="seed demo data")
    admin = sub.add_parser("create-admin", help="create a platform admin user")
    admin.add_argument("username")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    from .config import settings
    from .db import Database

    if args.cmd == "serve":
        import uvicorn

        from .app import create_app

        settings.demo = settings.demo or args.demo
        uvicorn.run(create_app(settings), host=args.host, port=args.port)
        return

    database = Database(settings.database_url)
    database.create_all()
    with contextlib.closing(database.session()) as db:
        if args.cmd == "tick":
            from .worker import tick

            print(tick(db))
        elif args.cmd == "seed":
            from .seed import seed_demo

            seed_demo(db)
            print("Demo data ready. Sign in as manager1 / medverify-demo")
        elif args.cmd == "create-admin":
            from .models import User
            from .security import hash_password

            password = getpass.getpass("Password (min 8 chars): ")
            if len(password) < 8:
                raise SystemExit("Password too short")
            db.add(User(username=args.username.lower(), display_name=args.username, role="admin",
                        password_hash=hash_password(password)))
            db.commit()
            print("Admin created")


if __name__ == "__main__":
    main()
