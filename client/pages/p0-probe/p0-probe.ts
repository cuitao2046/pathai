import { runP0LocalProbe } from '../../services/p0-local-probe';

const results = {
  'udp-and-http': '已收到 UDP 响应并连通 HTTP 探测端口。仅验证网络能力，未认证业务服务。',
  'udp-only': '已收到 UDP 响应，但 HTTP 探测失败。请检查端口、网络互访和防火墙。',
  timeout: '未收到 UDP 响应。请检查探测服务、广播转发、网络互访和系统权限。',
  unsupported: 'UDP 或安全随机数能力不可用。请检查手机微信与系统权限。',
};

Page({
  data: {
    busy: false,
    result: '尚未探测',
  },

  async onProbe() {
    if (this.data.busy) return;
    this.setData({ busy: true, result: '正在检测 UDP 广播和 HTTP 连通性…' });
    try {
      const result = await runP0LocalProbe();
      this.setData({ result: results[result] });
    } catch {
      this.setData({ result: '探测未完成。请检查网络权限后重试。' });
    } finally {
      this.setData({ busy: false });
    }
  },
});
