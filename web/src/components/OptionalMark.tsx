// 「（選填）」由 CSS ::after 產生，不計入 label 文字，getByLabel 與無障礙名稱維持原欄名。
export function OptionalMark() {
  return <span className="optional-mark" aria-hidden="true" />;
}
