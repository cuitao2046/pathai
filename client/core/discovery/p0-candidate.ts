export function isP0CandidateUrl(value: string): boolean {
  const match = /^http:\/\/(10|172|192)\.(\d{1,3})\.(\d{1,3})\.(\d{1,3}):(\d{1,5})$/.exec(value);
  if (!match) return false;
  const octets = [match[1], match[2], match[3], match[4]].map(Number);
  if (octets.some((octet) => octet < 0 || octet > 255)) return false;
  const privateAddress = octets[0] === 10 || octets[0] === 172 && octets[1] >= 16 && octets[1] <= 31 ||
    octets[0] === 192 && octets[1] === 168;
  const port = Number(match[5]);
  return privateAddress && port >= 1 && port <= 65535;
}

export type P0CandidateOutcome = 'cached-reachable' | 'discovery-reachable' | 'unavailable';

export async function resolveP0Candidate(
  checkCached: () => Promise<boolean>,
  discover: () => Promise<boolean>,
): Promise<P0CandidateOutcome> {
  const discovered = Promise.resolve().then(discover).then(Boolean, () => false);
  const cached = await Promise.resolve().then(checkCached).then(Boolean, () => false);
  if (cached) return 'cached-reachable';
  return await discovered ? 'discovery-reachable' : 'unavailable';
}
