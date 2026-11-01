import { resolveP0Candidate } from '../../core/discovery/p0-candidate';
import { checkP0Candidate, getP0Candidate } from '../../services/p0-candidate';
import { runP0LocalProbe } from '../../services/p0-local-probe';

Page({
  data: {
    busy: false,
    result: '尚未探测',
  },

  probeGeneration: 0,

  onShow() {
    this.probeGeneration += 1;
    this.setData({ busy: false, result: '尚未探测' });
    void this.onProbe();
  },

  onHide() {
    this.probeGeneration += 1;
    this.setData({ busy: false, result: '尚未探测' });
  },

  async onProbe() {
    if (this.data.busy) return;
    const generation = ++this.probeGeneration;
    this.setData({ busy: true, result: '正在同时检查缓存地址与局域网发现…' });
    try {
      const candidate = getP0Candidate();
      const outcome = await resolveP0Candidate(
        () => candidate === null ? Promise.resolve(false) : checkP0Candidate(candidate),
        async () => (await runP0LocalProbe()).result === 'udp-and-http',
      );
      if (generation !== this.probeGeneration) return;
      if (outcome === 'cached-reachable') {
        this.setData({ result: '缓存地址 HTTP 可达，已忽略本轮发现结果。尚未验证服务身份，不会连接导航业务。' });
      } else if (outcome === 'discovery-reachable') {
        this.setData({ result: '缓存地址不可达，但局域网发现探测成功。发现结果未经身份验证，不能重建导航连接。' });
      } else {
        this.setData({ result: '缓存地址和局域网发现均不可用。请检查服务、同网互访与防火墙，然后重试。' });
        wx.showModal({ title: '服务暂不可用', content: '请检查服务是否启动、手机与电脑能否互访，以及防火墙设置后重试。', showCancel: false });
      }
    } catch {
      if (generation !== this.probeGeneration) return;
      this.setData({ result: '探测未完成。请检查服务和网络权限后重试。' });
    } finally {
      if (generation === this.probeGeneration) this.setData({ busy: false });
    }
  },
});
