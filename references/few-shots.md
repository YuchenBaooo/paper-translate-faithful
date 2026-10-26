# Cox 2021：可迁移的 few-shot

来源及许可见 [署名与改编说明](../examples/cox2021/attribution.md)。原论文节选、中文改编和示例标题区别登记。原始案例的页数/字体/对象数量不是新论文要求；源 ID 仅用于追溯本例。

机器可读的九组输入/错误/决策见 [fewshots.json](../examples/cox2021/fewshots.json)。可运行合同 [reader-plan.json](../examples/cox2021/reader-plan.json) 含 23 个教学单元、22 个原生资产；仅节选，不是全文重译。[教学页预览](../examples/cox2021/teaching-preview.png) 是该合同的真实输出；[前后排版对照](../examples/cox2021/before-after-inline.png) 记录整篇作业中的实际修复。

## 1. 固定旧槽位 → 完整中文段落

**输入**：原文含 `Here r = (r, φ) while ψ±ℓ= A(r) exp(±iℓφ)...`，多个数学对象散在一段英文中。严格旧坐标译本在中文词组间留下大片空白。

**错误**：每个短语原位替换，认为字形和保护区像素通过就完成。

**输出模式**（实际合同单元 cox-p03-b042）：

```text
有{{asset:r}}，而{{asset:psi}}由包络{{asset:envelope}}及方位相位{{asset:azimuthal}}组成，其拓扑荷为{{asset:charge}}，携带{{asset:oam}}的OAM/光子。参数{{asset:a}}、{{asset:b}}和{{asset:alpha}}控制两偏振分量的相对振幅与相位，生成高阶庞加莱球上的全部态[72]。
```

这是合同内整段的节选；实际渲染使用完整 target，包括后一句。每个数学资产按原大小与基线随句排布。对下一论文，先确认是否已授权重排，再复用机制；不能照搬本段词汇。

## 2. 跨行 ψ 与被拆 C²/n

**输入**：p02-b041 的 ψmn(z) 表达式跨行；整个外接框中夹有不相关 Hm。p04-b006/b007 则把同一个折射率结构参数的上标本体与下标拆成不同提取块。

**错误**：把相交/相邻块递归合并，或把每个提取块当独立公式，带入多余 Hm、留下孤立 n。

**输出**：经原页与上下文确认后，用明确的 ordered pieces 组成 psi-mn、cn 两个独立资产；Hm 自己保留。检查 source-excerpts.pdf 相应资产页和 reader-plan 的 cox-p02-b041/cox-cn-definition。

## 3. 旧数学字体与独立公式

**输入**：CMEX 字形中的括号，其 Unicode/control text 和名义 bbox 不能描述实际轮廓。式(11) 还含英文条件词及独立编号。

**错误**：用 OCR/字符串“补”括号；整块公式复制时把邻近正文和旧编号带入，再叠一个新编号。

**输出**：eR/eL 资产保留真实括号、转置上标；glyph-bounds 给出嵌入 glyph ID 轮廓范围，再实际看图。式(11) 保留数学字形，把 for 译为“当”，编号单独登记一次；最终候选查看指数、负号、条件范围和编号净空。这里资产是本次已修复节选，不把某一 PDF 的 xref/字体资源名写进通用算法。

## 4. 黑色渐变器件中的 Laser

**输入**：[原标签](../examples/cox2021/laser-before.png)、[审过的旧字形 mask](../examples/cox2021/laser-mask.png)，目标“激光器”。

**错误**：白矩形遮盖，破坏圆柱渐变；整图生图重画，改变科学内容。

**输出**：[实际局部结果](../examples/cox2021/laser-after.png)。`raster_label_patch` 只修 mask，采用 local-harmonic 恢复背景，再放白色中文；核对 mask 未跨器件边界/光线，编辑区外变化为 0。这不自动证明 mask 内科学内容没被误删，必须看原图。示例大小为位图估计，不假称原字体精确恢复。

## 5. 表格、文献和最终文件

**输入**：表 I 的 `c=0, M1=1.759, M2=-0.7626, N1=-1.289, N2=0.3166, σ²=2150`；原提取块可能含多个文献条目。

**输出**：表体原网格和行列数据保持，表题译为中文；文献 [209]–[211] 按条目拼接原生行，保留原语题录、作者顺序与斜体。编号列块不能直接作为一个条目。平衡末页时只在条目边界分栏。

遇到换行闭标点，先判断非空，再移整串闭标点及前面的语义原子，避免 `'' in CLOSE` 误判。字体压缩在此论文出现长时间卡住后，改为可选有界 worker；比较全页像素/文本/书签后才采用，失败保留原字节。

## 运行、看图、拒绝未审校发布

从技能目录执行，输出目录必须尚不存在：

```text
python scripts/readerflow.py render examples/cox2021/reader-plan.json TASK/cox-demo
python scripts/readerflow.py audit TASK/cox-demo
python scripts/raster_label_patch.py examples/cox2021/laser-patch.json TASK/laser-demo
```

查看 TASK/cox-demo/pages 的所有图片和各资产。此例 render 后 review 保持 pending，直接 publish 应被拒绝。不能把示例通过记录复制成新论文的审校结果。可用以下命令复测原位及阅读辅助行为，报告均留在运行目录 qa-tests 下：

```text
python -B -m unittest discover -s scripts -p "test_*.py" -v
```
