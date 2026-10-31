using System.Collections.ObjectModel;
using BeaconProvisioner.Ble;
using BeaconProvisioner.Data;
using BeaconProvisioner.Models;
using BeaconProvisioner.Protocol;
using BeaconProvisioner.Services;

namespace BeaconProvisioner.ViewModels;

/// <summary>
/// 主视图模型：登录 → 台账 → 未入库清单 → 扫描（mock）→ 连接 → 读回 → 写入 → 重启 → 校验 → 上传。
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
    private bool _restarted;
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
            () => !IsBusy && _restarted && IsConnected && !_verified);
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
        : "实物模式（WinRT）：写入只作用于选中的信标；写入后只能人工断电重启";

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
                ResetProgress();
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

    private async Task LoginAsync()
    {
        if (string.IsNullOrWhiteSpace(ServerUrl) || string.IsNullOrWhiteSpace(Username))
        {
            StatusText = "请填写服务器地址与用户名";
            return;
        }

        IsBusy = true;
        try
        {
            _api.BaseUrl = ServerUrl.Trim();
            await _api.LoginAsync(Username.Trim(), Password);
            if (_api.MustChangePassword)
            {
                StatusText = "该账号尚未完成首次改密，请先在管理后台改密后再登录";
                return;
            }

            IsLoggedIn = true;
            Password = "";
            StatusText = "登录成功";
            Raise(nameof(PendingText));
            await RefreshAsync();
            await ScanAsync();
        }
        catch (LedgerApiException ex)
        {
            StatusText = $"登录失败：{ex.Message}（{ex.Code}）";
        }
        catch (Exception ex)
        {
            StatusText = $"登录失败：{ex.Message}";
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
                : "实物扫描中（WinRT）：等待 FFF0/iBeacon 广播…";
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
        VisibleDevices.Clear();
        foreach (var device in _devices)
        {
            if (OnlyUnwrittenDevices && device.WriteTimeText.Length > 0)
            {
                continue;
            }

            VisibleDevices.Add(device);
        }

        SelectedDevice = VisibleDevices.FirstOrDefault(d => d.Mac == keepMac) ?? VisibleDevices.FirstOrDefault();
        RefreshCommands();
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
            _session?.Dispose();
            _session = await _ble.ConnectAsync(SelectedDevice);
            IsConnected = true;
            if (!_restarted)
            {
                // 首次连接才重置进度；重启后的重连用于校验，不能清掉「已重启」状态
                ResetProgress();
            }

            ShowCurrent(_session.ReadCurrent());
            StatusText = _restarted
                ? $"已重新连接 {SelectedDevice.Name}；请点「2 校验」"
                : $"已连接 {SelectedDevice.Name}，请选择目标 beaconId";
        }
        catch (Exception ex)
        {
            StatusText = $"连接失败：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
        }
    }

    private Task DisconnectAsync()
    {
        _session?.Dispose();
        _session = null;
        IsConnected = false;
        ResetProgress();
        CurrentSettingsText = "未连接设备";
        StatusText = "已断开连接";
        return Task.CompletedTask;
    }

    private void ShowCurrent(BeaconCurrentState state)
    {
        CurrentSettingsText = $"""
            MAC：{_session?.Device.Mac ?? "—"}
            名称：{state.Name}
            UUID：{state.Uuid}
            Major/Minor：{state.Major} / {state.Minor}
            功率：{state.TxPowerDbm} dBm（码 {state.PowerCode}）
            间隔：{state.IntervalMs} ms
            校准 RSSI：{state.CalibratedRssi}
            模式：{state.Mode}
            """;
    }

    private void ResetProgress()
    {
        _restarted = false;
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

        IsBusy = true;
        try
        {
            var deviceMac = _session!.Device.Mac;
            var request = new BeaconWriteRequest
            {
                Uuid = target.Uuid!,
                Major = target.Major!.Value,
                Minor = target.Minor!.Value,
                Name = target.DeviceName,
                TxPowerDbm = txPowerDbm,
                IntervalMs = intervalMs,
            };

            var writeResult = _session.Write(request);
            if (!writeResult.Success)
            {
                StatusText = $"步骤 1/3 写入失败：{writeResult.Message}";
                return;
            }

            StatusText = "参数已写入，正在提交 FFFF 保存…";
            await _session.CommitAsync("minew123");
            _writeHistory.MarkWritten(deviceMac, DateTime.Now);

            // 保存后设备会断开；真机重启后原 GATT 连接也不存在 → 主动丢弃会话，要求重新连接
            _session?.Dispose();
            _session = null;
            IsConnected = false;
            CurrentSettingsText = "设备已断开（参数已保存，等待人工重启）";

            System.Windows.MessageBox.Show(
                $"{target.BeaconId} 参数已保存到信标。\n\n"
                + "E5 不支持指令重启，请人工断电重启该信标：\n"
                + "断电 → 等待 3~5 秒 → 上电，广播恢复后点击 OK。",
                "请人工重启信标",
                System.Windows.MessageBoxButton.OK,
                System.Windows.MessageBoxImage.Information);

            // 返回主窗口即清空上次写入的参数，等重新连接后显示从信标读回的值
            CurrentSettingsText = "未连接设备";
            // 刚写入的信标需要重新连接校验；确保它不会被「只显示未写入」过滤掉
            if (OnlyUnwrittenDevices)
            {
                OnlyUnwrittenDevices = false;
            }

            await _ble.EnsureReadyAsync();
            _ble.StartScan();
            SyncDevices();
            _restarted = true;
            StatusText = $"{target.BeaconId} 已确认人工重启；请重新「连接设备」，连接成功后再点「2 校验」";
        }
        catch (Exception ex)
        {
            StatusText = $"步骤 1/3 写入/保存异常：{ex.Message}";
        }
        finally
        {
            IsBusy = false;
            RefreshCommands();
        }
    }

    private Task VerifyAsync()
    {
        var target = _activeTarget;
        if (_session is null || !IsConnected || target is null)
        {
            StatusText = "请先点「连接设备」重新连接已重启的信标，再校验";
            return Task.CompletedTask;
        }

        try
        {
            var current = _session.ReadCurrent();
            ShowCurrent(current);
            var verify = Verify(target, current, _activeTxPower, _activeInterval);
            if (!verify.Passed)
            {
                StatusText = $"步骤 2/3 校验未通过：{verify.Message}（可重新写入并重启后再校验）";
                return Task.CompletedTask;
            }

            _lastPayload = BuildRecordPayload(target, current, verify);
            _verified = true;
            StatusText = "步骤 2/3 校验通过（full-readback）；请点「3 上传」";
        }
        catch (Exception ex)
        {
            StatusText = $"步骤 2/3 校验异常：{ex.Message}";
        }
        finally
        {
            RefreshCommands();
        }

        return Task.CompletedTask;
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
