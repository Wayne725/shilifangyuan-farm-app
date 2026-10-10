import { useEffect, useRef, useState } from "react";
import { apiFetch } from "../lib/api";
import "./RosterCsvImport.css";

interface CsvEntry {
  member_number: string;
  legal_name: string;
  email: string;
  phone: string;
  share_capital_amount: number;
  share_count: number;
}

interface CsvPreview {
  rows: Array<{ row_number: number; member_number: string; legal_name: string; entry: CsvEntry | null; errors: string[] }>;
  valid_count: number;
  error_count: number;
}

export function RosterCsvImport({ onDone }: { onDone: () => void }) {
  const [preview, setPreview] = useState<CsvPreview | null>(null);
  const [busy, setBusy] = useState<"checking" | "importing" | null>(null);
  const [error, setError] = useState("");
  const [success, setSuccess] = useState("");
  const fileInput = useRef<HTMLInputElement>(null);
  const controller = useRef<AbortController | null>(null);
  useEffect(() => () => controller.current?.abort(), []);

  async function checkFile(file?: File) {
    controller.current?.abort();
    setPreview(null);
    setError("");
    setSuccess("");
    if (!file) return;
    if (!/\.csv$/i.test(file.name) || file.size > 2 * 1024 * 1024) {
      setError("請選擇 2 MB 以內的 CSV 檔案");
      return;
    }
    const request = new AbortController();
    controller.current = request;
    setBusy("checking");
    try {
      setPreview(await apiFetch<CsvPreview>("/v1/admin/member-roster/import/preview", {
        method: "POST", body: file, headers: { "Content-Type": "text/csv" }, signal: request.signal,
      }));
    } catch (cause) {
      if (!request.signal.aborted) setError(cause instanceof Error ? cause.message : "CSV 檢查失敗");
    } finally {
      if (!request.signal.aborted) setBusy(null);
    }
  }

  async function importRows() {
    if (!preview || preview.error_count || !preview.valid_count || busy) return;
    setBusy("importing");
    setError("");
    try {
      const result = await apiFetch<{ imported_count: number }>("/v1/admin/member-roster/import", {
        method: "POST", body: JSON.stringify({ rows: preview.rows.map((row) => row.entry) }),
      });
      setPreview(null);
      if (fileInput.current) fileInput.current.value = "";
      setSuccess(`已匯入 ${result.imported_count} 筆社員`);
      onDone();
    } catch (cause) {
      setPreview(null);
      if (fileInput.current) fileInput.current.value = "";
      setError(cause instanceof Error ? cause.message : "匯入失敗，請重新選擇 CSV");
    } finally {
      setBusy(null);
    }
  }

  return <section className="social-form roster-csv-import" aria-labelledby="roster-csv-title">
    <div className="form-heading">
      <div><p className="eyebrow">MEMBER ROSTER</p><h2 id="roster-csv-title">匯入社員名冊</h2></div>
      <a className="button button-quiet" href="/templates/member-roster.csv" download>下載 CSV 範本</a>
    </div>
    <label className="field roster-csv-file"><span>選擇 CSV 檔案</span><input ref={fileInput} type="file" accept=".csv,text/csv" disabled={Boolean(busy)} onChange={(event) => void checkFile(event.target.files?.[0])} /></label>
    <p className="roster-csv-format">必填：社員編號、姓名、Email、手機　／　選填：股金、股數</p>
    <p className="roster-csv-format">UTF-8／Big5 · 最多 500 筆 · 2 MB · 手機保留開頭 0</p>
    {busy === "checking" && <p role="status">檢查中…</p>}
    {error && <p className="form-error" role="alert">{error}</p>}
    {success && <p className="roster-csv-success" role="status">{success}</p>}
    {preview && <>
      <p role="status">共 {preview.rows.length} 筆 · 可匯入 {preview.valid_count} 筆 · 錯誤 {preview.error_count} 筆</p>
      <div className="roster-csv-preview" role="region" aria-label="CSV 名冊預覽" tabIndex={0}>
        <table><thead><tr><th scope="col">CSV 列</th><th scope="col">社員編號</th><th scope="col">姓名</th><th scope="col">Email</th><th scope="col">手機</th><th scope="col">股金／股數</th><th scope="col">檢查結果</th></tr></thead>
          <tbody>{preview.rows.map((row) => <tr key={row.row_number}>
            <td>{row.row_number}</td><td>{row.member_number || "—"}</td><td>{row.legal_name || "—"}</td>
            <td>{row.entry?.email || "—"}</td><td>{row.entry?.phone || "—"}</td><td>{row.entry ? `${row.entry.share_capital_amount} 元／${row.entry.share_count} 股` : "—"}</td>
            <td className={row.errors.length ? "form-error" : ""}>{row.errors.join("；") || "可匯入"}</td>
          </tr>)}</tbody></table>
      </div>
      {preview.error_count > 0 && <p className="form-error">請修正錯誤列後重新選擇 CSV。</p>}
      <div className="form-actions"><button className="button button-primary" type="button" disabled={Boolean(busy) || preview.error_count > 0 || !preview.valid_count} onClick={() => void importRows()}>{busy === "importing" ? "匯入中…" : `匯入 ${preview.valid_count} 筆社員`}</button></div>
    </>}
  </section>;
}
