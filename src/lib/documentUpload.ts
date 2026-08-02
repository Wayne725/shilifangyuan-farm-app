import * as Crypto from "expo-crypto";

export type DocumentContentType =
  | "image/jpeg"
  | "image/png"
  | "application/pdf";

export type PickedDocument = {
  uri: string;
  bytes: Uint8Array;
  size_bytes: number;
  content_type: DocumentContentType;
  checksum_sha256: string;
};

function toHex(buffer: ArrayBuffer) {
  return Array.from(new Uint8Array(buffer))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

export async function sha256Hex(bytes: Uint8Array) {
  // Copy into a plain ArrayBuffer so the value satisfies BufferSource on every
  // platform (a Uint8Array can be backed by a SharedArrayBuffer).
  const copy = new Uint8Array(bytes.length);
  copy.set(bytes);
  const digest = await Crypto.digest(
    Crypto.CryptoDigestAlgorithm.SHA256,
    copy,
  );
  return toHex(digest);
}

/** Creates an unmistakable synthetic PDF; Sandbox never opens user storage. */
export async function pickMembershipDocument(): Promise<PickedDocument> {
  const content = [
    "%PDF-1.4",
    "% SHILIFANGYUAN SANDBOX TEST DOCUMENT - NOT A REAL ID",
    `1 0 obj << /Type /Catalog /SandboxCreatedAt (${new Date().toISOString()}) >> endobj`,
    "trailer << /Root 1 0 R >>",
    "%%EOF",
  ].join("\n");
  const bytes = Uint8Array.from(content, (character) =>
    character.charCodeAt(0),
  );
  return {
    uri: "sandbox://generated-membership-document.pdf",
    bytes,
    size_bytes: bytes.length,
    content_type: "application/pdf",
    checksum_sha256: await sha256Hex(bytes),
  };
}

/** Uploads the bytes with exactly the headers R2 signed into the URL. */
export async function putDocumentToStorage(
  uploadUrl: string,
  document: PickedDocument,
  requiredHeaders: Record<string, string>,
) {
  const headers: Record<string, string> = {
    ...requiredHeaders,
    "Content-Type": document.content_type,
  };

  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers,
    body: document.bytes as unknown as BodyInit,
  });
  if (!response.ok) {
    throw new Error(`證件上傳失敗（HTTP ${response.status}）`);
  }
}
