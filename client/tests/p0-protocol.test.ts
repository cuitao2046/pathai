import { isPrivateIpv4, parseP0Offer, P0_DISCOVERY_PROTOCOL, P0_DISCOVERY_SERVICE } from '../core/discovery/p0-protocol';

const offer = {
  protocol: P0_DISCOVERY_PROTOCOL,
  version: 1,
  type: 'P0_OFFER',
  serviceName: P0_DISCOVERY_SERVICE,
  requestId: '0123456789abcdef0123456789abcdef',
  port: 39002,
  authenticated: false,
};

describe('P0 局域网无凭据探测协议', () => {
  test('只接受 RFC1918 IPv4', () => {
    expect(isPrivateIpv4('10.0.0.3')).toBe(true);
    expect(isPrivateIpv4('172.31.4.3')).toBe(true);
    expect(isPrivateIpv4('192.168.1.20')).toBe(true);
    for (const address of ['172.32.0.1', '127.0.0.1', '8.8.8.8', '192.168.001.2', '192.168.1.256']) {
      expect(isPrivateIpv4(address)).toBe(false);
    }
  });

  test('只接收当前轮次与固定探测类型', () => {
    expect(parseP0Offer(offer, '192.168.1.20', offer.requestId)).toEqual({ address: '192.168.1.20', port: 39002 });
    expect(parseP0Offer(offer, '192.168.1.20', 'ffffffffffffffffffffffffffffffff')).toBeNull();
    expect(parseP0Offer({ ...offer, type: 'OFFER' }, '192.168.1.20', offer.requestId)).toBeNull();
    expect(parseP0Offer({ ...offer, port: 65536 }, '192.168.1.20', offer.requestId)).toBeNull();
    expect(parseP0Offer({ ...offer, authenticated: true }, '192.168.1.20', offer.requestId)).toBeNull();
    expect(parseP0Offer(offer, '8.8.8.8', offer.requestId)).toBeNull();
  });
});
