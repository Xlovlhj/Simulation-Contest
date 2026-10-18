# C题：新能源汽车能量流与热管理仿真

2026年第二届全国大学生仿真建模应用挑战赛C题工程，维护在 [Simulation-Contest](https://github.com/Xlovlhj/Simulation-Contest) 的 **`ybs-c`** 分支。

项目建立电动车动力、电池与温控模型，用行驶数据校准参数，再比较节能控制策略。19页技术稿及复现材料已整理；人工核验与完整AI披露尚未完成，正式视频暂缓。

## 先看成果

- [最终技术论文](交付材料/论文/C题论文_最终稿.pdf)
- [三问任务、通俗解释与实验指标](docs/三问任务与成果速览.md)
- [模型原理说明](docs/C题建模原理说明.md)
- [复现与交接说明](docs/交付与复现说明.md)
- [AI使用详情工作稿](AI工具使用详情.pdf)

克隆后双击 **`C题项目管理.html`**，集中查看文档、术语、进度和结果图表。GitHub文件预览不直接运行该HTML。

## 下载本分支

安装 [Git](https://git-scm.com/) 和 [Git LFS](https://git-lfs.com/)，然后运行：

```powershell
git lfs install
git clone --no-checkout --branch ybs-c --single-branch https://github.com/Xlovlhj/Simulation-Contest.git
Set-Location Simulation-Contest
git lfs install --local
git checkout ybs-c
git lfs pull
```

如仓库为私有仓库，需要先取得访问权限并登录。轨迹压缩文件和MP4由Git LFS保存；先克隆再检出的步骤兼容本机Git 2.45.1与LFS初始化过程。完成`git lfs pull`后再核对文件。

## 复现

建议Python 3.12。在仓库根目录依次执行：

```powershell
python reproduce.py verify
python reproduce.py prepare
python -m pip install -r records/requirements-lock.txt
python reproduce.py all
```

- `verify`核对当前提交对应的交接文件清单，LFS文件必须已下载为完整内容。
- `prepare`恢复23条轨迹，并核对9份官方输入。
- `all`核对17个主要数值及34项自动检查，按指纹复用冻结结果，不重新运行300次非线性拟合。

新增外部数据、测温处理和RC电压实验完整重算另用：

```powershell
python scripts/recompute_extension.py
```

结果目录、依赖及重算范围详见[复现说明](docs/交付与复现说明.md)。数值复现不需要浏览器；重建论文需要可选排版依赖、Chrome或Edge及相应中文字体。

## 文件组织

| 文件或目录 | 内容 |
|---|---|
| `src/`、`checks/`、`scripts/`、`configs/` | 模型代码、参数与核查程序 |
| `data/`、`赛题资料/` | 官方输入、公开数据、来源及许可 |
| `records/`、`results/` | 冻结记录、实验结果与图表 |
| `payload/` | 原轨迹的无损压缩文件及清单 |
| `docs/`、`paper/`、`portal/` | 文档、论文模板及工作台源文件 |
| `交付材料/论文/` | 当前19页论文PDF |
| `run.py`、`reproduce.py`、`requirements*.txt` | 运行入口及依赖 |

## Git与LFS约定

`.gitignore`排除Python环境、缓存、重复运行目录和旧交付ZIP，保留必要科学记录及已有开发样片。`.gitattributes`关闭自动换行转换，并对以下文件使用LFS：

```text
payload/*.tar.xz
*.mp4
```

源码、文档、论文PDF及小型数据仍由普通Git管理。此前已上传旧仓库的历史提交保持原样，本次从`ybs-c`新提交开始使用LFS；没有重写已发布历史。

当前`PACKAGE_MANIFEST.json`记录本次整理后的文件；原同学包及LFS配置前的清单分别保存在`records/github_source_package_manifest.json`、`records/github_pre_lfs_manifest.json`。修改已有文件后应保留Git历史并核验新版本，原清单校验不适用于修改后的文件。

## 结论边界

原Rint模型支持控制、长程和参数区间；一阶RC用于回顾电压比较。588/600均不称全新盲测。容量按160 Ah参考、80 Ah对照透明处理；滚阻可靠性、宽SOC外推、本车真实温度和传感器校准仍缺证据，低温不作稳健节能结论。第三方数据和组件遵循各自附带的许可说明。

向目标仓库上传的步骤见[终端上传指南](docs/GitHub上传到ybs-c分支.md)。上传GitHub不等于比赛系统提交。
