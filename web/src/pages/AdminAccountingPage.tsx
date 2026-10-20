import { AdminNav } from "../components/AdminNav";
import { AccountingExport } from "../components/AccountingExport";
import { useAuth } from "../context/AuthContext";

export function AdminAccountingPage() {
  const { user, openLogin } = useAuth();
  if (user?.user_role !== "admin") return <section className="admin-gate"><h1>會計匯出只向管理員開放。</h1>{!user && <button className="button button-primary" onClick={openLogin}>管理員登入</button>}</section>;
  return <section className="admin-module-page"><header className="workspace-heading"><div><p className="eyebrow">ACCOUNTING EXPORT / 會計匯出</p><h1>會計檔案</h1></div></header><AdminNav /><AccountingExport /></section>;
}
