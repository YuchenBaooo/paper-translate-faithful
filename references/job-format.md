# 作业格式及运行

需要 Python 3.10+ 与 PyMuPDF >=1.24.4。Windows 示例：

```powershell
python '<skill>/scripts/paperflow.py' doctor
python '<skill>/scripts/paperflow.py' prepare 'D:/papers/input.pdf' 'D:/papers/job-001'
# 查看 source-pages/*.png，修正 plan.json
python '<skill>/scripts/paperflow.py' freeze 'D:/papers/job-001'
python '<skill>/scripts/paperflow.py' batch 'D:/papers/job-001'
# 当前智能体读取 batch.json；写 translations-response.json
python '<skill>/scripts/paperflow.py' submit 'D:/papers/job-001' 'D:/papers/translations-response.json'
python '<skill>/scripts/paperflow.py' review-template 'D:/papers/job-001'
# 完成 extraction 和 units 实际审校，visual 暂时保持 pending
python '<skill>/scripts/paperflow.py' render 'D:/papers/job-001'
python '<skill>/scripts/paperflow.py' audit 'D:/papers/job-001'
# 逐页检查 qa-pages/*-pair.png 和实际 PDF；填写 visual 的真实候选哈希
python '<skill>/scripts/paperflow.py' publish 'D:/papers/job-001' 'D:/papers/input.zh.pdf'
```

首次 audit 因 visual pending 返回 fail 是预期行为，不要提前伪造 pass。`publish` 会重算全部检查；已有目标文件拒绝覆盖。

## plan.json

- `units`：每个出现位置一个 ID。所有原文字层 span 恰好归属一个 unit；合并时保留按阅读顺序的 `span_ids`，`source` 为这些文字串拼接（可换行但不能改字）。单元必须同字号、同字重、同斜体。多样式单元拆分，不能取平均字号。
- `page`：1 起始实际页序；`bbox`：未旋转页的 PDF point 矩形。native 目前只接收零旋转且 cropbox 原点为零的页面；其他走专门外部路线，并接受同一审计。
- `action`：translate 或 preserve；preserve 写真实 `reason`。未识别单元不得通过写 preserve 跳过。
- `role`：来自 typography 角色；自动提取的 unclassified 要对原页确认。不根据段落位置猜摘要和关键词。
- `kind`：text 或 raster_label。位图标签补 `source_visual_evidence`、`background_rgb`（0–1）和 `background_reviewed`。背景审核指检查了遮罩范围没有曲线/数据，不是仅仅“白色”。
- `size_pt`、`bold`、`italic`：从原文继承。OCR/位图标签也须测量后填写，不拿正文默认值猜。
- `protected_tokens`：变量、单位、缩写、引用、URLs 等，记录必须逐字保留的串。native 只负责同字体的安全文字区；复杂混排改专业引擎，不丢掉数学样式。
- `table_id,row,col,rowspan,colspan`：表格角色需登记。表格数字/公式可设 preserve，表头文字单独翻译，不能整表保留却声称已译表内文字。
- `protected`：保留对象的页与 bbox；公式 `kind: formula` 不允许覆盖。完整图可 `allow_label_edits: true`，仅容许位于图内的 figure_label/table_cell 等经过核对的标签框；其余图形像素仍检查不变。
- `page_inventory`：全篇逐页登记原图查看证据，包括位图里有没有文字；`all_visible_content_registered` 是核对后的结论，不是自动提取器能够代填的字段。

修改 plan 后重新 freeze。`contract.json` 冻结源文件、清单、术语与字体哈希。后续任何变化都使旧译文/审校/候选失效；不要手改哈希绕过。

## 翻译响应

```json
{
  "contract_sha256": "从 batch.json 原样带入",
  "translations": [
    {"id": "p0001-b0001-l000-s000", "text": "结果显示……"}
  ]
}
```

当前智能体负责最终句子；工具仅准备、校验和回填。每个可译 ID 出现一次；重复句在不同位置也不能去重。数字、符号和保护片段精确核对。数字格式变化可能触发保守拒绝，应解释并修正源译映射，不关闭检查。

## 审校响应

`review-template` 创建 `review.json`：

- `reviewer` 和 `method` 写实际智能体/人员及审查方法；串行自审写 serial-self-review。
- extraction：pass、全部实际页码、原图核对证据。
- units：每个 ID 的 meaning/terminology/numbers/structure 都通过且有简短证据。
- visual：实际 candidate SHA-256、pass、全部页码、逐页查看证据。
- unresolved：必须为 [] 才能交付；填入问题会阻止发布。

不伪造审校，不把未做事项改成 pass。自动检查能验证记录和哈希，不能验证审校者是否真的看过。

## 外部候选

```powershell
python '<skill>/scripts/paperflow.py' stage JOB ENGINE_RESULT.pdf
```

外部产物仍要求相同页数/尺寸、每个目标文本在原区域、字号/字重/斜体一致，写区之外渲染不变。BabelDOC 如进行了全页字体、链接或图形变化，严格审计可能拒绝；应据报错缩小改动范围或换路线，不调低门槛冒充精准对齐。

## 返回码

- 0：命令本身成功；prepare/freeze/render 成功不表示最终译本合格。
- 1：audit 已完成但门槛未通过。
- 2：输入、依赖、冻结合同、字号容量或审校前置条件阻止操作。

candidate.json/audit.json/delivery.json 分别记录候选、审计、实际交付；中间产物不覆盖源文。
