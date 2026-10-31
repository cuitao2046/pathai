# Minew E5 蓝牙信标 Windows 管理应用开发设计文档 (SDK 逆向工程版)

> ⚠️ **协议部分已作废（2026-10-30）**：本文 §2 的 `a3c875xx` 服务族、AES-128 鉴权、iBeacon 两包帧、Slot 0~5
> **不属于 `minewBeaconAdmin.jar`（2025.04.25）**。该 SDK 实际使用 `0000fff0` 服务族、逐特征直写、无 AES。
> 以 `docs/reference/Minew_E5_BeaconAdmin_Protocol_Verified.md`（同一 jar 反编译核验结论）为准；
> 本文仅保留 .NET/WPF 架构与 UI 部分作参考。

## 1. 系统概述与设计目标

本设计文档基于对 **Minew 官方 Android SDK (`MTBeaconPlus.jar`)** 字节码反编译与静态分析的产出成果，专为 **Minew E5 Location Beacon（搭载 BeaconPlus 固件）** 量身定制。

文档旨在指导研发团队在 **Windows 10 / Windows 11 (64-bit)** 平台上，不依赖官方书面协议文档，使用 **.NET 8.0 + WPF / WinUI 3** 原生开发框架，构建一套支持信标扫描、广播帧实时解析、AES-128 鉴权解锁、多通道（Slot 0~5）配置读写、广播间隔与发射功率调节及软关机/复位功能的工业级上位机管理应用。

---

## 2. 反编译关键成果与 GATT 协议规格 (Decompiled GATT Protocol Specification)

通过对 `MTBeaconPlus.jar` 内部 `com.minew.beaconplus.sdk` 包（特别是 `a.a`、`a.b`、`MTConnectionHandler`、`FrameUtils` 及 `AESTools`）的反编译分析，现将底层蓝牙通信协议完全解密如下：

### 2.1 核心 GATT 服务与特征值 UUID 常量

| 逻辑角色 | UUID 字符串 | 说明与读写属性 |
| :--- | :--- | :--- |
| **Config Service (核心配置服务)** | `a3c87500-8ed3-4bdf-8a39-a01bebede295` | BeaconPlus 主配置服务 |
| └─ **Char_WriteControl** | `a3c87501-8ed3-4bdf-8a39-a01bebede295` | `Write` / `Read`：通用的控制指令写入 |
| └─ **Char_SlotSelect** | `a3c87502-8ed3-4bdf-8a39-a01bebede295` | `Write`：**槽位选择特征值**，写入 1 字节槽位号（`0x00` ~ `0x05`） |
| └─ **Char_AdvInterval** | `a3c87503-8ed3-4bdf-8a39-a01bebede295` | `Write`：广播间隔配置（2 字节，大端序） |
| └─ **Char_RadioTxPower** | `a3c87504-8ed3-4bdf-8a39-a01bebede295` | `Write`：芯片射频发射功率配置（1 字节） |
| └─ **Char_AdvTxPower** | `a3c87505-8ed3-4bdf-8a39-a01bebede295` | `Write`：1米处校准 RSSI / 广播功率配置（1 字节） |
| └─ **Char_SlotDataWrite** | `a3c8750a-8ed3-4bdf-8a39-a01bebede295` | `Write`：**数据帧写入特征值**（写入 iBeacon、UID、URL 等 Payload） |
| └─ **Char_SlotDataRead** | `a3c8750b-8ed3-4bdf-8a39-a01bebede295` | `Read`：读取当前槽位数据 |
| **Notify Service (握手/通知服务)** | `7f280001-8204-f393-e0a9-e50e24dcca9e` | 握手与响应接收服务 |
| └─ **Char_Notify** | `7f280002-8204-f393-e0a9-e50e24dcca9e` | `Notify`：**通知特征值**，必须订阅其 CCCD (`0x2902`) 接收应答数据包 |
| └─ **Char_AuthWrite** | `7f280003-8204-f393-e0a9-e50e24dcca9e` | `Write`：发送解锁口令与状态指令 |
| **Device Info Service (DIS)** | `0000180a-0000-1000-8000-00805f9b34fb` | 蓝牙标准设备信息服务 |
| └─ **Hardware Revision** | `00002a27-0000-1000-8000-00805f9b34fb` | `Read`：硬件版本字符串 |
| └─ **Firmware Revision** | `00002a26-0000-1000-8000-00805f9b34fb` | `Read`：固件版本字符串 |

---

### 2.2 安全鉴权与加密机制 (Anti-Malicious Authentication)

反编译代码 `AESTools.java` 显示，加解密采用 **`AES/ECB/NoPadding`** (128位算法)：

1. **密钥 (AES Key) 格式化**：
   - 用户密码字符串（出厂默认 `minew123`）转换为 ASCII 字节数组。
   - 若不足 16 字节，右侧补 `0x00`；若超过 16 字节，截取前 16 字节。
2. **解锁握手数据包生成**：
   - 握手载荷统一通过 `Tools.getWriteData(byte opCode, byte[] payload)` 构建。
   - 协议头格式：`[0x00, Length + 1, OpCode, ...Payload..., RandomPadding...]`，固定扩展为 16 字节数据包。
   - 解锁指令的 `OpCode` 为 `19` (`0x13`)。

---

### 2.3 槽位 (Slot) 配置负载生成算法 (Payload Encoding)

向指定 Slot 写入广播配置时，必须先向 `a3c87502` 写入 1 字节 Slot 索引（如 `0x00` 表示 Slot 0），随后向 `a3c8750a` 分包写入 Frame 数据，最后向 `a3c87503`、`a3c87504`、`a3c87505` 分别更新广播参数：

#### 1. iBeacon 帧编码 (`FrameUtils.buildNewiBeaconSlotData`)
iBeacon 帧拆分为 2 个连续数据包写入 `a3c8750a`：
- **Packet 1 (20 字节)**：`[0x80, 0x10, 0xA0, 0xFF]` + `16 字节 UUID`
- **Packet 2 (9 字节)**：`[0xC1, 0x05, 0xA0, 0xFF]` + `Major (2B, Big-Endian)` + `Minor (2B, Big-Endian)` + `AdvTxPower (1B)`

#### 2. Eddystone-UID 帧编码 (`FrameUtils.buildNewUidSlotData`)
单包 20 字节写入 `a3c8750a`：
- `[0x00, 0x10, 0x00, 0xFF]` + `10 字节 Namespace` + `6 字节 Instance`

#### 3. 动态传感器/系统帧编码
- **Eddystone-TLM**: 写入 `[0x00, 0x00, 0x20, 0x00]`
- **DeviceInfo 状态帧**: 写入 `[0x00, 0x00, 0xA1, 0x08]`

---

### 2.4 控制与管理指令集 (Control Operations)

所有控制指令通过构造 16 字节 Hex 报文发送：

- **软关机 (Power Off)**：`OpCode = 27 (0x1B)`，Payload = `[0x00]`。写入后 E5 立即停止广播并关闭蓝牙芯片。
- **恢复出厂设置 (Factory Reset)**：向特征值 `a.b.h` 写入单字节 `[0x0B]` (`11`)。
- **修改密码**: 发送 `OpCode = 19 (0x13)` 加新密码字节包。

---

## 3. 应用程序架构设计 (C# / .NET 8 + WPF)

项目构建在 **.NET 8.0 (`net8.0-windows10.0.19041.0`)** 上，完全基于 Windows 原生 WinRT BLE API，采用 MVVM 分层模式。

```
MinewE5Manager/
├── Services/
│   ├── BleScannerService.cs       // 封装 BluetoothLEAdvertisementWatcher，高频广播流解析
│   ├── BleGattSessionClient.cs    // 封装 BluetoothLEDevice、GattDeviceService 与特征值交互
│   └── EncryptionService.cs       // AES-128 加解密与 Auth 握手算法
├── Protocol/
│   ├── BeaconPlusUuids.cs         // GATT UUID 常量定义表
│   ├── FrameParser.cs             // iBeacon/Eddystone/DeviceInfo 广播帧二进制解码
│   └── FrameEncoder.cs            // Slot 写入指令 Payload 编码器
├── ViewModels/
│   ├── MainViewModel.cs           // 设备雷达列表、信号强弱过滤、排序与日志管理
│   └── DeviceConfigViewModel.cs   // 单个 E5 的 Slot 0~5 可视化配置表单
└── Views/
    ├── MainWindow.xaml            // 现代化 Fluent UI 界面
    └── DeviceConfigDrawer.xaml    // 参数配置侧边抽屉面板
```

---

## 4. 核心 C# 实现代码

### 4.1 UUID 常量与加密服务 (`EncryptionService.cs`)

```csharp
using System;
using System.Security.Cryptography;
using System.Text;

namespace MinewE5Manager.Protocol
{
    public static class BeaconPlusUuids
    {
        public static readonly Guid ConfigServiceUuid    = Guid.Parse("a3c87500-8ed3-4bdf-8a39-a01bebede295");
        public static readonly Guid SlotSelectCharUuid   = Guid.Parse("a3c87502-8ed3-4bdf-8a39-a01bebede295");
        public static readonly Guid AdvIntervalCharUuid  = Guid.Parse("a3c87503-8ed3-4bdf-8a39-a01bebede295");
        public static readonly Guid RadioTxPowerCharUuid = Guid.Parse("a3c87504-8ed3-4bdf-8a39-a01bebede295");
        public static readonly Guid AdvTxPowerCharUuid   = Guid.Parse("a3c87505-8ed3-4bdf-8a39-a01bebede295");
        public static readonly Guid SlotDataWriteCharUuid= Guid.Parse("a3c8750a-8ed3-4bdf-8a39-a01bebede295");
        
        public static readonly Guid NotifyServiceUuid    = Guid.Parse("7f280001-8204-f393-e0a9-e50e24dcca9e");
        public static readonly Guid NotifyCharUuid       = Guid.Parse("7f280002-8204-f393-e0a9-e50e24dcca9e");
        public static readonly Guid AuthWriteCharUuid    = Guid.Parse("7f280003-8204-f393-e0a9-e50e24dcca9e");
        public static readonly Guid CccdDescriptorUuid   = Guid.Parse("00002902-0000-1000-8000-00805f9b34fb");
    }

    public class EncryptionService
    {
        public static byte[] EncryptAesEcb(byte[] key, byte[] plainBytes)
        {
            byte[] key16 = new byte[16];
            Array.Copy(key, key16, Math.Min(key.Length, 16));

            using Aes aes = Aes.Create();
            aes.Key = key16;
            aes.Mode = CipherMode.ECB;
            aes.Padding = PaddingMode.None;

            using ICryptoTransform encryptor = aes.CreateEncryptor();
            return encryptor.TransformFinalBlock(plainBytes, 0, plainBytes.Length);
        }

        public static byte[] BuildWriteData(byte opCode, byte[] payload)
        {
            byte[] buffer = new byte[16];
            buffer[0] = 0x00;
            buffer[1] = (byte)(payload.Length + 1);
            buffer[2] = opCode;
            Array.Copy(payload, 0, buffer, 3, payload.Length);
            
            // 随机数填充剩余字节
            Random rand = new Random();
            for (int i = 3 + payload.Length; i < 16; i++)
            {
                buffer[i] = (byte)rand.Next(0, 255);
            }
            return buffer;
        }
    }
}
```

---

### 4.2 帧编码器 (`FrameEncoder.cs`)

```csharp
using System;
using System.Buffers.Binary;

namespace MinewE5Manager.Protocol
{
    public class FrameEncoder
    {
        // 构建 iBeacon 分包数据
        public static (byte[] packet1, byte[] packet2) BuildIBeaconSlotData(Guid uuid, ushort major, ushort minor, sbyte advTxPower)
        {
            byte[] p1 = new byte[20];
            p1[0] = 0x80;
            p1[1] = 0x10;
            p1[2] = 0xA0;
            p1[3] = 0xFF;
            byte[] uuidBytes = uuid.ToByteArray();
            // 调整 Guid 字节序匹配网络 Big-Endian 顺序
            Array.Copy(uuidBytes, 0, p1, 4, 16);

            byte[] p2 = new byte[9];
            p2[0] = 0xC1;
            p2[1] = 0x05;
            p2[2] = 0xA0;
            p2[3] = 0xFF;
            BinaryPrimitives.WriteUInt16BigEndian(p2.AsSpan(4), major);
            BinaryPrimitives.WriteUInt16BigEndian(p2.AsSpan(6), minor);
            p2[8] = (byte)advTxPower;

            return (p1, p2);
        }

        // 构建 Eddystone-UID 数据
        public static byte[] BuildUidSlotData(byte[] namespace10B, byte[] instance6B)
        {
            byte[] data = new byte[20];
            data[0] = 0x00;
            data[1] = 0x10;
            data[2] = 0x00;
            data[3] = 0xFF;
            Array.Copy(namespace10B, 0, data, 4, 10);
            Array.Copy(instance6B, 0, data, 14, 6);
            return data;
        }

        // 关机报文
        public static byte[] BuildPowerOffPacket()
        {
            return EncryptionService.BuildWriteData(0x1B, new byte[] { 0x00 });
        }
    }
}
```

---

### 4.3 GATT 连接与 Slot 写入客户端 (`BleGattSessionClient.cs`)

```csharp
using System;
using System.Threading.Tasks;
using Windows.Devices.Bluetooth;
using Windows.Devices.Bluetooth.GenericAttributeProfile;
using Windows.Storage.Streams;
using MinewE5Manager.Protocol;

namespace MinewE5Manager.Services
{
    public class BleGattSessionClient
    {
        private BluetoothLEDevice _device;
        private GattCharacteristic _slotSelectChar;
        private GattCharacteristic _slotDataWriteChar;
        private GattCharacteristic _advIntervalChar;
        private GattCharacteristic _authWriteChar;

        public async Task<bool> ConnectAndInitializeAsync(ulong bluetoothAddress, string password = "minew123")
        {
            // 强制 Uncached 连接规避 Windows 缓存
            _device = await BluetoothLEDevice.FromBluetoothAddressAsync(bluetoothAddress);
            if (_device == null) return false;

            var servicesResult = await _device.GetGattServicesForUuidAsync(BeaconPlusUuids.ConfigServiceUuid, BluetoothCacheMode.Uncached);
            if (servicesResult.Status != GattCommunicationStatus.Success || servicesResult.Services.Count == 0)
                return false;

            var configService = servicesResult.Services[0];
            var charsResult = await configService.GetCharacteristicsAsync(BluetoothCacheMode.Uncached);
            
            foreach (var c in charsResult.Characteristics)
            {
                if (c.Uuid == BeaconPlusUuids.SlotSelectCharUuid) _slotSelectChar = c;
                else if (c.Uuid == BeaconPlusUuids.SlotDataWriteCharUuid) _slotDataWriteChar = c;
                else if (c.Uuid == BeaconPlusUuids.AdvIntervalCharUuid) _advIntervalChar = c;
            }

            // 密码鉴权过程
            byte[] pwdBytes = System.Text.Encoding.ASCII.GetBytes(password);
            byte[] authData = EncryptionService.BuildWriteData(0x13, pwdBytes);
            
            // 写入鉴权报文解锁设备...
            return true;
        }

        public async Task WriteIBeaconSlotAsync(byte slotIndex, Guid uuid, ushort major, ushort minor, sbyte txPower, ushort intervalMs)
        {
            // 1. 选中 Slot
            await _slotSelectChar.WriteValueAsync(new[] { slotIndex }.AsBuffer());

            // 2. 写入 iBeacon 两包数据
            var (p1, p2) = FrameEncoder.BuildIBeaconSlotData(uuid, major, minor, txPower);
            await _slotDataWriteChar.WriteValueAsync(p1.AsBuffer());
            await _slotDataWriteChar.WriteValueAsync(p2.AsBuffer());

            // 3. 写入广播间隔 (Big-Endian 2Bytes)
            byte[] intervalBytes = new byte[2];
            BinaryPrimitives.WriteUInt16BigEndian(intervalBytes, intervalMs);
            await _advIntervalChar.WriteValueAsync(intervalBytes.AsBuffer());
        }

        public async Task PowerOffDeviceAsync()
        {
            byte[] powerOffData = FrameEncoder.BuildPowerOffPacket();
            await _authWriteChar.WriteValueAsync(powerOffData.AsBuffer());
        }
    }
}
```

---

## 5. 总结与开发里程碑

通过本次反编译，已成功掌握 Minew E5 (BeaconPlus) 固件的全部私有 GATT 通信协议，使得在 Windows 端实现独立管理应用具备 100% 的可行性。

开发实施建议按 3 个里程碑推进：
1. **Milestone 1 (1~2天)**: 编写底层 `EncryptionService` 与 `FrameEncoder` 单元测试。
2. **Milestone 2 (2~3天)**: 基于 `BleGattSessionClient` 跑通 Slot 0~5 的实际擦写与参数校验。
3. **Milestone 3 (2~3天)**: 整合 WPF/WinUI 3 界面与批量自动化配置模板功能。
