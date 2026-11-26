"""python -m reqcheck.server [--host 127.0.0.1] [--port 8000]"""

import argparse

import uvicorn

from reqcheck.server.app import create_app


def main() -> None:
    p = argparse.ArgumentParser(prog="python -m reqcheck.server", description=__doc__)
    p.add_argument("--host", default="127.0.0.1", help="адрес; для доступа из сети — 0.0.0.0")
    p.add_argument("--port", type=int, default=8000)
    args = p.parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
