import { runP0LocalProbe } from '../../services/p0-local-probe';
import { P0_PROBE_RESULT_LABELS } from '../../services/p0-probe-labels';

Page({
  data: {
    busy: false,
    result: '尚未探测',
  },

  async onProbe() {
    if (this.data.busy) return;
    this.setData({ busy: true, result: '正在检测 UDP 广播和 HTTP 连通性…' });
    try {
      const diagnostic = await runP0LocalProbe();
      const phase = diagnostic.phase === 'binding' ? '绑定阶段' : diagnostic.phase === 'listening' ? '监听/广播阶段' : 'HTTP 探测阶段';
      const detail = diagnostic.result === 'udp-error' || diagnostic.result === 'udp-send-failed'
        ? `（${phase}${diagnostic.errorCode === null ? '' : `，错误码 ${diagnostic.errorCode}`}）` : '';
      this.setData({ result: P0_PROBE_RESULT_LABELS[diagnostic.result] + detail });
    } catch {
      this.setData({ result: '探测未完成。请检查网络权限后重试。' });
    } finally {
      this.setData({ busy: false });
    }
  },
});
