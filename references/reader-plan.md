# reader 合同与辅助输入

`paperflow.py` 的原位 plan 与本合同不同，不能互换。完整、可运行示例：`examples/cox2021/reader-plan.json`，它仅是教学节选；取结构与机制，不能把示例文句、数量、字号作为新论文默认。

## 顶层与单元

`edition:"reader"`、`mode_basis`（当前任务的重排依据）、`assets_manifest`、`page`、`fonts`、`styles`、`units`、`blocks` 必填；`version:1` 指 reader 数据格式，技能版本另为 2.0.0。路径相对各自 JSON；字体可用 `auto-cjk`/`auto-cjk-bold` 或实际绝对路径。系统找不到字体则失败，字体不随包分发。

```json
{
  "page":{"width":595,"height":842,"margins":[40,48,40,40],"columns":2,"gutter":20},
  "fonts":{"regular":"auto-cjk","bold":"auto-cjk-bold","latin_regular":"tiro","latin_bold":"tibo"},
  "styles":{"body":{"size_pt":10,"bold":false,"leading":1.43,"indent_em":2,"after_pt":5,"max_tracking_pt":0.6},"heading":{"size_pt":10,"bold":true},"caption":{"size_pt":8,"bold":false,"leading":1.43}},
  "units":[{"id":"p1-s1","action":"translate","delivery":"text","source":"Source sentence and its equation.","target":"经核对的中文{{asset:m1}}。","required_assets":["m1"]}],
  "blocks":[{"id":"b1","kind":"paragraph","unit_ids":["p1-s1"],"text":"经核对的中文{{asset:m1}}。","style":"body"}]
}
```

这段展示字段，须补顶层 mode/manifest 与真实 m1 资产后才能运行。所有长度为 PDF point。`styles` 按原文角色提取；同角色字号/字重不同就拆样式。`leading` 是倍率，行中数学更高时自动增加净空；不是全篇固定行距。字体与字号的原文对应仍需审校证据，renderer 不会自动从源 PDF 推导该映射。

每个 `unit`：稳定 `id`、`source`、`action:translate|preserve`、`delivery:text|asset`。text 要有非空 target；asset 要有 asset ID 且 manifest 的 `source_units` 含此 unit。preserve 必须有 reason。译在图资产中的标签也单独登记，target 写实际译文；脚本核对绑定，审校核对资产中的真正文字。

`required_assets` 统计**出现次数**：一个物理资产服务多个单元时，仅在一个单元登记该次出现，其余通过资产 source_units 绑定；句中重复数学对象须按真实次数登记。每个 unit 在 blocks 的 unit_ids 中恰好一次；text/图注必须等于对应 text units 的 target 拼接（忽略空白，不忽略文字）。未知资产、缺漏、重复、损坏占位符都拒绝。

## block

| kind | 关键字段及行为 |
|---|---|
| heading | text/style、可选 level/full_width；书签，随下一内容。标题含数学时用特定样式 paragraph，避免把占位符写进标题文字 |
| paragraph | text/style、可选 full_width/keep_together；数学原子不拆分、不缩字；正文末两行与紧随公式保留 |
| equation | asset、可选 number；原大矢量公式，独立编号净空；manifest.contains_number 为 true 时不能再加 number |
| figure/table | asset/caption/caption_style、可选 full_width/fit；图题在下、表题在上，图表与题注一起留空间 |
| references | entries:[{id,asset}]、可选 balance_last_page；完整条目，不拆条目续行 |
| page_break | 显式分页，不能用来掩盖排版失败 |

超过单栏的不可拆数学原子会明确失败，须调整语义分段/授权版式或专门处理；不会无限循环或自动缩小。图表缩放与数学不缩放是不同处理，字体要求严格时图表内字体也须维持，设置 `fit:false` 并另选版式。

## 向量 assets-spec.json

```json
{
  "sources":{"original":{"path":"source.pdf","sha256":"实际源文件SHA256"}},
  "assets":[{"id":"m1","role":"inline_math","composition":"horizontal","gap_pt":0.6,"source_units":["p1-s1"],"evidence":"实际核对过的干净裁切依据","pieces":[{"source":"original","page":1,"bbox":[50,80,90,95],"baseline":91}]}]
}
```

role 可用 inline_math/equation/figure/table/reference/metadata。pieces 有明确顺序；horizontal 按 baseline 对齐；vertical 按行顺序加 gap_pt、可选 offset_x，常用于文献续行。`contains_number` 登记保留的旧编号。所有裁图需视觉确认无邻行、邻公式、旧编号及遮挡；“evidence 非空”不会证明它真实。

`vector_assets.py compose` 输出 assets.pdf/assets.json/逐资产 PNG，manifest 记录 PDF 哈希、每个资产页码、尺寸、基线、源单元、源片段及证据。内容流可能含裁切框外的原对象；供本地核对使用，导出第三方前须另核查可提取信息，不能把 clip 当文档脱敏。

## 参考文献与位图标签

reference-regions 输入包括 sources、按实际阅读顺序排列的 regions（每项 source/page/bbox/evidence），可选 expected_numbers 数组与 line_gap_pt。输出仍是待核对 vector assets-spec 提案，不直接等于通过审校。

label-patch 输入见 `examples/cox2021/laser-patch.json`：source_png/mask_png 及各自 SHA、mask_reviewed/evidence、edit_bbox_px、protected_bboxes_px、background:white|local-harmonic、font、font_size_pt_estimate、pixels_per_point、translation、center_px、text_rgb、可选 rotation/iterations。mask 中白像素为删除的旧字形，尺寸与原图一致；只改审过的局部背景，保护区不能碰。字号为测量估计，不能声称恢复了位图原字体元数据。检查 candidate.png 后才整合入图资产。

## 审校合同

render 写入 contract.json、layout.json、review.json；audit 验证冻结输入、候选哈希和技术结构。review 必须如实填写 reviewer/method、每个 unit 的 meaning/numbers/structure=pass 和非空 evidence、visual.status/readability=pass、完整页码与证据，unresolved 必须为空。pending、旧哈希、未知/重复单元和技术失败均不能 publish。不存在自动全绿模板；串行自审写 `serial-self-review`，不能伪称独立审校。
