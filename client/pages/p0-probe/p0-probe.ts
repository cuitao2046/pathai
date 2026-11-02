import { resolveBusinessCandidate } from '../../core/discovery/p0-candidate';
import { checkP0Candidate, getP0Candidate, saveP0Candidate } from '../../services/p0-candidate';
import { discoverBusinessCandidate } from '../../services/business-discovery';

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
      const outcome = await resolveBusinessCandidate(
        () => candidate === null ? Promise.resolve(false) : checkP0Candidate(candidate),
        () => discoverBusinessCandidate(),
        () => generation === this.probeGeneration,
        (url) => { saveP0Candidate(url); },
      );
      if (generation !== this.probeGeneration) return;
      if (outcome === 'cached-reachable') {
        this.setData({ result: '缓存地址的业务端口 HTTP 可达，已忽略本轮发现结果。未验证服务身份，不会连接导航业务。' });
      } else if (outcome === 'discovery-reachable') {
        this.setData({ result: '缓存地址不可达；发现地址的业务端口 HTTP 可达，已缓存未认证候选。不会连接导航业务。' });
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
