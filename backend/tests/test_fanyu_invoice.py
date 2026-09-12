from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from app.integrations.common import (
    HTTPResponse,
    IntegrationConfigurationError,
    IntegrationResponseError,
)
from app.integrations.fanyu_invoice import (
    FanyuInvoiceAdapter,
    FanyuInvoiceSettings,
    fanyu_signature,
)
from app.integrations.invoice import InvoiceIssueRequest, InvoiceLine


def fanyu_settings(**overrides) -> FanyuInvoiceSettings:
    values = {
        "company_id": "15989995",
        "user_id": "15989995ADMIN",
        "auth_password": "A15989995",
        "api_key": "test-api-key",
        "seller_id": "15989995",
        "signature_verified": True,
    }
    values.update(overrides)
    return FanyuInvoiceSettings(**values)


def test_fanyu_requires_an_explicitly_verified_signature_contract() -> None:
    with pytest.raises(IntegrationConfigurationError, match="簽章測試向量"):
        FanyuInvoiceAdapter(
            fanyu_settings(signature_verified=False)
        )


def test_fanyu_signature_is_stable_hmac_sha256_base64() -> None:
    assert fanyu_signature("20190820042311", "test-api-key") == (
        "lrntmggb3XC7QGdY6iC3rKedrV77AsRSQcEeN2mzW1E="
    )


def test_fanyu_builds_cloud_personal_invoice_without_paper() -> None:
    adapter = FanyuInvoiceAdapter(fanyu_settings())
    request = InvoiceIssueRequest(
        relate_number="INVSLF0001",
        customer_email="buyer@example.test",
        customer_id="USER0001",
        buyer_type="personal",
        items=[
            InvoiceLine(
                name="白米",
                quantity=2,
                unit_price=100,
                unit="包",
                tax_type="1",
            )
        ],
        carrier_type="mobile_barcode",
        carrier_number="/AB12+-.",
    )

    data = adapter.build_issue_data(request)

    assert data["process_type"] == "C"
    assert data["buyerID"] == "0000000000"
    assert data["buyerName"] == "buye"
    assert data["printMark"] == "N"
    assert data["carrierType"] == "3J0002"
    assert data["carrierID1"] == "/AB12+-."
    assert data["carrierID2"] == "/AB12+-."
    assert data["notifyEmail"] == "buyer@example.test"
    assert data["salesAmount"] == "200"
    assert data["taxAmount"] == "0"
    assert data["totalAmount"] == "200"
    assert data["Details"][0] == {
        "description": "白米",
        "quantity": "2",
        "unit": "包",
        "unitprice": "100",
        "amount": "200",
        "sequenceNumber": "0001",
        "remark": "",
        "taxType": "1",
    }


def test_fanyu_builds_member_cloud_carrier_from_customer_email() -> None:
    adapter = FanyuInvoiceAdapter(fanyu_settings())
    request = InvoiceIssueRequest(
        relate_number="INVSLF0006",
        customer_email="buyer@example.test",
        customer_id="USER0006",
        buyer_type="personal",
        items=[InvoiceLine(name="白米", quantity=1, unit_price=100)],
        carrier_type="cloud",
    )

    data = adapter.build_issue_data(request)

    assert data["carrierType"] == "EG0478"
    assert data["carrierID1"] == "buyer@example.test"
    assert data["carrierID2"] == "buyer@example.test"
    assert data["printMark"] == "N"


def test_fanyu_builds_company_member_cloud_carrier_from_customer_email() -> None:
    adapter = FanyuInvoiceAdapter(fanyu_settings())
    request = InvoiceIssueRequest(
        relate_number="INVSLF0008",
        customer_email="accounting@example.test",
        buyer_type="company",
        buyer_tax_id="12345675",
        buyer_name="測試股份有限公司",
        items=[InvoiceLine(name="白米", quantity=1, unit_price=105)],
        carrier_type="cloud",
    )

    data = adapter.build_issue_data(request)

    assert data["process_type"] == "B"
    assert data["carrierType"] == "EG0478"
    assert data["carrierID1"] == "accounting@example.test"
    assert data["carrierID2"] == "accounting@example.test"
    assert data["printMark"] == "N"
    assert data["notifyEmail"] == "accounting@example.test"


@pytest.mark.parametrize(
    ("customer_email", "customer_phone", "message"),
    [
        ("", "0912345678", "Email"),
        (f"{'a' * 53}@example.test", "", "64"),
    ],
)
def test_fanyu_member_cloud_carrier_rejects_unusable_email(
    customer_email: str,
    customer_phone: str,
    message: str,
) -> None:
    adapter = FanyuInvoiceAdapter(fanyu_settings())
    request = InvoiceIssueRequest(
        relate_number="INVSLF0007",
        customer_email=customer_email,
        customer_phone=customer_phone,
        buyer_type="personal",
        items=[InvoiceLine(name="白米", quantity=1, unit_price=100)],
        carrier_type="cloud",
    )

    with pytest.raises(ValueError, match=message):
        adapter.build_issue_data(request)


def test_fanyu_builds_company_invoice_with_untaxed_line_amounts() -> None:
    adapter = FanyuInvoiceAdapter(fanyu_settings())
    request = InvoiceIssueRequest(
        relate_number="INVSLF0002",
        customer_email="accounting@example.test",
        buyer_type="company",
        buyer_tax_id="12345675",
        buyer_name="測試股份有限公司",
        carrier_type="mobile_barcode",
        carrier_number="/AB12+-.",
        items=[
            InvoiceLine(
                name="應稅商品",
                quantity=2,
                unit_price=105,
                tax_type="1",
            ),
            InvoiceLine(
                name="免稅商品",
                quantity=1,
                unit_price=80,
                tax_type="3",
            ),
        ],
    )

    data = adapter.build_issue_data(request)

    assert data["process_type"] == "B"
    assert data["buyerID"] == "12345675"
    assert data["buyerName"] == "測試股份有限公司"
    assert data["printMark"] == "N"
    assert data["carrierType"] == "3J0002"
    assert data["carrierID1"] == "/AB12+-."
    assert data["carrierID2"] == "/AB12+-."
    assert data["notifyEmail"] == "accounting@example.test"
    assert data["taxType"] == "9"
    assert data["salesAmount"] == "200"
    assert data["freetaxSalesamount"] == "80"
    assert data["taxAmount"] == "10"
    assert data["totalAmount"] == "290"
    assert data["Details"][0]["unitprice"] == "100"
    assert data["Details"][0]["amount"] == "200"
    assert data["Details"][1]["unitprice"] == "80"

    prepared = adapter.prepare_invoice(request)
    assert prepared.provider_request == data
    assert str(prepared.items[0].unit_price) == "100"
    assert prepared.sales_amount == 200
    assert prepared.tax_amount == 10
    assert prepared.total_amount == 290


@pytest.mark.asyncio
@pytest.mark.parametrize('buyer_type', ['personal', 'company'])
async def test_fanyu_web2_cloud_invoice_and_query_preserve_provider_contract(buyer_type):
    requests = []

    async def transport(url, payload, headers, timeout):
        requests.append((url, payload))
        return HTTPResponse(
            status_code=200,
            body='{"statusCode":"0","respData":{"invNo":"AB12345678","invDate":"20260908","status":"0"}}',
            headers={},
        )

    adapter = FanyuInvoiceAdapter(
        fanyu_settings(base_url='https://web2.einvoice.com.tw/einv'),
        transport=transport,
    )
    request = InvoiceIssueRequest(
        relate_number='ISOLATED2026090801',
        customer_email='buyer@example.test',
        buyer_type=buyer_type,
        buyer_tax_id='12345675' if buyer_type == 'company' else '',
        buyer_name='測試公司' if buyer_type == 'company' else '',
        carrier_type='cloud',
        items=[InvoiceLine(name='隔離驗證商品', quantity=1, unit_price=105)],
    )
    issued = await adapter.issue_invoice(request)
    queried = await adapter.query_invoice(request.relate_number, buyer_type=buyer_type)

    assert [url for url, _ in requests] == [
        'https://web2.einvoice.com.tw/einv/openInvoice',
        'https://web2.einvoice.com.tw/einv/queryInvoice',
    ]
    data = requests[0][1]['reqData']
    assert data['carrierType'] == 'EG0478'
    assert data['carrierID1'] == data['carrierID2'] == data['notifyEmail'] == 'buyer@example.test'
    assert data['printMark'] == 'N'
    assert data['process_type'] == ('B' if buyer_type == 'company' else 'C')
    assert 'invNo' not in data
    assert issued.invoice_number == queried['InvoiceNo'] == 'AB12345678'


@pytest.mark.asyncio
@pytest.mark.parametrize('buyer_type', ['personal', 'company'])
async def test_web2_accepts_invoice_detail_sequence_with_three_character_limit(buyer_type):
    async def transport(url, payload, headers, timeout):
        detail = payload['reqData']['Details'][0]
        if len(detail['sequenceNumber']) > 3:
            return HTTPResponse(200, '{"statusCode":"2","statusDesc":"序號0001: 商品明細排列序號長度不可超過3;"}', {})
        assert detail['sequenceNumber'] == '001'
        return HTTPResponse(200, '{"statusCode":"0","respData":{"invNo":"AB12345678","invDate":"20260908"}}', {})

    adapter = FanyuInvoiceAdapter(
        fanyu_settings(base_url='https://web2.einvoice.com.tw:443/einv/'),
        transport=transport,
    )
    request = InvoiceIssueRequest(
        relate_number='ISOLATEDWEB2SEQUENCE', customer_email='buyer@example.test',
        buyer_type=buyer_type, buyer_tax_id='12345675', buyer_name='隔離測試公司',
        items=[InvoiceLine(name='隔離驗證商品', quantity=1, unit_price=10)],
    )
    result = await adapter.issue_invoice(request)
    assert result.invoice_number == 'AB12345678'


@pytest.mark.asyncio
async def test_web2_rejects_too_many_details_before_sending_invoice():
    async def transport(*args):
        pytest.fail('超出序號範圍不應送出開票')

    adapter = FanyuInvoiceAdapter(
        fanyu_settings(base_url='https://web2.einvoice.com.tw/einv'), transport=transport,
    )
    request = InvoiceIssueRequest(
        relate_number='ISOLATEDWEB2LIMIT', customer_email='buyer@example.test',
        items=[InvoiceLine(name='隔離商品', quantity=1, unit_price=1)] * 1000,
    )
    with pytest.raises(ValueError, match='999'):
        await adapter.issue_invoice(request)


@pytest.mark.parametrize(('host', 'size', 'first', 'last'), [
    ('web2.einvoice.com.tw', 999, '001', '999'),
    ('web.einvoice.com.tw', 1000, '0001', '1000'),
    ('webtest.einvoice.com.tw', 1000, '0001', '1000'),
])
def test_fanyu_sequence_format_preserves_legacy_hosts_and_unique_numbers(host, size, first, last):
    adapter = FanyuInvoiceAdapter(fanyu_settings(base_url=f'https://{host}/einv'))
    prepared = adapter.prepare_invoice(InvoiceIssueRequest(
        relate_number='ISOLATEDSEQUENCE', customer_email='buyer@example.test',
        items=[InvoiceLine(name='隔離商品', quantity=1, unit_price=1)] * size,
    ))
    sequences = [item['sequenceNumber'] for item in prepared.provider_request['Details']]
    assert sequences[0] == first
    assert sequences[-1] == last
    assert len(set(sequences)) == size
    assert prepared.items[-1].sequence_number == size


@pytest.mark.asyncio
async def test_fanyu_open_and_query_use_official_paths_and_normalize_result() -> None:
    requests = []

    async def transport(url, payload, headers, timeout):
        requests.append((url, payload, headers, timeout))
        if url.endswith("/openInvoice"):
            response = {
                "statusCode": "0",
                "statusDesc": "",
                "respData": {
                    "invNo": "AB12345678",
                    "invDate": "20260826",
                    "invTime": "12:34:56",
                    "randomNumber": "1234",
                },
            }
        else:
            response = {
                "statusCode": "0",
                "statusDesc": "",
                "respData": {
                    "invNo": "AB12345678",
                    "invDate": "20260826",
                    "invTime": "12:34:56",
                    "randomNumber": "1234",
                    "status": "0",
                },
            }
        return HTTPResponse(status_code=200, body=__import__("json").dumps(response), headers={})

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    request = InvoiceIssueRequest(
        relate_number="INVSLF0003",
        customer_email="buyer@example.test",
        buyer_type="personal",
        items=[InvoiceLine(name="白米", quantity=1, unit_price=100)],
    )

    issued = await adapter.issue_invoice(request)
    queried = await adapter.query_invoice("INVSLF0003", buyer_type="personal")

    assert issued.invoice_number == "AB12345678"
    assert issued.invoice_date == "2026-08-26 12:34:56"
    assert queried["RtnCode"] == 1
    assert queried["InvoiceNo"] == "AB12345678"
    assert queried["ProviderStatus"] == "0"
    assert requests[0][0].endswith("/openInvoice")
    assert requests[1][0].endswith("/queryInvoice")
    assert requests[0][2] == {"Content-Type": "application/json"}
    assert requests[0][1]["auth"] == "QTE1OTg5OTk1"
    assert requests[0][1]["reqData"]["printMark"] == "N"


@pytest.mark.asyncio
async def test_fanyu_query_treats_status_three_as_not_found() -> None:
    async def transport(url, payload, headers, timeout):
        return HTTPResponse(
            status_code=200,
            body='{"statusCode":"3","statusDesc":"查無資料","respData":{}}',
            headers={},
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    result = await adapter.query_invoice("INVSLF0004", buyer_type="company")

    assert result == {
        "RtnCode": 0,
        "RtnMsg": "查無電子發票",
        "InvoiceNo": "",
        "InvoiceDate": "",
        "RandomNumber": "",
        "ProviderStatus": "",
        "ProviderResponse": {},
    }


@pytest.mark.asyncio
async def test_fanyu_query_treats_live_status_two_not_issued_as_not_found() -> None:
    async def transport(url, payload, headers, timeout):
        return HTTPResponse(
            status_code=200,
            body=(
                '{"statusCode":"2","statusDesc":'
                '"尚未用此銷貨單號碼開立發票(INVSLF0004)","respData":{}}'
            ),
            headers={},
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    result = await adapter.query_invoice("INVSLF0004", buyer_type="personal")

    assert result["RtnCode"] == 0
    assert result["InvoiceNo"] == ""


@pytest.mark.asyncio
async def test_fanyu_rejects_provider_error_without_leaking_credentials() -> None:
    secret = "private-fanyu-password"
    api_key = "private-fanyu-api-key"

    async def transport(url, payload, headers, timeout):
        return HTTPResponse(
            status_code=200,
            body=(
                '{"statusCode":"2","statusDesc":"密碼 '
                f'{secret} APIKey {api_key} 驗證失敗","respData":{{}}}}'
            ),
            headers={},
        )

    adapter = FanyuInvoiceAdapter(
        fanyu_settings(auth_password=secret, api_key=api_key),
        transport=transport,
    )

    with pytest.raises(IntegrationResponseError) as error:
        await adapter.query_invoice("INVSLF0004", buyer_type="company")

    message = str(error.value)
    assert secret not in message
    assert api_key not in message
    assert "2" in message


@pytest.mark.asyncio
async def test_fanyu_issue_rejects_success_without_invoice_number() -> None:
    async def transport(url, payload, headers, timeout):
        return HTTPResponse(
            status_code=200,
            body='{"statusCode":"0","statusDesc":"","respData":{}}',
            headers={},
        )

    adapter = FanyuInvoiceAdapter(fanyu_settings(), transport=transport)
    request = InvoiceIssueRequest(
        relate_number="INVSLF0005",
        customer_email="buyer@example.test",
        buyer_type="personal",
        items=[InvoiceLine(name="白米", quantity=1, unit_price=100)],
    )

    with pytest.raises(IntegrationResponseError, match="缺少有效發票號碼"):
        await adapter.issue_invoice(request)
