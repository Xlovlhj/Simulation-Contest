# 把C题项目上传到GitHub

**已有整理好的本地仓库时：** 直接按[本地仓库使用说明](GitHub本地仓库使用说明.md)，在Desktop添加比赛目录下的`c2026-ev-simulation`即可。下面保留手工从头整理的操作，供以后重建时参考。

**推荐：GitHub Desktop＋私有仓库，从现有同学复现包开始。** 先备份并共享可复现版本，后续修改继续提交。当前技术稿已完成，人工核验和AI披露仍待本人据实完成；上传GitHub不会改变这些状态，也不等于完成比赛提交。

## 1. 准备账号和软件

1. 在 <https://github.com> 注册并登录账号。
2. 从 <https://desktop.github.com/> 安装GitHub Desktop，启动后点击“Sign in to GitHub.com”登录。
3. 按提示填写Git提交身份。邮箱可使用GitHub“Settings → Emails”提供的noreply邮箱；不需要把密码或令牌写进项目文件。

以下按钮名称以GitHub Desktop英文界面为例，版本不同可能略有差异。

## 2. 新建本地仓库

1. GitHub Desktop中选择“File → New repository”。
2. Name填`c2026-ev-simulation`；Description可填“新能源汽车能量流与热管理仿真”。
3. Local path选择方便找到的位置，默认的Documents/GitHub也可以。软件会在其下再建`c2026-ev-simulation`文件夹。
4. 不勾选“Initialize this repository with a README”；Git ignore和License先选None，原工程及外部数据的权利、许可说明照常保留。
5. 点击“Create repository”，再点“Show in Explorer”打开新仓库文件夹。

这一步只创建本地仓库，还没有上传。GitHub Desktop可能自动生成`.gitattributes`，下一步用提供的版本替换。

## 3. 把正确的项目文件复制进去

1. 找到原开发项目中的`交付材料/C题_同学复现包.zip`，另找一个临时位置**全新解压**。
2. 进入解压后的`C题复现工程`，复制它里面的全部文件和文件夹，粘贴进刚新建的`c2026-ev-simulation`仓库。
3. 复制的是内部内容，不要再套一层`C题复现工程`。仓库顶层应能直接看到`run.py`、`reproduce.py`、`README.md`、`src`等。
4. 从原开发项目的`docs/github_upload/`复制`.gitignore`和`.gitattributes`到新仓库顶层；遇到同名`.gitattributes`选择替换。保留新仓库已有的`.git`目录。
5. 可以把本指南另复制一份到新仓库的`docs/`，作为操作说明。

**先保持这份解压副本不运行、不编辑已有文件，完成首次上传。** 原开发目录含大量重复解压和核查副本，`records`约3.06 GB；不适合作为新手首次上传的起点。完整同学包已筛选必要内容，解压内容约65 MB，最大单文件约30.23 MB；该大小检查针对本次已交付快照。

### 应当上传的内容

以下都已包含在完整同学包中，按上述操作整体复制即可，不必逐个挑选。

| 文件或目录 | 用途 |
|---|---|
| `src/`、`checks/`、`scripts/`、`configs/` | 模型、参数、核查与运行脚本 |
| `run.py`、`reproduce.py`、全部`requirements*.txt` | 启动入口和依赖 |
| `data/`、`赛题资料/` | 官方输入、处理数据、公开数据与原许可说明；当前按私有仓库共享 |
| 包内`records/` | 冻结参数、实验结果、来源哈希、依赖快照及真实AI记录 |
| 包内`results/` | 图表与关键结果，保留科学结果，不是临时截图缓存 |
| `payload/` | 23条原轨迹的压缩文件及清单，**不能省略** |
| `docs/`、`paper/`、`portal/` | 项目文档、论文源模板、工作台源文件及第三方许可 |
| `C题项目管理.html`、`C题开发日志.md`、`AGENTS.md` | 离线阅读入口、过程记录与协作约定 |
| `交付材料/论文/C题论文_技术定稿.pdf` | 当前19页最终稿的兼容名称，内容与最终稿相同 |
| `AI工具使用详情.pdf`、`.docx` | 真实工作稿，不因上传就变成完整披露 |
| `README.md`、`PACKAGE_MANIFEST.json`、`EXPECTED_RESULTS.json` | 阅读入口、包内文件校验和数值基准 |
| 新增`.gitignore`、`.gitattributes` | 忽略运行副本并保持文件字节一致 |

### 不要额外搬入的内容

- 其他题目工程、整个外层赛题ZIP，以及本项目旧版交付包。
- 原开发目录的`records/package_checks/`、`records/extension_checks/`、`records/package_payload/`、`reproduced/`等重复副本。
- 原开发目录的全部未压缩`results/timeseries/`、浏览器核查截图、最终稿预览截图。轨迹通过`payload/`恢复，无需重复上传。
- Python环境`.venv/`、缓存`__pycache__/`和临时文件。
- 填好个人信息或签署后的承诺书、账号密码和访问令牌。

不要把“同学复现包.zip”作为仓库唯一内容。把内部文件展开上传，GitHub才能显示代码、比较修改和协作。

## 4. 检查后上传

1. 回到GitHub Desktop左侧“Changes”。应看到几百个必要文件，而不是数千个核查副本。
2. 确认`.gitignore`和`.gitattributes`已被选中，`payload/trajectories.tar.xz`也在其中；没有`.venv`和`__pycache__`。
3. 左下Summary填`Initial import: C problem reproducible project`，点击“Commit to main”。这是把当前版本保存进本地Git记录。
4. 点击顶部“Publish repository”。
5. 仓库名保留`c2026-ev-simulation`，**勾选“Keep this code private”**。当前仍在比赛准备阶段，先供本人和受邀队友使用；以后是否公开另行决定。
6. 点击“Publish repository”，等待完成，再点“View on GitHub”。网页应能看到README、源码目录、论文和工作台HTML文件。

首次上传不需要Git命令，也不需要Git LFS：当前推荐文件集合没有单文件超过100 MiB。GitHub浏览器直接上传单文件限制25 MiB，而本包轨迹压缩文件约30.23 MB，所以推荐Desktop。不要把网页预览HTML文件理解为已经部署网站；工作台仍以下载后双击方式使用。

## 5. 邀请两名同学

1. 在GitHub仓库网页进入“Settings → Collaborators”（部分界面为“Collaborators and teams”）。
2. 点击“Add people”，输入同学的GitHub用户名并发送邀请。
3. 同学接受邀请后，在各自GitHub Desktop中选择“File → Clone repository”，选择该仓库，完整下载。
4. 需要协作修改时，先“Fetch origin/Pull origin”，再修改、提交、推送；不要同时改同一段文件。

你自己执行邀请操作；本指南没有替你发送邀请或上传文件。

## 6. 同学如何复现

在克隆后的仓库根目录打开PowerShell，使用Python 3.12。先保持已有文件不修改，然后依次运行：

```powershell
python reproduce.py verify
python reproduce.py prepare
python -m pip install -r records/requirements-lock.txt
python reproduce.py all
```

`verify`检查交付快照中的文件是否变化，`prepare`恢复23条轨迹并核对官方输入，`all`隔离运行原主实验并核对17个主要数值及34项检查。新增外部数据与RC试验另用`python scripts/recompute_extension.py`重算，详细说明见包内README。

`.gitattributes`中的`* -text`用于避免Git自动把LF、CRLF互换，保持现有SHA256校验可复现。`.gitignore`只阻止新增临时文件进入Git，不删除本地文件，也不删除已经提交的内容。

**两种记录要分清：** Git提交记录允许你持续修改；`PACKAGE_MANIFEST.json`校验的是本次交付快照。修改已有论文或代码后，原快照校验失败属于预期现象，应重新做必要核验并发布新版本，不能为了通过检查直接跳过文件。新增本指南和Git配置文件不影响原清单中已有文件的校验。

## 7. 以后更新

1. 修改前点击“Fetch origin”，如有更新再“Pull origin”。
2. 修改代码或文档，完成对应核验；在真实人工记录中写明自己实际做了什么。
3. GitHub Desktop检查“Changes”，Summary写具体内容，例如`Revise thermal-model explanation`。
4. 点击“Commit to main”，再点“Push origin”。只有Commit仍是本地保存，Push后队友才能取得更新。

已有数据缺口、AI详情未完成、未签署和未上传比赛系统等状态仍应保留。不要在此阶段把技术稿标记为已正式提交。

## 官方操作说明

- GitHub Desktop：<https://desktop.github.com/>
- 发布已有项目：<https://docs.github.com/en/desktop/adding-and-cloning-repositories/adding-an-existing-project-to-github-using-github-desktop>
- 文件大小限制：<https://docs.github.com/en/repositories/working-with-files/managing-large-files/about-large-files-on-github>
