export function contactPhoneIsValid(value: string): boolean {
  const phone = value.replace(/\s/g, "");
  return !phone || (phone.startsWith("09") ? /^09[0-9]{8}$/ : /^0[0-9]{1,2}-?[0-9]{6,8}$/).test(phone);
}
