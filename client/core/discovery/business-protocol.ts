import { isPrivateIpv4, P0_DISCOVERY_PROTOCOL } from './p0-protocol';

export const BUSINESS_SERVICE = 'my-backend';

export function parseBusinessOffer(data: unknown, address: string, requestId: string, port: number): string | null {
  if (!isPrivateIpv4(address) || !data || typeof data !== 'object' || Array.isArray(data)) return null;
  const value = data as Record<string, unknown>;
  if (Object.keys(value).length !== 7 || value.protocol !== P0_DISCOVERY_PROTOCOL ||
    value.version !== 1 || value.type !== 'BUSINESS_OFFER' || value.serviceName !== BUSINESS_SERVICE ||
    value.requestId !== requestId || value.authenticated !== false || value.port !== port) return null;
  return `http://${address}:${port}`;
}
