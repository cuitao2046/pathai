// pages/zones/zones.js — 分区管理：列表 + 新增/编辑/删除 + 锚点选择
const zones = require('../../utils/zones.js')
const fpstore = require('../../utils/fpstore.js')
const refEls = require('../../data/reference_elements.js')

function floorOf(el) { return el.floor }

Page({
  data: {
    zones: [],            // 已定义分区（含记录数/FP数）
    showForm: false,
    mode: 'add',          // add | edit
    editingZoneId: '',
    formName: '',
    formFloor: 1,
    formAnchor: null,     // 选中的锚点元素
    anchorSearch: '',
    anchorCandidates: []  // 按楼层+关键词过滤后的候选
  },

  onShow() { this.refresh() },

  refresh() {
    const list = zones.loadZones()
    const st = fpstore.zoneStats()
    const stMap = {}
    st.forEach((s) => { if (s.zoneId) stMap[s.zoneId] = s })
    const zonesView = list.map((z) => ({
      zoneId: z.zoneId,
      name: z.name,
      floor: z.floor,
      anchorId: z.anchor.anchorId,
      anchorLabel: z.anchor.anchorLabel,
      abs: z.anchor.abs,
      recordCount: (stMap[z.zoneId] || {}).recordCount || 0,
      fpCount: (stMap[z.zoneId] || {}).fpCount || 0
    }))
    this.setData({ zones: zonesView })
  },

  // ===== 表单 =====
  openAdd() {
    this.setData({
      showForm: true, mode: 'add', editingZoneId: '',
      formName: '', formFloor: 1, formAnchor: null, anchorSearch: '',
      anchorCandidates: this._filterAnchors(1, '')
    })
  },

  openEdit(e) {
    const zid = e.currentTarget.dataset.id
    const z = zones.getZone(zid)
    if (!z) return
    this.setData({
      showForm: true, mode: 'edit', editingZoneId: zid,
      formName: z.name, formFloor: z.floor,
      formAnchor: { id: z.anchor.anchorId, label: z.anchor.anchorLabel, type: z.anchor.anchorType, floor: z.floor, abs: z.anchor.abs },
      anchorSearch: '',
      anchorCandidates: this._filterAnchors(z.floor, '')
    })
  },

  closeForm() { this.setData({ showForm: false }) },

  onFormName(e) { this.setData({ formName: e.detail.value }) },
  onFloor(e) {
    const f = parseInt(e.currentTarget.dataset.f, 10)
    this.setData({ formFloor: f, anchorCandidates: this._filterAnchors(f, this.data.anchorSearch) })
  },
  onAnchorSearch(e) {
    const kw = e.detail.value
    this.setData({ anchorSearch: kw, anchorCandidates: this._filterAnchors(this.data.formFloor, kw) })
  },

  _filterAnchors(floor, kw) {
    const k = (kw || '').trim().toLowerCase()
    let els = refEls.elements.filter((el) => el.floor === floor)
    if (k) els = els.filter((el) => el.label.toLowerCase().includes(k) || el.id.toLowerCase().includes(k))
    return els.slice(0, 200)  // 渲染上限，避免长列表卡顿
  },

  pickAnchor(e) {
    const idx = e.currentTarget.dataset.idx
    const el = this.data.anchorCandidates[idx]
    if (!el) return
    this.setData({ formAnchor: el })
  },

  saveZone() {
    const d = this.data
    if (!d.formAnchor) {
      wx.showToast({ title: '请先选择参考锚点元素', icon: 'none' })
      return
    }
    try {
      if (d.mode === 'edit') {
        zones.updateZone(d.editingZoneId, {
          name: d.formName, floor: d.formFloor, anchor: d.formAnchor
        })
      } else {
        zones.addZone({
          name: d.formName, floor: d.formFloor, anchor: d.formAnchor
        })
      }
      this.setData({ showForm: false })
      this.refresh()
      wx.showToast({ title: '已保存', icon: 'success' })
    } catch (err) {
      wx.showModal({ title: '保存失败', content: (err && err.message) || '请检查输入', showCancel: false })
    }
  },

  removeZone(e) {
    const zid = e.currentTarget.dataset.id
    const z = zones.getZone(zid)
    wx.showModal({
      title: '删除分区',
      content: '确认删除分区「' + (z ? z.name : zid) + '」？\n（已采集的指纹记录不删除，仅失去分区归属标记）',
      confirmColor: '#dc2626',
      success: (r) => {
        if (r.confirm) {
          zones.removeZone(zid)
          this.refresh()
          wx.showToast({ title: '已删除', icon: 'success' })
        }
      }
    })
  }
})
