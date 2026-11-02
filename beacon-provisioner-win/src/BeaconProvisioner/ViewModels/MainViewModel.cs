using System.Collections.ObjectModel;
using BeaconProvisioner.Ble;
using BeaconProvisioner.Data;
using BeaconProvisioner.Models;
using BeaconProvisioner.Protocol;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.ViewModels;

/// <summary>
/// 主视图模型：登录 → 台账 → 未入库清单 → 扫描（mock）→ 连接 → 读回 → 写入（保存即生效）→ 连接校验 → 上传。
/// 真机 BLE 后续替换 `MockBleEnvironment`，业务状态机不变（docs/27 §6）。
/// </summary>
public sealed class MainViewModel : ObservableObject
{
    private readonly LedgerApi _api = new();
    private readonly AppSettingsStore _settings = new();
    private readonly PendingUploadStore _pending = new();
    private readonly DeviceWriteHistoryStore _writeHistory = new();
    private readonly List<BleDeviceInfo> _devices = new();
    private IBleEnvironment _ble;
    private bool _useMockBle;
    private bool _onlyUnwrittenDevices;
    private bool _onlyProjectDevices = true;

    private string _serverUrl = "http://127.0.0.1:8000";
    private string _username = "admin";
    private string _password = "";
    private string _statusText = "未登录";
    private string _mapVersion = "";
    private bool _isLoggedIn;
    private bool _isBusy;
    private string _scanText = "";
    private string _recordSummary = "";
    private string _currentSettingsText = "未连接设备";
    private bool _isConnected;
    private BeaconConfigItem? _selectedBeacon;
    private BeaconConfigItem? _selectedLedgerBeacon;
    private PendingUpload? _selectedPendingUpload;
    private DeploymentRecord? _selectedRecord;
    private DeploymentRecord? _selectedEditRecord;
    private double? _editTxPowerDbm;
    private int? _editIntervalMs;
    private BeaconConfigItem? _activeTarget;
    private double? _activeTxPower;
    private int? _activeInterval;
    private string _activeSource = "intake";
    private string? _selectedUnboundId;
    private BleDeviceInfo? _selectedDevice;
    private IBleSession? _session;
    private bool _verified;
    private DeploymentRecordRequest? _lastPayload;

    public MainViewModel()
    {
        _ble = _settings.BleMode == "real"
            ? new WinRtBleEnvironment()
            : new MockBleEnvironment();
        _ble.DevicesChanged += OnDevicesChanged;
        _useMockBle = _ble.IsMock;

        LoginCommand = new RelayCommand(LoginAsync, () => !IsBusy && !IsLoggedIn);
        RefreshCommand = new RelayCommand(RefreshAsync, () => IsLoggedIn && !IsBusy);
        CommitCommand = new RelayCommand(CommitAsync, () => IsLoggedIn && !IsBusy);
        LocateCommand = new RelayCommand(LocateAsync, () => IsLoggedIn && !IsBusy);
        ScanCommand = new RelayCommand(ScanAsync, () => IsLoggedIn && !IsBusy);
        ConnectCommand = new RelayCommand(ConnectAsync, () => !IsBusy && !IsConnected && SelectedDevice is not null);
        DisconnectCommand = new RelayCommand(DisconnectAsync, () => !IsBusy && IsConnected);
        WriteCommand = new RelayCommand(WriteAsync,
            () => !IsBusy && IsConnected && SelectedBeacon is not null && SelectedUnboundId is not null);
        WriteEditCommand = new RelayCommand(WriteEditAsync,
            () => !IsBusy && IsConnected && CanWriteEdit());
        VerifyCommand = new RelayCommand(VerifyAsync,
            () => !IsBusy && IsConnected && !_verified && _activeTarget is not null);
        UploadCommand = new RelayCommand(UploadAsync, () => !IsBusy && _verified && _lastPayload is not null);
        RetryPendingCommand = new RelayCommand(RetryPendingAsync, () => !IsBusy && _pending.Count > 0);
        SyncPendingUploads();
    }

    public ObservableCollection<BeaconConfigItem> Beacons { get; } = new();
    public ObservableCollection<string> UnboundIds { get; } = new();
    public ObservableCollection<DeploymentRecord> Records { get; } = new();
    public ObservableCollection<BleDeviceInfo> VisibleDevices { get; } = new();
    public ObservableCollection<PendingUpload> PendingUploads { get; } = new();

    public RelayCommand LoginCommand { get; }
    public RelayCommand RefreshCommand { get; }
    public RelayCommand CommitCommand { get; }
    public RelayCommand LocateCommand { get; }
    public RelayCommand ScanCommand { get; }
    public RelayCommand ConnectCommand { get; }
    public RelayCommand DisconnectCommand { get; }
    public RelayCommand WriteCommand { get; }
    public RelayCommand WriteEditCommand { get; }
    public RelayCommand VerifyCommand { get; }
    public RelayCommand UploadCommand { get; }
    public RelayCommand RetryPendingCommand { get; }

    public string AppTitle => _ble.IsMock
        ? "信标配置工具（Mock 蓝牙）"
        : "信标配置工具（实物 BLE）";

    public string IntakeTabTitle => _ble.IsMock ? "入库（Mock）" : "入库（实物）";

    public string ScanButtonText => _ble.IsMock ? "扫描（Mock）" : "扫描（BLE）";

    public string ModeHintText => _ble.IsMock
        ? "Mock 模式：3 台模拟信标，写入只作用于选中的信标"
        : "实物模式（WinRT）：写入只作用于选中的信标；保存后立即生效，无需重启";

    /// <summary>扫描列表过滤：只显示本机尚未写入过的信标。</summary>
    public bool OnlyUnwrittenDevices
    {
        get => _onlyUnwrittenDevices;
        set
        {
            if (Set(ref _onlyUnwrittenDevices, value))
            {
                ApplyDeviceFilter();
            }
        }
    }

    /// <summary>
    /// 扫描列表过滤：只显示本项目信标（带 FFF0 配置服务的 E5、台账 UUID、或出厂全 F 的未写参设备），
    /// 剔除周围其他项目的 iBeacon 与无关蓝牙设备。
    /// </summary>
    public bool OnlyProjectDevices
    {
        get => _onlyProjectDevices;
        set
        {
            if (Set(ref _onlyProjectDevices, value))
            {
                ApplyDeviceFilter();
            }
        }
    }

    /// <summary>蓝牙模式开关：true=Mock，false=实物 WinRT；切换立即生效（需先断开连接）。</summary>
    public bool UseMockBle
    {
        get => _useMockBle;
        set
        {
            if (value == _ble.IsMock)
            {
                return;
            }

            if (IsConnected)
            {
                StatusText = "请先断开连接，再切换蓝牙模式";
                Raise();
                return;
            }

            _ble.DevicesChanged -= OnDevicesChanged;
            _ble.StopScan();
            _ble = value ? new MockBleEnvironment() : new WinRtBleEnvironment();
            _ble.DevicesChanged += OnDevicesChanged;
            _settings.SaveBleMode(value ? "mock" : "real");
            _devices.Clear();
            VisibleDevices.Clear();
            SelectedDevice = null;
            ResetProgress();
            _useMockBle = value;
            Raise();
            Raise(nameof(AppTitle));
            Raise(nameof(IntakeTabTitle));
            Raise(nameof(ScanButtonText));
            Raise(nameof(ModeHintText));
            StatusText = value
                ? "已切换到 Mock 蓝牙（无需硬件）"
                : "已切换到实物 BLE（需蓝牙适配器与信标）";
            RefreshCommands();
            if (IsLoggedIn)
            {
                _ = ScanAsync();
            }
        }
    }

    private void OnDevicesChanged(object? sender, EventArgs e)
    {
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is null)
        {
            return;
        }

        if (!dispatcher.CheckAccess())
        {
            dispatcher.BeginInvoke(() => SyncDevices());
        }
        else
        {
            SyncDevices();
        }
    }

    public string ServerUrl
    {
        get => _serverUrl;
        set => Set(ref _serverUrl, value);
    }

    public string Username
    {
        get => _username;
        set => Set(ref _username, value);
    }

    public string Password
    {
        get => _password;
        set => Set(ref _password, value);
    }

    public bool IsLoggedIn
    {
        get => _isLoggedIn;
        private set
        {
            if (Set(ref _isLoggedIn, value))
            {
                Raise(nameof(LoginButtonText));
                RefreshCommands();
            }
        }
    }

    public bool IsBusy
    {
        get => _isBusy;
        private set
        {
            if (Set(ref _isBusy, value))
            {
                RefreshCommands();
            }
        }
    }

    public string StatusText
    {
        get => _statusText;
        private set => Set(ref _statusText, value);
    }

    public string MapVersion
    {
        get => _mapVersion;
        private set => Set(ref _mapVersion, value);
    }

    public string ScanText
    {
        get => _scanText;
        set => Set(ref _scanText, value);
    }

    public string RecordSummary
    {
        get => _recordSummary;
        private set => Set(ref _recordSummary, value);
    }

    public string CurrentSettingsText
    {
        get => _currentSettingsText;
        private set => Set(ref _currentSettingsText, value);
    }

    public bool IsConnected
    {
        get => _isConnected;
        private set
        {
            if (Set(ref _isConnected, value))
            {
                RefreshCommands();
            }
        }
    }

    public BeaconConfigItem? SelectedBeacon
    {
        get => _selectedBeacon;
        private set
        {
            if (Set(ref _selectedBeacon, value))
            {
                Raise(nameof(SelectedDetails));
                RefreshCommands();
            }
        }
    }

    public BeaconConfigItem? SelectedLedgerBeacon
    {
        get => _selectedLedgerBeacon;
        set
        {
            if (Set(ref _selectedLedgerBeacon, value))
            {
                Raise(nameof(LedgerDetails));
            }
        }
    }

    public string LedgerDetails => SelectedLedgerBeacon is null
        ? "请在左侧选择一条信标记录"
        : BeaconDetailFormatter.Format(SelectedLedgerBeacon);

    public PendingUpload? SelectedPendingUpload
    {
        get => _selectedPendingUpload;
        set
        {
            if (Set(ref _selectedPendingUpload, value))
            {
                Raise(nameof(PendingDetails));
            }
        }
    }

    public string PendingDetails => SelectedPendingUpload is null
        ? "请在左侧选择一条待上传记录"
        : PendingUploadDetailFormatter.Format(SelectedPendingUpload);

    public DeploymentRecord? SelectedRecord
    {
        get => _selectedRecord;
        set
        {
            if (Set(ref _selectedRecord, value))
            {
                Raise(nameof(RecordDetails));
            }
        }
    }

    public string RecordDetails => SelectedRecord is null
        ? "请在左侧选择一条上传记录"
        : DeploymentRecordDetailFormatter.Format(
            SelectedRecord,
            Beacons.FirstOrDefault(b => b.BeaconId == SelectedRecord.BeaconId));

    public IReadOnlyList<double> PowerLevels => BeaconCodec.PowerLevelsDbm;

    public DeploymentRecord? SelectedEditRecord
    {
        get => _selectedEditRecord;
        set
        {
            if (!Set(ref _selectedEditRecord, value))
            {
                return;
            }

            if (value is not null)
            {
                var planned = Beacons.FirstOrDefault(b => b.BeaconId == value.BeaconId);
                var power = value.Written.TxPower;
                if (power == 0 && planned?.TxPower is not null)
                {
                    power = planned.TxPower.Value;
                }

                var interval = value.Written.Interval;
                if (interval == 0 && planned?.BroadcastInterval is not null)
                {
                    interval = planned.BroadcastInterval.Value;
                }

                EditTxPowerDbm = power;
                EditIntervalMs = interval;
            }

            Raise(nameof(EditDetails));
            ResetProgress();
            RefreshCommands();
        }
    }

    public double? EditTxPowerDbm
    {
        get => _editTxPowerDbm;
        set
        {
            if (Set(ref _editTxPowerDbm, value))
            {
                RefreshCommands();
            }
        }
    }

    public int? EditIntervalMs
    {
        get => _editIntervalMs;
        set
        {
            if (Set(ref _editIntervalMs, value))
            {
                RefreshCommands();
            }
        }
    }

    public string EditDetails
    {
        get
        {
            if (SelectedEditRecord is null)
            {
                return "请在左侧选择一条已入库记录；仅发射功率与广播间隔可编辑";
            }

            var planned = Beacons.FirstOrDefault(b => b.BeaconId == SelectedEditRecord.BeaconId);
            return DeploymentRecordDetailFormatter.Format(SelectedEditRecord, planned)
                   + "\n\n可编辑：发射功率 / 广播间隔（写入后更新真实台账，source=field_edit）";
        }
    }

    public string? SelectedUnboundId
    {
        get => _selectedUnboundId;
        set
        {
            if (Set(ref _selectedUnboundId, value))
            {
                SelectedBeacon = Beacons.FirstOrDefault(b => b.BeaconId == value);
            }
        }
    }

    public BleDeviceInfo? SelectedDevice
    {
        get => _selectedDevice;
        set
        {
            if (Set(ref _selectedDevice, value))
            {
                RefreshCommands();
            }
        }
    }

    public string PendingText => $"待上传：{_pending.Count} 条";

    public string LoginButtonText => IsLoggedIn ? "已登录" : "登录";

    public string SelectedDetails => SelectedBeacon is null
        ? "未选择信标"
        : BeaconDetailFormatter.Format(SelectedBeacon);

    private void RefreshCommands()
    {
        LoginCommand.RaiseCanExecuteChanged();
        RefreshCommand.RaiseCanExecuteChanged();
        CommitCommand.RaiseCanExecuteChanged();
        LocateCommand.RaiseCanExecuteChanged();
        ScanCommand.RaiseCanExecuteChanged();
        ConnectCommand.RaiseCanExecuteChanged();
        DisconnectCommand.RaiseCanExecuteChanged();
        WriteCommand.RaiseCanExecuteChanged();
        WriteEditCommand.RaiseCanExecuteChanged();
        VerifyCommand.RaiseCanExecuteChanged();
        UploadCommand.RaiseCanExecuteChanged();
        RetryPendingCommand.RaiseCanExecuteChanged();
    }

    private void SyncPendingUploads()
    {
        var keep = SelectedPendingUpload?.BeaconId;
        PendingUploads.Clear();
        foreach (var item in _pending.Items)
        {
            PendingUploads.Add(item);
        }

        SelectedPendingUpload =
            PendingUploads.FirstOrDefault(i => i.BeaconId == keep)
            ?? PendingUploads.FirstOrDefault();
        Raise(nameof(PendingDetails));
        Raise(nameof(PendingText));
        RefreshCommands();
    }

    /// <summary>窗口加载后调用：本地有 7 天内有效的登录缓存则免密登录。</summary>
    public async Task InitializeAsync()
    {
        if (!_settings.TryLoadSession(out var serverUrl, out var username, out var password))
        {
            return;
        }

        ServerUrl = serverUrl;
        Username = username;
        Password = password;
        await LoginAsync(useCachedSession: true);
    }

    private Task LoginAsync() => LoginAsync(useCachedSession: false);

    private async Task LoginAsync(bool useCachedSession)
    {
        if (string.IsNullOrWhiteSpace(ServerUrl) || string.IsNullOrWhiteSpace(Username))
        {
            StatusText = "请填写服务器地址与用户名";
            return;
        }

        var serverUrl = ServerUrl.Trim();
        var username = Username.Trim();
        var password = Password;

        IsBusy = true;
        try
        {
            _api.BaseUrl = serverUrl;
            await _api.LoginAsync(username, password);
            if (_api.MustChangePassword)
            {
                _settings.ClearSession();
                Password = "";
                StatusText = "该账号尚未完成首次改密，请先在管理后台改密后再登录";
                return;
            }

            IsLoggedIn = true;
            Password = "";
            _settings.SaveSession(serverUrl, username, password);
            StatusText = useCachedSession
                ? "已使用本地缓存登录（7 天内免密，过期后需重新输入密码）"
                : "登录成功";
            Raise(nameof(PendingText));
            await RefreshAsync();
            await ScanAsync();
        }
        catch (LedgerApiException ex)
        {
            // 仅凭据错误才清缓存：网络/服务端故障不应让用户重新输密码
            if (ex.StatusCode is 401 or 403)
            {
                _settings.ClearSession();
            }

            StatusText = useCachedSession
                ? $"本地缓存登录失败：{ex.Message}（{ex.Code}），请输入密码重新登录"
                : $"登录失败：{ex.Message}（{ex.Code}）";
        }
        catch (Exception ex)
        {
            StatusText = useCachedSession
                ? $"本地缓存登录失败：{ex.Message}，请检查服务器后重新登录"
                : $"登录失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task RefreshAsync()
    {
        IsBusy = true;
        try
        {
            var ledger = await _api.GetBeaconsAsync();
            MapVersion = ledger.MapVersion;
            Beacons.Clear();
            foreach (var item in ledger.Items)
            {
                Beacons.Add(item);
            }

            var errors = LedgerValidator.Validate(ledger.Items);
            if (errors.Count > 0)
            {
                StatusText = $"台账校验失败（{errors.Count} 项）：{string.Join("；", errors.Take(3))}";
                IsLoggedIn = false;
                return;
            }

            await LoadRecordsAsync();
            StatusText = $"台账 {ledger.Items.Count} 枚 · 版本 {ledger.MapVersion} · 校验通过";
        }
        catch (LedgerApiException ex)
        {
            StatusText = $"拉取台账失败：{ex.Message}（{ex.Code}）";
        }
        catch (Exception ex)
        {
            StatusText = $"拉取台账失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task LoadRecordsAsync()
    {
        var response = await _api.GetRecordsAsync();
        var summary = response.Summary;
        Records.Clear();
        UnboundIds.Clear();
        foreach (var record in response.Items)
        {
            Records.Add(record);
        }

        foreach (var id in summary.Missing)
        {
            UnboundIds.Add(id);
        }

        RecordSummary =
            $"计划 {summary.Total} · 已入库 {summary.Recorded} · 已校验 {summary.Verified} · 未入库 {summary.Missing.Count} · {PendingText}";
        Raise(nameof(PendingText));
    }

    private Task LocateAsync()
    {
        var text = ScanText.Trim();
        if (text.Length == 0)
        {
            StatusText = "请输入或扫描 beaconId";
            return Task.CompletedTask;
        }

        var match = Beacons.FirstOrDefault(b =>
            string.Equals(b.BeaconId, text, StringComparison.OrdinalIgnoreCase));
        if (match is null)
        {
            SelectedBeacon = null;
            StatusText = $"不是本场地信标标签：{text}";
            return Task.CompletedTask;
        }

        SelectedBeacon = match;
        if (UnboundIds.Contains(match.BeaconId))
        {
            SelectedUnboundId = match.BeaconId;
        }

        StatusText = UnboundIds.Contains(match.BeaconId)
            ? $"{match.BeaconId} 未入库，已选中"
            : $"{match.BeaconId} 已在真实部署台账中";
        return Task.CompletedTask;
    }

    private async Task ScanAsync()
    {
        try
        {
            await _ble.EnsureReadyAsync();
            _ble.StartScan();
            SyncDevices();
            StatusText = _ble.IsMock
                ? $"Mock 扫描：{_devices.Count} 台（出厂三元组相同，按 MAC 区分）"
                : $"实物扫描中（WinRT）：已发现 {VisibleDevices.Count} 台本项目信标，等待 iBeacon / FFF0 广播…";
        }
        catch (Exception ex)
        {
            StatusText = $"扫描失败：{ex.Message}（请检查蓝牙适配器与系统蓝牙开关）";
        }
    }

    private void SyncDevices()
    {
        _devices.Clear();
        foreach (var device in _ble.Devices.OrderByDescending(d => d.Rssi))
        {
            var writtenAt = _writeHistory.GetLastWrittenAt(device.Mac);
            device.WriteTimeText = writtenAt?.ToString("yyyy-MM-dd HH:mm:ss") ?? "";
            _devices.Add(device);
        }

        ApplyDeviceFilter();
    }

    private void ApplyDeviceFilter()
    {
        var keepMac = SelectedDevice?.Mac;
        var projectUuids = ProjectUuids();
        VisibleDevices.Clear();
        foreach (var device in _devices)
        {
            if (OnlyProjectDevices && !IsProjectDevice(device, projectUuids))
            {
                continue;
            }

            if (OnlyUnwrittenDevices && device.WriteTimeText.Length > 0)
            {
                continue;
            }

            VisibleDevices.Add(device);
        }

        SelectedDevice = VisibleDevices.FirstOrDefault(d => d.Mac == keepMac) ?? VisibleDevices.FirstOrDefault();
        RefreshCommands();
    }

    /// <summary>本项目信标的 UUID 集合：系统 UUID + 台账里出现过的 UUID。</summary>
    private HashSet<string> ProjectUuids()
    {
        var set = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
        {
            LedgerValidator.SystemUuid,
        };
        foreach (var beacon in Beacons)
        {
            if (!string.IsNullOrWhiteSpace(beacon.Uuid))
            {
                set.Add(beacon.Uuid);
            }
        }

        return set;
    }

    /// <summary>
    /// 是否属于本项目：带 FFF0 配置服务（本工具能连），或 UUID 命中台账/系统 UUID，
    /// 或 UUID 为全 F / 全 0（出厂未写参，必须放行才能首次入库）。
    /// </summary>
    private static bool IsProjectDevice(BleDeviceInfo device, HashSet<string> projectUuids)
    {
        if (device.IsE5)
        {
            return true;
        }

        if (device.Uuid.Length == 0 || IsUnprovisionedUuid(device.Uuid))
        {
            return true;
        }

        return projectUuids.Contains(device.Uuid);
    }

    private static bool IsUnprovisionedUuid(string uuid)
    {
        var hex = uuid.Replace("-", "");
        return hex.All(c => c is 'F' or 'f' or '0');
    }

    private async Task ConnectAsync()
    {
        if (SelectedDevice is null)
        {
            return;
        }

        IsBusy = true;
        try
        {
            CloseSession();
            var newSession = await _ble.ConnectAsync(SelectedDevice);
            _session = newSession;
            IsConnected = true;
            ResetProgress();
            var restored = RestoreWrittenTarget(newSession.Device.Mac);

            // GATT 读写内部同步等待 WinRT 回调；放到线程池执行，避免 UI 线程参与等待而死锁/挂起。
            var current = await Task.Run(() => newSession.ReadCurrent());
            ShowCurrent(current);

            // 广播可能拿不到名称（部分固件只在 FFF9 里保存），连接后用读回值补全列表。
            if (SelectedDevice is { } scanned && current.Name.Length > 0)
            {
                scanned.Name = current.Name;
                ApplyDeviceFilter();
            }

            StatusText = restored is not null
                ? $"已连接 {SelectedDevice?.DisplayName}；本机记录该信标已写入 {restored} 且未上传，可点「2 校验」"
                : $"已连接 {SelectedDevice?.DisplayName}，请选择目标 beaconId";
        }
        catch (Exception ex)
        {
            IsConnected = false;
            StatusText = $"连接失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private static void LogBle(string message)
    {
        try
        {
            var dir = System.IO.Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData),
                "BeaconProvisioner");
            System.IO.Directory.CreateDirectory(dir);
            System.IO.File.AppendAllText(
                System.IO.Path.Combine(dir, "ble.log"),
                $"{DateTime.Now:yyyy-MM-dd HH:mm:ss} {message}{Environment.NewLine}");
        }
        catch (Exception)
        {
            // 日志失败不影响主流程
        }
    }

    private bool SessionAlive()
    {
        try
        {
            return _session is not null && _session.IsAlive;
        }
        catch (Exception)
        {
            return false;
        }
    }

    private void CloseSession()
    {
        try
        {
            _session?.Dispose();
        }
        catch (Exception)
        {
            // 已失效的会话可能重复 Dispose，忽略
        }

        _session = null;
    }

    private void DropSession(string message)
    {
        CloseSession();
        IsConnected = false;
        CurrentSettingsText = "未连接设备";
        StatusText = message;
        ResetProgress();
    }

    private Task DisconnectAsync()
    {
        CloseSession();
        IsConnected = false;
        ResetProgress();
        CurrentSettingsText = "未连接设备";
        StatusText = "已断开连接";
        return Task.CompletedTask;
    }

    /// <summary>连接的设备若「已写入且未上传」，按本机写入记录恢复写入目标，用于对照展示与校验。</summary>
    private string? RestoreWrittenTarget(string mac)
    {
        if (!_writeHistory.IsWrittenPendingUpload(mac))
        {
            return null;
        }

        var record = _writeHistory.GetRecord(mac)!;
        var target = Beacons.FirstOrDefault(b => b.BeaconId == record.BeaconId);
        if (target is null)
        {
            return null;
        }

        _activeTarget = target;
        _activeTxPower = record.TxPowerDbm ?? target.TxPower;
        _activeInterval = record.IntervalMs ?? target.BroadcastInterval;
        _activeSource = record.Source;
        if (UnboundIds.Contains(target.BeaconId))
        {
            _selectedUnboundId = target.BeaconId;
            Raise(nameof(SelectedUnboundId));
        }

        SelectedBeacon = target;
        RefreshCommands();
        return target.BeaconId;
    }

    private void ShowCurrent(BeaconCurrentState state)
    {
        var readback = $"""
            【蓝牙读回】
            MAC：{_session?.Device.Mac ?? "—"}
            名称：{state.Name}
            UUID：{state.Uuid}
            Major/Minor：{state.Major} / {state.Minor}
            功率：{state.TxPowerDbm} dBm（码 {state.PowerCode}）
            间隔：{state.IntervalMs} ms
            校准 RSSI：{state.CalibratedRssi}
            模式：{state.Mode}
            """;
        var target = _activeTarget;
        if (target is null)
        {
            CurrentSettingsText = readback;
            return;
        }

        static string Mark(bool same) => same ? "一致" : "不一致";
        var expectedUuid = target.Uuid ?? "—";
        var power = _activeTxPower;
        var interval = _activeInterval;
        var expected = $"""
            【写入配置：{target.BeaconId}（{(_activeSource == "field_edit" ? "现场编辑" : "入库")}）】
            名称：{target.DeviceName}（{Mark(state.Name == target.DeviceName)}）
            UUID：{expectedUuid}（{Mark(string.Equals(state.Uuid, expectedUuid, StringComparison.OrdinalIgnoreCase))}）
            Major/Minor：{target.Major} / {target.Minor}（{Mark(state.Major == target.Major && state.Minor == target.Minor)}）
            功率：{power} dBm（{Mark(power is not null && Math.Abs(state.TxPowerDbm - power.Value) < 0.01)}）
            间隔：{interval} ms（{Mark(state.IntervalMs == interval)}）
            """;
        CurrentSettingsText = expected + "\n\n" + readback;
    }

    private void ResetProgress()
    {
        _verified = false;
        _lastPayload = null;
        _activeTarget = null;
        _activeTxPower = null;
        _activeInterval = null;
        _activeSource = "intake";
        RefreshCommands();
    }

    private async Task WriteAsync()
    {
        var target = SelectedBeacon;
        if (_session is null || target is null || target.Uuid is null || target.Major is null ||
            target.Minor is null || target.TxPower is null || target.BroadcastInterval is null)
        {
            StatusText = "请先连接设备并选择未入库 beaconId";
            return;
        }

        await DoWriteAsync(target, target.TxPower.Value, target.BroadcastInterval.Value, "intake");
    }

    private bool CanWriteEdit()
    {
        if (SelectedEditRecord is null || EditTxPowerDbm is null || EditIntervalMs is null)
        {
            return false;
        }

        var planned = Beacons.FirstOrDefault(b => b.BeaconId == SelectedEditRecord.BeaconId);
        return planned?.Uuid is not null && planned.Major is not null && planned.Minor is not null
               && BeaconCodec.IsValidInterval(EditIntervalMs.Value)
               && BeaconCodec.PowerCodeFor(EditTxPowerDbm.Value) >= 0;
    }

    private async Task WriteEditAsync()
    {
        var record = SelectedEditRecord;
        var target = record is null ? null : Beacons.FirstOrDefault(b => b.BeaconId == record.BeaconId);
        if (_session is null || target is null || target.Uuid is null || target.Major is null ||
            target.Minor is null || EditTxPowerDbm is null || EditIntervalMs is null)
        {
            StatusText = "请先连接设备，并选择可编辑的已入库记录（仅功率/间隔可改）";
            return;
        }

        if (!BeaconCodec.IsValidInterval(EditIntervalMs.Value))
        {
            StatusText = "广播间隔需在 100~10000 ms";
            return;
        }

        if (BeaconCodec.PowerCodeFor(EditTxPowerDbm.Value) < 0)
        {
            StatusText = $"功率 {EditTxPowerDbm} dBm 不在设备档位列表";
            return;
        }

        await DoWriteAsync(target, EditTxPowerDbm.Value, EditIntervalMs.Value, "field_edit");
    }

    private async Task DoWriteAsync(
        BeaconConfigItem target,
        double txPowerDbm,
        int intervalMs,
        string source)
    {
        _activeTarget = target;
        _activeTxPower = txPowerDbm;
        _activeInterval = intervalMs;
        _activeSource = source;

        var connectedMac = _session?.Device.Mac ?? SelectedDevice?.Mac;
        var device = _devices.FirstOrDefault(d => d.Mac == connectedMac) ?? SelectedDevice;
        if (device is null)
        {
            DropSession("请先扫描并选择要写入的信标");
            return;
        }

        IsBusy = true;
        try
        {
            // 写入前刷新 GATT 会话：规避 WinRT 闲置回收/旧会话特征失效
            StatusText = "正在刷新设备连接…";
            CloseSession();
            try
            {
                _session = await _ble.ConnectAsync(device);
                IsConnected = true;
            }
            catch (Exception ex)
            {
                DropSession($"重新连接设备失败：{ex.Message}；请重新「扫描 → 连接设备」后再写入");
                return;
            }

            var deviceMac = _session.Device.Mac;
            var request = new BeaconWriteRequest
            {
                Uuid = target.Uuid!,
                Major = target.Major!.Value,
                Minor = target.Minor!.Value,
                Name = target.DeviceName,
                TxPowerDbm = txPowerDbm,
                IntervalMs = intervalMs,
            };

            var writeResult = await Task.Run(() => _session.Write(request));
            if (!writeResult.Success && writeResult.NeedsReconnect && SelectedDevice is not null)
            {
                // GATT 会话可能被系统回收；自动重连一次并重试写入
                StatusText = "GATT 会话已失效，正在重新连接并重试写入…";
                CloseSession();
                try
                {
                    var retrySession = await _ble.ConnectAsync(SelectedDevice);
                    _session = retrySession;
                    IsConnected = true;
                    deviceMac = _session.Device.Mac;
                    writeResult = await Task.Run(() => _session.Write(request));
                }
                catch (Exception ex)
                {
                    DropSession($"自动重连失败：{ex.Message}；请重新「扫描 → 连接设备」后再写入");
                    return;
                }
            }

            if (!writeResult.Success)
            {
                if (writeResult.NeedsReconnect)
                {
                    DropSession($"步骤 1/3 写入失败：{writeResult.Message}");
                }
                else
                {
                    ResetProgress();
                    StatusText = $"步骤 1/3 写入失败：{writeResult.Message}";
                }

                return;
            }

            StatusText = "参数已写入，正在提交 FFFF 保存…";
            try
            {
                await _session.CommitAsync("minew123");
            }
            catch (ObjectDisposedException ex)
            {
                // 提交阶段会话失效：参数通常已在设备内存中，重连后再提交一次
                LogBle($"{deviceMac} 提交 FFFF 时会话失效：{ex}");
                StatusText = "保存阶段连接失效，正在重新连接并重新提交…";
                CloseSession();
                _session = await _ble.ConnectAsync(device);
                IsConnected = true;
                await _session.CommitAsync("minew123");
            }

            _writeHistory.MarkWritten(deviceMac, DateTime.Now, target.BeaconId, source, txPowerDbm, intervalMs);

            // E5 保存后自动生效，无需重启；部分固件提交 FFFF 后会主动断开，读回失败时重连再读一次
            var readOk = false;
            try
            {
                if (SessionAlive())
                {
                    ShowCurrent(await Task.Run(() => _session!.ReadCurrent()));
                    readOk = true;
                }
            }
            catch (Exception ex)
            {
                LogBle($"{deviceMac} 保存后读回失败：{ex.Message}");
            }

            if (!readOk)
            {
                try
                {
                    StatusText = "保存完成，正在重新连接设备以读回参数…";
                    CloseSession();
                    _session = await _ble.ConnectAsync(device);
                    IsConnected = true;
                    ShowCurrent(await Task.Run(() => _session.ReadCurrent()));
                    readOk = true;
                }
                catch (Exception ex)
                {
                    LogBle($"{deviceMac} 保存后重连读回失败：{ex.Message}");
                }
            }

            // 刚写入的信标要能继续校验，确保不被「只显示未写入」过滤掉
            if (OnlyUnwrittenDevices)
            {
                OnlyUnwrittenDevices = false;
            }

            SyncDevices();
            if (readOk)
            {
                StatusText = $"{target.BeaconId} 已写入并保存（立即生效）；请点「2 校验」";
            }
            else
            {
                CloseSession();
                IsConnected = false;
                CurrentSettingsText = "未连接设备";
                StatusText = $"{target.BeaconId} 已写入并保存（立即生效），设备已断开；请选中该信标「连接设备」后点「2 校验」";
            }
        }
        catch (Exception ex)
        {
            if (_session is not null && !SessionAlive())
            {
                DropSession($"步骤 1/3 写入/保存失败：连接已失效，请重新连接后再试（{ex.Message}）");
            }
            else
            {
                ResetProgress();
                StatusText = $"步骤 1/3 写入/保存异常：{ex.Message}";
            }
        }
        finally
        {
            IsBusy = false;
            RefreshCommands();
        }
    }

    private async Task VerifyAsync()
    {
        var target = _activeTarget;
        if (_session is null || !IsConnected || target is null)
        {
            StatusText = "请先选中已写入且未上传的信标并「连接设备」，再校验";
            return;
        }

        if (!SessionAlive() && SelectedDevice is null)
        {
            DropSession("连接已失效（信标可能已断电/重启，或连接超过 5 分钟自动断开），请重新「扫描 → 连接设备」后再校验");
            return;
        }

        try
        {
            if (!SessionAlive())
            {
                // 提交 FFFF 后固件会释放特征，旧会话已不可用：先自动重连再读回，
                // 而不是让用户重新「扫描 → 连接设备」。
                if (SelectedDevice is null)
                {
                    DropSession("连接已失效（信标可能已断电/重启，或连接超过 5 分钟自动断开），请重新「扫描 → 连接设备」后再校验");
                    return;
                }

                StatusText = "连接已失效，正在重新连接设备后校验…";
                CloseSession();
                IsConnected = false;
                _session = await _ble.ConnectAsync(SelectedDevice);
                IsConnected = true;
            }

            BeaconCurrentState current;
            try
            {
                current = await Task.Run(() => _session.ReadCurrent());
            }
            catch (Exception ex) when (SelectedDevice is not null)
            {
                // 提交 FFFF 后固件常会主动断连（特征全部释放），读回必然失败：
                // 自动重连一次再读，避免把“连接断了”误报成“校验不通过”。
                LogBle($"校验读回失败，尝试自动重连：{ex.Message}");
                StatusText = "读回失败，正在重新连接设备后重试…";
                CloseSession();
                IsConnected = false;
                var retrySession = await _ble.ConnectAsync(SelectedDevice);
                _session = retrySession;
                IsConnected = true;
                current = await Task.Run(() => retrySession.ReadCurrent());
            }

            ShowCurrent(current);
            var verify = Verify(target, current, _activeTxPower, _activeInterval);
            if (!verify.Passed)
            {
                StatusText = $"步骤 2/3 校验未通过：{verify.Message}（可重新写入后再校验）";
                return;
            }

            _lastPayload = BuildRecordPayload(target, current, verify);
            _verified = true;
            StatusText = "步骤 2/3 校验通过（full-readback）；请点「3 上传」";
        }
        catch (Exception ex)
        {
            DropSession($"步骤 2/3 校验失败：{ex.Message}；请重新「扫描 → 连接设备」后再校验");
        }
        finally
        {
            RefreshCommands();
        }
    }

    private async Task UploadAsync()
    {
        var payload = _lastPayload;
        var target = _activeTarget;
        if (payload is null || target is null)
        {
            StatusText = "没有待上传的校验结果";
            return;
        }

        IsBusy = true;
        try
        {
            await _api.PutDeploymentRecordAsync(target.BeaconId, payload);
            _pending.Remove(target.BeaconId);
            if (!string.IsNullOrEmpty(payload.DeviceMac))
            {
                _writeHistory.MarkUploaded(payload.DeviceMac);
            }

            StatusText = $"步骤 3/3：{target.BeaconId} 已上传真实部署台账";
        }
        catch (Exception ex)
        {
            _pending.Add(new PendingUpload
            {
                BeaconId = target.BeaconId,
                Request = payload,
                CreatedAt = DateTime.Now.ToString("s"),
                LastError = ex.Message,
            });
            StatusText = $"步骤 3/3 上传失败（{ex.Message}）；已加入本地队列，可在记录页查看并重试";
        }
        finally
        {
            SyncPendingUploads();
            SelectedUnboundId = null;
            ResetProgress();
            await LoadRecordsAsync();
            IsBusy = false;
        }
    }

    private sealed class VerifyOutcome
    {
        public bool Passed { get; init; }
        public string Message { get; init; } = "";
    }

    private static VerifyOutcome Verify(
        BeaconConfigItem target,
        BeaconCurrentState current,
        double? expectedTxPower,
        int? expectedInterval)
    {
        var expectedUuid = BeaconCodec.UuidFromBytes(BeaconCodec.UuidToBytes(target.Uuid!));
        if (!string.Equals(current.Uuid, expectedUuid, StringComparison.OrdinalIgnoreCase))
        {
            return new VerifyOutcome { Passed = false, Message = $"UUID 不一致：{current.Uuid}" };
        }

        if (current.Major != target.Major || current.Minor != target.Minor)
        {
            return new VerifyOutcome
            {
                Passed = false,
                Message = $"三元组不一致：{current.Major}/{current.Minor}",
            };
        }

        if (!string.Equals(current.Name, target.DeviceName, StringComparison.Ordinal))
        {
            return new VerifyOutcome { Passed = false, Message = $"名称不一致：{current.Name}" };
        }

        if (expectedTxPower is not null && Math.Abs(current.TxPowerDbm - expectedTxPower.Value) > 0.01)
        {
            return new VerifyOutcome
            {
                Passed = false,
                Message = $"功率不一致：{current.TxPowerDbm} dBm",
            };
        }

        if (expectedInterval is not null && current.IntervalMs != expectedInterval.Value)
        {
            return new VerifyOutcome
            {
                Passed = false,
                Message = $"间隔不一致：{current.IntervalMs} ms",
            };
        }

        return new VerifyOutcome { Passed = true, Message = "full-readback 一致" };
    }

    private DeploymentRecordRequest BuildRecordPayload(
        BeaconConfigItem target,
        BeaconCurrentState current,
        VerifyOutcome verify)
    {
        return new DeploymentRecordRequest
        {
            MapVersion = MapVersion,
            DeviceMac = _session?.Device.Mac,
            DeviceName = target.DeviceName,
            NameWrite = _activeSource == "field_edit" ? "unchanged" : "written",
            Operator = _api.Username,
            Source = _activeSource,
            Written = new WrittenValues
            {
                Uuid = target.Uuid!,
                Major = target.Major!.Value,
                Minor = target.Minor!.Value,
                Name = target.DeviceName,
                TxPower = _activeTxPower ?? target.TxPower!.Value,
                Interval = _activeInterval ?? target.BroadcastInterval!.Value,
            },
            Verify = new VerifyResult
            {
                Passed = verify.Passed,
                Mode = "full-readback",
                Platform = "windows",
                Observed = new ObservedValues
                {
                    Uuid = current.Uuid,
                    Major = current.Major,
                    Minor = current.Minor,
                    Name = current.Name,
                    TxPower = current.TxPowerDbm,
                    Interval = current.IntervalMs,
                },
            },
        };
    }

    private async Task RetryPendingAsync()
    {
        IsBusy = true;
        var succeeded = 0;
        string? firstError = null;
        try
        {
            foreach (var item in _pending.Items.ToList())
            {
                try
                {
                    await _api.PutDeploymentRecordAsync(item.BeaconId, item.Request);
                    _pending.Remove(item.BeaconId);
                    succeeded++;
                }
                catch (Exception ex)
                {
                    // 保留在队列，下次重试；记录错误供详情面板展示
                    firstError ??= ex.Message;
                    _pending.SetLastError(item.BeaconId, ex.Message);
                }
            }

            StatusText = firstError is null
                ? $"已补传 {succeeded} 条；剩余 {_pending.Count} 条"
                : succeeded > 0
                    ? $"已补传 {succeeded} 条；{firstError}（剩余 {_pending.Count} 条）"
                    : $"补传失败：{firstError}（剩余 {_pending.Count} 条）";
            SyncPendingUploads();
            await LoadRecordsAsync();
        }
        finally
        {
            IsBusy = false;
        }
    }

    private async Task CommitAsync()
    {
        IsBusy = true;
        try
        {
            var result = await _api.CommitAsync(_api.Username);
            RecordSummary =
                $"commit：{result.Status}（轮次 {result.RoundId}）· 已入库 {result.Recorded}/{result.Total}";
            StatusText = result.Status == "committed"
                ? "本轮完整性校验通过"
                : $"本轮未完成：缺 {result.Missing.Count} 台、未校验 {result.Unverified.Count} 台";
            await LoadRecordsAsync();
        }
        catch (LedgerApiException ex)
        {
            StatusText = $"commit 失败：{ex.Message}（{ex.Code}）";
        }
        catch (Exception ex)
        {
            StatusText = $"commit 失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }
}
