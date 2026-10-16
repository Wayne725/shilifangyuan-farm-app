import { useState } from "react";

import { resolveAsset } from "../lib/api";

export function MealPhoto({ src, alt, className = "" }: { src?: string | null; alt: string; className?: string }) {
  const [failedSource, setFailedSource] = useState<string>();
  if (!src || failedSource === src) return null;
  return <img className={`meal-photo ${className}`} src={resolveAsset(src)} alt={alt} width={96} height={96} loading="lazy" decoding="async" onError={() => setFailedSource(src)} />;
}
