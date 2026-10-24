import { useId, useState } from "react";
import "./AdminReasonChoices.css";

export function AdminReasonChoices({ label, value, onChange, choices, disabled = false, maxLength = 2000 }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  choices: Array<{ label: string; value: string }>;
  disabled?: boolean;
  maxLength?: number;
}) {
  const labelId = useId();
  const [custom, setCustom] = useState(() => Boolean(value && !choices.some(choice => choice.value === value)));
  return <fieldset className="admin-reason-choices" disabled={disabled}>
    <legend id={labelId}>{label}</legend>
    <select className="admin-reason-select" aria-labelledby={labelId} required value={custom ? "__custom__" : value} onChange={(event) => {
      const isCustom = event.target.value === "__custom__";
      setCustom(isCustom);
      onChange(isCustom ? "" : event.target.value);
    }}>
      <option value="" disabled>請選擇原因</option>
      {choices.map((choice) => <option key={choice.value} value={choice.value}>{choice.label}</option>)}
      <option value="__custom__">其他原因</option>
    </select>
    {custom ? <label className="field"><span>自訂{label}</span><textarea aria-label={`自訂${label}`} required maxLength={maxLength} rows={2} value={value} onChange={(event) => onChange(event.target.value)} /></label> : value && <p className="admin-selected-reason">{value}</p>}
  </fieldset>;
}
