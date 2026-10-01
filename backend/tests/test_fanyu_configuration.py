import pytest

from app.integrations.common import IntegrationConfigurationError
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice import InvoiceIssueRequest, InvoiceLine
from app.integrations.invoice_service import (
    invoice_adapter_from_settings,
    invoice_context_from_settings,
)
from tests.support import make_test_settings
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_infrastructure import valid_production_settings


@pytest.mark.parametrize('api_user', ['15989995ADMIN', 'WEB15989995', 'assigned-api-user'])
@pytest.mark.parametrize('base_url', [
    'https://api01.einvoice.com.tw/einv',
    'https://web2.einvoice.com.tw/einv',
    'https://webtest.einvoice.com.tw/einv',
])
def test_configured_api_user_reaches_envelope_and_binding_unchanged(monkeypatch, api_user, base_url):
    monkeypatch.setenv('FANYU_INVOICE_USER_ID', api_user)
    settings = make_test_settings(
        invoice_provider='fanyu',
        fanyu_invoice_base_url=base_url,
        fanyu_invoice_company_id='15989995',
        fanyu_invoice_seller_id='15989995',
        fanyu_invoice_auth_password='isolated-password',
        fanyu_invoice_api_key='isolated-api-key',
        fanyu_invoice_signature_verified=True,
    )
    adapter = invoice_adapter_from_settings(settings)

    async def no_network(*_args, **_kwargs):
        pytest.fail('設定與封包測試不得連線供應商')

    adapter.transport = no_network
    data = adapter.build_issue_data(InvoiceIssueRequest(
        relate_number='ISOLATEDUSERCONFIG',
        customer_email='buyer@example.test',
        buyer_type='personal',
        carrier_type='cloud',
        items=[InvoiceLine(name='隔離測試商品', quantity=1, unit_price=10)],
    ))
    envelope = adapter.build_envelope(data)

    assert settings.fanyu_invoice_user_id == api_user
    assert envelope['userID'] == api_user
    assert envelope['companyID'] == '15989995'
    assert envelope['reqData']['sellerID'] == '15989995'
    assert envelope['reqData']['totalAmount'] == '10'
    assert envelope['reqData']['carrierType'] == 'EG0478'
    assert envelope['reqData']['carrierID1'] == envelope['reqData']['carrierID2'] == 'buyer@example.test'
    assert envelope['reqData']['printMark'] == 'N'
    assert 'userID' not in envelope['reqData']
    assert adapter.binding_context == invoice_context_from_settings(settings)
    assert adapter.binding_context['user_id'] == api_user


@pytest.mark.parametrize('boundary', ['adapter', 'production_startup'])
@pytest.mark.parametrize('base_url', [
    'https://web2.einvoice.com.tw/einv/Login/Logout.action',
    'https://web.einvoice.com.tw/einv/openInvoice',
    'http://web2.einvoice.com.tw/einv',
    'https://web2.einvoice.com.tw.evil.example/einv',
    'https://web2.einvoice.com.tw/',
    'https://user:password@web2.einvoice.com.tw/einv',
    'https://web2.einvoice.com.tw:8443/einv',
    'https://web2.einvoice.com.tw:not-a-port/einv',
    'https://web2.einvoice.com.tw/einv?redirect=other',
    'https://web2.einvoice.com.tw/einv#fragment',
    'https://web2.einvoice.com.tw/einv\n',
])
def test_fanyu_rejects_noncanonical_api_base_without_sending(boundary, base_url):
    with pytest.raises((RuntimeError, IntegrationConfigurationError), match='汎宇|FANYU_INVOICE_BASE_URL'):
        if boundary == 'adapter':
            FanyuInvoiceAdapter(fanyu_settings(base_url=base_url))
        else:
            valid_production_settings(
                invoice_provider='fanyu',
                fanyu_invoice_base_url=base_url,
                fanyu_invoice_stage=False,
                fanyu_invoice_company_id='15989995',
                fanyu_invoice_seller_id='15989995',
                fanyu_invoice_user_id='isolated-user',
                fanyu_invoice_auth_password='isolated-password',
                fanyu_invoice_api_key='isolated-api-key',
                fanyu_invoice_signature_verified=True,
            ).validate_runtime_secrets()


@pytest.mark.parametrize('base_url', [
    'https://web2.einvoice.com.tw/einv/',
    'https://web2.einvoice.com.tw:443/einv',
    'https://web.einvoice.com.tw/einv/',
])
def test_fanyu_accepts_canonical_https_with_optional_trailing_slash(base_url):
    FanyuInvoiceAdapter(fanyu_settings(base_url=base_url))


def test_production_rejects_fanyu_test_host_even_when_stage_flag_is_false():
    with pytest.raises(RuntimeError, match='FANYU_INVOICE_BASE_URL'):
        valid_production_settings(
            invoice_provider='fanyu',
            fanyu_invoice_base_url='https://webtest.einvoice.com.tw/einv',
            fanyu_invoice_stage=False,
            fanyu_invoice_company_id='15989995',
            fanyu_invoice_seller_id='15989995',
            fanyu_invoice_user_id='isolated-user',
            fanyu_invoice_auth_password='isolated-password',
            fanyu_invoice_api_key='isolated-api-key',
            fanyu_invoice_signature_verified=True,
        ).validate_runtime_secrets()
