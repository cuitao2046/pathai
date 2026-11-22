from pathlib import Path
ROOT = Path(r'C:\Users\xinni\.codex\worktrees\fix-location-lifecycle\pathai')
path = ROOT / 'client/core/position/location-pipeline.ts'
text = path.read_text(encoding='utf-8')
def replace(old, new):
    global text
    assert old in text, old[:70]
    text = text.replace(old, new)
replace('private readonly aggregator:', 'private aggregator:')
replace('  private running = false;', '  private running = false;\n  private generation = 0;\n  private inFlight: { generation: number; promise: Promise<void> } | null = null;')
replace('    this.running = true;\n    this.mode = mode;', '    const generation = ++this.generation;\n    this.running = true;\n    this.aggregator = new ScanWindowAggregator(this.opts.windowMs);\n    this.lastFloorId = null;\n    this.mode = mode;')
replace('      await this.opts.source.start((sample) => this.aggregator.push(sample));', '      await this.opts.source.start((sample) => {\n        if (this.isCurrent(generation)) this.aggregator.push(sample);\n      });')
replace('      this.running = false;\n      throw err;', '      if (!this.isCurrent(generation)) return;\n      this.running = false;\n      throw err;')
replace('    this.schedule();\n  }\n\n  /** 切换', '    if (this.isCurrent(generation)) this.schedule();\n  }\n\n  private isCurrent(generation: number): boolean {\n    return this.running && this.generation === generation;\n  }\n\n  /** 切换')
replace('    this.running = false;\n    if (this.timer', '    this.running = false;\n    ++this.generation;\n    this.inFlight = null;\n    if (this.timer')
replace('''    try {
      await this.collectAndReport();
    } finally {
      this.schedule();
    }
  }

  private schedule(): void {
    if (!this.running) {
      return;
    }''', '''    const generation = this.generation;
    if (this.inFlight?.generation === generation) return this.inFlight.promise;
    if (this.timer !== null) clearTimeout(this.timer);
    this.timer = null;
    const operation = { generation, promise: Promise.resolve() };
    this.inFlight = operation;
    operation.promise = this.collectAndReport(generation).finally(() => {
      if (this.inFlight === operation) this.inFlight = null;
      if (this.isCurrent(generation)) this.schedule();
    });
    return operation.promise;
  }

  private schedule(): void {
    if (!this.running || this.inFlight?.generation === this.generation) return;
    if (this.timer !== null) clearTimeout(this.timer);''')
replace('    this.timer = setTimeout(() => {\n      void this.tick();', '    this.timer = setTimeout(() => {\n      this.timer = null;\n      void this.tick();')
replace('  private async collectAndReport(retryOnStale = true): Promise<void> {', '  private async collectAndReport(generation: number, retryOnStale = true): Promise<void> {\n    if (!this.isCurrent(generation)) return;')
replace('    } catch (err) {\n      const failure =', '    } catch (err) {\n      if (!this.isCurrent(generation)) return;\n      const failure =')
replace('await this.collectAndReport(false);', 'await this.collectAndReport(generation, false);')
replace('    this.lastFloorId = estimate.position?', '    if (!this.isCurrent(generation)) return;\n    this.lastFloorId = estimate.position?')
path.write_text(text, encoding='utf-8')
probe = Path(r'D:\code\pathai\client\review-artifacts\probes_client.test.ts').read_text(encoding='utf-8')
probe = probe.replace('Review reproductions: these assertions document current defects, not desired behavior.', 'Regression tests for cancelled requests and stale starts.')
probe = probe.replace('review: a request completed after stop still delivers a position', 'a request completed after stop cannot deliver a position')
probe = probe.replace('expect(onEstimate).toHaveBeenCalledTimes(1);', 'expect(onEstimate).not.toHaveBeenCalled();')
probe = probe.replace('review: an old start completion schedules a second loop after restart', 'an old start completion cannot schedule a second loop after restart')
probe = probe.replace('expect(jest.getTimerCount()).toBe(2);', 'expect(jest.getTimerCount()).toBe(1);')
probe += '''
test('concurrent manual ticks share one request and leave one timer', async () => {
  jest.spyOn(ScanWindowAggregator.prototype, 'snapshot').mockReturnValue([{ beaconId: 'b', rssi: -60, observedAtMs: Date.now(), n: 1 }]);
  let complete!: (response: unknown) => void;
  const request = jest.fn(() => new Promise(resolve => { complete = resolve; }));
  const pipeline = new LocationPipeline({ client: { request } as unknown as ApiClient,
    map: { siteId: 's', requireVersion: () => 'v', beaconRegistry: new BeaconRegistry([]) },
    source: { start: async () => {}, stop: () => {} }, sequence: { next: () => 1 }, onEstimate: jest.fn(), onUnusable: jest.fn() });
  await pipeline.start();
  const a = pipeline.tick(), b = pipeline.tick();
  expect(request).toHaveBeenCalledTimes(1);
  complete({ data: { quality: 'unreliable' } });
  await Promise.all([a,b]);
  expect(jest.getTimerCount()).toBe(1);
});
'''
(ROOT / 'client/tests/location-lifecycle.test.ts').write_text(probe, encoding='utf-8')
print('Applied lifecycle fix')
