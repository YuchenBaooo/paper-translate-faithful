# 主路线与可执行备选

先按 SKILL.md 的当前任务约束选 alignment/reader。下表的原坐标、同页数门槛适用于 alignment；已授权阅读优化的 reader 使用 reading-layout.md/reader-plan.md 的内容与资产绑定验收，不对其错误套用原坐标检查。文字提取、识别、语义与图表准确性要求两者相同。

| 触发事实 | 主路线 | 备选及退出条件 |
|---|---|---|
| 电子 PDF，可靠文本层 | 原生对象提取 + 原页交叉核对；本技能 native 区域回填 | 混合公式/样式复杂时改 BabelDOC IR；不能只把文档重排为 Markdown |
| 行内公式混排、Form XObject 或复杂跨栏 | BabelDOC 有序段落捕获 → 当前智能体译文审校 → reviewed 重建 | IR 漏字或误保护时，按原页拆出安全文字区和公式保护区；不得以截图代替整页中文 |
| 电子文本与页面字形不一致 | PyMuPDF 与 Poppler 双路径定位，局部 OCR 原图复核 | MinerU / PaddleOCR，最后 Tesseract；一致结论仍需原图核对，不采用多数票冒充真值 |
| 扫描论文 | OCR 的字符、坐标和置信度 + 全页视觉核对 | 必须恢复所有文字单元、图表与公式区域后才 freeze；低清晰度无法判读留 pending |
| 可编辑的图内文字 | 分离自然语言标签，原坐标替换 | 如果文本和艺术图形不可分，转局部标签修复，不涂销整个图 |
| 位图中的英文 | 原图/坐标 → OCR/视觉读图 → 标签对照 → 精确遮罩文字 → 回填中文 | 简单纯色文字底可用 native 的 raster_label；复杂底纹、穿过曲线的标签须制作经检查的局部修补图片；失败即未满足 |
| 表格 | 原表网格/合并关系保留，逐单元格译文 | find_tables/MinerU 辅助定位 → 人工确认行列；不能整体当正文重新流排 |
| 有可信出版 TeX | 用 TeX 辅助识别公式、章节、标签 | 以出版 PDF 为最终几何依据；重编译页数改变不算原位合格 |
| native 字体或容量冲突 | 在原框内保持指定字号试排 | 不缩字绕过门槛；用户明确放宽后才变更配置和重审 |

## BabelDOC 的使用与本技能衔接

BabelDOC 和 PDFMathTranslate 是外部引擎，不是本技能强制安装的另一套 skill。按其当前真实 API/CLI 与 `--help` 接入；首次模型/字体下载单列，记录版本、原文哈希、引擎配置和产物哈希。先检查已安装的 CLI，失败才查看官方接口，不臆造参数。

使用 `capture`/`reviewed` 捕获与回放机制：捕获引擎实际分段，智能体完成译文和审校，然后离线回放。**不要原样采用按标准化源文本去重的映射**：重复句子在不同图表/语境中必须有不同 occurrence ID；若外部引擎接口无法保留位置身份，明确登记这一限制，回到本技能逐区域回填。

原位模式的 `plan.json` 是其统一合同。将捕获段落与原页对象建立显式映射，将最终译文纳入 `submit`，外部重建后 `stage`，再 `audit`。引擎若只保留表格或图中英文，需要补标签/单元格修复后重新 stage；禁止保留英文却报“全译”。原位外部 PDF 仍要核查每个单元在原 bbox 内的实际文字和字体；候选坐标不一致就失败。阅读模式另建 reader 合同；当前 readerflow 只验收自己生成并登记的候选，不能用它为任意外部 PDF 自动背书。

本技能包含原创可选桥接 `scripts/babeldoc_bridge.py`。在已验证的独立 Python 3.12/BabelDOC 0.6.x 运行时内：

```text
python scripts/babeldoc_bridge.py capture JOB --allow-model-assets
# 保留 babeldoc-segments.json；另存 review 副本，为每个 segment 填最终 text 和 unit_ids
python scripts/babeldoc_bridge.py render JOB --reviews JOB/babeldoc-reviewed.json --allow-model-assets
python scripts/paperflow.py stage JOB <引擎实际返回的候选路径>
python scripts/paperflow.py audit JOB
```

`--allow-model-assets` 只在当前任务已授权必要模型/字体下载时传入。桥接不调用翻译供应商，不去重重复句；回放顺序、公式占位、合同或单元绑定变化即拒绝。不能建立合法映射时，使用 native 分区域/其他审过的外部候选路线，不绕过绑定。桥接算法有单元测试；制作环境未装 BabelDOC，真实 SDK/模型运行未认证。其输出永远是候选，不自动放行原框字体或表格/图字。

## OCR 命令探测

已安装工具才调用；参数以本机 `--help` 为准。例如本地 MinerU 的公开入口是 `mineru -p INPUT.pdf -o JOB/ocr -b pipeline`，不是交给在线上传服务。Tesseract 在页面 PNG 上运行并输出 TSV/hOCR 可提供坐标。按 PDF 页面的 cropbox、旋转和渲染 DPI 换算回 PDF point；不能把 0–1000 的归一化坐标直接当 point。

OCR 只读副本。不要删除原始模型输出、转成有损 JPG 或用 OCR 文本层冒充原始字形；不要静默修改 PDF 尺寸或页码。重建后所有扫描页都必须逐页验收。

局部无损提取与本地 Tesseract 可用原创工具：

```text
python scripts/ocr_region.py SOURCE.pdf NEW_OUTPUT_DIR --page 2 --bbox 50 120 300 260 --dpi 300
python scripts/ocr_region.py SOURCE.pdf OTHER_NEW_DIR --page 2 --bbox 50 120 300 260 --dpi 300 --tesseract --language eng
```

第一条仅提图，不要求 OCR 环境；第二条调用已安装的本地 Tesseract。输出 `region.png`、`ocr-proposal.json` 和可选 TSV。坐标按实际 pixmap 的像素原点换算（包含边界取整），所有词的 reviewed 仍为 false，必须看图后才导入 plan。无字体标签 bbox 时测量并记录不确定性，不能默认复制整页的正文字号。
