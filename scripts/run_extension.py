"""阶段十入口；不更改原run.py/reproduce.py及旧实验。"""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.extension import run
from bev.extension_report import publish
from bev.portal import build_portal

run()
summary = publish()
print('Voltage model:', summary['preferred_voltage_model'])
print('Guarded comfort/control:', summary['guarded_practical_pass'], '/', summary['guarded_sensor_trials'])
print(build_portal())
