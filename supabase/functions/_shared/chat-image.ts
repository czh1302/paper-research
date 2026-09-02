export const CHAT_IMAGE_TYPES = new Set([
  "image/jpeg",
  "image/png",
  "image/webp",
  "image/gif",
]);
export const CHAT_IMAGE_MAX_COUNT = 4;
export const CHAT_IMAGE_MAX_BYTES = 10 * 1024 * 1024;
export const CHAT_IMAGE_MAX_TOTAL_BYTES = 25 * 1024 * 1024;

export function safeChatImageName(name: string): string {
  return name.replace(/[^A-Za-z0-9._-]+/g, "_").replace(/^\.+/, "").slice(0, 160) || "image";
}

function uint24le(bytes: Uint8Array, offset: number): number {
  return bytes[offset] | (bytes[offset + 1] << 8) | (bytes[offset + 2] << 16);
}

export function inspectChatImage(bytes: Uint8Array): { mimeType: string; width: number; height: number } {
  if (bytes.length >= 24
    && bytes.slice(0, 8).every((value, index) => value === [137, 80, 78, 71, 13, 10, 26, 10][index])) {
    const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    return { mimeType: "image/png", width: view.getUint32(16), height: view.getUint32(20) };
  }
  if (bytes.length >= 10) {
    const signature = new TextDecoder("ascii").decode(bytes.slice(0, 6));
    if (signature === "GIF87a" || signature === "GIF89a") {
      const view = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
      return { mimeType: "image/gif", width: view.getUint16(6, true), height: view.getUint16(8, true) };
    }
  }
  if (bytes.length >= 30
    && new TextDecoder("ascii").decode(bytes.slice(0, 4)) === "RIFF"
    && new TextDecoder("ascii").decode(bytes.slice(8, 12)) === "WEBP") {
    const chunk = new TextDecoder("ascii").decode(bytes.slice(12, 16));
    if (chunk === "VP8X") {
      return { mimeType: "image/webp", width: uint24le(bytes, 24) + 1, height: uint24le(bytes, 27) + 1 };
    }
    if (chunk === "VP8L" && bytes[20] === 0x2f) {
      const width = 1 + (bytes[21] | ((bytes[22] & 0x3f) << 8));
      const height = 1 + ((bytes[22] >> 6) | (bytes[23] << 2) | ((bytes[24] & 0x0f) << 10));
      return { mimeType: "image/webp", width, height };
    }
    if (chunk === "VP8 " && bytes[23] === 0x9d && bytes[24] === 0x01 && bytes[25] === 0x2a) {
      const width = (bytes[26] | (bytes[27] << 8)) & 0x3fff;
      const height = (bytes[28] | (bytes[29] << 8)) & 0x3fff;
      return { mimeType: "image/webp", width, height };
    }
  }
  if (bytes.length >= 4 && bytes[0] === 0xff && bytes[1] === 0xd8) {
    let offset = 2;
    const sofMarkers = new Set([0xc0, 0xc1, 0xc2, 0xc3, 0xc5, 0xc6, 0xc7, 0xc9, 0xca, 0xcb, 0xcd, 0xce, 0xcf]);
    while (offset + 8 < bytes.length) {
      if (bytes[offset] !== 0xff) { offset += 1; continue; }
      while (offset < bytes.length && bytes[offset] === 0xff) offset += 1;
      const marker = bytes[offset++];
      if (marker === 0xd9 || marker === 0xda) break;
      if (marker === 0x01 || (marker >= 0xd0 && marker <= 0xd7)) continue;
      if (offset + 2 > bytes.length) break;
      const length = (bytes[offset] << 8) | bytes[offset + 1];
      if (length < 2 || offset + length > bytes.length) break;
      if (sofMarkers.has(marker) && length >= 7) {
        const height = (bytes[offset + 3] << 8) | bytes[offset + 4];
        const width = (bytes[offset + 5] << 8) | bytes[offset + 6];
        return { mimeType: "image/jpeg", width, height };
      }
      offset += length;
    }
  }
  throw new Error("The uploaded file is not a supported image");
}
