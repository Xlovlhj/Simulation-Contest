# V2论文支撑材料

此包对应V2论文：305边/308转向、221个内部反演候选、44组治理运行。包内SUPPORT_MANIFEST.json列出实际所有文件及SHA-256，以下为使用说明。

直接打开outputs/revision_v2/index.html查看冻结结果，或运行python3 project_v2.py启动本地只读页面。运行python3 scripts/package_paper_v2.py --check核验解压文件。查看与核验不联网、不采集、不运行仿真。

主要目录：data/raw/baidu保存脱敏观测，data/processed保存结构化样本与路网；research/parameter_audit保存来源、参数处置及历史计数；outputs/identification_v2/calibration保存反演及时间评价，outputs/governance_v2保存44组摘要与逐次小记录；manuscript/v2保存论文源文；outputs/paper_v2保存论文、AI详情和图表；scripts保存实际程序。

为了满足20MB上限，包不含SUMO/Python环境、完整大型逐步XML、55.9MB政府环评原PDF或视频。保留图4所需两组实际FCD。环评下载网址、文件摘要、表53/82页码及提取计数保存在来源目录中。原始全量运行产物在主工作区保留，包内run_manifest列出的某些大文件未随包携带，不能据此宣称包内已核验所有原始XML。

完整重算需要准备Python与SUMO1.28.0；依赖见requirements-project.txt。以python3 scripts/calibrate_inverse_v2.py --help、python3 scripts/run_governance_v2.py --help查看真实选项。标定程序输出目录固定，已有fit会拒绝覆盖；重算应另外复制整个工作目录，把副本内原calibration目录另存后依次freeze、fit、evaluate。治理程序可以--output指定独立输出目录。缺少大型XML的现有运行目录不能当作完整缓存使用。不要移动或清理原工作区的冻结成果。只复核数字、阅读论文或运行只读页面不需要安装这些工具。

paper.pdf为当前机器字体替代版；paper.docx指定规定字体，需在合法字体环境重导出。AI详情位于outputs/paper_v2/ai_usage.pdf。队员独立核心工作与实质复核未记为完成。V1视频未重新录制，不代表本论文的治理结论。
