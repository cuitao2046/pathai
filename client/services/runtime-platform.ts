/**
 * 运行宿主判定：开发者工具模拟器与真机需要不同的服务端寻址路径。
 *
 * 模拟器的网络请求由开发机进程发出，`127.0.0.1` 指向开发机本身；
 * 真机上的 `127.0.0.1` 指向手机自身，不能用于访问开发机后端。
 */
export function isDevtoolsRuntime(): boolean {
  try {
    const info = typeof wx.getDeviceInfo === 'function' ? wx.getDeviceInfo() : wx.getSystemInfoSync();
    return info.platform === 'devtools';
  } catch {
    return false;
  }
}
