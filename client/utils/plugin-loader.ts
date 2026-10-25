/**
 * requirePlugin 薄包装：集中一处便于测试替换与错误归一。
 */
export function loadPlugin<T = unknown>(name: string): T {
  return requirePlugin(name) as T;
}
