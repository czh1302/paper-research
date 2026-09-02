import { describe, expect, it } from "vitest";
import { inspectChatImage, safeChatImageName } from "./chat-image.ts";

describe("experiment chat attachments", () => {
  it("recognizes bounded PNG metadata by file signature", () => {
    const bytes = new Uint8Array(24);
    bytes.set([137, 80, 78, 71, 13, 10, 26, 10]);
    new DataView(bytes.buffer).setUint32(16, 1280);
    new DataView(bytes.buffer).setUint32(20, 720);
    expect(inspectChatImage(bytes)).toEqual({ mimeType: "image/png", width: 1280, height: 720 });
  });

  it("rejects malformed and excessive attachment identifiers", () => {
    expect(() => inspectChatImage(new Uint8Array([1, 2, 3]))).toThrow(/supported image/);
  });

  it("sanitizes the display name before using it in a private object path", () => {
    const name = safeChatImageName("../../界面 截图.png");
    expect(name).not.toContain("/");
    expect(name.startsWith(".")).toBe(false);
    expect(name.endsWith(".png")).toBe(true);
  });
});
