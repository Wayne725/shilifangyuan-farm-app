import ipaddress
import json
import ssl
import threading
import urllib.request
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import URLError
from urllib.request import urlopen

import boto3
import certifi
import httpx
import pytest
from botocore.config import Config
from botocore.exceptions import SSLError
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.integrations.common import IntegrationError, post_json, post_json_no_redirect
from app.integrations.line_login import LineLoginClient
from app.integrations.r2_storage import R2DocumentStorage, R2StorageSettings
from tests.support import make_test_settings


@pytest.fixture
def private_ca_server(tmp_path, monkeypatch):
    now = datetime.now(timezone.utc)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Isolated test CA")])
    ca_cert = (
        x509.CertificateBuilder()
        .subject_name(ca_name)
        .issuer_name(ca_name)
        .public_key(ca_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    server_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    server_cert = (
        x509.CertificateBuilder()
        .subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")]))
        .issuer_name(ca_name)
        .public_key(server_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=1))
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(
            x509.SubjectAlternativeName([
                x509.DNSName("localhost"),
                x509.IPAddress(ipaddress.ip_address("127.0.0.1")),
            ]),
            critical=False,
        )
        .sign(ca_key, hashes.SHA256())
    )
    ca_path = tmp_path / "ca.pem"
    cert_path = tmp_path / "server.pem"
    key_path = tmp_path / "server.key"
    ca_path.write_bytes(ca_cert.public_bytes(serialization.Encoding.PEM))
    cert_path.write_bytes(server_cert.public_bytes(serialization.Encoding.PEM))
    key_path.write_bytes(server_key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    ))

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.end_headers()
            payload = {"ret": 0, "data": "isolated"} if b"apiname=" in body else {"ok": True}
            self.wfile.write(json.dumps(payload).encode())

        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(
                b'<ListAllMyBucketsResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
                b'<Owner><ID>isolated</ID></Owner><Buckets/></ListAllMyBucketsResult>'
            )

        def log_message(self, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(cert_path, key_path)
    server.socket = context.wrap_socket(server.socket, server_side=True)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    monkeypatch.setenv("SSL_CERT_FILE", str(ca_path))
    monkeypatch.setenv("AWS_CA_BUNDLE", str(ca_path))
    monkeypatch.setenv("NO_PROXY", "127.0.0.1,localhost")
    monkeypatch.setattr(urllib.request, "_opener", None)
    try:
        yield f"https://127.0.0.1:{server.server_port}/", str(ca_path)
    finally:
        server.shutdown()
        server.server_close()
        worker.join()


@pytest.mark.parametrize("transport", [post_json, post_json_no_redirect])
async def test_provider_transport_rejects_ca_added_only_to_system_trust(private_ca_server, transport):
    url, _ca_path = private_ca_server
    with urlopen(url, context=ssl.create_default_context(), timeout=3) as response:
        assert response.status == 200

    with pytest.raises(IntegrationError) as caught:
        await transport(url, {"test": True})
    assert isinstance(caught.value.__cause__, URLError)
    assert isinstance(caught.value.__cause__.reason, ssl.SSLCertVerificationError)


@pytest.mark.parametrize("transport", [post_json, post_json_no_redirect])
async def test_provider_transport_accepts_the_application_ca_bundle(private_ca_server, monkeypatch, transport):
    url, ca_path = private_ca_server
    monkeypatch.setattr(certifi, "where", lambda: ca_path)
    response = await transport(url, {"test": True})
    assert response.status_code == 200
    assert response.json() == {"ok": True}


@pytest.mark.parametrize("trusted_by_application", [False, True])
async def test_line_transport_uses_application_trust(private_ca_server, monkeypatch, trusted_by_application):
    url, ca_path = private_ca_server
    if trusted_by_application:
        monkeypatch.setattr(certifi, "where", lambda: ca_path)
    original_client = httpx.AsyncClient

    class LocalClient(original_client):
        async def post(self, _url, **kwargs):
            return await super().post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", LocalClient)
    client = LineLoginClient(make_test_settings())
    if trusted_by_application:
        assert await client._post("token", {"code": "isolated"}) == {"ok": True}
    else:
        with pytest.raises(IntegrationError) as caught:
            await client._post("token", {"code": "isolated"})
        assert isinstance(caught.value.__cause__, httpx.ConnectError)
        assert "CERTIFICATE_VERIFY_FAILED" in str(caught.value.__cause__)


@pytest.mark.parametrize("trusted_by_application", [False, True])
def test_r2_transport_uses_application_trust(private_ca_server, monkeypatch, trusted_by_application):
    url, ca_path = private_ca_server
    if trusted_by_application:
        monkeypatch.setattr(certifi, "where", lambda: ca_path)
    original_client = boto3.client

    def client_without_retries(*args, **kwargs):
        kwargs["config"] = kwargs["config"].merge(Config(retries={"max_attempts": 0}))
        return original_client(*args, **kwargs)

    monkeypatch.setattr(boto3, "client", client_without_retries)
    client = R2DocumentStorage._build_client(R2StorageSettings(
        endpoint_url=url,
        access_key_id="isolated-access-key",
        secret_access_key="isolated-secret-key",
        bucket="isolated-bucket",
    ))
    try:
        if trusted_by_application:
            assert client.list_buckets()["Buckets"] == []
        else:
            with pytest.raises(SSLError, match="CERTIFICATE_VERIFY_FAILED"):
                client.list_buckets()
    finally:
        client.close()


@pytest.mark.parametrize("trusted_by_application", [False, True])
async def test_feie_transport_uses_application_trust(private_ca_server, monkeypatch, trusted_by_application):
    from app.integrations.feie import FeieClient, FeieError

    url, ca_path = private_ca_server
    if trusted_by_application:
        monkeypatch.setattr(certifi, "where", lambda: ca_path)
    original_client = httpx.AsyncClient

    class LocalClient(original_client):
        async def post(self, _url, **kwargs):
            return await super().post(url, **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", LocalClient)
    settings = make_test_settings(
        printer_enabled=True, printer_dry_run=False, feie_user="isolated-user",
        feie_ukey="isolated-key", feie_printer_sn="isolated-device",
        feie_ukey_rotated=True, feie_ap_endpoint_verified=True,
    )
    client = FeieClient(settings)
    if trusted_by_application:
        assert await client._call("Open_queryPrinterStatus") == {"ret": 0, "data": "isolated"}
    else:
        with pytest.raises(FeieError, match="connection_not_established"):
            await client._call("Open_queryPrinterStatus")
