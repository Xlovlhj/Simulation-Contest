"""阶段十：外部数据适配、测温决策与隔离RC比较。AI辅助：Codex。"""
from __future__ import annotations

from datetime import datetime, timezone
import time

import numpy as np
import pandas as pd
from scipy.integrate import cumulative_trapezoid, trapezoid
from scipy.io import loadmat
from scipy.optimize import least_squares

from .calibration import prepare_trip, protocol as calibration_protocol, forward
from .control import Rule, control_protocol, scenario_parameters
from .cycles import load_cltc
from .durability import summarize_trace
from .io import PROJECT_ROOT, read_json, write_json, sha256, verify_sources
from .model import InitialState, InfeasibleSimulation, load_parameters, simulate
from .paper import verify_evidence
from .remediation import CorrectedProblem, corrected_forward, scaled_curves, ImprovedController
from .voltage_rc import simulate as rc_simulate

ROOT = PROJECT_ROOT/'records/experiments/extension_v1'


def protocol():
    return read_json(PROJECT_ROOT/'configs/extension_protocol.json')


def begin():
    verify_sources()
    old = verify_evidence()
    files = ['configs/extension_protocol.json', 'src/bev/extension.py', 'src/bev/voltage_rc.py',
             'checks/extension_checks.py', 'configs/calibration_protocol.json',
             'records/experiments/remediation_v1/candidate_parameters.json',
             'src/bev/remediation.py', 'configs/remediation_protocol.json']
    files += [p.relative_to(PROJECT_ROOT).as_posix() for p in (ROOT/'inputs').glob('*') if p.is_file()]
    fingerprint = {**old, **{f: sha256(PROJECT_ROOT/f) for f in files}}
    target = ROOT/'protocol_freeze.json'
    if target.exists():
        if read_json(target)['fingerprint'] != fingerprint:
            raise ValueError('阶段十输入/协议/代码变化：须保留原结果，建立新实验版本')
    else:
        write_json(target, {'time_utc': datetime.now(timezone.utc).isoformat(),
                           'fingerprint': fingerprint, 'evaluation_is_untouched': False,
                           'protocol': protocol(), 'status': 'before_new_fits_and_results'})


def fit_record(fit, started):
    return {'estimate': fit.x.tolist(), 'cost': float(fit.cost), 'success': bool(fit.success),
            'message': str(fit.message), 'nfev': fit.nfev, 'active_mask': fit.active_mask.tolist(),
            'runtime_s': time.perf_counter()-started}


def metrics(trip, trace, out, pred, label):
    measured = trapezoid(trip.observation.battery_power_w, trip.observation.time_s)/3.6e6
    return {'trip_id': trip.trip_id, 'variant': label,
            'role': 'train' if trip.trip_id in protocol()['train_trip_ids'] else 'retrospective',
            'energy_ape_pct': float(100*abs(out['net_energy_kwh']-measured)/abs(measured)),
            'soc_rmse_pp': float(100*np.sqrt(np.mean((pred['soc']-trip.observation.soc)**2))),
            'voltage_rmse_v': float(np.sqrt(np.mean((pred['voltage_v']-trip.observation.voltage_v)**2))),
            'max_electrical_balance_residual_w': out['max_battery_electrical_residual_w'],
            'max_thermal_balance_residual_w': out['max_battery_thermal_residual_w'],
            'energy_kwh': out['net_energy_kwh']}


def road():
    begin()
    target = ROOT/'road_results.json'
    if target.exists():
        return read_json(target)
    cfg = protocol()['road']
    df = pd.read_csv(ROOT/'inputs/epa_2026_electric_roadload.csv')
    keys = cfg['deduplicate_by']
    cols = ['Target Coef A (lbf)', 'Target Coef B (lbf/mph)', 'Target Coef C (lbf/mph**2)']
    cohort = df.groupby(keys, dropna=False)[cols].median().reset_index()
    cohort['test_mass_kg'] = cohort[keys[-1]]*0.45359237
    ref = load_parameters()
    lo, hi = cfg['mass_ratio_bounds']
    cohort = cohort[cohort.test_mass_kg.between(lo*ref['mass_kg'], hi*ref['mass_kg'])].copy()
    cohort = cohort[np.isfinite(cohort[cols+['test_mass_kg']]).all(axis=1)]
    a, b, c = [cohort[n].to_numpy()*4.448221615/(0.44704**i) for i, n in enumerate(cols)]
    valid = (a > 0) & (c > 0)
    for kmh in [20, 50, 80, 100, 120]:
        valid &= (a+b*kmh/3.6+c*(kmh/3.6)**2 > 0)
    cohort = cohort[valid].copy()
    a, b, c = a[valid], b[valid], c[valid]
    if len(cohort) < 3:
        result = {'sufficient_cohort': False, 'configurations': len(cohort), 'Cr_reliability_solved': False}
        write_json(target, result)
        return result
    cohort['A_N'], cohort['B_N_per_m_s'], cohort['C_N_per_m_s2'] = a, b, c
    cohort['A_equivalent_coefficient_not_pure_Cr'] = a/(cohort.test_mass_kg*ref['gravity_m_s2'])
    proxy = float(cohort.A_equivalent_coefficient_not_pure_Cr.median())
    cohort.to_csv(ROOT/'epa_cohort.csv', index=False, encoding='utf-8-sig')
    write_json(ROOT/'road_proxy_freeze.json', {'proxy': proxy, 'configurations': len(cohort),
               'cohort_sha256': sha256(ROOT/'epa_cohort.csv'), 'selection_uses_trip_errors': False,
               'time_utc': datetime.now(timezone.utc).isoformat()})
    problem = CorrectedProblem()
    old = read_json(PROJECT_ROOT/'records/experiments/remediation_v1/candidate_parameters.json')
    start = np.array([old[n] for n in problem.names])
    lower = np.r_[problem.cfg['lower_bounds'], .1]
    upper = np.r_[problem.cfg['upper_bounds'], 2.]
    free = [0, 2, 3, 4, 5]
    def expand(x):
        vector = start.copy()
        vector[1], vector[free] = proxy, x
        return vector
    started = time.perf_counter()
    fit = least_squares(lambda x: problem.residual(expand(x)), start[free],
                        bounds=(lower[free], upper[free]), max_nfev=cfg['fit_max_nfev'],
                        x_scale='jac', diff_step=.001, ftol=2e-5, xtol=2e-5, gtol=2e-5)
    parameters = problem.parameters(expand(fit.x))
    write_json(ROOT/'road_fixed_parameters.json', parameters)
    old_cost = float(.5*np.sum(problem.residual(start)**2))
    rows = []
    for trip in problem.trips:
        for label, p in [('stage8_free_Cr', old), ('EPA_A_proxy_fixed', parameters)]:
            trace, out, pred = corrected_forward(trip, p, .5)
            rows.append(metrics(trip, trace, out, pred, label))
    speeds = [20, 50, 80, 100, 120]
    force = []
    for kmh in speeds:
        v = kmh/3.6
        f = a+b*v+c*v*v
        force.append({'speed_kmh': kmh, 'EPA_p10_N': float(np.quantile(f, .1)),
                      'EPA_median_N': float(np.median(f)), 'EPA_p90_N': float(np.quantile(f, .9)),
                      'old_model_N': ref['mass_kg']*ref['gravity_m_s2']*old['rolling_coefficient']
                        +.5*ref['air_density_kg_m3']*old['drag_coefficient']*ref['frontal_area_m2']*v*v})
    result = {'configurations': len(cohort), 'electricity_rows': len(df), 'proxy_not_pure_Cr': proxy,
              'proxy_p10_p90': cohort.A_equivalent_coefficient_not_pure_Cr.quantile([.1, .9]).tolist(),
              'fit': fit_record(fit, started), 'old_training_cost': old_cost,
              'cost_relative_increase': float((fit.cost-old_cost)/old_cost), 'training_metrics': rows,
              'force_comparison': force, 'Cr_reliability_solved': False,
              'adopted_as_target_Cr': False, 'limits': cfg['provenance']}
    write_json(target, result)
    print('Road experiment:', result['configurations'], proxy, result['cost_relative_increase'], flush=True)
    return result


def thermal_prediction(t, current, initial, a, b):
    out = np.empty(len(t))
    out[0] = initial
    for k, dt in enumerate(np.diff(t)):
        decay = np.exp(-b*dt)
        out[k+1] = 40+(out[k]-40)*decay+(a*current[k]**2/b)*(-np.expm1(-b*dt))
    return out


def temperature():
    begin()
    target = ROOT/'temperature_results.json'
    if target.exists():
        return read_json(target)
    cfg = protocol()['temperature']
    mat = loadmat(ROOT/'inputs/oxford_ExampleDC_C1.mat', simplify_cells=True)['ExampleDC_C1']['dc']
    t, current, actual = [np.asarray(mat[n], float).ravel() for n in ['t', 'i', 'T']]
    current = -current/1000  # 牛津放电为负mA -> 本工程放电为正A
    if (np.diff(t) <= 0).any() or not np.isfinite(np.r_[t, current, actual]).all():
        raise ValueError('牛津样例有无效数据')
    cut = int(.6*len(t))
    runs = []
    for start in cfg['starts']:
        started = time.perf_counter()
        fit = least_squares(lambda x: thermal_prediction(t[:cut], current[:cut], actual[0], *np.exp(x))-actual[:cut],
                            np.log(start), bounds=(np.log([cfg['bounds_a'][0], cfg['bounds_b'][0]]),
                            np.log([cfg['bounds_a'][1], cfg['bounds_b'][1]])), max_nfev=100)
        row = fit_record(fit, started)
        row['a_b'] = np.exp(fit.x).tolist()
        runs.append(row)
    best = min(runs, key=lambda r: r['cost'])
    write_json(ROOT/'temperature_parameter_freeze.json', {'a_b': best['a_b'], 'runs': runs,
               'train_samples': cut, 'selection': 'train_only', 'time_utc': datetime.now(timezone.utc).isoformat()})
    predicted = thermal_prediction(t, current, actual[0], *best['a_b'])
    forecasts = {'thermal_I_squared': predicted,
                 'train_end_persistence': np.full(len(t), actual[cut-1]),
                 'train_mean': np.full(len(t), np.mean(actual[:cut])), 'ambient40': np.full(len(t), 40.)}
    scores = []
    for label, y in forecasts.items():
        for role, sl in [('train', slice(0, cut)), ('temporal_holdout', slice(cut, None))]:
            scores.append({'variant': label, 'role': role, 'rmse_c': float(np.sqrt(np.mean((y[sl]-actual[sl])**2))),
                           'max_abs_error_c': float(np.max(np.abs(y[sl]-actual[sl])))})
    frame = pd.DataFrame({'time_s': t, 'discharge_current_a': current, 'measured_c': actual,
                          'predicted_c': predicted, 'role': ['train']*cut+['temporal_holdout']*(len(t)-cut)})
    frame.to_csv(ROOT/'oxford_temperature_predictions.csv', index=False, encoding='utf-8-sig')
    result = {'scores': scores, 'fit_runs': runs, 'train_samples': cut, 'holdout_samples': len(t)-cut,
              'train_temperature_range_c': [float(actual[:cut].min()), float(actual[:cut].max())],
              'holdout_temperature_range_c': [float(actual[cut:].min()), float(actual[cut:].max())],
              'a_R_over_C': best['a_b'][0], 'b_H_over_C': best['a_b'][1],
              'target_vehicle_measured_precision_solved': False, 'independent_cell_validation': False,
              'claim': cfg['claim']}
    write_json(target, result)
    print('External temperature:', scores, flush=True)
    return result


class DirectReading:
    def update(self, now, measurement, ambient):
        return measurement


def sensor():
    begin()
    target = ROOT/'sensor_results.json'
    if target.exists():
        return read_json(target)
    cfg = protocol()['sensor']
    base = read_json(PROJECT_ROOT/'configs/calibrated.json')
    rules = {'baseline': Rule(**control_protocol()['baseline']),
             'optimized': Rule(**read_json(PROJECT_ROOT/'configs/optimized_rule.json'))}
    rows, comparisons = [], []
    for ta in cfg['temperatures_c']:
        nominal = scenario_parameters(base, ta, control_protocol())
        for case in cfg['stress_cases']:
            plant = dict(nominal)
            ambient_bias = 0.
            if case == 'capacity_plus20pct': plant['cabin_heat_capacity_j_k'] *= 1.2
            if case == 'conductance_plus20pct': plant['cabin_environment_w_k'] *= 1.2
            if case == 'occupant_plus50W': plant['occupant_heat_w'] += 50.
            if case == 'ambient_plus0.5C': ambient_bias = .5
            for bias in cfg['bias_c']:
                pair = {}
                for strategy, rule in rules.items():
                    cycle = load_cltc(ta)[0]
                    end = float(cycle.time_s.iloc[-1])
                    initial = InitialState(.94, ta, 24.)
                    controller = ImprovedController(nominal, rule, end)
                    controller.observer = DirectReading()
                    controller.drive.cfg = {**controller.drive.cfg, 'sensor_uncertainty_margin_c': cfg['guard_margin_c']}
                    def sense(o):
                        return controller({**o, 'cabin_c': o['cabin_c']+bias+cfg['noise_amplitude_c']*np.sin(2*np.pi*o['time_s']/37),
                                           'battery_c': o['battery_c']+bias, 'ambient_c': o['ambient_c']+ambient_bias})
                    trace, _ = simulate(cycle, plant, initial, max_step_s=.5, controller=sense)
                    out = summarize_trace(trace, initial, plant, controller)
                    conditions = {k: v for k, v in out['constraints'].items() if k != 'cabin_terminal'}
                    sensed = trace.cabin_c+bias+cfg['noise_amplitude_c']*np.sin(2*np.pi*trace.time_s/37)
                    # 区间是传感器误差假设下的推论，不用仿真真值作为控制输入。
                    bound = abs(bias)+cfg['noise_amplitude_c']
                    out.update(case=case, sensor_bias_c=bias, strategy=strategy, ambient_c=ta,
                               practical_constraints=conditions, practical_pass=bool(all(conditions.values())),
                               inferred_cabin_lower_c=float(sensed.min()-bound),
                               inferred_cabin_upper_c=float(sensed.max()+bound))
                    rows.append(out)
                    pair[strategy] = out
                b, o = pair['baseline'], pair['optimized']
                cabin_penalty = nominal['cabin_heat_capacity_j_k']*(abs(o['cabin_final_c']-b['cabin_final_c'])+2*1.05)/min(nominal['cooling_cop'], nominal['heater_efficiency'])/3.6e6
                battery_penalty = nominal['battery_heat_capacity_j_k']*max(o['battery_final_c']-b['battery_final_c']+2., 0)/nominal['cooling_cop']/3.6e6 if ta >= 25 else 0.
                comparisons.append({'ambient_c': ta, 'case': case, 'sensor_bias_c': bias,
                                    'both_practical_pass': b['practical_pass'] and o['practical_pass'],
                                    'raw_saved_kwh': b['energy_kwh']-o['energy_kwh'],
                                    'ideal_uncertainty_adjusted_saved_kwh': b['energy_kwh']-o['energy_kwh']-cabin_penalty-battery_penalty,
                                    'cabin_penalty_kwh': cabin_penalty, 'battery_penalty_kwh': battery_penalty})
                print('Sensor guarded direct:', ta, case, bias, flush=True)
    result = {'trials': rows, 'comparisons': comparisons,
              'practical_pass': sum(r['practical_pass'] for r in rows), 'trials_total': len(rows),
              'strict_terminal_pass': sum(r['constraints']['cabin_terminal'] for r in rows),
              'paired_practical_pass': sum(r['both_practical_pass'] for r in comparisons),
              'positive_ideal_adjusted_pairs': sum(r['ideal_uncertainty_adjusted_saved_kwh'] > 0 for r in comparisons),
              'sensor_bias_actually_calibrated': False, 'certification': cfg['certification']}
    write_json(target, result)
    pd.DataFrame(comparisons).to_csv(ROOT/'sensor_comparisons.csv', index=False, encoding='utf-8-sig')
    return result


def rc_forward(trip, parameters, step):
    trace, out = rc_simulate(trip.cycle, parameters, trip.initial, max_step_s=step,
                             curves=scaled_curves(parameters.get('ocv_scale', 1.)))
    t = trip.observation.time_s.to_numpy()
    pred = {'soc': np.interp(t, np.r_[trip.cycle.time_s.iloc[0], trace.time_s], np.r_[trip.initial.soc, trace.soc]),
            'voltage_v': np.interp(t, (trace.time_start_s+trace.time_s)/2, trace.voltage_v)}
    return trace, out, pred


class RCProblem:
    def __init__(self):
        self.cfg, self.config = protocol()['voltage'], calibration_protocol()
        self.trips = [prepare_trip(tid) for tid in protocol()['train_trip_ids']]
        self.size = None
        self.calls = 0

    def parameters(self, vector):
        return {**load_parameters(), **dict(zip(self.cfg['names'], map(float, vector))),
                'polarization_initial_v': self.cfg['initial_vp_v'], 'parameter_status': 'stage10_RC_candidate'}

    def residual(self, vector):
        self.calls += 1
        cfg = self.config
        chunks = []
        try:
            for trip in self.trips:
                trace, _, pred = rc_forward(trip, self.parameters(vector), self.cfg['fit_step_s'])
                obs = trip.observation
                t = obs.time_s.to_numpy()
                w = np.r_[np.diff(t)/2, 0.]+np.r_[0., np.diff(t)/2]
                w = np.sqrt(w/w.sum())
                edges = np.r_[np.arange(t[0], t[-1], cfg['power_window_s']), t[-1]]
                measured = cumulative_trapezoid(obs.battery_power_w, t, initial=0)
                predicted = np.r_[0., np.cumsum(trace.battery_w*trace.dt_s)]
                mw = np.diff(np.interp(edges, t, measured))/np.diff(edges)
                pw = np.diff(np.interp(edges, np.r_[t[0], trace.time_s], predicted))/np.diff(edges)
                chunks.extend([(pred['soc']-obs.soc.to_numpy())/cfg['soc_scale_fraction']*w,
                               (pred['voltage_v']-obs.voltage_v.to_numpy())/cfg['voltage_scale_v']*w,
                               (pw-mw)/cfg['power_window_scale_w']/np.sqrt(len(mw)),
                               np.array([(predicted[-1]-measured[-1])/(cfg['net_energy_relative_scale']*abs(measured[-1]))])])
        except InfeasibleSimulation:
            if self.size is None: raise
            return np.full(self.size, 1e4)
        output = np.concatenate(chunks)
        self.size = len(output)
        return output


def voltage():
    begin()
    target = ROOT/'voltage_results.json'
    if target.exists():
        return read_json(target)
    cfg = protocol()['voltage']
    problem = RCProblem()
    scaled = read_json(PROJECT_ROOT/'records/experiments/remediation_v1/candidate_parameters.json')
    old = read_json(PROJECT_ROOT/'configs/calibrated.json')
    runs = []
    for rp, tau in cfg['starts_rc']:
        initial = [scaled[n] for n in cfg['names'][:6]]+[rp, tau]
        started = time.perf_counter()
        fit = least_squares(problem.residual, initial, bounds=(cfg['lower'], cfg['upper']),
                            x_scale='jac', diff_step=cfg['diff_step'], max_nfev=cfg['max_nfev'],
                            ftol=2e-5, xtol=2e-5, gtol=2e-5)
        row = fit_record(fit, started)
        row['initial_rp_tau'] = [rp, tau]
        runs.append(row)
        write_json(ROOT/'rc_fit_progress.json', {'runs': runs})
        print('RC training:', row, flush=True)
    best = min(runs, key=lambda r: r['cost'])
    candidate = problem.parameters(best['estimate'])
    write_json(ROOT/'rc_parameters.json', candidate)
    write_json(ROOT/'rc_training_freeze.json', {'runs': runs, 'selected_by': 'training_objective_only',
               'parameters_sha256': sha256(ROOT/'rc_parameters.json'),
               'time_utc': datetime.now(timezone.utc).isoformat(), 'evaluation_is_untouched': False})
    rows, convergence, sensitivity = [], [], []
    for tid in protocol()['train_trip_ids']+protocol()['evaluation_trip_ids']:
        trip = prepare_trip(tid)
        for label, p, fn in [('original_Rint', old, forward), ('stage8_OCV_scaled', scaled, corrected_forward),
                             ('new_one_RC', candidate, rc_forward)]:
            trace, out, pred = fn(trip, p, cfg['evaluation_step_s'])
            rows.append(metrics(trip, trace, out, pred, label))
            if label == 'new_one_RC':
                frame = pd.DataFrame({'time_s': trip.observation.time_s, 'measured_voltage_v': trip.observation.voltage_v,
                                      'predicted_voltage_v': pred['voltage_v'], 'measured_soc': trip.observation.soc, 'predicted_soc': pred['soc']})
                frame.to_csv(ROOT/f'rc_trip_{tid}_predictions.csv', index=False, encoding='utf-8-sig')
                _, fine, fine_pred = rc_forward(trip, p, .25)
                convergence.append({'trip_id': tid, 'energy_relative_diff': abs(fine['net_energy_kwh']-out['net_energy_kwh'])/abs(fine['net_energy_kwh']),
                                    'voltage_rms_diff_v': float(np.sqrt(np.mean((fine_pred['voltage_v']-pred['voltage_v'])**2))),
                                    'soc_final_diff_pp': 100*abs(fine['soc_final']-out['soc_final']),
                                    'passed': bool(abs(fine['net_energy_kwh']-out['net_energy_kwh'])/abs(fine['net_energy_kwh']) < .01
                                        and np.sqrt(np.mean((fine_pred['voltage_v']-pred['voltage_v'])**2)) < .1)})
        if tid in protocol()['train_trip_ids']:
            for vp in cfg['initial_vp_training_sensitivity_v']:
                trace, out, pred = rc_forward(trip, {**candidate, 'polarization_initial_v': vp}, .5)
                sensitivity.append({**metrics(trip, trace, out, pred, 'initial_polarization_sensitivity'), 'initial_vp_v': vp})
    gates = []
    for tid in protocol()['evaluation_trip_ids']:
        selected = {r['variant']: r for r in rows if r['trip_id'] == tid}
        n, o, s = [selected[k] for k in ['new_one_RC', 'original_Rint', 'stage8_OCV_scaled']]
        gates.append({'trip_id': tid, 'voltage_10pct_better_than_scaled': n['voltage_rmse_v'] <= .9*s['voltage_rmse_v'],
                      'energy_not_materially_worse_than_original': n['energy_ape_pct'] <= o['energy_ape_pct']+.5,
                      'soc_not_materially_worse_than_original': n['soc_rmse_pp'] <= o['soc_rmse_pp']+.2,
                      'electrical_and_thermal_balance': n['max_electrical_balance_residual_w'] < 1e-6 and n['max_thermal_balance_residual_w'] < 1e-5})
    adopted = all(all(v for k, v in r.items() if k != 'trip_id') for r in gates) and all(r['passed'] for r in convergence)
    result = {'metrics': rows, 'fits': runs, 'gates': gates, 'convergence': convergence,
              'initial_polarization_sensitivity_training_only': sensitivity,
              'adopt_RC_as_retrospective_candidate': adopted, 'main_frozen_model_overwritten': False,
              'evaluation_is_untouched': False, 'no_test_specific_retuning': True,
              'low_soc_measured_accuracy_solved': False, 'Cr_reliability_solved': False}
    write_json(target, result)
    pd.DataFrame(rows).to_csv(ROOT/'voltage_comparison.csv', index=False, encoding='utf-8-sig')
    print('RC adoption gate:', adopted, gates, flush=True)
    return result


def run(part='all'):
    steps = {'road': road, 'temperature': temperature, 'sensor': sensor, 'voltage': voltage}
    for name, fn in steps.items():
        if part in ['all', name]: fn()
    verify_sources()
    verify_evidence()


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('part', nargs='?', default='all', choices=['all', 'road', 'temperature', 'sensor', 'voltage'])
    run(parser.parse_args().part)
