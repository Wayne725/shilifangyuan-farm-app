import * as Crypto from "expo-crypto";
import { File } from "expo-file-system";
import * as ImagePicker from "expo-image-picker";
import { Platform } from "react-native";

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

const MAX_DOCUMENT_BYTES = 8 * 1024 * 1024;

const CONTENT_TYPES: Record<string, DocumentContentType> = {
  "image/jpeg": "image/jpeg",
  "image/jpg": "image/jpeg",
  "image/png": "image/png",
  "application/pdf": "application/pdf",
};

function contentTypeFrom(mimeType: string | undefined, uri: string) {
  const normalized = (mimeType ?? "").toLowerCase();
  if (CONTENT_TYPES[normalized]) return CONTENT_TYPES[normalized];
  const extension = uri.split("?")[0]?.split(".").pop()?.toLowerCase();
  if (extension === "png") return "image/png";
  if (extension === "pdf") return "application/pdf";
  if (extension === "jpg" || extension === "jpeg") return "image/jpeg";
  return null;
}

async function readBytes(uri: string): Promise<Uint8Array> {
  if (Platform.OS === "web") {
    const response = await fetch(uri);
    return new Uint8Array(await response.arrayBuffer());
  }
  return new File(uri).bytes();
}

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

/**
 * Opens the photo library, reads the picked file and hashes it locally.
 * Returns null when the user dismisses the picker.
 */
export async function pickMembershipDocument(): Promise<PickedDocument | null> {
  const permission = await ImagePicker.requestMediaLibraryPermissionsAsync();
  if (!permission.granted) {
    throw new Error("需要相簿權限才能選擇證件影像");
  }
  const result = await ImagePicker.launchImageLibraryAsync({
    mediaTypes: ["images"],
    allowsMultipleSelection: false,
    quality: 1,
  });
  if (result.canceled || !result.assets.length) return null;

  const asset = result.assets[0]!;
  const content_type = contentTypeFrom(asset.mimeType, asset.uri);
  if (!content_type) {
    throw new Error("只接受 JPEG、PNG 或 PDF 檔案");
  }
  const bytes = await readBytes(asset.uri);
  if (!bytes.length) {
    throw new Error("無法讀取所選檔案");
  }
  if (bytes.length > MAX_DOCUMENT_BYTES) {
    throw new Error("檔案不可超過 8 MB");
  }
  return {
    uri: asset.uri,
    bytes,
    size_bytes: bytes.length,
    content_type,
    checksum_sha256: await sha256Hex(bytes),
  };
}

/** Uploads the bytes with exactly the headers R2 signed into the URL. */
export async function putDocumentToStorage(
  uploadUrl: string,
  document: PickedDocument,
  requiredHeaders: Record<string, string>,
) {
  const headers: Record<string, string> = { ...requiredHeaders };
  // Browsers refuse to let scripts set Content-Length; they compute it.
  delete headers["Content-Length"];
  headers["Content-Type"] = document.content_type;

  const response = await fetch(uploadUrl, {
    method: "PUT",
    headers,
    body: document.bytes as unknown as BodyInit,
  });
  if (!response.ok) {
    throw new Error(`證件上傳失敗（HTTP ${response.status}）`);
  }
}
