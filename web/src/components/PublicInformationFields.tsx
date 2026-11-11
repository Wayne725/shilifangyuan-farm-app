import type { PublicInformation } from "../lib/public-information";

export function PublicInformationFields({ value, onChange }: {
  value: PublicInformation; onChange: (value: PublicInformation) => void;
}) {
  const update = (key: keyof PublicInformation, text: string | boolean) => onChange({ ...value, [key]: text });
  return <section className="admin-settings-section"><details><summary>營業資訊與政策</summary>
    <div className="field-grid two-columns">
      {([['legal_name', '登記名稱', 160], ['tax_id', '營業統一編號', 8], ['address', '營業地址', 300], ['phone', '客服電話', 40], ['email', '客服 Email', 254], ['policy_version', '政策版本', 40]] as const).map(([key, label, limit]) =>
        <label className="field" key={key}><span>{label}</span><input value={value[key]} type={key === 'email' ? 'email' : 'text'} maxLength={limit} required={value.published} onChange={event => update(key, event.target.value)} /></label>)}
    </div>
    {([['privacy', '隱私權與個資告知'], ['terms', '服務條款'], ['returns', '退換貨與退款政策']] as const).map(([key, label]) =>
      <label className="field" key={key}><span>{label}</span><textarea rows={8} maxLength={30000} value={value[key]} required={value.published} onChange={event => update(key, event.target.value)} /></label>)}
    <label className="check-field"><input type="checkbox" checked={value.published} onChange={event => update('published', event.target.checked)} />公開營業資訊與政策</label>
  </details></section>;
}
