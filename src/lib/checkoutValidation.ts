export type LogisticsValidationInput = {
  deliveryAddress: string;
  incompatibleTemperature: boolean;
  logisticsProviderLabel: string;
  missingRate: boolean;
  recipientName: string;
  recipientPhone: string;
  shippingDataError: boolean;
  shippingDataLoading: boolean;
  unsupportedProductNames: string[];
};

function ecpayStringLength(value: string) {
  return Array.from(value).reduce(
    (length, character) =>
      length + (character.codePointAt(0)! > 0x7f ? 2 : 1),
    0,
  );
}

export function isValidEcpayReceiverName(value: string) {
  const name = value.trim();
  const length = ecpayStringLength(name);
  return length >= 4 && length <= 10 && /^\p{L}+$/u.test(name);
}

export function isValidTaiwanReceiverPhone(value: string) {
  const phone = value.replace(/\s/g, "");
  if (phone.startsWith("09")) return /^09\d{8}$/.test(phone);
  return /^0\d{1,2}-?\d{6,8}$/.test(phone);
}

export function getLogisticsBlockers(input: LogisticsValidationInput) {
  const blockers: string[] = [];
  if (input.unsupportedProductNames.length) {
    blockers.push(
      `${input.unsupportedProductNames.join("、")}不支援${input.logisticsProviderLabel}`,
    );
  }
  if (input.incompatibleTemperature) {
    blockers.push("購物車含不同溫層商品，物流訂單需要分開結帳");
  }
  if (input.shippingDataLoading) blockers.push("正在確認商品與運費資料");
  if (input.shippingDataError) blockers.push("配送商品或運費資料載入失敗");
  if (input.missingRate) {
    blockers.push(`目前沒有適用於${input.logisticsProviderLabel}的運費費率`);
  }
  if (!isValidEcpayReceiverName(input.recipientName)) {
    blockers.push("收件姓名需為 2–5 個中文字或 4–10 個英文字，不可含數字、空格或符號");
  }
  if (!isValidTaiwanReceiverPhone(input.recipientPhone)) {
    blockers.push(
      "手機需為 09 開頭的 10 碼數字；市話請輸入含區碼的完整號碼",
    );
  }
  const address = input.deliveryAddress.trim();
  if (!address) blockers.push("請填寫完整配送地址或希望取貨的地區");
  else if (ecpayStringLength(address) > 60) {
    blockers.push("配送地址超過綠界允許的 60 字元限制");
  }
  return blockers;
}
