export class RequestTimeoutError extends Error {
  timeoutMs: number;

  constructor(timeoutMs: number) {
    super(`Request timed out after ${timeoutMs}ms`);
    this.name = "RequestTimeoutError";
    this.timeoutMs = timeoutMs;
  }
}

type FetchImplementation = (
  input: RequestInfo | URL,
  init?: RequestInit,
) => Promise<Response>;

type FetchTimeoutOptions = {
  timeoutMs?: number;
  fetchImpl?: FetchImplementation;
};

export async function fetchWithTimeout(
  input: RequestInfo | URL,
  init: RequestInit = {},
  {
    timeoutMs = 12_000,
    fetchImpl = fetch,
  }: FetchTimeoutOptions = {},
): Promise<Response> {
  const controller = new AbortController();
  const callerSignal = init.signal;
  const forwardAbort = () => controller.abort(callerSignal?.reason);
  if (callerSignal?.aborted) forwardAbort();
  else callerSignal?.addEventListener("abort", forwardAbort, { once: true });

  let timeoutId: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_resolve, reject) => {
    timeoutId = setTimeout(() => {
      const error = new RequestTimeoutError(timeoutMs);
      controller.abort(error);
      reject(error);
    }, timeoutMs);
  });

  try {
    return await Promise.race([
      fetchImpl(input, { ...init, signal: controller.signal }),
      timeout,
    ]);
  } finally {
    if (timeoutId) clearTimeout(timeoutId);
    callerSignal?.removeEventListener("abort", forwardAbort);
  }
}
