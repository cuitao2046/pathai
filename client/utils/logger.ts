/**
 * 统一日志：模块级 tag 前缀 + 级别开关。
 * debug 级默认关闭；联调时通过 setLogLevel('debug') 打开。
 */
export type LogLevel = 'debug' | 'info' | 'warn' | 'error';

const LEVEL_ORDER: Record<LogLevel, number> = { debug: 10, info: 20, warn: 30, error: 40 };

let minLevel: LogLevel = 'info';

export function setLogLevel(level: LogLevel): void {
  minLevel = level;
}

export interface Logger {
  debug(...args: unknown[]): void;
  info(...args: unknown[]): void;
  warn(...args: unknown[]): void;
  error(...args: unknown[]): void;
}

export function createLogger(tag: string): Logger {
  const log = (level: LogLevel, args: unknown[]): void => {
    if (LEVEL_ORDER[level] < LEVEL_ORDER[minLevel]) {
      return;
    }
    const fn =
      level === 'debug'
        ? console.log
        : level === 'info'
          ? console.info
          : level === 'warn'
            ? console.warn
            : console.error;
    fn(`[${tag}]`, ...args);
  };
  return {
    debug: (...args) => log('debug', args),
    info: (...args) => log('info', args),
    warn: (...args) => log('warn', args),
    error: (...args) => log('error', args),
  };
}
