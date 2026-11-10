/**
 * 版本列表双口径文案：记录数（recordCount）与点位数（fpCount）必须同时出现，
 * 已裁剪明细的旧版本不能展示成「0 个点位」。
 */
import {
  versionCountsText,
  versionListLabel,
  versionRollbackConfirmText,
  versionTimeLabel,
} from '../core/collection/version-format';
import type { FingerprintLibraryVersionSummary } from '../typings/api';

function summary(
  patch: Partial<FingerprintLibraryVersionSummary> = {},
): FingerprintLibraryVersionSummary {
  return {
    version: 'fpl-20261110115454835010-33f40c',
    siteId: 'default',
    recordCount: 595,
    fpCount: 594,
    insertedCount: 595,
    updatedCount: 0,
    conflictCount: 0,
    deletedCount: 0,
    retainedCount: 0,
    previousCount: 0,
    mode: 'merge',
    replaceFloors: [],
    sourceFileName: 'fp.json',
    createdAt: '2026-11-10T11:54:54.000Z',
    active: false,
    duplicated: false,
    noChanges: false,
    detailsPruned: false,
    ...patch,
  };
}

describe('版本列表双口径文案', () => {
  it('普通版本同时显示记录数与点位数', () => {
    expect(versionTimeLabel('fpl-20261110115454835010-33f40c')).toBe('20261110115454');
    expect(versionListLabel(summary())).toBe(
      '20261110115454 · 595 条记录 / 594 个点位',
    );
  });

  it('当前 active 版本追加「当前」', () => {
    expect(versionListLabel(summary({ active: true }))).toBe(
      '20261110115454 · 595 条记录 / 594 个点位 · 当前',
    );
  });

  it('已裁剪明细的旧版本显示「明细已裁剪」，不出现点位数', () => {
    const label = versionListLabel(summary({ detailsPruned: true }));
    expect(label).toBe('20261110115454 · 595 条记录 · 明细已裁剪');
    expect(label).not.toContain('个点位');
  });

  it('回滚确认写明两个口径', () => {
    expect(versionRollbackConfirmText(summary())).toContain(
      '595 条记录 / 594 个点位',
    );
    expect(versionRollbackConfirmText(summary())).toContain('fpl-20261110115454835010-33f40c');
  });

  it('已裁剪版本的回滚确认直接拒绝，不出现 0 个点位', () => {
    const text = versionRollbackConfirmText(summary({ detailsPruned: true, fpCount: 0 }));
    expect(text).toContain('无法回滚');
    expect(text).not.toContain('0 个点位');
  });

  it('只有摘要的空点位版本（fpCount=0）仍显示真实双口径', () => {
    expect(versionCountsText(summary({ fpCount: 0 }))).toBe(
      '595 条记录 / 0 个点位',
    );
  });
});
