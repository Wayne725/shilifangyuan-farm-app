import ssl

import certifi


def ca_bundle_path() -> str:
    return certifi.where()


def create_tls_context() -> ssl.SSLContext:
    context = ssl.create_default_context(cafile=ca_bundle_path())
    context.set_alpn_protocols(["http/1.1"])
    return context
