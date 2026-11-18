import { createCancelSource, type CancelSource } from '../../core/discovery/cancel-token';
import { resolveBusinessCandidate } from '../../core/discovery/p0-candidate';
import { checkP0Candidate, getP0Candidate, getP0DeviceId, saveP0Candidate } from '../../services/p0-candidate';
import { discoverBusinessCandidate } from '../../services/business-discovery';

Page({
  data: {
    busy: false,
    result: '尚未探测',
  },

  probeGeneration: 0,
  probeCancel: null as CancelSource | null,

  onShow() {
    this.probeCancel?.cancel();
    this.probeCancel = null;
    this.probeGeneration += 1;
    this.setData({ busy: false, result: '尚未探测' });
    void this.onProbe();
  },

  onHide() {
    this.probeCancel?.cancel();
    this.probeCancel = null;
    this.probeGeneration += 1;
    this.setData({ busy: false, result: '尚未探测' });
  },

  async onProbe() {
    if (this.data.busy) return;
    const generation = ++this.probeGeneration;
    const round = createCancelSource();
    this.probeCancel = round;
    this.setData({ busy: true, result: '正在同时检查缓存地址与局域网发现…' });
    try {
      const candidate = getP0Candidate();
      const knownDeviceId = getP0DeviceId();
      const outcome = await resolveBusinessCandidate(
        () => candidate === null
          ? Promise.resolve(false)
          : checkP0Candidate(candidate, knownDeviceId, round.token),
        () => discoverBusinessCandidate(knownDeviceId, round.token).then(found => found?.url ?? null),
        () => generation === this.probeGeneration,
        (url) => { saveP0Candidate(url); },
      );
      // 缓存命中或本轮结束后，立即中止另一支仍在进行的探测/广播。
      round.cancel();
      if (generation !== this.probeGeneration) return;
      if (outcome === 'cached-reachable') {
        this.setData({ result: '缓存地址的 /healthz 与设备身份校验通过；本轮发现结果已忽略。' });
      } else if (outcome === 'discovery-reachable') {
        this.setData({ result: '缓存地址不可达；发现地址已通过 /healthz 身份校验并写入候选缓存。' });
      } else {
        this.setData({ result: '缓存地址和局域网发现均不可用。请检查服务、同网互访与防火墙，然后重试。' });
        wx.showModal({ title: '服务暂不可用', content: '请检查服务是否启动、手机与电脑能否互访，以及防火墙设置后重试。', showCancel: false });
      }
    } catch {
      round.cancel();
      if (generation !== this.probeGeneration) return;
      this.setData({ result: '探测未完成。请检查服务和网络权限后重试。' });
    } finally {
      if (generation === this.probeGeneration) this.setData({ busy: false });
      if (this.probeCancel === round) this.probeCancel = null;
    }
  },

});
