import { P1Pairing } from './p1-protocol';

export type P1Candidate = { endpoint: string; bootId?: string };
export type P1Resolution = { source: 'cached' | 'discovered'; endpoint: string; bootId: string } | null;

export class P1Resolver {
  private generation = 0;
  private pending: Promise<P1Resolution> | null = null;
  private timer: ReturnType<typeof setTimeout> | null = null;

  invalidate(): void {
    this.generation++;
    this.pending = null;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
  }

  resolve(
    pairing: P1Pairing,
    cached: P1Candidate,
    discover: () => Promise<P1Candidate[]>,
    verify: (candidate: P1Candidate, pairing: P1Pairing) => Promise<string | null>,
    accept: (resolution: NonNullable<P1Resolution>) => void,
    timeoutMs = 12000,
  ): Promise<P1Resolution> {
    if (this.pending) return this.pending;
    const generation = ++this.generation;
    const deadline = Date.now() + timeoutMs;
    let expire!: () => void;
    const expired = new Promise<P1Resolution>((resolve) => { expire = () => resolve(null); });
    this.timer = setTimeout(() => {
      if (generation === this.generation) this.invalidate();
      expire();
    }, timeoutMs);
    const discovered = Promise.resolve().then(discover).catch(() => [] as P1Candidate[]);
    const run = async (): Promise<P1Resolution> => {
      let bootId: string | null = null;
      try { bootId = await verify(cached, pairing); } catch { bootId = null; }
      if (generation !== this.generation || Date.now() >= deadline) return null;
      if (bootId) {
        const result = { source: 'cached' as const, endpoint: cached.endpoint, bootId };
        accept(result);
        return result;
      }
      const candidates = await discovered;
      if (generation !== this.generation || Date.now() >= deadline) return null;
      for (const candidate of candidates.slice(0, 16)) {
        if (Date.now() >= deadline) return null;
        try { bootId = await verify(candidate, pairing); } catch { bootId = null; }
        if (generation !== this.generation || Date.now() >= deadline) return null;
        if (!bootId) continue;
        const result = { source: 'discovered' as const, endpoint: candidate.endpoint, bootId };
        accept(result);
        return result;
      }
      return null;
    };
    const current = Promise.race([Promise.resolve().then(run), expired]).finally(() => {
      if (generation === this.generation) {
        this.pending = null;
        if (this.timer !== null) clearTimeout(this.timer);
        this.timer = null;
      }
    });
    this.pending = current;
    return current;
  }
}
