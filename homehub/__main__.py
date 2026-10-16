"""Cross-platform entry point: `python -m homehub`.

Works the same on Windows, macOS, and Linux, and reads the host, port, and
certificate location from `.env` instead of hard-coding them in a shell script.
"""
import argparse
import sys

import uvicorn

from .config import CERT_DIR, HOST, PORT, PROJECT_ROOT


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog='python -m homehub', description='Run the HomeHub server.')
    parser.add_argument('--host', default=HOST, help='Address to bind (default from HOMEHUB_HOST)')
    parser.add_argument('--port', type=int, default=PORT, help='Port to bind (default from HOMEHUB_PORT)')
    parser.add_argument('--http', action='store_true',
                        help='Serve plain HTTP for local development on this machine only')
    parser.add_argument('--reload', action='store_true', help='Reload on source changes (development)')
    args = parser.parse_args(argv)

    options = {'host': args.host, 'port': args.port, 'reload': args.reload}
    if args.reload:
        options['reload_dirs'] = [str(PROJECT_ROOT / 'homehub')]
    if args.http:
        if args.host not in {'127.0.0.1', 'localhost', '::1'}:
            parser.error('--http may only be used with --host 127.0.0.1; LAN access requires HTTPS')
    else:
        certificate, key = CERT_DIR / 'server.crt', CERT_DIR / 'server.key'
        missing = [path for path in (certificate, key) if not path.exists()]
        if missing:
            print(f'Missing {", ".join(path.name for path in missing)} in {CERT_DIR}.\n'
                  f'Create them with:\n'
                  f'  python scripts/make_cert.py --host YOUR-LAN-IP\n'
                  f'Or serve plain HTTP on this machine only with:\n'
                  f'  python -m homehub --host 127.0.0.1 --port {args.port} --http',
                  file=sys.stderr)
            return 1
        options['ssl_certfile'] = str(certificate)
        options['ssl_keyfile'] = str(key)

    scheme = 'http' if args.http else 'https'
    display = '127.0.0.1' if args.host in {'0.0.0.0', '::'} else args.host
    print(f'HomeHub listening on {scheme}://{display}:{args.port}')
    uvicorn.run('homehub.main:app', **options)
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
