// pages/points/points.js — 标定点管理（现场手输真值）
const pointsMod = require('../../utils/points.js')
const cstore = require('../../utils/cstore.js')

Page({
  data: {
    points: [],
    scenes: pointsMod.SCENES,
    sceneIndex: 0,
    floors: ['1F', '2F'],
    floorIndex: 0,
    truthX: '',
    truthY: '',
    envNote: '',
    markType: '',
    editingId: ''
  },

  onShow() { this.refresh() },

  refresh() {
    const list = pointsMod.listPoints().map((p) => Object.assign({}, p, {
      truthText: `${p.truth[0]}, ${p.truth[1]}`,
      sessionCount: cstore.sessionsFor(p.pointId).length
    }))
    this.setData({ points: list })
  },

  onFloorChange(e) { this.setData({ floorIndex: Number(e.detail.value) }) },
  onSceneChange(e) { this.setData({ sceneIndex: Number(e.detail.value) }) },
  onTruthXInput(e) { this.setData({ truthX: e.detail.value }) },
  onTruthYInput(e) { this.setData({ truthY: e.detail.value }) },
  onEnvNoteInput(e) { this.setData({ envNote: e.detail.value }) },
  onMarkTypeInput(e) { this.setData({ markType: e.detail.value }) },

  onEdit(e) {
    const pointId = e.currentTarget.dataset.id
    const p = pointsMod.getPoint(pointId)
    if (!p) return
    this.setData({
      editingId: pointId,
      floorIndex: p.floor >= 2 ? 1 : 0,
      sceneIndex: Math.max(0, pointsMod.SCENES.indexOf(p.scene)),
      truthX: String(p.truth[0]),
      truthY: String(p.truth[1]),
      envNote: p.envNote || '',
      markType: p.markType || ''
    })
  },

  onCancelEdit() { this.resetForm() },

  resetForm() {
    this.setData({
      editingId: '', floorIndex: 0, sceneIndex: 0,
      truthX: '', truthY: '', envNote: '', markType: ''
    })
  },

  onSave() {
    const input = {
      floor: this.data.floorIndex + 1,
      truthX: this.data.truthX,
      truthY: this.data.truthY,
      scene: this.data.scenes[this.data.sceneIndex],
      envNote: this.data.envNote,
      markType: this.data.markType
    }
    try {
      if (this.data.editingId) {
        pointsMod.updatePoint(this.data.editingId, input)
        wx.showToast({ title: '已更新', icon: 'success' })
      } else {
        const created = pointsMod.addPoint(input)
        wx.showToast({ title: `已新增 ${created.pointId}`, icon: 'success' })
      }
      this.resetForm()
      this.refresh()
    } catch (err) {
      wx.showModal({ title: '保存失败', content: err.message || '输入不合法', showCancel: false })
    }
  },

  onDelete(e) {
    const pointId = e.currentTarget.dataset.id
    wx.showModal({
      title: `删除 ${pointId}`,
      content: '将同时删除该点的全部采样会话，不可恢复。确认？',
      confirmColor: '#dc2626',
      success: (r) => {
        if (!r.confirm) return
        pointsMod.removePoint(pointId)
        cstore.removeSessionsFor(pointId)
        if (this.data.editingId === pointId) this.resetForm()
        this.refresh()
      }
    })
  },

  onClearAll() {
    wx.showModal({
      title: '清空全部标定点',
      content: '将删除所有标定点与采样会话，不可恢复。确认？',
      confirmColor: '#dc2626',
      success: (r) => {
        if (!r.confirm) return
        pointsMod.clearPoints()
        cstore.clearAll()
        this.resetForm()
        this.refresh()
      }
    })
  }
})
