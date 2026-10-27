import type { InvoicePreference } from "./commerce";

export async function saveInvoiceCarrierIfRequested(
  preference: InvoicePreference,
  savedBarcode: string | null | undefined,
  saveMobileBarcode: (barcode: string | null) => Promise<void>,
): Promise<void> {
  if (!preference.saveCarrier || preference.carrierType !== "mobile_barcode") return;
  const barcode = (preference.carrierValue || "").trim().toUpperCase();
  if (!/^\/[0-9A-Z+\-.]{7}$/.test(barcode)) throw new Error("請輸入正確的手機條碼");
  if (barcode !== savedBarcode) await saveMobileBarcode(barcode);
}
