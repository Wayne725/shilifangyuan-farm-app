import { useState } from "react";

export function AdminReasonChoices({ label, value, onChange, choices, disabled = false, maxLength = 2000 }: {
  label: string;
  value: string;
  onChange: (value: string) => void;
  choices: Array<{ label: string; value: string }>;
  disabled?: boolean;
  maxLength?: number;
}) {
  const [custom, setCustom] = useState(false);
  return <fieldset className="admin-reason-choices" disabled={disabled}>
    <legend>{label}</legend>
    <div className="admin-quick-buttons">
      {choices.map((choice) => <button key={choice.value} className="button button-quiet" type="button" aria-pressed={!custom && value === choice.value} onClick={() => { setCustom(false); onChange(choice.value); }}>{choice.label}</button>)}
      <button className="button button-quiet" type="button" aria-pressed={custom} onClick={() => { setCustom(true); onChange(""); }}>其他原因</button>
    </div>
    {custom ? <label className="field"><span>自訂{label}</span><textarea aria-label={`自訂${label}`} required maxLength={maxLength} rows={2} value={value} onChange={(event) => onChange(event.target.value)} /></label> : value && <p className="admin-selected-reason">{value}</p>}
  </fieldset>;
}
