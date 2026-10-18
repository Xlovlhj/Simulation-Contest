# C题：新能源汽车能量流与热管理仿真

2026年第二届全国大学生仿真建模应用挑战赛C题工程。建立电动车模型、用行驶记录校准参数，并比较节能控制策略。

本仓库是可复现的技术稿快照；人工核验和AI披露尚未完成，正式视频暂缓。缺失本车热实测、独立路阻及宽SOC数据等限制如实保留。

## 阅读入口

- [19页最终技术论文](交付材料/论文/C题论文_最终稿.pdf)
- [三问任务、通俗解释和成果](docs/三问任务与成果速览.md)
- [本地仓库与GitHub Desktop操作](docs/GitHub本地仓库使用说明.md)
- [复现说明](docs/交付与复现说明.md)
- [AI使用详情工作稿](AI工具使用详情.pdf)

克隆或下载后，双击`C题项目管理.html`，集中阅读文档、术语和结果；GitHub网页不直接运行这个HTML。

## 复现

建议Python 3.12，在仓库根目录依次执行：

```powershell
python reproduce.py verify
python reproduce.py prepare
python -m pip install -r records/requirements-lock.txt
python reproduce.py all
```

`verify`核对本次GitHub初始快照，`prepare`恢复23条轨迹并核对9份官方输入；`all`核对17个主要数值和34项自动检查，冻结结果按指纹复用，不重新进行300次非线性拟合。新增外部数据及RC实验完整重算使用`python scripts/recompute_extension.py`。

## 目录

| 目录 | 内容 |
|---|---|
| src、checks、scripts、configs | 代码、检查与参数 |
| data、赛题资料 | 必要数据、题目、来源和许可 |
| records、results | 冻结记录、实验结果和图表 |
| payload | 无损压缩的原轨迹及清单 |
| docs、paper、portal | 文档、论文模板与工作台 |
| 交付材料/论文 | 当前论文PDF |

原始同学包清单保存在`records/github_source_package_manifest.json`；当前`PACKAGE_MANIFEST.json`显式记录本仓库整理后的文件。科学代码、输入、参数和冻结结果保持原有数值。后续修改已有文件时保留Git历史、完成相应核验并发布新版本；原快照校验不适用于改后的文件。
