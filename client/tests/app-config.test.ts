/**
 * 启动入口配置：地图页为首屏（自动发现 → 加载地图 → 导航），
 * 设置页与隔离探测页仍保留可进入，便于现场排查。
 */
const appJson = require('../app.json') as { pages: string[] };

test('地图页为启动首屏', () => {
  expect(appJson.pages[0]).toBe('pages/map/map');
});

test('设置页与隔离探测页仍注册', () => {
  expect(appJson.pages).toContain('pages/settings/settings');
  expect(appJson.pages).toContain('pages/p0-probe/p0-probe');
});
