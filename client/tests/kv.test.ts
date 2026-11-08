/**
 * kv：字符串/JSON 读写、损坏数据容错、storage 异常降级内存。
 */
import { getJson, getString, remove, setJson, setString } from '../utils/kv';
import { wxStub } from './setup-wx';

afterEach(() => {
  wxStub.__storage.clear();
});

test('字符串读写走 pathai: 前缀', () => {
  expect(getString('missing')).toBeNull();
  setString('token', 'abc');
  expect(getString('token')).toBe('abc');
  expect(wxStub.__storage.get('pathai:token')).toBe('abc');
  remove('token');
  expect(getString('token')).toBeNull();
  expect(wxStub.__storage.has('pathai:token')).toBe(false);
});

test('JSON 读写与损坏数据容错', () => {
  setJson('route', { routeId: 'r1' });
  expect(getJson<{ routeId: string }>('route')).toEqual({ routeId: 'r1' });

  wxStub.__storage.set('pathai:bad', '{not-json');
  expect(getJson('bad')).toBeNull();
});

test('storage 写入异常时降级为内存（当次会话可用），并返回 false 供调用方告警', () => {
  const original = wxStub.setStorageSync;
  wxStub.setStorageSync = () => {
    throw new Error('disk full');
  };
  try {
    expect(setString('k', 'v')).toBe(false);
    expect(setJson('j', { a: 1 })).toBe(false);
    expect(getString('k')).toBe('v');
    expect(getJson('j')).toEqual({ a: 1 });
  } finally {
    wxStub.setStorageSync = original;
  }
});

test('正常写入返回 true', () => {
  expect(setString('ok', 'v')).toBe(true);
  expect(setJson('ok-json', { a: 1 })).toBe(true);
});
