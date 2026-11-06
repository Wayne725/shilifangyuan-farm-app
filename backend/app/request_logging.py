import logging
import re


def redact_request_target(target: str) -> str:
    path = target.split("?", 1)[0]
    if path != "/logistics/selection-result":
        path = re.sub(r"^/logistics/[^/]+", "/logistics/{token}", path)
    return re.sub(r"^/payments/[^/]+/checkout(?=/|$)", "/payments/{attempt_id}/checkout", path)


class CapabilityAccessLogFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.args, tuple) and len(record.args) == 5 and isinstance(record.args[2], str):
            client, method, target, version, status = record.args
            record.args = (client, method, redact_request_target(target), version, status)
        return True


def install_access_log_redaction() -> None:
    logger = logging.getLogger("uvicorn.access")
    if not any(isinstance(item, CapabilityAccessLogFilter) for item in logger.filters):
        logger.addFilter(CapabilityAccessLogFilter())
