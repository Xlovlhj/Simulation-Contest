# B题V2材料与正式提交检查

当前论文、AI说明与支撑材料采用V2：305边/308允许转向、221个内部反演候选、44组治理。V1论文已完整归档；B_demo.mp4与旧B_support.zip仍是V1，不能与V2论文混交。

## 当前材料

- B_paper.pdf、B_paper.docx：19页匿名论文及可编辑文件。
- B_ai_usage.pdf：3页V2实际AI详情；同一内容在新版支撑包outputs/paper_v2/ai_usage.pdf。
- B_support_v2.zip：代码、脱敏必要数据、结果、4图及两组图示FCD、只读页面、AI说明；包内SUPPORT_MANIFEST.json逐项记录大小及SHA-256。
- support_manifest_v2.json：包外清单及整包摘要。
- 承诺书_待队员填写签署.doc：官方空白模板，未代填身份或签名。

论文内容审阅见outputs/quality/paper_v2_content_review.json（147项）；技术检查见paper_v2_technical_check.json（215项）。原V1 delivery_check.json只证明旧版历史交付，不是V2验收。包的实际收录与省略范围见manuscript/v2/SUPPORT_README.md。

## 尚需完成的参赛事项

1. 视频：需按V2模型、治理负结果重新录制，现有视频仅作历史参考。
2. 核心工作及AI规则：赛方要求核心建模、仿真实现与分析由参赛队员独立完成。本次AI已深度参与并如实披露；队员须按正式适用规则独立完成必要核心工作、理解并实质修订成果。自动审阅不是人工完成记录。包内AI规定标2025年试行，格式规范引用2026年版本，须核实适用文件。
3. 字体与分页：DOCX指定题目三号黑体、一级四号黑体、其余小四宋体和单倍行距；本机PDF嵌入Noto等替代字体。须在合法规定字体环境重新导出，核对目录、表格和连续居中页码。当前论文无页眉、无队员身份。
4. 队号与承诺：自行填写并签署承诺书，扫描为PDF/JPG/PNG，单独提交。正式命名为B队伍号.pdf、B队伍号fj.zip、B队伍号cns.pdf/jpg/png；视频连字符写法以提交系统最新要求为准。
5. 正式提交：论文、承诺书及支撑材料截至北京时间2026-10-20 20:00；视频截至10月21日20:00。上传后还须点击提交并确认“已提交”。本项目未代签、未自动上传或发送邮件。

## 体积与使用

论文PDF与支撑ZIP各≤20MB；视频≤3分钟、至少1280×720、≤300MB，单独提交。新版支撑包不含环境、全部大XML、55.9MB环评原PDF、视频或凭据；保留图4实际用到的两组FCD。完整源输出在工作区保留。

```text
python3 project_v2.py
python3 scripts/package_paper_v2.py --check
```

解压包也可运行上述查看与核验命令。重新运行仿真需准备SUMO1.28.0与相应依赖，见包内manuscript/v2/SUPPORT_README.md。不要压缩整个工作区或混入config/*.local.json。
