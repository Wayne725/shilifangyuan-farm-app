import { DataState, LoadingLines } from "../components/Shared";
import { useStorefrontStatus } from "../components/StorefrontStatus";
import './PolicyPages.css';

function PolicyPage({ kind, title }: { kind: 'privacy' | 'terms' | 'returns'; title: string }) {
  const platform = useStorefrontStatus();
  const information = platform.data?.public_information;
  return <section className="content-section"><h1>{title}</h1>
    {platform.isPending ? <LoadingLines count={4} /> : platform.isError ? <DataState title="內容無法讀取" detail={platform.error.message} onAction={() => void platform.refetch()} />
      : !information?.published ? <DataState title="內容尚未發布" detail="" />
      : <><p>{information.legal_name} · 版本 {information.policy_version}</p>
        {information[kind].split(/\n\s*\n/).map((paragraph, index) => <p key={index} className="policy-paragraph">{paragraph}</p>)}
        <address>{information.address}<br /><a href={`tel:${information.phone.replace(/[^+0-9]/g, '')}`}>{information.phone}</a> · <a href={`mailto:${information.email}`}>{information.email}</a></address>
      </>}
  </section>;
}

export const PrivacyPage = () => <PolicyPage kind="privacy" title="隱私權與個資告知" />;
export const TermsPage = () => <PolicyPage kind="terms" title="服務條款" />;
export const ReturnsPage = () => <PolicyPage kind="returns" title="退換貨與退款政策" />;
