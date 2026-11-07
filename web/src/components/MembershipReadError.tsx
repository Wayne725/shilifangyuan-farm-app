import { DataState } from "./Shared";

export function MembershipReadError({ error, onRetry }: { error: Error; onRetry: () => void }) {
  return <DataState kind="error" title="社員資格暫時無法讀取" detail={error.message} actionLabel="重新確認資格" onAction={onRetry} />;
}
