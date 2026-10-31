using System.ComponentModel;
using System.Runtime.CompilerServices;
using System.Windows.Input;

namespace BeaconProvisioner.ViewModels;

public abstract class ObservableObject : INotifyPropertyChanged
{
    public event PropertyChangedEventHandler? PropertyChanged;

    protected void Raise([CallerMemberName] string? name = null) =>
        PropertyChanged?.Invoke(this, new PropertyChangedEventArgs(name));

    protected bool Set<T>(ref T field, T value, [CallerMemberName] string? name = null)
    {
        if (EqualityComparer<T>.Default.Equals(field, value))
        {
            return false;
        }

        field = value;
        Raise(name);
        return true;
    }
}

public sealed class RelayCommand : ICommand
{
    private readonly Func<Task> _execute;
    private readonly Func<bool>? _canExecute;
    private bool _running;

    public RelayCommand(Func<Task> execute, Func<bool>? canExecute = null)
    {
        _execute = execute;
        _canExecute = canExecute;
    }

    public event EventHandler? CanExecuteChanged;

    public bool CanExecute(object? parameter) => !_running && (_canExecute?.Invoke() ?? true);

    public async void Execute(object? parameter)
    {
        if (!CanExecute(parameter))
        {
            return;
        }

        _running = true;
        RaiseCanExecuteChanged();
        try
        {
            // 不要 ConfigureAwait(false)：CanExecuteChanged 必须在 UI 线程触发，
            // 否则 WPF 的 ButtonBase.UpdateCanExecute 会因跨线程访问 DispatcherObject 崩溃。
            await _execute();
        }
        catch (Exception ex)
        {
            System.Windows.MessageBox.Show(ex.Message, "操作失败");
        }
        finally
        {
            _running = false;
            RaiseCanExecuteChanged();
        }
    }

    public void RaiseCanExecuteChanged()
    {
        var handler = CanExecuteChanged;
        if (handler is null)
        {
            return;
        }

        // 双保险：即使调用方不在 UI 线程，也切回 Dispatcher 再通知按钮。
        var dispatcher = System.Windows.Application.Current?.Dispatcher;
        if (dispatcher is not null && !dispatcher.CheckAccess())
        {
            dispatcher.Invoke(() => handler(this, EventArgs.Empty));
            return;
        }

        handler(this, EventArgs.Empty);
    }
}
