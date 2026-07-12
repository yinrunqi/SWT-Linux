---
name: operations
description: SolidWorks 可用原子操作及其参数清单（21 个 operation）
triggers: []
applies_to: [text, three_view]
---
## 可用操作及参数

### 创建类操作

1. **new_part** — 新建零件文档（无参数）
   - 🔴 **一个 plan 里 new_part 只能调用一次**（永远是第 1 步）！
   - `new_part` 的语义是"关闭当前文档并打开一个全新的空文档"，**不是** "在当前文档里新建一个实体"。
   - 如果零件包含**多个不相连的实体**（例如两个独立的齿轮、两个分离的小零件），**不要**用第二次 new_part 分开建——那会销毁前一个实体！
   - **正确做法**：一次 new_part → 在同一文档里连续用 `sketch_* + feature_extrusion` 画多个独立实体（SolidWorks 允许同一零件文件内存在多个不相连的实体）。
   - 例：两个齿轮 → 一次 new_part → 画小齿轮圆 + 拉伸 + 齿 + 阵列 → **不要 new_part** → 直接画大齿轮圆(在不同位置) + 拉伸 + 齿 + 阵列。SolidWorks 检测到新草图与已有实体不接触，会自动生成一个新实体（同零件多实体）。
   - 💡 **多实体零件的推荐建模顺序**（避免重复选面 + 缩短特征树深度）：
     ```
     step 1: new_part
     step 2: select_plane index=0                    ← 前视基准面（草图1）
     step 3: 在**同一草图1里**同时画所有实体的外圈轮廓 + 所有轴孔的圆
            例：小齿轮 sketch_circle R=20 中心 (-31,0)
                大齿轮 sketch_circle R=40 中心 (+31,0)
                小齿轮轴孔 sketch_circle R=4 中心 (-31,0)   ← 同一草图, 与外圈同心
                大齿轮轴孔 sketch_circle R=6 中心 (+31,0)   ← 同一草图, 与外圈同心
     step 4: feature_extrusion depth=0.015           ← 一步拉伸出两个圆环(外圈-内孔自动挖空)
     step 5+: 依次 select_plane index=0 → sketch_rectangle 齿 → feature_extrusion → feature_circular_pattern
     ```
     **原理**：**同心多圆在同一草图 + 一次拉伸 = 自动形成"圆环"体（外圆凸起-内孔挖除）**——
     不需要单独 select_face 选顶面再画孔，也不需要额外的 cut_extrusion。这是最简洁的多实体轴孔方案。
     好处：轴孔自然融入第一步拉伸，后续步骤特征树浅，绝不会因射线选面超时。
2. **select_plane** — 选择基准面并进入草图编辑
   - index: int — 0=前视基准面, 1=上视基准面, 2=右视基准面
3. **sketch_line** — 在草图中画直线
   - x1, y1: float — 起点坐标（米）
   - x2, y2: float — 终点坐标（米）
4. **sketch_rectangle** — 在草图中画矩形（对角线两点）
   - x1, y1: float — 左下角坐标（米）
   - x2, y2: float — 右上角坐标（米）
5. **sketch_circle** — 在草图中画圆
   - cx, cy: float — 圆心坐标（米）。在基准面草图中这是 2D 草图坐标；在实体面草图中这是**模型 3D 坐标**的 X 和 Y 分量。
   - cz: float — 圆心 Z 坐标（可选，默认 0）。**在实体面草图中必须正确设置**以指定圆心在模型空间中的完整 3D 位置。
   - radius: float — 半径（米）
6. **sketch_centerline** — 在草图中画中心线（构造线，用于旋转轴）
   - x1, y1: float — 起点坐标（米）
   - x2, y2: float — 终点坐标（米）
   - 中心线不参与轮廓封闭，仅用作旋转特征的旋转轴
7. **sketch_arc** — 在草图中画圆弧
   - cx, cy: float — 圆弧圆心坐标（米）
   - startX, startY: float — 圆弧起点坐标（米）
   - endX, endY: float — 圆弧终点坐标（米）
   - direction: int — 绘制方向。1=顺时针, -1=逆时针
8. **sketch_ellipse** — 在草图中画椭圆
   - cx, cy: float — 椭圆中心坐标（米）
   - majorRadius: float — 长轴半径（米），沿 X 方向
   - minorRadius: float — 短轴半径（米），沿 Y 方向
   - 椭圆是闭合曲线，可直接用于拉伸或切除
9. **sketch_polygon** — 在草图中画正 n 边形（3~64 边）
   - cx, cy: float — 多边形中心坐标（米），在实体面上时可加 cz
   - circumradius: float — **外接圆半径**（=中心到顶点的距离，单位：米）
   - sides: int — 边数（正整数，3~64）
   - startAngleDeg: float (可选，默认 0) — 第一个顶点相对于草图 X 轴的角度（度）。
     * 正六边形要"尖朝上"（顶点在正上方）→ startAngleDeg=90；
     * 要"平放"（一条边水平在下方）→ startAngleDeg=0。
   - ⚠️ 若用户给的是"对边距/两平行边距离"（如 M20 螺母对边距 30mm），
     圆周半径需换算：正六边形的外接圆半径 = 对边距 / √3 = 对边距 × 0.5774；
     正八边形的外接圆半径 = 对边距 / (2·cos(π/8)) ≈ 对边距 × 0.5412。
   - 多边形闭合，可直接用于 feature_extrusion 或 feature_cut_extrusion
10. **sketch_slot** — 在草图中画一个 length × width 的槽形轮廓
   - cx, cy: float — 槽中心坐标（米），在实体面上时可加 cz
   - length: float — 沿长轴方向的总长度（米）
   - width: float — 沿短轴方向的宽度（米）
   - angleDeg: float (可选，默认 0) — 长轴相对草图 X 轴的旋转角度（度，暂仅支持 0）
   - ⚠️ **当前实现说明**：本地 SolidWorks 环境下退化为中心矩形（四个角是直角而非圆角）。
     功能上与真正的长圆孔等价——用于钥匙圈孔、螺栓过孔、装配调整用槽都合格。
   - 闭合，可直接用于拉伸/切除。
11. **feature_extrusion** — 拉伸当前草图（支持普通拉伸 + 拔模梯形拉伸）
   - depth: float — 拉伸深度（米）
   - flipDirection: bool — 是否翻转拉伸方向（可选，默认 false）。⚠️ 在实体面上拉伸时，默认方向就是面的外法向量方向（离开面的方向），通常不需要设 true。仅在需要向面的内侧拉伸时才设 true。
   - midPlane: bool — 是否使用中间面拉伸（可选，默认 false）。从草图面两侧各拉伸 depth/2
   - **draftAngleDeg**: float — 拔模角度（度，可选，默认 0=无拔模）。>0 时启用梯形拉伸，即顶面与底面尺寸不同。适用零件：**键盘键帽、花盆、漏斗、锥台、灯罩、纸杯**等。
   - **draftOutward**: bool — 拔模方向（可选，默认 false）。false=**内收**（顶面小于底面，最常见，如键帽/花盆）；true=**外扩**（顶面大于底面，如漏斗）。
   - **拔模角计算公式**：`draftAngleDeg = degrees(atan((底半宽-顶半宽)/深度))`
     - 例：底 18mm、顶 14mm、深 8mm → `atan((18-14)/2 / 8)` = `atan(2/8)` ≈ **14.04°**（内收）
     - 例：底 30mm、顶 50mm、深 20mm → `atan((50-30)/2 / 20)` = `atan(10/20)` ≈ **26.57°**（外扩，draftOutward=true）
   - **重要**：拔模拉伸后**包围盒仍以底面尺寸为准**（因为底面是最大轮廓）；体积公式为**棱台/圆台**：`V = h/3 · (A_底 + A_顶 + √(A_底·A_顶))`
10. **feature_cut_extrusion** — 切除拉伸（挖孔/开槽/减法建模）
   - depth: float — 切除深度（米）
   - endCondition: str — 终止条件（可选，默认 "blind"）
     - "blind": 盲孔（指定深度）
     - "through_all": 完全贯穿
     - "mid_plane": 从草图面两侧各切一半深度
     - "up_to_next": 切到下一个面
   - flipDirection: bool — **"翻转切除侧"（可选，默认 false）**。⚠️ 这个参数控制"切掉轮廓的哪一侧"，不是简单的方向反转：
     - **flipDirection=false（默认，绝大多数情况都用它）**：从草图面朝实体【内部】挖掉轮廓内的材料，得到正确的孔/凹槽/凹坑。
     - flipDirection=true：切掉轮廓【外侧】的材料（即保留轮廓内、挖掉轮廓外）。这会把整个面挖掉一圈，几乎总是错误的，除非你确实想做"反向挖除"。
     - 经验法则：在面上画一个小圆/小矩形想挖出一个凹坑或孔时，**一律用默认 flipDirection=false**。
11. **feature_revolve** — 旋转凸台或旋转切除（创建轴对称零件）
    - angle: float — 旋转角度（度），默认 360（完整旋转）
    - isCut: bool — 是否为旋转切除（默认 false，即旋转凸台）
    - reverseDir: bool — 是否反转旋转方向（可选，默认 false）
    - **重要：草图中必须包含一条中心线作为旋转轴**。旋转轮廓必须在中心线的一侧。
    - 典型流程：select_plane → sketch_centerline（画旋转轴）→ 画轮廓草图 → feature_revolve
12. **close_sketch** — 退出草图编辑（不拉伸时使用）
13. **feature_fillet** — 圆角特征（在已有实体的边上添加圆角过渡）
    - radius: float — 圆角半径（米）
    - edgeSelection: str — 选边方式（可选，默认 "all"）
      - "all": 选中实体的所有边
      - "ray": 通过射线选中特定边（需提供 rx,ry,rz,dx,dy,dz）
      - **"面1_面2" 语义化选边（强烈推荐！）**：直接指定边的位置，C# 端自动计算射线
        - 格式：两个面名用下划线连接，表示这两个面的交线
        - 面名：top(顶面), bottom(底面), front(前面), back(后面), left(左面), right(右面)
        - 示例：
          - "top_front": 顶面与前面的交边
          - "top_back": 顶面与后面的交边
          - "top_left": 顶面与左面的交边
          - "top_right": 顶面与右面的交边
          - "bottom_front": 底面与前面的交边
          - "front_left": 前面与左面的交边
          - "front_right": 前面与右面的交边
    - rx, ry, rz, dx, dy, dz: float — 射线参数（仅 edgeSelection="ray" 时使用）
    - **不需要进入草图**，直接对已有实体的边执行。
    - 典型流程：创建实体 → feature_fillet
    - ⚠️ **优先使用语义化选边**（如 "top_front"），避免手动计算射线坐标！
    - 🚫 **严禁使用单个面名作为 edgeSelection**（如 "top"、"front" 是错误的！）。edgeSelection 要么是 "all"/"ray"，要么必须是 **两个面名用下划线连接** 的边（如 "top_front"）。
    - ⚠️ **多条边圆角必须拆成多个 feature_fillet 步骤**：一个 feature_fillet 步骤只能处理一条语义化边。例如「对顶面的 4 条边做 R5mm 圆角」要拆成 4 个步骤：
      - feature_fillet {radius: 0.005, edgeSelection: "top_front"}
      - feature_fillet {radius: 0.005, edgeSelection: "top_back"}
      - feature_fillet {radius: 0.005, edgeSelection: "top_left"}
      - feature_fillet {radius: 0.005, edgeSelection: "top_right"}
    - 若要对整个实体所有边圆角，直接用 edgeSelection="all"（一步即可）。
14. **feature_chamfer** — 倒角特征（在已有实体的边上添加倒角）
    - type: str — 倒角类型（可选，默认 "equal_distance"）
      - "equal_distance": 等距倒角（两侧距离相同）
      - "distance_distance": 不等距倒角（两侧距离不同）
      - "distance_angle": 距离-角度倒角
    - distance1: float — 倒角距离（米）
    - distance2: float — 第二距离（distance_distance 模式，米，可选）
    - angle: float — 倒角角度（distance_angle 模式，度，可选，默认 45）
    - edgeSelection: str — 选边方式（可选，默认 "all"），同 feature_fillet（支持 "all"、"ray"、语义化选边）
    - rx, ry, rz, dx, dy, dz: float — 射线参数（仅 edgeSelection="ray" 时使用）
    - **不需要进入草图**，直接对已有实体的边执行。
    - 典型流程：创建实体 → feature_chamfer

15. **feature_circular_pattern** — 圆周阵列（把最后一个特征围绕坐标轴均匀复制 N 份）✅ 已修复几何生效
    - count: int — 阵列总数（含源特征本身，≥2）
    - axis: str — 旋转轴（可选，默认 "y"），可选 "x"/"y"/"z"，轴穿过坐标原点
    - angleDeg: float — 阵列覆盖角度（可选，默认 360，即整圆均分）
    - reverseDir: bool — 反转阵列方向（可选，默认 false）
    - ⭐ **典型场景**：齿轮的齿、法兰盘的螺栓孔、通风口、风扇叶片、花瓣等所有"围绕中心轴均匀分布"的重复特征。
    - 🚫 **不要手工绕圈画多个圆/多边形**：如"6 个螺栓孔围绕中心均布"，
      正确做法 = 先建**一个**孔（select_face + sketch_circle + feature_cut_extrusion），
      再调 feature_circular_pattern {count: 6}，一步生成其余 5 个孔。
    - ⚠️ 阵列前需确保源特征已完成。阵列以特征树中**最后一个非草图非基准面特征**为源。
    - ⚠️ 轴的方向决定了阵列在哪个平面内均布：如果零件在上视面画（拉伸沿 Y 轴），
      通常用 axis="y" 让阵列在 XZ 平面内均布（绕 Y 轴转）。
    - ⚠️ **前提条件**：C# 服务需要在实体上找到一个"轴线沿指定 axis 方向"的**圆柱面**（不是圆边）来创建参考轴。
      **自动满足**的常见场景：任何圆盘/圆柱底座（有外圆柱面）、任何贯穿孔（内圆柱面）。
      **不满足**的场景：全直边多面体（如立方体上没圆孔时）→ 需要先挖至少一个孔再阵列。
      对齿轮/法兰盘/风扇等典型阵列场景，前提天然满足。

16. **feature_linear_pattern** — 线性阵列（把最后一个**已完成的实体特征**沿 1~2 个坐标轴复制 num1×num2 份）
    - 🔴 **绝对禁止**：**不要阵列草图！**阵列的对象必须是**已完成的实体特征**（拉伸/切除/圆角/倒角等），**不是草图**。
    - ❌ 错误做法：sketch_rectangle → feature_linear_pattern → feature_extrusion（阵列复制了草图但没实体，拉伸只作用于原始草图）
    - ✅ 正确做法：sketch_rectangle → **feature_extrusion**（先拉伸出一个实体键帽种子） → feature_linear_pattern（再阵列 65 份）

    - num1: int — 方向 1 的实例总数（含源特征本身，≥2）
    - spacing1: float — 方向 1 每份之间的间距（**米**，> 0）
    - dir1Axis: str — 方向 1 的轴（可选，默认 "x"），可选 "x"/"y"/"z"
    - num2: int — 方向 2 的实例总数（可选，默认 1；=1 表示单向阵列）
    - spacing2: float — 方向 2 每份之间的间距（**米**，num2 ≥2 时必填）
    - dir2Axis: str — 方向 2 的轴（可选，默认 "y"），必须与 dir1Axis 不同
    - flip1: bool — 反转方向 1（默认 false = 沿 +axis 方向）
    - flip2: bool — 反转方向 2
    - ⭐ **典型场景**：**键盘按键矩阵**（如 10×3=30 个键帽）、**格栅通风孔**（如 5×20=100 个圆孔）、
      **蜂窝板、瓷砖、砖墙、齿条**——所有**网格式重复分布**的特征。
    - 🚫 **强烈禁止手工画 N 次 sketch + N 次拉伸/切除**：如"生成 5×13 个键帽"，
      正确做法 = 先在**角落位置**建**一个**键帽种子（select_face + sketch_rectangle + feature_extrusion 拔模），
      再调 feature_linear_pattern {num1:13, spacing1:0.018, num2:5, spacing2:0.018}，一步生成其余 64 个。
      画 65 次 + 65 次拉伸 = **步数爆炸 130 步**；用阵列 = 4 步搞定。
    - ⚠️ 阵列前需确保源特征已完成。阵列以特征树中**最后一个非草图非基准面特征**为源。
    - ⚠️ **种子位置策略**：种子应放在**阵列的"起始角"**（比如 -X 边、-Y 边），因为阵列默认沿 +axis 方向复制。
      若把种子放在中央位置，阵列会向一侧展开，副本可能飞出实体外。
    - ⚠️ **间距 = 中心到中心距离**（不是"间隙"）。例如 3 个键帽宽 15mm，中间留 3mm 间隙 → 中心距 = 15+3 = 18mm。
    - ⚠️ **前提条件**：C# 服务需要在实体上找到一条沿指定 axis 的**直线边**作方向参考。
      任何长方体/立方体天然有 12 条这样的边（4×X + 4×Y + 4×Z），前提天然满足。
      圆盘/球体等无直线边的实体，此 API 不可用（需要先建长方体底座）。

### 查询类操作（用于了解当前模型状态）

14. **feature_tree** — 获取特征树（无参数）
   - 返回所有特征的名称、类型、索引列表
   - **每个特征还会返回其关联的尺寸列表**（尺寸名、全名、当前值）
   - 用于了解当前模型有哪些特征和可编辑的尺寸
15. **body_bounding_box** — 获取实体包围盒（无参数）
   - 返回 minX/minY/minZ/maxX/maxY/maxZ 和尺寸 sizeX/sizeY/sizeZ（米）
   - 用于了解当前模型的空间范围

### 编辑类操作

16. **modify_dimension** — 直接修改已有特征的尺寸值 ⭐ 推荐
    - featureName: str — 特征名称（从 feature_tree 返回值中获取，如 "Boss-Extrude1" 或 "凸台-拉伸1"）
    - dimensionName: str — 尺寸名称（可选，如 "D1"；不填则修改该特征的第一个尺寸）
    - **newValue 和 delta 二选一**：
      - newValue: float — 设为**绝对值**（米）。用于"改为 50mm"、"设置成 100mm"等场景。
      - delta: float — 在当前值基础上**增减**（米）。用于"再拉伸 50mm"、"加高 20mm"、"缩短 10mm"等场景。
        - delta 为正数表示增大，负数表示减小。
        - 使用 delta 时，系统会自动从 feature_tree 查询当前值并加上 delta。
    - 修改后自动重建模型

17. **select_face_and_sketch** — 通过射线选中一个面并进入草图编辑
    - rx, ry, rz: float — 射线起点坐标（米），应在实体外部
    - dx, dy, dz: float — 射线方向向量（单位向量）
    - 选中面后自动进入该面的草图编辑模式，然后可以画草图 + 拉伸
