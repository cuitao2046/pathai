/**
 * mock 当前位置（局部米制，1 层 (-37,-48)，邻近真实信标 BK-01-008）。
 * 单一事实源：地图页待命位置点绘制 与 mock 模拟导航起点共用，
 * 保证「显示的位置」与「导航起点/模拟行走出发点」完全一致。
 */
export const MOCK_POSITION = { floorId: '1', x: -37, y: -48 } as const;

/** mock 模拟导航步行速度（米/秒，用户指定：类高德模拟导航）。 */
export const MOCK_WALK_SPEED_MPS = 1.5;
