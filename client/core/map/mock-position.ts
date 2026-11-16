/**
 * mock 当前位置（局部米制，1 层 (-40,11)，位于 F1-CR-0050 走道，邻近真实信标 BK-01-010/-009）。
 * 单一事实源：地图页待命位置点绘制 与 mock 模拟导航起点共用，
 * 保证「显示的位置」与「导航起点/模拟行走出发点」完全一致。
 */
export const MOCK_POSITION = { floorId: '1', x: -40, y: 11 } as const;

/** mock 模拟导航步行速度（米/秒，用户指定：类高德模拟导航）。 */
export const MOCK_WALK_SPEED_MPS = 1.5;
