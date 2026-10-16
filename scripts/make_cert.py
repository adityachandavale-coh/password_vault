"""Create a private local CA and server certificate for a LAN host.

Install only ca.crt on client devices, then explicitly trust it. Keep ca.key private.
"""
import argparse
import ipaddress
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from homehub.config import CERT_DIR


def write_private_key(path: Path, key) -> None:
    path.touch(mode=0o600, exist_ok=True)
    path.write_bytes(key.private_bytes(serialization.Encoding.PEM,
        serialization.PrivateFormat.TraditionalOpenSSL, serialization.NoEncryption()))


def subject_names(hosts: list[str]) -> list[x509.GeneralName]:
    names: list[x509.GeneralName] = []
    for name in hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(name)))
        except ValueError:
            names.append(x509.DNSName(name))
    return names


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', required=True, help='LAN IP address or DNS hostname used by clients')
    parser.add_argument('--dns', action='append', default=[], help='Additional DNS name; repeat as needed')
    parser.add_argument('--out', default=None,
                        help=f'Output directory (default: {CERT_DIR})')
    args = parser.parse_args()
    output = Path(args.out).expanduser().resolve() if args.out else CERT_DIR
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
          # macOS, iOS, and Android reject a trusted CA without keyCertSign.
          .add_extension(x509.KeyUsage(digital_signature=False, content_commitment=False,
                                       key_encipherment=False, data_encipherment=False,
                                       key_agreement=False, key_cert_sign=True, crl_sign=True,
                                       encipher_only=False, decipher_only=False), critical=True)
          .add_extension(x509.SubjectKeyIdentifier.from_public_key(ca_key.public_key()), critical=False)
          .sign(ca_key, hashes.SHA256()))
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=3072)
    server = (x509.CertificateBuilder()
              .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, args.host)]))
              .issuer_name(ca_name).public_key(server_key.public_key())
              .serial_number(x509.random_serial_number())
              # Apple platforms reject leaf certificates valid for more than 825 days.
              .not_valid_before(now - timedelta(days=1)).not_valid_after(now + timedelta(days=365))
              .add_extension(x509.SubjectAlternativeName(subject_names([args.host, *args.dns])), critical=False)
              .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
              .add_extension(x509.KeyUsage(digital_signature=True, content_commitment=False,
                                           key_encipherment=True, data_encipherment=False,
                                           key_agreement=False, key_cert_sign=False, crl_sign=False,
                                           encipher_only=False, decipher_only=False), critical=True)
              .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
              .add_extension(x509.SubjectKeyIdentifier.from_public_key(server_key.public_key()), critical=False)
              .add_extension(x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()), critical=False)
              .sign(ca_key, hashes.SHA256()))
    write_private_key(output / 'ca.key', ca_key)
    write_private_key(output / 'server.key', server_key)
    (output / 'ca.crt').write_bytes(ca.public_bytes(serialization.Encoding.PEM))
    (output / 'server.crt').write_bytes(server.public_bytes(serialization.Encoding.PEM))
    print(f'Certificates created in {output}. Install ca.crt on each client; keep ca.key private.')


if __name__ == '__main__':
    main()
