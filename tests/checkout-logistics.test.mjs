import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import test from "node:test";

import {
  getLogisticsBlockers,
  isValidEcpayReceiverName,
  isValidTaiwanReceiverPhone,
} from "../src/lib/checkoutValidation.ts";


const validInput = {
  deliveryAddress: "臺北市中正區測試路一號",
  incompatibleTemperature: false,
  logisticsProviderLabel: "宅配",
  missingRate: false,
  recipientName: "王小明",
  recipientPhone: "0912345678",
  shippingDataError: false,
  shippingDataLoading: false,
  unsupportedProductNames: [],
};


test("低於免運門檻不會阻擋宅配下一步", () => {
  assert.deepEqual(getLogisticsBlockers(validInput), []);
});


test("物流按鈕被擋時會指出商品與資料原因", () => {
  assert.deepEqual(
    getLogisticsBlockers({
      ...validInput,
      recipientPhone: "09123456",
      unsupportedProductNames: ["雞蛋", "青江菜"],
    }),
    [
      "雞蛋、青江菜不支援宅配",
      "手機需為 09 開頭的 10 碼數字；市話請輸入含區碼的完整號碼",
    ],
  );
});


test("收件姓名先依綠界規則驗證", () => {
  assert.equal(isValidEcpayReceiverName("王小明"), true);
  assert.equal(isValidEcpayReceiverName("A王"), false);
  assert.equal(isValidEcpayReceiverName("王😀明"), false);
  assert.equal(isValidEcpayReceiverName("Chen"), true);
});


test("手機與市話使用不同規則", () => {
  assert.equal(isValidTaiwanReceiverPhone("0912345678"), true);
  assert.equal(isValidTaiwanReceiverPhone("02-23456789"), true);
  assert.equal(isValidTaiwanReceiverPhone("09123456"), false);
});


test("原生付款與物流使用可自動關閉的回站流程", async () => {
  const paymentSource = await readFile(
    new URL("../src/lib/payment.ts", import.meta.url),
    "utf8",
  );

  assert.match(paymentSource, /openAuthSessionAsync/);
  assert.match(paymentSource, /client=native/);
  assert.match(paymentSource, /payment-return/);
  assert.match(paymentSource, /logistics-return/);
});
