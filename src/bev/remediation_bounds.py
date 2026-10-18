"""训练内滚阻下界诊断；负值只诊断失配，不作为车辆参数。AI辅助：Codex。"""
import numpy as np
from scipy.optimize import least_squares
from .io import PROJECT_ROOT,read_json,write_json,sha256
from .remediation import EXPERIMENT,CorrectedProblem,begin,protocol


def run():
    begin()
    source=EXPERIMENT/'candidate_parameters.json'
    candidate=read_json(source)
    fingerprint={'candidate':sha256(source),'code':sha256(PROJECT_ROOT/'src/bev/remediation_bounds.py')}
    freeze={'fingerprint':fingerprint,'train_only':True,'trips':[575,596],
            'rolling_lower_bounds':[0.,-.01],'purpose':'Check whether removing the engineering floor repairs the boundary issue. Negative Cr is diagnostic only and must not be adopted.',
            'upper_Cr':.03,'max_nfev':80,'no_retrospective_trip_evaluation':True}
    path=EXPERIMENT/'rolling_bounds_protocol.json'
    if path.exists() and read_json(path)!=freeze:raise ValueError('滚阻诊断代码或来源已变化')
    write_json(path,freeze)
    target=EXPERIMENT/'rolling_bounds_results.json'
    if target.exists():return read_json(target)
    problem=CorrectedProblem();start=np.array([candidate[k] for k in problem.names])
    cfg=protocol();rows=[]
    for floor in freeze['rolling_lower_bounds']:
        lower=np.r_[problem.cfg['lower_bounds'],cfg['ocv_scale_bounds'][0]];lower[1]=floor
        upper=np.r_[problem.cfg['upper_bounds'],cfg['ocv_scale_bounds'][1]]
        fit=least_squares(problem.residual,start,bounds=(lower,upper),x_scale='jac',diff_step=.001,
                          max_nfev=80,ftol=2e-5,xtol=2e-5,gtol=2e-5)
        row={'Cr_lower_bound':floor,'parameters':dict(zip(problem.names,map(float,fit.x))),
             'cost':float(fit.cost),'active_mask':fit.active_mask.tolist(),'success':bool(fit.success),
             'physically_admissible_Cr':bool(fit.x[1]>=0),'adopt_as_final_parameters':False}
        rows.append(row);print(row,flush=True)
    result={'rows':rows,'fingerprint':fingerprint,'train_only':True,'not_a_confidence_interval':True,
            'interpretation':'A lower objective after changing a boundary does not establish physical reliability. Negative rolling resistance would expose mismatch rather than solve it.'}
    write_json(target,result);return result


if __name__=='__main__':run()
