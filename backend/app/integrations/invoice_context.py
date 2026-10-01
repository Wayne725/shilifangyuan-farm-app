from urllib.parse import urlparse

from .fanyu_endpoints import is_fanyu_api_base_url


def fanyu_account_context(*, base_url: str, company_id: str, seller_id: str, user_id: str) -> dict:
    if not is_fanyu_api_base_url(base_url):
        raise ValueError("汎宇發票環境網址無效，無法記錄帳戶")
    return {
        "version": 1,
        "provider": "fanyu",
        "base_url": f"https://{urlparse(base_url).hostname}/einv",
        "company_id": company_id,
        "seller_id": seller_id,
        "user_id": user_id,
    }
