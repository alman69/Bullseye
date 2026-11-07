import { describe, expect, it } from "vitest";
import { describeAccess } from "./access-path";

describe("access address labels", () => {
  it.each([
    "http://localhost:8000",
    "http://127.0.0.1:8000",
    "http://[::1]:8000",
  ])("identifies laptop-only access: %s", (url) => {
    expect(describeAccess(url).kind).toBe("This laptop");
  });
  it.each([
    "http://192.168.4.2:8000",
    "http://10.84.1.2:8000",
    "http://172.16.1.2",
    "http://[fd00::1]",
    "http://bullseye.local",
  ])("identifies local-network access: %s", (url) => {
    expect(describeAccess(url).kind).toBe("Local network");
  });
  it("labels a public HTTPS origin without claiming tunnel or rover health", () => {
    expect(
      describeAccess("https://sample.trycloudflare.com/?private=query"),
    ).toEqual({
      origin: "https://sample.trycloudflare.com",
      secure: true,
      kind: "Public address",
    });
  });
});
