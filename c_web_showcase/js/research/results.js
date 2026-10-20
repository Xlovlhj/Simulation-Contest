// Immutable experiment summaries. Interactive sliders never modify these values.
const response = await fetch(new URL('../../data/research-results.json', import.meta.url));
if (!response.ok) throw new Error('论文实验摘要载入失败');
const DATA = await response.json();
export const RESULTS = DATA.summaries;
export const RESULT_SOURCES = DATA.sources;
export const TEMPERATURES = [25, 40, -10];
export const RESULT_SCOPE = '论文已完成的固定实验；与滑条当前状态计算分别展示';
export function comparison(ambientC = 25, scope = 'control') {
  const rows = scope === 'long' ? RESULTS.long : scope === 'sameTarget' ? RESULTS.sameTarget : RESULTS.control;
  const row = rows.find(r => r.ambient_c === Number(ambientC));
  if (!row) return null; // Do not interpolate a new experimental claim.
  return {
    ...row,
    scope,
    savingPct: scope === 'long' ? row.total_saving_pct : row.corrected_saving_pct,
    accountingLabel: scope === 'long' ? '含停车恢复的总节能率' : '末端热修正节能率',
    durationLabel: scope === 'long' ? '90分钟驾驶 + 120分钟停车' : '30分钟CLTC',
    baselineEnergyKwh: row.baseline_energy_kwh,
    optimizedEnergyKwh: row.optimized_energy_kwh,
    correctedEnergyKwh: scope === 'long' ? row.optimized_energy_kwh
      : scope === 'sameTarget' ? row.accounting.corrected_energy_kwh : row.optimized.corrected_energy_kwh,
  };
}
