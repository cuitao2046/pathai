/** Persist an unresolved create before sending, scoped to backend + WeChat identity + site. */
import { getJson, getString, setJson, remove } from '../../utils/kv';
import { ApiClientError } from '../http/errors';
import type { CreateSessionRequest } from '../../typings/api';

export interface PendingNavCreate { key: string; body: CreateSessionRequest }
export interface NavCreateJournal {
  load(): PendingNavCreate | null;
  save(attempt: PendingNavCreate): void;
  clear(): void;
}

export function createNavJournal(base: string | null, siteId: string): NavCreateJournal {
  const user = base ? getJson<{ userId: string }>(`auth:wechat:${base}`)?.userId : null;
  const key = base && user ? `nav:pending:${base}:${user}:${siteId}` : null;
  const storageKey = () => {
    if (!key) throw new ApiClientError({ code: 'UNAUTHENTICATED', message: '请先完成身份连接' });
    return key;
  };
  return {
    load: () => {
      const name = storageKey();
      const record = getJson<PendingNavCreate>(name);
      if (getString(name) !== null && (!record || typeof record.key !== 'string' || !record.key ||
          !record.body || record.body.siteId !== siteId || typeof record.body.destinationId !== 'string')) {
        throw new ApiClientError({ code: 'HTTP_ERROR', message: '导航恢复记录异常，暂不能创建新会话' });
      }
      return record;
    },
    save: attempt => {
      if (!setJson(storageKey(), attempt)) throw new ApiClientError({ code: 'HTTP_ERROR', message: '无法保存导航恢复记录，请检查存储空间' });
    },
    clear: () => {
      const name = storageKey();
      remove(name);
      if (getString(name) !== null) throw new ApiClientError({ code: 'HTTP_ERROR', message: '无法清除导航恢复记录' });
    },
  };
}
