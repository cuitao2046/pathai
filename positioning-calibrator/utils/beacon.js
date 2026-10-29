// utils/beacon.js — iBeacon 扫描封装（与 fingerprint-collector/utils/beacon.js 同源）
// startBeaconDiscovery 必须传入目标 uuid 数组（本场地全部信标共用同一 uuid）。

let _discovering = false
let _updateHandler = null
let _adapterReady = false

function _ensureAdapter() {
  return new Promise((resolve, reject) => {
    wx.openBluetoothAdapter({
      success: () => { _adapterReady = true; resolve(true) },
      fail: (err) => reject(err)
    })
  })
}

// uuids: string[]  onUpdate(beacons[])  onError(err)
function start(uuids, onUpdate, onError) {
  _updateHandler = onUpdate
  return _ensureAdapter().then(() => new Promise((resolve, reject) => {
    wx.startBeaconDiscovery({
      uuids: uuids,
      success: () => {
        _discovering = true
        wx.onBeaconUpdate((res) => {
          if (_updateHandler) _updateHandler(normalize(res.beacons || []))
        })
        wx.onBeaconServiceChange((r) => {
          if (!r.available && onError) onError(r)
        })
        resolve(true)
      },
      fail: (err) => reject(err)
    })
  }))
}

function normalize(beacons) {
  return beacons
    .map((b) => ({
      uuid: b.uuid,
      major: b.major,
      minor: b.minor,
      rssi: typeof b.rssi === 'number' ? b.rssi : null,
      accuracy: typeof b.accuracy === 'number' ? +b.accuracy.toFixed(2) : null,
      txPower: typeof b.txPower === 'number' ? b.txPower : null,
      proximity: b.proximity
    }))
    .sort((a, b) => (a.rssi == null ? 999 : a.rssi) - (b.rssi == null ? 999 : b.rssi))
}

function stop() {
  return new Promise((resolve) => {
    if (!_discovering) return resolve(true)
    wx.stopBeaconDiscovery({
      complete: () => {
        _discovering = false
        try { wx.offBeaconUpdate && wx.offBeaconUpdate() } catch (e) {}
        resolve(true)
      }
    })
  })
}

function isDiscovering() { return _discovering }

module.exports = { start, stop, isDiscovering, normalize }
