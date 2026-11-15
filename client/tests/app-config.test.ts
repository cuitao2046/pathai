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

describe('发布护栏 assertReleaseSafety', () => {
  // 当前 env.ts 联调开关为开：正式版必须被拦截
  const env = require('../config/env') as { assertReleaseSafety(): void };

  function expectLaunchThrows(envVersion: string, throws: boolean): void {
    (wx as unknown as { __envVersion: string }).__envVersion = envVersion as never;
    let launched = false;
    try {
      env.assertReleaseSafety();
      launched = true;
    } catch (err) {
      expect(throws).toBe(false);
      expect((err as Error).message).toContain('发布检查失败');
    }
    if (launched) {
      expect(throws).toBe(true);
    }
  }

  test('正式版 + 联调开关常开 → 抛错阻断启动', () => {
    expectLaunchThrows('release', false);
  });

  test('开发者工具/体验版不阻断', () => {
    expectLaunchThrows('develop', true);
    expectLaunchThrows('trial', true);
  });
});
