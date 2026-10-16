"""Create a private local CA and server certificate for a LAN host.

Install only ca.crt on client devices, then explicitly trust it. Keep ca.key private.
"""
import argparse
import ipaddress
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID


def write_private_key(path: Path, key) -> None:
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='LAN IP address or DNS hostname used by clients')
    parser.add_argument('--dns', action='append', default=[], help='Additional DNS name; repeat as needed')
    parser.add_argument('--out', default='data/certs')
    args = parser.parse_args()
    output = Path(args.out)
    output.mkdir(parents=True, exist_ok=True)
    if any(output.iterdir()):
        raise SystemExit(f'{output} is not empty; keep existing certificates or choose another directory')
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, 'HomeHub Local CA')])
    ca = (x509.CertificateBuilder().subject_name(ca_name).issuer_name(ca_name)
          .public_key(ca_key.public_key()).serial_number(x509.random_serial_number())
          .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=3650))
          .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    names = []
    for name in [args.host, *args.dns]:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            names.append(x509.DNSName(name))
    server = (x509.CertificateBuilder()
              .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.host)]))
              .issuer_name(ca_name).public_key(server_key.public_key())
              .serial_number(x509.random_serial_number())
              .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
              .add_extension(x509.SubjectAlternativeName(names), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .sign(ca_key, hashes.SHA256()))
    write_private_key(output / 'ca.key', ca_key)
    write_private_key(output / 'server.key', server_key)
    (output / 'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (output / 'server.crt').write_bytes(server.public_bytes(serialization.Encoding.PEM))
    print(f'Certificates created in {output}. Install ca.crt on each client; keep ca.key private.')


if __name__ == '__main__':
    main()

