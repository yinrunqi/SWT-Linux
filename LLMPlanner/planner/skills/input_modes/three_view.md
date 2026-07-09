---
name: three_view
description: 三视图输入模式 —— 从带尺寸标注的三视图图片提取结构化 IR
triggers: [三视图, orthographic, three-view, front top right]
applies_to: [three_view]
---
## 三视图输入模式（Vision 阶段 A）

用户上传的图片是一张**带尺寸标注的三视图工程图纸**。你的任务是**只做"看图 → 结构化 IR"**，
输出严格符合 `ThreeViewIR` schema 的 JSON 结构。**不要**在此阶段排 SolidWorks 步骤——那是阶段 B 的工作。

### 一、视图↔基准面映射（重要约定）

| 视图 | SolidWorks 基准面 | select_plane index |
|---|---|---|
| 主视图（front，看前面）  | 前视基准面 | 0 |
| 俯视图（top，从上往下）  | 上视基准面 | 1 |
| 侧视图（right，看右侧） | 右视基准面 | 2 |

**深度维度取自相邻视图**：
- 主视图给出零件的 **X（宽）** 和 **Y（高）**
- 俯视图给出零件的 **X（宽）** 和 **Z（深）**  ← Z 是主视图看不到的深度
- 侧视图给出零件的 **Z（深）** 和 **Y（高）**

拿到"总长/总宽/总高"时按此映射填入 `overall_size_mm`。

### 二、必须先逐条枚举全部尺寸

在填充 `profiles` 和 `features` 之前，**先完整枚举** `dimensions` 数组——图纸上**每一个可读到的
尺寸标注数字**都必须列出来（哪怕你觉得多余）。这是三视图能建对的**核心保障**。

- 每条尺寸至少给 `label`（人类描述）和 `value_mm`（毫米数值）
- 尽量补充 `applies_to`（如 `front.width` / `hole1.diameter` / `plate.thickness`），方便阶段 B 使用
- 如果尺寸标注写的是 φ/⌀（直径符号），value_mm 填**直径值**，label 里写"孔径 xxmm"或"直径 xxmm"

🚫 严禁遗漏尺寸；🚫 严禁把不同尺寸合并成一条。

### 三、零件类型（part_type）判断

看主视图轮廓与俯视图/侧视图深度即可决定：

- **extrude_prism**：主视图是任意封闭多边形/带孔多边形，俯视图和侧视图都是简单矩形
  （深度是常数）——最常见的 2.5D 拉伸件。
- **revolve**：主视图是关于中心线对称的半截面，俯视图是一个（或几个同心）圆环。——轴对称件。
- **plate_with_holes**：主视图为平板矩形，视图上分布多个圆（表示孔）。
- **stepped_shaft**：主视图为多段矩形拼接（不同直径的圆柱），俯视图为同心圆环。
- **l_bracket**：主视图为 L 型（一个矩形被切一个矩形缺口），俯视图/侧视图为矩形。
- **flange**：主视图为圆盘，俯视图/侧视图上均匀分布若干圆孔。
- 其它情况填 **other**。

### 四、特征提取要点（features）

- 每个圆（在主视图/俯视图/侧视图中）都可能是**孔**，都要提取为一个 `FeatureHint(kind='hole')`
- 圆的位置用**该视图内的相对坐标**描述（如"距左边 10mm、距底边 8mm"）
- 孔径写在 `size_desc`（如 `⌀5mm 贯穿` 或 `⌀5mm 深 10mm`）
- 图上有 R 标注 → 圆角（`kind='fillet'`）；C 或 45° 标注 → 倒角（`kind='chamfer'`）
- 长圆槽/腰形孔 → `kind='slot'`，标 length×width
- 凹坑/沉孔 → `kind='pocket'`

### 五、绝对尺寸恢复

**优先使用图纸上直接标注的毫米数值**（若数字后有 mm、单位默认为 mm）。如果整张图纸完全没有
尺寸标注（罕见），才把 `dimensions` 留空并在 `assumptions` 里注明"图纸未标注尺寸，需外部提示"。

### 六、profiles 描述格式

每个视图一条 `ViewProfile`：
- `view_name`：`front`/`top`/`right`
- `base_plane`：与 view_name 对应的 SolidWorks 基准面
- `outline_desc`：用自然中文精确描述该视图的形状。示例：
    - `"主视图轮廓：一个 60×40mm 的矩形，右上角切除一个 30×20mm 的小矩形形成 L 形"`
    - `"俯视图轮廓：一个 60×30mm 的矩形，中间从上到下分布 3 个等距圆孔（每个 ⌀5mm）"`

### 七、约束

- 输出必须是符合 `ThreeViewIR` schema 的**纯 JSON**（不要 markdown 代码块包裹）
- **不要**输出 SolidWorks 操作步骤——那是阶段 B 的事
- **不要**编造图上没有的尺寸；未标注就在 `assumptions` 里说明
- 若图片明显不是三视图（如照片/渲染图/单张视图），把 `part_type` 填 `other` 并在 `assumptions` 里说明识别到的情况

阶段 B 会拿到这个 IR + core 里的所有建模规则/示例，把 IR 精确翻译为 `SwModelingPlan`。

### 八、阶段 B 特别指引（防止典型错误）

以下几条**在两阶段管线的阶段 B（IR → SwModelingPlan）中格外重要**：

1. **L 形 / 带缺口的凹形轮廓 —— 严禁在同一草图里画外接矩形 + 缺口矩形**！
   两个矩形相交但不嵌套会触发 core/03_rules 中的"同一草图轮廓合法性约束"错误
   （SolidWorks 会报"草图轮廓无效/未闭合"）。**必须**采用以下两种正确画法之一：

   ⭐ **方案 A（强烈推荐，一次拉伸最省事）**：用 `sketch_line` 逐段画出 L 形闭合轮廓的每一条边线，
   再一次 `feature_extrusion`。**共 8 个步骤**，示例（外接 60×40mm，右上角切除 30×20mm，深度 20mm）：
   ```
   1. new_part {}
   2. select_plane {index: 0}                                            ← 前视基准面
   3. sketch_line {x1:0,    y1:0,    x2:0.06, y2:0}                      ← 底边
   4. sketch_line {x1:0.06, y1:0,    x2:0.06, y2:0.02}                   ← 右下短竖(高 20mm=0.02)
   5. sketch_line {x1:0.06, y1:0.02, x2:0.03, y2:0.02}                   ← 缺口底
   6. sketch_line {x1:0.03, y1:0.02, x2:0.03, y2:0.04}                   ← 缺口左竖(长 20mm=0.02)
   7. sketch_line {x1:0.03, y1:0.04, x2:0,    y2:0.04}                   ← 顶边(长 30mm=0.03)
   8. sketch_line {x1:0,    y1:0.04, x2:0,    y2:0}                      ← 左边(高 40mm=0.04)
   9. feature_extrusion {depth: 0.02}                                    ← 一次拉伸 20mm
   ```

   ⭐ **方案 B（先加料再切除，共 8 步）**：完整步骤**一步不能省**：
   ```
   1. new_part {}
   2. select_plane {index: 0}                                            ← 前视基准面
   3. sketch_rectangle {x1:0, y1:0, x2:0.06, y2:0.04}                    ← 外接矩形 60×40
   4. feature_extrusion {depth: 0.02}                                    ← ⚠️必须先拉伸出实体!!
   5. select_face_and_sketch {rx:0.045, ry:0.03, rz:0.01, dx:0, dy:0, dz:-1}  ← 选前面(Z=0 平面): 起点 Z=0.01, 方向 dz=-1 射向 Z=0 面
   6. sketch_rectangle {x1:0.03, y1:0.02, z1:0, x2:0.06, y2:0.04, z2:0}  ← 在前面画缺口矩形 30×20 (⚠️ z1=z2=前面 Z 坐标=0)
   7. feature_cut_extrusion {depth: 0.02, endCondition: "through_all"}   ← 贯穿切除
   ```

   🚫 **绝对禁止**：把 `feature_cut_extrusion` 放在 `feature_extrusion` 之前（空气里切不了东西）；
       或者只写 `sketch_rectangle` 两个矩形就直接 `feature_extrusion`（会报"轮廓无效"）。

1b. **T 形 / H 形 / 工字形 / 十字形等带多个凹角的复杂轮廓 —— 必须先在草稿纸上按 CCW 顺序推出所有顶点，再用 sketch_line 逐段画**。**严禁把每根"杆"当作独立矩形分别画**——多个矩形拼接会自相交/自重叠，SolidWorks 会报"草图轮廓无效"。

   ⭐ **T 形正确画法（共 11 步）**：
   例：外接 60mm(宽) × 45mm(高)，顶部横杆 60×15mm，底部竖杆 20×30mm 居中，拉伸深度 15mm。
   坐标系：主视图 X 轴向右、Y 轴向上，原点在**竖杆左下角**。

   T 形的 8 个顶点按 CCW（逆时针）顺序：
   ```
        ┌────────────────────────────────────────┐  ← y=45 (顶部)
        │  (0,45) ← 顶点 6                        │
        │                                        │
        │                       (60,45) ← 顶点 5  │
        │                                        │
        │  (0,30) ← 顶点 7   (60,30) ← 顶点 4     │  ← y=30 横杆底面
        │       ┌──────────────┐                 │
        │       │              │                 │
        │       │              │  (40,30) ← 顶点 3│
        │       │              │                 │
        │       │(20,30)← 顶点 8                  │
        │       │              │                 │
        │       │              │                 │
        │       │              │                 │  ← y=0 底部
        │       (20,0) ← 顶点 1  (40,0) ← 顶点 2  │
   ```
   步骤：
   ```
   1. new_part {}
   2. select_plane {index: 0}
   3. sketch_line {x1:0.02, y1:0,    x2:0.04, y2:0}       ← 竖杆底边（顶点1→2）
   4. sketch_line {x1:0.04, y1:0,    x2:0.04, y2:0.03}    ← 竖杆右边（2→3）
   5. sketch_line {x1:0.04, y1:0.03, x2:0.06, y2:0.03}    ← 横杆底右侧（3→4）
   6. sketch_line {x1:0.06, y1:0.03, x2:0.06, y2:0.045}   ← 横杆右边（4→5）
   7. sketch_line {x1:0.06, y1:0.045,x2:0,    y2:0.045}   ← 横杆顶边（5→6）
   8. sketch_line {x1:0,    y1:0.045,x2:0,    y2:0.03}    ← 横杆左边（6→7）
   9. sketch_line {x1:0,    y1:0.03, x2:0.02, y2:0.03}    ← 横杆底左侧（7→8）
   10. sketch_line {x1:0.02,y1:0.03, x2:0.02, y2:0}       ← 竖杆左边（8→1，闭合）
   11. feature_extrusion {depth: 0.015}
   ```

   🚫 **绝对禁止的 T 形画法**：
   - 把竖杆画成一个矩形、横杆画成另一个矩形，两个矩形直接叠加 → **自相交轮廓**，拉伸失败
   - 顶点顺序混乱（一会儿 CW 一会儿 CCW）→ 自相交轮廓
   - 从 (0.6, 0) 拉一条线直接连到 (0, 0.15) → **穿过竖杆内部**，自相交

   同类形状（H 形/工字形/十字形/E 形）都套用此方法：**先在坐标纸上定好所有顶点，再按 CCW 顺序逐段 sketch_line**。凹角数 = 顶点数 - 4（矩形有 0 个凹角、L 形有 1 个、T 形有 2 个、十字形有 4 个）。

1c. **梯形/键帽/花盆/漏斗 — 顶面与底面尺寸不同的零件必须用拔模拉伸**！

   识别信号（Vision 阶段 A 就要认出来）：
   - 主视图轮廓是**梯形**（两侧斜边，不是直角矩形）
   - 或者俯视图上出现"**外框实线 + 内框虚线/较小的方框**"—— 这就是键帽的经典画法：底面 18×18 外框 + 顶面 14×14 内框
   - 侧视图也是梯形（宽面在下、窄面在上）

   ⭐ **键帽正确画法（3 步，超简单）**：
   ```
   1. new_part {}
   2. select_plane {index: 0}                                            ← 前视基准面
   3. sketch_rectangle {x1:-0.009, y1:-0.009, x2:0.009, y2:0.009}        ← 只画**底面**轮廓 (18×18mm 居中)
   4. feature_extrusion {                                                ← 一次拔模拉伸搞定
        depth: 0.008,
        draftAngleDeg: 14.04,   ← atan((18-14)/2 / 8) ≈ 14.04°
        draftOutward: false     ← 顶面小于底面 → 内收
      }
   ```

   拔模角计算：`draftAngleDeg = degrees(atan((底半宽 - 顶半宽) / 深度))`
   - 键帽 底 18mm/顶 14mm/深 8mm → `atan(2/8)` ≈ **14.04°** 内收
   - 花盆 底 40mm/口 60mm/高 30mm → `atan(10/30)` ≈ **18.43°** 外扩（draftOutward=true）
   - 漏斗内壁 上口 50mm/下口 20mm/高 40mm → `atan(15/40)` ≈ **20.56°** 内收

   🚫 **绝对禁止**：
   - 只画底面矩形然后普通 `feature_extrusion depth=8`（结果会是 18×18×8 长方体，不是梯形键帽）
   - 试图分别画底面和顶面然后 loft 放样（当前 API 不支持 loft；拔模拉伸是唯一正确路径）
   - 把 draftAngleDeg 当作弧度传（**必须是度**！SolidWorks API 内部会自动转弧度）

   **验证**：拔模后包围盒仍等于底面尺寸（因为底最大）；体积用棱台公式 `V = h/3·(A底+A顶+√(A底·A顶))` 而非 `A×h`。

1d. **复合特征 — 底座之外的圆角/凹槽/面上多孔阵列（现代精巧件必备）**

   真实的日常物件（开关面板、门锁扣板、键帽、遥控器…）几乎都是"底座拉伸 + 若干附加特征"的组合。识别这些附加特征并按**正确顺序**追加到 SwModelingPlan 就是阶段 B 的核心工作。

   **A. 边圆角（feature_fillet）**：
   识别信号：图纸上外轮廓的角是**弧线**而不是直角；或标注了 "R5"、"⌢R3" 等字样。
   正确画法：先建好底座（拉伸完），**再**追加 `feature_fillet` 步骤。

   ⚠️ **edgeSelection 只支持三种取值**（其他任何字符串都会 400）：
   1. `"all"` — 圆角实体的所有边（会顺带把不想圆的边也圆角，需谨慎）
   2. `"面1_面2"` — 由两个面名交出的一条边（**每次只圆角一条边**）
      - 可用面名：`top` / `bottom` / `front` / `back` / `left` / `right`
      - 示例：`top_front`（顶面与前面的交线）、`front_left`（前面与左面的交线，即某个竖直棱边）
   3. `"ray"` — 用射线选一条边（需要 rx/ry/rz/dx/dy/dz 参数）

   ⚠️ **绝对不能用**：`"vertical_corners"`、`"top_perimeter"`、`"all_perimeter"` — 这些语义名 **C# 服务不认识，会 400**！要圆多条边**必须拆成多次 feature_fillet 分别指定**。

   **对应关系速查**（矩形板底座沿 Z 拉伸时）：
   | 你想圆角的边 | 该边由哪两面相交 | edgeSelection |
   |---|---|---|
   | 顶面周边（4 条）| top-front / top-back / top-left / top-right | 拆成 4 次 fillet |
   | 底面周边（4 条）| bottom-front / bottom-back / bottom-left / bottom-right | 拆成 4 次 fillet |
   | 四竖直棱边（4 条，连接顶底的立柱边）| front-left / front-right / back-left / back-right | 拆成 4 次 fillet |
   | 全部 12 条边 | 直接用 `"all"` | 一次 fillet |

   示例（86 型面板的 4 竖直棱边 R3 圆角，必须 4 次）：
   ```
   feature_fillet {radius: 0.003, edgeSelection: "front_left"}
   feature_fillet {radius: 0.003, edgeSelection: "front_right"}
   feature_fillet {radius: 0.003, edgeSelection: "back_left"}
   feature_fillet {radius: 0.003, edgeSelection: "back_right"}
   ```

   ⚠️ **顺序**：圆角必须在**所有拉伸/切除之后**执行，否则后续面上加特征时会因面变成曲面而失败。

   **B. 面上凹槽（不贯穿的矩形/圆形/腰形切除）**：
   识别信号：主视图或俯视图上出现"外框实线 + 内框实线"（不是虚线，因为凹槽底部可见）；侧视图上有"阶梯"轮廓。
   正确画法：`select_face_and_sketch` 选中要凹陷的面 → `sketch_rectangle/circle/slot` → `feature_cut_extrusion` 指定深度（**不用 through_all**）。
   ```
   select_face_and_sketch {rx:0, ry:0, rz:0.011, dx:0, dy:0, dz:-1}   ← 选顶面（Z=10 上方射线向下）
   sketch_rectangle {x1:-0.025, y1:-0.025, x2:0.025, y2:0.025}         ← 中央 50×50 凹槽
   feature_cut_extrusion {depth: 0.003}                                ← 挖深 3mm
   ```

   **C. 面上多孔阵列（对称 N 孔）**：
   识别信号：主视图或俯视图上有**多个相同尺寸的圆**（如 4 角螺丝孔、6 边法兰孔）。
   正确画法：一次 `select_face_and_sketch` → 多次 `sketch_circle`（一个孔一个圆）→ 一次 `feature_cut_extrusion(through_all)` 一起切。
   ```
   select_face_and_sketch {rx:0, ry:0, rz:0.011, dx:0, dy:0, dz:-1}
   sketch_circle {cx:0.04, cy:0.04, cz:0, radius:0.0015}   ← 右上 ⌀3
   sketch_circle {cx:-0.04, cy:0.04, cz:0, radius:0.0015}  ← 左上
   sketch_circle {cx:0.04, cy:-0.04, cz:0, radius:0.0015}  ← 右下
   sketch_circle {cx:-0.04, cy:-0.04, cz:0, radius:0.0015} ← 左下
   feature_cut_extrusion {depth: 0.01, endCondition: "through_all"}
   ```
   ⚠️ 4 个 sketch_circle **必须在同一次 select_face_and_sketch 之后连续画**，否则会退到不同草图导致 cut 只切中最后一个。

   **D. 面选择的坐标系（重要）**：
   `select_face_and_sketch` 用**射线**选中面。射线起点（rx/ry/rz）必须在目标面**外侧**，方向（dx/dy/dz）指向实体内部。
   - 顶面 (+Z 面, Z=depth)：起点 rz=depth+0.001, 方向 dz=-1
   - 底面 (-Z 面, Z=0)：    起点 rz=-0.001,      方向 dz=+1
   - 前面 (+Y 面, Y=max)：   起点 ry=max+0.001,   方向 dy=-1
   - 后面 (-Y 面, Y=min)：   起点 ry=min-0.001,   方向 dy=+1
   - 右面 (+X 面, X=max)：   起点 rx=max+0.001,   方向 dx=-1
   - 左面 (-X 面, X=min)：   起点 rx=min-0.001,   方向 dx=+1
   （即使方向搞反了，C# 服务会自动反转重试，但把起点放到面外侧不容易翻车。）

   **E. 面内草图坐标系**：进入面草图后，`sketch_*` 的 cx/cy 是**面内 2D 坐标**（不是模型 3D 坐标）。顶面/底面上，(cx, cy) 直接对应 (X, Y) 模型坐标；侧面上则映射到面所在平面。第一版建议只在顶面/底面加特征。

   **F. 顺序总纲（Golden Order）**：
   ```
   1. new_part
   2. select_plane + sketch_底面轮廓 + feature_extrusion (可选拔模)   ← 底座
   3. 面上凹槽（若有）：select_face_and_sketch + sketch_* + feature_cut_extrusion(blind)
   4. 面上通孔阵列（若有）：select_face_and_sketch + N×sketch_circle + feature_cut_extrusion(through_all)
   5. 圆角（若有）：feature_fillet × N（对多条边**必须拆成多次**，每次用 `"面1_面2"`；或一次用 `"all"` 圆所有边）
   ```
   ⚠️ **圆角必须最后**！在有圆角边的面上再加特征会失败。

   **G. 完整示例：86 型开关面板 (B07)** — **⚠️ 必须完全按此模板照抄坐标系！**

   零件：86×86×10mm 底座（前视面拉伸），顶面中央 50×50×3mm 凹槽，四角 ⌀3 贯穿孔，四竖直边 R3 圆角。

   **关键认知**：因为底座在**前视基准面（XY 平面）**画的、沿 **Z 方向拉伸**，所以：
   - "顶面" = 拉伸出的 **+Z 面**（Z=depth=0.01m），**不是** +Y 面！
   - "底面" = 起始面（Z=0）
   - "侧面" = ±X 或 ±Y 面

   因此选顶面的射线起点必须是 **rz=0.011（Z=10mm+ε）、方向 dz=-1**。**严禁**用 ry=0.011、dy=-1（那会选中 Y=+43 的侧面，不是顶面）！

   完整 17 步 plan：
   ```
   1.  new_part {}
   2.  select_plane {index:0}                                                     ← 前视面 XY
   3.  sketch_rectangle {x1:-0.043, y1:-0.043, x2:0.043, y2:0.043}                ← 86×86 底座轮廓
   4.  feature_extrusion {depth:0.01}                                             ← 沿 Z 拉伸 10mm → 顶面在 Z=10mm
   5.  select_face_and_sketch {rx:0, ry:0, rz:0.011, dx:0, dy:0, dz:-1}           ← ⭐ 选顶面 (Z=10mm 那个方形)
   6.  sketch_rectangle {x1:-0.025, y1:-0.025, x2:0.025, y2:0.025}                ← 面内 2D 坐标：中央 50×50 凹槽
   7.  feature_cut_extrusion {depth:0.003}                                        ← 挖 3mm 深 (不 through_all)
   8.  select_face_and_sketch {rx:0, ry:0, rz:0.011, dx:0, dy:0, dz:-1}           ← 再选一次顶面
   9.  sketch_circle {cx:0.035, cy:0.035, radius:0.0015}                          ← 右上角 ⌀3
   10. sketch_circle {cx:-0.035, cy:0.035, radius:0.0015}                         ← 左上角
   11. sketch_circle {cx:0.035, cy:-0.035, radius:0.0015}                         ← 右下角
   12. sketch_circle {cx:-0.035, cy:-0.035, radius:0.0015}                        ← 左下角
   13. feature_cut_extrusion {depth:0.01, endCondition:"through_all"}             ← 一次贯穿切 4 孔
   14. feature_fillet {radius:0.003, edgeSelection:"front_left"}                  ← ⭐ 竖直棱边 1
   15. feature_fillet {radius:0.003, edgeSelection:"front_right"}                 ← 竖直棱边 2
   16. feature_fillet {radius:0.003, edgeSelection:"back_left"}                   ← 竖直棱边 3
   17. feature_fillet {radius:0.003, edgeSelection:"back_right"}                  ← 竖直棱边 4
   ```

   **常见错误对比表**：
   | 错误 | 后果 |
   |---|---|
   | `select_face_and_sketch {rx:0, ry:0.011, rz:0, dx:0, dy:-1, dz:0}` | 选中的是 +Y 面（86×10 侧面），不是顶面 |
   | 在面上画 rectangle 时坐标写全局 3D 而不是 2D 面内 | 例如 y=0.01 想表示"在 Z=10mm 的顶面上"—— 是错的！面内 sketch_rectangle 只有 x1/y1/x2/y2 两维 |
   | 4 个 sketch_circle 分散在多次 select_face_and_sketch 之间 | 每次 select 会创建新草图，导致最后一次 cut 只切最后一个圆 |
   | 圆角放在切除之前 | 圆角改变了顶面拓扑，后续 select_face_and_sketch 可能失败 |
   | `feature_fillet {edgeSelection: "vertical_corners"}` 或 `"top_perimeter"` | ❌ **C# 服务不认这些语义名，会 400**！必须拆成 `front_left` / `front_right` / `back_left` / `back_right` 四次 fillet |

2. **拉伸方向 & 深度取自哪个视图**：
   - 若 `base_view=front`（主视图对应前视基准面 index=0），拉伸深度取俯视图/侧视图的 Z 方向尺寸
   - 若 `base_view=top` (index=1)，拉伸深度取主视图/侧视图的 Y 方向尺寸
   - 若 `base_view=right` (index=2)，拉伸深度取主视图/俯视图的 X 方向尺寸

3. **孔的位置换算**：IR 里 features 描述孔的位置常用"距底边 X mm、距左边 Y mm"，阶段 B 要把它换算成对应
   基准面草图坐标（原点在草图中心或左下取决于是否用绝对坐标画的轮廓）。若外形矩形是从 (0,0) 画到 (W,H)，
   那孔中心 = (距左边 mm/1000, 距底边 mm/1000)。

4. **枚举完整**：IR 的 `features` 里有几个孔就要有几个 `sketch_circle + feature_cut_extrusion`（或一次
   sketch 多个圆 + 一次 cut）。禁止合并、省略。

5. **对称件优先 midPlane**：如 IR 中 `part_type` 是 revolve/hex_nut/dice 等对称件且未指定原点位置，
   拉伸时加 `midPlane: true` 让零件在原点居中，后续选面坐标计算最简。
