/** Page address only. This deliberately does not infer Cloudflare/rover health. */
export function describeAccess(address: string) {
  const url = new URL(address);
  const host = url.hostname.toLowerCase();
  const loopback =
    ["localhost", "127.0.0.1", "[::1]", "::1", "0.0.0.0"].includes(host) ||
    host.startsWith("127.");
  const octets = host.split(".").map(Number);
  const privateV4 =
    octets.length === 4 &&
    octets.every((n) => Number.isInteger(n) && n >= 0 && n <= 255) &&
    (octets[0] === 10 ||
      (octets[0] === 192 && octets[1] === 168) ||
      (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
      (octets[0] === 169 && octets[1] === 254));
  const privateV6 = /^\[?(fc|fd|fe80:)/.test(host) && host.includes(":");
  return {
    origin: url.origin,
    secure: url.protocol === "https:",
    kind: loopback
      ? "This laptop"
      : privateV4 || privateV6 || host.endsWith(".local")
        ? "Local network"
        : "Public address",
  };
}
