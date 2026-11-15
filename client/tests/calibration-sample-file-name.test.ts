import {
  isSampleFileName,
  parseExportTimestamp,
  sampleKindText,
} from '../core/calibration/sample-file-name';

describe('sample-file-name', () => {
  it('recognizes real static export names', () => {
    expect(isSampleFileName('static_samples_1794284029673.json')).toBe(true);
    expect(parseExportTimestamp('static_samples_1794284029673.json')).toBe(1794284029673);
    expect(sampleKindText('static_samples_1794284029673.json')).toBe('静态标定');
  });

  it('recognizes real logdist export names', () => {
    const name = 'logdist_BK-01-001_1794284029673.json';
    expect(isSampleFileName(name)).toBe(true);
    expect(parseExportTimestamp(name)).toBe(1794284029673);
    expect(sampleKindText(name)).toBe('对数等距');
  });

  it('accepts beacon ids sanitized with underscores', () => {
    expect(isSampleFileName('logdist_BK_01_001_1794284029673.json')).toBe(true);
  });

  it('keeps accepting the historical logdist_samples name seen in real uploads', () => {
    expect(isSampleFileName('logdist_samples_1794285564566.json')).toBe(true);
    expect(sampleKindText('logdist_samples_1794285564566.json')).toBe('对数等距');
  });

  it('rejects other exports and malformed names', () => {
    expect(isSampleFileName('fingerprint_BK-01-001_1794284029673.json')).toBe(false);
    expect(isSampleFileName('static_samples_179428402967.json')).toBe(false);
    expect(isSampleFileName('logdist_BK-01-001.json')).toBe(false);
    expect(isSampleFileName('static_samples_1794284029673.txt')).toBe(false);
  });
});
