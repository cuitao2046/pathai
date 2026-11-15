import { createNavJournal } from '../core/nav/create-journal';
import { remove, setJson, setString } from '../utils/kv';
import { wxStub } from './setup-wx';

const base = 'http://localhost:8000';
const user = 'anon_0123456789abcdef';
const pendingKey = `nav:pending:${base}:${user}:s`;
const attempt = { key: 'original', body: { siteId: 's', mapVersion: 'v1', destinationId: 'p', userMode: 'blind' as const, locale: 'zh-CN', origin: { x: 1, y: 2, floorId: '1', confidence: 1, observedAt: '2026-11-15T00:00:00Z' } } };
beforeEach(() => {
  remove(pendingKey); remove(`auth:wechat:${base}`);
  setJson(`auth:wechat:${base}`, { userId: user });
});

test('pending attempt survives journal recreation and remains scoped to backend/user/site', () => {
  createNavJournal(base, 's').save(attempt);
  expect(createNavJournal(base, 's').load()).toEqual(attempt);
  expect(createNavJournal(base, 'other-site').load()).toBeNull();
  setJson(`auth:wechat:${base}`, { userId: 'anon_fedcba9876543210' });
  expect(createNavJournal(base, 's').load()).toBeNull();
  expect(() => createNavJournal('http://localhost:8001', 's').load()).toThrow();
  createNavJournal(base, 's');
});

test('corrupt recovery state cannot silently become a new attempt', () => {
  setString(pendingKey, '{broken');
  expect(() => createNavJournal(base, 's').load()).toThrow();
});

test('failed persistent write blocks sending an unjournaled create', () => {
  const journal = createNavJournal(base, 's');
  const spy = jest.spyOn(wxStub, 'setStorageSync').mockImplementation(() => { throw new Error('full'); });
  try { expect(() => journal.save(attempt)).toThrow(); }
  finally { spy.mockRestore(); }
});
