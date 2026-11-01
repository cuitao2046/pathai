export const P0_DISCOVERY_PROTOCOL = 'local-service-discovery';
export const P0_DISCOVERY_SERVICE = 'pathai-p0-probe';

export interface P0Offer {
  address: string;
  port: number;
}

export function isPrivateIpv4(address: string): boolean {
  const parts = address.split('.');
  if (parts.length !== 4 || parts.some((part) => !/^(0|[1-9]\d{0,2})$/.test(part))) return false;
  const octets = parts.map(Number);
  if (octets.some((part) => part > 255)) return false;
  return octets[0] === 10 || (octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31) ||
    (octets[0] === 192 && octets[1] === 168);
}

export function parseP0Offer(data: unknown, address: string, requestId: string): P0Offer | null {
  if (!isPrivateIpv4(address) || typeof data !== 'object' || data === null || Array.isArray(data)) return null;
  const value = data as Record<string, unknown>;
  if (value.protocol !== P0_DISCOVERY_PROTOCOL || value.version !== 1 ||
      value.type !== 'P0_OFFER' || value.serviceName !== P0_DISCOVERY_SERVICE ||
      value.requestId !== requestId || value.authenticated !== false ||
      typeof value.port !== 'number' || !Number.isInteger(value.port) || value.port < 1 || value.port > 65535) {
    return null;
  }
  return { address, port: value.port };
}
