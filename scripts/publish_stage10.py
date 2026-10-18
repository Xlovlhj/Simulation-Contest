"""同步本轮真实决定、工程状态与文档。重复运行不重复追加。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.io import read_json, write_json
from bev.extension_report import publish

s = publish()
project = read_json(ROOT/'portal/project.json')
project.update(stage_label='阶段十 · 外部试验与RC电压改进',
               current_title='电压采用新RC方案；滚阻和真实热精度暂未解决。',
               current_description='外部数据已实际试用。容量透明双口径；测温采用误差裕量，保留1组舒适性失败和低温节能不稳。旧模型与对照完整保留。',
               current_tasks=[
                   {'title': '看五项试验结论', 'detail': '成功、失败及采用范围。', 'link': 'docs/五项问题的试验结果与处理决定.md'},
                   {'title': '看更新论文', 'detail': '新增5.6与6.7节，旧结果保留。', 'link': 'docs/论文/C题论文_技术定稿.md'},
                   {'title': '交给同学复现', 'detail': '用最新完整同学包；新实验可隔离重算。', 'link': 'docs/交付与复现说明.md'}])
for stage in project['stages']:
    for item in stage['items']:
        if item['text'] == '30kWh / 160Ah口径获得确认':
            item.update(text='容量冲突披露与双口径对照', done=True, status='用户接受透明处理；无官方勘误')
        elif item['text'] == '跨SOC电压迁移问题解决':
            item.update(status='RC回顾改善；更宽SOC实测仍缺，尚非全面解决')
        elif item['text'] == '滚阻参数可靠性论证':
            item.update(status='EPA参照固定后训练变差，不替换；仍未解决')
        elif item['text'] == '测温偏差下的终态方案':
            item.update(text='测温误差的处理方案与失败边界', done=True, status='改为舒适性与热核算；59/60，不承诺0.1℃')
    if stage['name'] == '辨识与可靠性' and not any(i['text'] == '新RC与两旧方案比较' for i in stage['items']):
        stage['items'].append({'text': '新RC与两旧方案比较', 'done': True, 'status': '选RC用于电压；原控制证据保留'})
    if stage['name'] == '控制与验证' and not any(i['text'] == '真实热精度与测温误差校准' for i in stage['items']):
        stage['items'].append({'text': '真实热精度与测温误差校准', 'done': False, 'status': '外部电芯试用未成功；无本车热实测'})
project['limits'] = [
    'RC在588/600回顾电压RMSE为6.03/8.30V；宽SOC准确性仍缺证据。',
    'Cr与RC欧姆内阻贴搜索下界，参数物理可靠性未解决。',
    'EPA外车参照和牛津实测电芯已试用，未迁移替换本车参数。',
    '直接反馈误差裕量59/60；低温一组舒适性失败，低温理想保守核算无正节省。',
    '容量按160Ah参考/80Ah对照透明处理，不认定官方笔误。',
    '旧控制和长程结果仍由各自冻结Rint模型支持，未冒充RC结论。',
    '原观测器精确30/30、失配20/60属于历史证据，真实偏差仍未校准。']
write_json(ROOT/'portal/project.json', project)

terms = read_json(ROOT/'portal/terms.json')
for term in terms:
    if term['id'] == 'one-rc':
        term.update(symbol='R0、Rp、τ', note='已训练并推荐用于本轮回顾电压预测；不自动解决低SOC、真实参数和温度缺数据。', source='extension_v1/voltage_results.json；voltage_rc.py')
extra = [
    {'id': 'polarization-time', 'name': '极化时间常数', 'symbol': 'τ=RpCp', 'category': '能量与电池', 'unit': 's',
     'professional': '一阶RC状态在零电流下指数衰减至原值1/e的时间，本轮拟合约7.53s。', 'plain': '电池的短时压降记忆，多久能消退大半。',
     'note': '拟合值受模型、初值和数据覆盖限制，不是厂家认证。', 'source': 'extension_v1/rc_parameters.json'},
    {'id': 'polarization-storage', 'name': '极化储能与发热', 'symbol': 'Ep=½Cpvp²', 'category': '能量与电池', 'unit': 'J',
     'professional': 'RC支路输入能量分为电阻热及电容储能变化；储能变化可正可负，不能全部计入焦耳热。',
     'plain': '有些能量先暂存，之后再释放；不能全当成当场发热。', 'note': '程序按每子步恒流精确积分支路。', 'source': 'voltage_rc.py / rc_step'},
    {'id': 'sensor-guard', 'name': '测温误差裕量', 'symbol': 'εT', 'category': '建模与验证', 'unit': '℃',
     'professional': '在声明的读数误差范围内收紧控制目标，为真实舒适温区留空间；动态误差仍需试验。',
     'plain': '温度计可能读偏，就别把目标贴着舒适区边缘。', 'note': '本轮1.1℃是工程设定，不是校准证书；60组有1组失败。', 'source': 'extension_protocol.json / sensor'}]
for term in extra:
    if not any(t['id'] == term['id'] for t in terms): terms.append(term)
write_json(ROOT/'portal/terms.json', terms)

state = read_json(ROOT/'records/latest_state.json')
state.update(status='stage10_external_trials_and_RC_voltage_selected',
             next='Review new RC voltage evidence and failure limits; user handles independent work. Same-car Cr, broad-SOC and measured thermal accuracy remain unresolved.',
             extension_summary='records/extension_summary.json',
             extension_recomputed=read_json(ROOT/'records/extension_runtime_verification.json')['passed'])
entry = 'Stage10 EPA and Oxford actual trials, guarded sensor60cases and one-RC comparison; RC selected for voltage only.'
if entry not in state['completed']: state['completed'].append(entry)
state['important'] = project['limits']
write_json(ROOT/'records/latest_state.json', state)

def append(path, marker, text):
    p = ROOT/path
    previous = p.read_text(encoding='utf-8')
    if marker not in previous: p.write_text(previous+'\n\n'+text.strip()+'\n', encoding='utf-8')

append('C题开发日志.md', '阶段十：外部数据实际试用与RC比较', '''### 2026-10-18：阶段十：外部数据实际试用与RC比较

按用户顺序先完成外部路阻、容量处理、真实温度及测温方案，再训练新电压模型。新实验单独置于records/experiments/extension_v1；原9官方输入、66科研来源、阶段八协议/代码/参数未变。

EPA458个Electricity测试行按0.8—1.25倍质量、配置去重和有效Target规则得到10配置。A等效系数中位0.007548，不是纯轮胎Cr；固定后重新训练五参数，目标恶化35.8847%，不采用替换。容量160Ah参考/80Ah对照及透明披露获用户接受，未获得官方勘误。

牛津740mAh/40℃样例按前60%拟合正单节点热组合参数，后40%连续预测：RMSE0.543088℃，末温保持0.532117℃，均温0.460684℃，未优于简单基线；不迁移本车，不声称真实热精度。原README/许可和数据随新实验保存。

测温采用直接读数反馈、1.1℃目标裕量、相同到达前冷却，扫描±1℃偏差及±0.05℃噪声、三温度五种失配共60例；59例满足移除严格座舱终态后的其余约束，严格0.1℃为0/60。−10℃/导热+20%/偏差+1℃优化舒适性失败。29/30双方可行；20/30同时可行且理想误差热修正仍正节省，低温十组无正收益。该预算是假设，不能写成真实校准或硬件上界。

一阶RC只训575/596，两初值按原综合目标选解并冻结。原/OCV缩放/RC在588电压RMSE15.0378/8.53996/6.03299V；600为25.6585/13.3327/8.30268V。RC能耗APE0.78409/4.36274%，SOC RMSE2.64152/2.59230pp，通过预定门槛，推荐用于电压预测。Cr与R0仍贴界；宽SOC未解决。问题一、控制、长程和区间继续保留冻结原模型，不冒充RC结果。

7项解析核查、4路步长检查通过。python scripts/recompute_extension.py在新隔离目录从输入完整重算四分支，1791数值及全部状态匹配；本机环境，不是新系统或人工独立核验。新协议/代码在首轮计算前冻结。阶段八交付PDF/ZIP已存交付材料/历史版本/阶段八。

实际命令：python scripts/prepare_extension_inputs.py；设置PYTHONPATH=src后python checks/extension_checks.py、python -m bev.extension all；python scripts/run_extension.py；python scripts/recompute_extension.py。后续更新论文5.6/6.7、AI实际工作稿、工作台和两包，具体排版/包运行结果在阶段末补记。''')
append('records/decisions.md', 'D-阶段十', '''## D-阶段十：外部数据与模型取舍

1. EPA只作外部总路阻参照；固定A等效值训练变差，不替换Cr。不把Target A称纯滚阻。
2. 用户接受容量透明双口径；不等待官方未给资料，不认定标称/可用关系。
3. 牛津热模型未优于基线，不迁移，也不增加复杂度硬拟合。
4. 真实0.1℃并非赛题门槛；采用误差裕量舒适性和热核算，明确59/60及低温失败，不把仿真偏差当真实校准。
5. 只按训练目标冻结RC后回顾比较，推荐RC用于电压；原Rint及OCV缩放保留。控制、长程、参数区间未换模型，宽SOC与Cr/R0物理可靠性不宣称解决。''')
append('records/ai_usage.md', '阶段十实际AI辅助', '''## 阶段十实际AI辅助

Codex实际写并运行外部路阻固定值重拟合、牛津热预测、60组误差裕量对照和一阶RC训练/比较；生成协议、哈希、代码、7项核查、图表及论文补充。外部参数迁移失败如实保留。工具使用详情新增A-07，人工工作没有新增已完成证据；具体发布版本未核实。''')
append('docs/交付与复现说明.md', '阶段十更新入口', '''## 阶段十更新入口

最新两包已加入外部数据试用、测温处理和新RC比较；第八阶段文件留在开发目录的交付材料/历史版本/阶段八。直接给同学最新C题_同学复现包.zip即可，不需要另下载EPA或牛津文件。

旧all仍复现原冻结证据。新结果核对与完整重算：

```powershell
python scripts/run_extension.py
python scripts/recompute_extension.py
python scripts/predict_voltage_rc.py --trip 588
```

第二条从输入隔离重新拟合全部新实验，核对1791数值及状态。第三条实际运行推荐RC，输出到results/RC预测。外部输入原许可及对象说明在records/experiments/extension_v1/inputs。EPA随包为本轮实际使用的458行列提取，原XLSX下载地址及SHA256在provenance.json；不伪装成本车试验。牛津原始样例MAT与README随包。

推荐RC仅指本轮电压预测。旧控制、长程及300次区间仍由原模型支持。滚阻、真实热精度、宽SOC和真实传感器校准仍未解决。''')
append('README.md', '阶段十最新进展', '''## 阶段十最新进展

见[五项问题的试验结果与处理决定](docs/五项问题的试验结果与处理决定.md)。外部路阻/温度已实际试用，未解决本车滚阻或真实热精度；测温采用裕量方案并保留59/60结果及低温失败。电压推荐一阶RC：588/600回顾RMSE6.03/8.30V，原Rint及OCV缩放保留。

python scripts/run_extension.py核对结果并同步文档；python scripts/recompute_extension.py隔离完整重算；python scripts/predict_voltage_rc.py --trip 588实际运行推荐模型。原all、improve及66项冻结证据不改。论文5.6/6.7和最新两包同步这些新增结论；旧交付留历史版本/阶段八。''')
print('Stage10 state/documents updated')
