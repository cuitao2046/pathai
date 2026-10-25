/**
 * 首页：C5 里程碑接入按住说话与意图闭环；当前为占位骨架。
 */
Page({
  data: {
    title: 'PathAI 室内导航',
    subtitle: '语音说「带我去……」开始导航（C5 启用）',
  },

  onGoSettings() {
    wx.navigateTo({ url: '/pages/settings/settings' });
  },
});
