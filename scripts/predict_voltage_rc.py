"""运行本轮推荐的RC整车正向电压预测，输出另存。"""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from bev.calibration import prepare_trip
from bev.extension import begin, rc_forward
from bev.io import read_json, write_json

parser = argparse.ArgumentParser()
parser.add_argument('--trip', type=int, default=588, choices=[575, 596, 588, 600])
args = parser.parse_args()
begin()
parameters = read_json(ROOT/'records/experiments/extension_v1/rc_parameters.json')
trace, out, pred = rc_forward(prepare_trip(args.trip), parameters, .5)
destination = ROOT/'results/RC预测'
destination.mkdir(exist_ok=True)
trace.to_csv(destination/f'路程{args.trip}_RC轨迹.csv', index=False, encoding='utf-8-sig')
write_json(destination/f'路程{args.trip}_RC摘要.json', out)
print(destination)
