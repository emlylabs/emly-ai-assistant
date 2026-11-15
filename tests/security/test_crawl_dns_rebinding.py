"""G13i: the crawler connects to the address it checked.

``validate_url`` resolved the hostname and checked the addresses, then
``requests`` resolved it again to connect. A DNS server answering with a
zero TTL could pass the check with a public address and then send the
connection to an internal one. Now the name is resolved once and the
connection goes to the checked address, with the hostname kept for the
Host header, TLS SNI and certificate verification.

These tests use real local servers on 127.0.0.1, so loopback is allowed
for the test host only.
"""
from __future__ import annotations

import datetime
import ipaddress
import socket
import ssl
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest
import requests

from services import crawl_fetch

HOST = "rebind.test"


class _Echo(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"<html>host={self.headers.get('Host')}</html>".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *a):
        pass


def _serve(server):
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server


@pytest.fixture
def lookups(monkeypatch):
    """``rebind.test`` resolves to 127.0.0.1 and every lookup is recorded.
    Loopback is allowed for this test only."""
    seen = []
    real = socket.getaddrinfo

    def fake(host, *a, **k):
        if host == HOST:
            seen.append(host)
            return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 0))]
        return real(host, *a, **k)

    monkeypatch.setattr(crawl_fetch.socket, "getaddrinfo", fake)
    real_blocked = crawl_fetch._is_blocked
    monkeypatch.setattr(crawl_fetch, "_is_blocked",
                        lambda ip: False if ip == ipaddress.ip_address("127.0.0.1") else real_blocked(ip))
    return seen


def test_the_name_is_resolved_once_per_fetch(lookups):
    server = _serve(ThreadingHTTPServer(("127.0.0.1", 0), _Echo))
    try:
        url = f"http://{HOST}:{server.server_port}/page"
        result = crawl_fetch.fetch_html(url)
    finally:
        server.shutdown()

    assert lookups == [HOST]  # the check's lookup only; the connect didn't resolve again
    assert result["html"] == f"<html>host={HOST}:{server.server_port}</html>"
    assert result["final_url"] == url


def test_a_rebound_answer_is_never_used(monkeypatch):
    """First answer public, every later answer internal: the request must
    go to the first (checked) address."""
    answers = iter(["93.184.216.34"] + ["10.0.0.7"] * 5)
    monkeypatch.setattr(crawl_fetch.socket, "getaddrinfo",
                        lambda host, *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (next(answers), 0))])
    sent = []
    monkeypatch.setattr(crawl_fetch, "_pinned_get", lambda url, ip, **kw: sent.append(ip) or
                        type("R", (), {"is_redirect": False, "headers": {}, "close": lambda self: None})())
    crawl_fetch.safe_get("http://flip.example/", headers={})
    assert sent == ["93.184.216.34"]


# ------------------------------------------------------------------ TLS
def _cert(tmp_path, name):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID

    key = ec.generate_private_key(ec.SECP256R1())
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, name)])
    now = datetime.datetime.now(datetime.timezone.utc)
    cert = (x509.CertificateBuilder().subject_name(subject).issuer_name(subject).public_key(key.public_key())
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - datetime.timedelta(minutes=5))
            .not_valid_after(now + datetime.timedelta(hours=1))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName(name)]), critical=False)
            .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / f"{name}.crt", tmp_path / f"{name}.key"
    cert_path.write_bytes(cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                           serialization.NoEncryption()))
    return str(cert_path), str(key_path)


@pytest.fixture
def tls_server(tmp_path):
    started = []
    sni = []

    def start(cert_name):
        cert, key = _cert(tmp_path, cert_name)
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(cert, key)
        ctx.sni_callback = lambda sock, name, c: sni.append(name)
        server = ThreadingHTTPServer(("127.0.0.1", 0), _Echo)
        server.socket = ctx.wrap_socket(server.socket, server_side=True)
        started.append(_serve(server))
        return server.server_port, cert

    yield start, sni
    for s in started:
        s.shutdown()


def test_tls_uses_the_hostname_for_sni_and_the_certificate(tls_server, lookups):
    start, sni = tls_server
    port, cert = start(HOST)
    resp = crawl_fetch._pinned_get(f"https://{HOST}:{port}/", "127.0.0.1", headers={}, verify=cert)
    try:
        assert resp.status_code == 200
        assert resp.text == f"<html>host={HOST}:{port}</html>"
        assert resp.url == f"https://{HOST}:{port}/"
    finally:
        resp.close()
    assert sni == [HOST]
    assert lookups == []  # connecting by address never resolved the name


def test_tls_certificate_for_another_name_is_rejected(tls_server, lookups):
    start, _ = tls_server
    port, cert = start("other.test")
    with pytest.raises(requests.exceptions.SSLError):
        crawl_fetch._pinned_get(f"https://{HOST}:{port}/", "127.0.0.1", headers={}, verify=cert)
