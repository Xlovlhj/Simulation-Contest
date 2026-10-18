# 把当前项目上传到Simulation-Contest的ybs-c分支

**本地准备已完成，最后由本人执行推送。** 目标为`https://github.com/Xlovlhj/Simulation-Contest.git`，分支`ybs-c`。用户确认远程分支尚未创建。旧远程`origin`保留，新目标使用`contest`，避免后续误推到旧仓库。

## 按顺序执行

在`c2026-ev-simulation`文件夹中右键打开PowerShell。以下各组命令成功后再执行下一组；报错时保留错误信息，先处理后续再继续。

### 1. 确认所在分支

```powershell
git switch ybs-c
git status
```

当前配置已在本地提交，正常应显示`nothing to commit, working tree clean`。

### 2. 确认目标仓库

```powershell
git remote set-url contest https://github.com/Xlovlhj/Simulation-Contest.git
git remote -v
```

`contest`已在本地配置。输出中它应指向新目标；`origin`是旧仓库，保留供回溯。本次使用`contest`推送。

### 3. 检查LFS和完整文件

```powershell
git lfs install --local
git lfs ls-files
git lfs fsck
python reproduce.py verify
```

LFS清单应包含轨迹压缩文件和15秒开发样片，文件校验应通过。当前本地提交的Git对象内保存LFS指针，工作目录仍是完整原文件；这两种形式的不同是正常现象。

### 4. 获取远程状态

```powershell
git fetch contest
git ls-remote --heads contest ybs-c
```

按用户确认，第二条应成功且无输出，表示分支尚未创建；它如果报错，不能视为“分支不存在”。如有哈希输出，说明分支已存在，先核对内容再决定合并，不执行强制推送。

网络连接重置、仓库不存在或无访问权限需先处理网络/登录/仓库授权。HTTPS认证按Git Credential Manager提示在浏览器完成；GitHub账号普通密码不能代替访问令牌。不要将凭证写入远程URL或文件。

### 5. 上传并创建远程分支

```powershell
git push --set-upstream contest ybs-c
```

普通推送会通过Git LFS钩子上传两份LFS对象，再上传Git提交。成功后在仓库网页的分支选择器切换`ybs-c`查看文件；默认首页可能仍显示其他分支。该命令不会要求改动远程默认分支。

如果普通推送提示non-fast-forward，保留当前分支，不添加`--force`；先获取并核对新出现的远程提交。

## 队友克隆

```powershell
git lfs install
git clone --branch ybs-c --single-branch https://github.com/Xlovlhj/Simulation-Contest.git
Set-Location Simulation-Contest
git lfs pull
python reproduce.py verify
```

随后按README执行prepare、安装依赖和all。LFS未下载时，轨迹文件可能只有百余字节的指针文本，文件校验会明确失败。

## 这次改动的范围

已整理README、忽略规则和LFS规则；不重新训练模型，不改科学输入、冻结参数或数值结果，不重写旧仓库已发布历史。新的快照清单明确记录文档及配置变化，并保留之前清单。人工核验与AI披露仍按真实完成情况更新；未代替用户执行远程推送或比赛提交。
