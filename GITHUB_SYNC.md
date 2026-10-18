# B题GitHub同步说明

目标仓库：https://github.com/Xlovlhj/Simulation-Contest

目标分支：xqw-b。仓库现有ybs-c分支保存C题，本分支独立保存当前B题成果，不合并或改写ybs-c。

上传使用明确文件清单：V1和V2已交付支撑包的公开文件并集，加当前根目录说明、源码、赛题原件、实验记录、研究证据及提交材料。对应路径读取工作区当前内容；不会拿旧压缩包里的文本覆盖新版文件。清单与大小、SHA及检查结果见outputs/quality/git_upload_inventory.json。

百度AK/SK、config/*.local.json、工具环境、缓存、运行锁、本地日志及大部分可再生SUMO XML不上传。55.9MB环评原PDF留在本地，仓库保存公开下载地址、SHA、提取数据与关键页截图。图4所需两份FCD随成果保留。全部原始运行仍在原工作区，不因Git同步而删除。

仓库含最新19页论文、3页AI说明及B_support_v2.zip；V1交付归档和视频也保留并明确标为历史版本，视频尚未按V2重录。

Git操作使用工作区内的独立目录.cache/github_xqw_b_checkout。原工作区根目录已有只读空.git，本次不修改它，不修改系统Git配置。提交使用已登录账号的GitHub noreply地址。本分支不需要Git LFS；.gitattributes禁止自动换行转换，以免改变来源摘要。

克隆本分支后，可直接打开outputs/revision_v2/index.html，或运行python3 project_v2.py查看V2结果。完整重算的依赖和已省略大文件说明见manuscript/v2/SUPPORT_README.md。prepare_git_snapshot.py仅生成上传清单及可选本地副本，不自动联网、提交或推送。
