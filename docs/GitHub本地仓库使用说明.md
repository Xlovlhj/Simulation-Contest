# 已整理好的GitHub本地仓库

**当前目标已改为已有仓库的`ybs-c`分支：** 请优先使用[终端上传指南](GitHub上传到ybs-c分支.md)。下文保留首次创建独立仓库时的Desktop操作。

**直接添加比赛目录下的`c2026-ev-simulation`文件夹。** 它是专门整理的独立副本，已带本地Git仓库、首次提交、必要数据、19页论文、代码、轨迹压缩文件和项目工作台。

## 现在只需四步

1. 打开GitHub Desktop，选择 **File → Add local repository**。
2. 点击 **Choose…**，选择与`C题项目`同级的 **`c2026-ev-simulation`**，再点 **Add repository**。
3. 点击顶部 **Publish repository**。仓库名保留`c2026-ev-simulation`，**保持勾选`Keep this code private`**。
4. 点击 **Publish repository**，等待上传完成。点 **View on GitHub** 即可查看。

如果还没登录，先按软件提示登录自己的GitHub账号。首次提交已在本地准备，不必重新挑选文件、初始化或另建README。原`C题项目`开发目录和交付ZIP继续保留；仓库整理和GitHub发布都不等于比赛系统提交。

## 里面有什么

包含模型与核查代码、参数、官方输入、外部数据及许可、冻结结果、论文源文件与PDF、真实AI详情工作稿、开发日志和离线HTML。轨迹放在`payload/`，用复现命令恢复；缓存、重复运行副本和旧ZIP已排除。15秒开发样片仅用于工作台演示，正式视频仍暂缓。

`PACKAGE_MANIFEST.json`是此次GitHub初始快照的文件清单。原同学包清单另存在`records/github_source_package_manifest.json`，可以追溯导入来源；没有把整理后的文档谎称为原始包字节。科学代码、输入、参数与冻结数值保持一致。

## 队友如何运行

在仓库网页“Settings → Collaborators → Add people”邀请队友，由本人操作。对方接受后，用GitHub Desktop克隆仓库，进入仓库根目录依次运行：

```powershell
python reproduce.py verify
python reproduce.py prepare
python -m pip install -r records/requirements-lock.txt
python reproduce.py all
```

建议Python 3.12。首次安装依赖通常需要联网；已有数据的数值复现和工作台阅读不依赖联网。`all`核对17个主要数值和34项自动检查，新增RC等试验另用`python scripts/recompute_extension.py`。

仓库的README写明论文、工作台和结果入口。后续先Pull、修改并核验，再Commit和Push；修改已入清单的文件后，初始快照校验失败属于预期，应保留原提交并核查新版本。人工核验和AI披露仍按实际完成情况更新。
