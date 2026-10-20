// Retain CarClear's scenes; tell the research story in the paper's question order.
import anatomy from './research-anatomy.js';
import battery from './research-battery.js';
import electric from './research-electric.js';
import climate from './research-climate.js';
import drive from './research-drive.js';
import results from './research-results.js';

export const BOX = { slug: 'carclear-ev', title: '电动车节能研究 · C题成果展示' };
export const CHAPTERS = [
  { ...anatomy, phase: '研究概览' },
  { ...drive, phase: '问题一 · 建模与基准' },
  { ...battery, phase: '问题二 · 实测校准' },
  { ...electric, phase: '问题三 · 策略选择' },
  { ...climate, phase: '问题三 · 冷热差异' },
  { ...results, phase: '研究结论 · 同条件对照' },
];
