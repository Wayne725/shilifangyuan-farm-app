import json
from io import BytesIO
from urllib.error import HTTPError

import pytest

from app.integrations.common import HTTPResponse, IntegrationConfigurationError, IntegrationResponseError, post_json_no_redirect
from app.integrations.fanyu_endpoints import FANYU_PRODUCTION_BASE_URL
from app.integrations.fanyu_invoice import FanyuInvoiceAdapter
from app.integrations.invoice import InvoiceIssueRequest, InvoiceLine
from tests.test_fanyu_invoice import fanyu_settings
from tests.test_infrastructure import valid_production_settings


API_BASE = 'https://api01.einvoice.com.tw/einv'


def test_production_reference_is_corrected_without_switching_test_defaults():
    assert FANYU_PRODUCTION_BASE_URL == API_BASE
    assert fanyu_settings().base_url == 'https://webtest.einvoice.com.tw/einv'


@pytest.mark.parametrize('base_url', [API_BASE, API_BASE + '/', 'https://api01.einvoice.com.tw:443/einv'])
def test_api01_adapter_and_production_startup_accept_verified_base(base_url):
    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=base_url))
    assert adapter.binding_context['base_url'] == API_BASE
    assert adapter.transport is post_json_no_redirect
    valid_production_settings(
        invoice_provider='fanyu', fanyu_invoice_stage=False,
        fanyu_invoice_base_url=base_url, fanyu_invoice_company_id='15989995',
        fanyu_invoice_seller_id='15989995', fanyu_invoice_user_id='isolated-user',
        fanyu_invoice_auth_password='isolated-password', fanyu_invoice_api_key='isolated-key',
        fanyu_invoice_signature_verified=True,
    ).validate_runtime_secrets()


@pytest.mark.parametrize('base_url', [
    'https://api01.einvoice.com.tw',
    'https://api01.einvoice.com.tw/',
    'https://api01.einvoice.com.tw/einv/queryInvoice',
    'https://api01.einvoice.com.tw/einv/Login/LoginPage.action',
    'http://api01.einvoice.com.tw/einv',
    'https://api01.einvoice.com.tw.evil.example/einv',
    'https://user:password@api01.einvoice.com.tw/einv',
    'https://api01.einvoice.com.tw:8443/einv',
    'https://api01.einvoice.com.tw/einv?redirect=other',
    'https://api01.einvoice.com.tw/einv#fragment',
    'https://api01.einvoice.com.tw/einv\n',
])
def test_api01_rejects_wrong_path_and_unsafe_url_before_sending(base_url):
    with pytest.raises(IntegrationConfigurationError):
        FanyuInvoiceAdapter(fanyu_settings(base_url=base_url))


@pytest.mark.asyncio
@pytest.mark.parametrize('buyer_type', ['personal', 'company'])
async def test_api01_issue_query_and_void_only_change_issue_sequence_padding(buyer_type):
    requests = []

    async def transport(url, payload, *_args):
        requests.append((url, payload))
        return HTTPResponse(200, json.dumps({
            'statusCode': '0',
            'respData': {'invNo': 'AB12345678', 'invDate': '20260910', 'status': '0'},
        }), {})

    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=API_BASE + '/'), transport)
    request = InvoiceIssueRequest(
        relate_number='ISOLATEDAPI01', customer_email='buyer@example.test',
        buyer_type=buyer_type, buyer_tax_id='12345675' if buyer_type == 'company' else '',
        buyer_name='隔離測試公司' if buyer_type == 'company' else '',
        carrier_type='cloud', items=[InvoiceLine(name='隔離測試商品', quantity=1, unit_price=105)],
    )
    await adapter.issue_invoice(request)
    await adapter.query_invoice(request.relate_number, buyer_type=buyer_type)
    await adapter.cancel_invoice(
        invoice_number='AB12345678', invoice_date='20260910',
        reason='隔離測試', order_id='ISOLATEDAPI01R', buyer_type=buyer_type,
        buyer_tax_id=request.buyer_tax_id, notify_email=request.customer_email,
    )
    assert [url for url, _ in requests] == [API_BASE + path for path in ['/openInvoice', '/queryInvoice', '/cancelInvoice']]
    data = requests[0][1]['reqData']
    legacy_adapter = FanyuInvoiceAdapter(
        fanyu_settings(base_url='https://web.einvoice.com.tw/einv'), transport,
    )
    legacy_data = legacy_adapter.build_issue_data(request)
    assert legacy_data['Details'][0]['sequenceNumber'] == '0001'
    legacy_data['Details'][0]['sequenceNumber'] = '001'
    assert data == legacy_data
    assert data['Details'][0]['sequenceNumber'] == '001'
    assert data['carrierType'] == 'EG0478'
    assert data['carrierID1'] == data['carrierID2'] == data['notifyEmail'] == 'buyer@example.test'
    assert data['printMark'] == 'N'
    assert 'invNo' not in data

    await legacy_adapter.query_invoice(request.relate_number, buyer_type=buyer_type)
    await legacy_adapter.cancel_invoice(
        invoice_number='AB12345678', invoice_date='20260910',
        reason='隔離測試', order_id='ISOLATEDAPI01R', buyer_type=buyer_type,
        buyer_tax_id=request.buyer_tax_id, notify_email=request.customer_email,
    )
    assert requests[1][1]['reqData'] == requests[3][1]['reqData']
    assert requests[2][1]['reqData'] == requests[4][1]['reqData']


@pytest.mark.parametrize('base_url', [
    API_BASE, API_BASE + '/', 'https://api01.einvoice.com.tw:443/einv/',
])
def test_api01_ten_dollar_trial_payload_uses_verified_sequence_and_carrier(base_url):
    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=base_url))
    prepared = adapter.prepare_invoice(InvoiceIssueRequest(
        relate_number='ISOLATEDAPI01TRIAL', customer_email='buyer@example.test',
        carrier_type='cloud',
        items=[InvoiceLine(name='隔離測試便當', quantity=1, unit_price=10)],
    ))

    data = prepared.provider_request
    assert data['Details'][0]['sequenceNumber'] == '001'
    assert data['totalAmount'] == '10'
    assert data['carrierType'] == 'EG0478'
    assert data['carrierID1'] == data['carrierID2'] == data['notifyEmail'] == 'buyer@example.test'
    assert data['printMark'] == 'N'
    assert prepared.items[0].sequence_number == 1
    assert adapter.binding_context == {
        'version': 1, 'provider': 'fanyu', 'base_url': API_BASE,
        'company_id': adapter.settings.company_id,
        'seller_id': adapter.settings.seller_id,
        'user_id': adapter.settings.user_id,
    }


@pytest.mark.parametrize(('size', 'last'), [
    (2, '002'), (999, '999'), (1000, '1000'), (9999, '9999'),
])
def test_api01_local_padding_does_not_infer_a_three_digit_provider_limit(size, last):
    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=API_BASE))
    prepared = adapter.prepare_invoice(InvoiceIssueRequest(
        relate_number='ISOLATEDAPI01PADDING', customer_email='buyer@example.test',
        items=[InvoiceLine(name='隔離商品', quantity=1, unit_price=1)] * size,
    ))
    sequences = [item['sequenceNumber'] for item in prepared.provider_request['Details']]

    # 此處只驗證本機不截斷；api01 超過 3 位序號尚未向供應商實測。
    assert sequences[0] == '001'
    assert sequences[-1] == last
    assert len(set(sequences)) == size
    assert prepared.items[-1].sequence_number == size


@pytest.mark.asyncio
async def test_api01_preserves_existing_local_detail_limit_before_sending():
    async def transport(*_args):
        pytest.fail('超過既有本機明細上限不得送出開票')

    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=API_BASE), transport)
    request = InvoiceIssueRequest(
        relate_number='ISOLATEDAPI01LIMIT', customer_email='buyer@example.test',
        items=[InvoiceLine(name='隔離商品', quantity=1, unit_price=1)] * 10000,
    )
    with pytest.raises(ValueError, match='9999'):
        await adapter.issue_invoice(request)


@pytest.mark.asyncio
async def test_fanyu_default_transport_stops_on_redirect_without_forwarding_credentials(monkeypatch):
    import app.integrations.common as common

    calls = []

    class FakeOpener:
        def open(self, request, timeout):
            calls.append(request.full_url)
            raise HTTPError(request.full_url, 302, 'Found', {'Location': 'https://other.example/einv/queryInvoice'}, BytesIO(b''))

    def build_opener(handler):
        assert handler.redirect_request(None, None, 302, 'Found', {}, 'https://other.example') is None
        return FakeOpener()

    monkeypatch.setattr(common, 'build_opener', build_opener)
    monkeypatch.setattr(common, 'urlopen', lambda *_args, **_kwargs: pytest.fail('不得使用自動重新導向的傳輸'))
    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=API_BASE))
    with pytest.raises(IntegrationResponseError, match='HTTP 302'):
        await adapter.query_invoice('ISOLATEDREDIRECT', buyer_type='personal')
    assert calls == [API_BASE + '/queryInvoice']
