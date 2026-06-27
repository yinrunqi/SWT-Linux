# SolidWorks Copilot 功能扩展规划

> 文档版本: v1.1 (完整版) | 日期: 2026-04-06 | 作者: AI Assistant
>
> 目标：全面分析当前 SolidWorks AI 自动建模项目的现状与不足，规划增添更多 SolidWorks 功能的路线图。
>
> **v1.1 更新**：
> - 为全部 4 个 Phase 的每个新增功能补充了端到端测试用例（含 LLM Prompt → 预期计划 → 预期结果 → 验证方法）
> - 补充了详细的 API 方法参数说明（FeatureCut4、FeatureRevolve2、CreateArc/CreateCenterLine/CreatePolygon 等）
> - 补充了 API 参考来源说明和 JS 渲染页面的注意事项
> - Phase 3 增加了扫掠/放样的操作流程、截图/视图的具体 API 调用方式
> - Phase 4 增加了装配体/工程图/参数化模板/API 文档化的端点设计和实现方案
> - 共计 **25 个测试用例**，覆盖全部 4 个 Phase 的规划功能
>
> **测试约定**：每个功能至少配备 1 个完整的端到端测试用例，格式为：用户 Prompt → 预期 LLM 建模计划 → 预期 SolidWorks 执行结果。测试用例同时用于验证 C# API 端点、Python Executor 映射和 LLM System Prompt 的正确性。

### API 参考来源

本文档中的 SolidWorks API 信息来自以下官方文档：

- **SolidWorks API Help 2026 首页**: `https://help.solidworks.com/2026/english/api/sldworksapiprogguide/Welcome-SolidWorks_API_Help.htm`
- **核心 API 模块**: SOLIDWORKS API Help → SolidWorks.Interop.sldworks 命名空间
  - `IFeatureManager` — 特征创建（拉伸、切除、旋转、圆角、倒角、阵列、镜像、抽壳、扫掠、放样等）
  - `ISketchManager` — 草图绘制（直线、圆、弧、中心线、矩形、多边形、样条线等）
  - `IModelDoc2` — 文档操作（保存、打开、撤销、重做、尺寸修改、截图等）
  - `IModelDocExtension` — 扩展操作（另存为、射线选择等）
  - `ISldWorks` — 应用程序入口（连接、新建文档、打开文档等）
- **常量枚举**: `SolidWorks.Interop.swconst` 命名空间（`swEndConditions_e`, `swSelectType_e` 等）

> ⚠️ 注意：SolidWorks API Help 页面通过 JavaScript 动态渲染，无法直接网页抓取。建议在本地 SolidWorks 安装目录中查看 API Help（`{SolidWorks安装目录}\api\Help\`），或在 Visual Studio 中通过对象浏览器查看 COM 接口定义。

---

## 一、项目现状总览

### 1.1 架构概览

```
用户自然语言 ──→ [Python LLMPlanner] ──→ LLM API ──→ SwModelingPlan (JSON)
                                                          │
                                                    [HTTP 请求]
                                                          ↓
SolidWorks 实例 ←── [C# HTTP API :5100] ←── 逐步执行操作
```

项目采用**双层解耦架构**：
- **C# 层** (`Chapter1.ConnectToSw/`)：SolidWorks COM API 的 HTTP 网关，`SwCommands.cs` 封装原子操作
- **Python 层** (`LLMPlanner/`)：LLM 自然语言解析 + HTTP 执行器
- **Web 层** (`LLMPlanner/web/`)：ChatGPT 风格的可视化 Demo 页面

### 1.2 已实现功能清单

| 类别 | 操作 | API 端点 | 说明 |
|------|------|----------|------|
| **文档** | 新建零件 | `POST /api/new-part` | 基于默认模板创建 `.sldprt` |
| **草图** | 选择基准面 | `POST /api/select-plane` | 0=前视, 1=上视, 2=右视 |
| **草图** | 画直线 | `POST /api/sketch/line` | 两点坐标 |
| **草图** | 画矩形 | `POST /api/sketch/rectangle` | 对角线两点 |
| **草图** | 画圆 | `POST /api/sketch/circle` | 圆心+半径 |
| **草图** | 退出草图 | `POST /api/close-sketch` | — |
| **特征** | 拉伸凸台 | `POST /api/feature/extrusion` | 单向盲孔拉伸 |
| **特征** | 修改尺寸 | `POST /api/modify-dimension` | 修改已有特征尺寸值 |
| **查询** | 获取特征树 | `GET /api/feature-tree` | 含尺寸信息 |
| **查询** | 获取包围盒 | `GET /api/body-bounding-box` | min/max 坐标 |
| **面操作** | 射线选面+草图 | `POST /api/select-face-and-sketch` | 射线拾取面 |
| **组合** | 创建长方体 | `POST /api/create-box` | 一键创建 |

### 1.3 LLM 规划器已支持的操作枚举（`schemas.py`）

```python
class OperationType(str, Enum):
    NEW_PART, SELECT_PLANE, SKETCH_LINE, SKETCH_RECTANGLE, SKETCH_CIRCLE,
    FEATURE_EXTRUSION, CLOSE_SKETCH,
    FEATURE_TREE, BODY_BOUNDING_BOX,
    SELECT_FACE_AND_SKETCH, MODIFY_DIMENSION
```

共 **11 种操作**，覆盖"创建简单凸台 + 查询 + 修改尺寸"的基本流程。

---

## 二、当前不足分析

### 2.1 草图能力极度有限 ⚠️

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| 仅支持直线、矩形、圆 3 种草图元素 | 无法绘制圆弧、椭圆、多边形、样条线等 | 🔴 高 |
| 不支持草图约束（水平/竖直/相切/同心等） | 草图无法精确定位，尺寸不确定 | 🔴 高 |
| 不支持草图尺寸标注 | 无法对草图元素添加精确尺寸 | 🟡 中 |
| 不支持草图修剪/延伸 | 无法编辑已有草图线段 | 🟡 中 |
| 不支持草图偏移、镜像 | 复杂草图需要大量手动绘制 | 🟡 中 |

### 2.2 特征操作严重缺失 ⚠️

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| **仅支持凸台拉伸**，缺少切除拉伸 | 无法挖孔、开槽、做减法建模 | 🔴 高 |
| 缺少旋转凸台/切除 | 无法创建轴对称零件（轴、轮毂等） | 🔴 高 |
| 缺少圆角/倒角 | 无法做边缘过渡处理 | 🔴 高 |
| 缺少线性阵列/圆周阵列 | 无法创建重复特征（螺丝孔阵列等） | 🟡 中 |
| 缺少镜像特征 | 对称零件需要重复建模 | 🟡 中 |
| 缺少抽壳 | 无法创建薄壁件 | 🟡 中 |
| 拉伸仅支持"单向盲孔"终止条件 | 无法双向拉伸、完全贯穿、到面终止等 | 🟡 中 |
| 缺少扫掠（Sweep）和放样（Loft） | 无法创建复杂曲面零件 | 🟠 中低 |

### 2.3 文档与文件管理不足

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| 不支持保存/另存为 | 建模完成后无法保存文件 | 🔴 高 |
| 不支持打开已有文件 | 只能新建零件，无法编辑已有模型 | 🔴 高 |
| 仅支持零件文档（Part） | 无法创建装配体和工程图 | 🟡 中 |
| 不支持导出（STEP/STL/IGES 等） | 无法输出通用格式 | 🟡 中 |
| 不支持撤销/重做 | 操作失误无法回退 | 🟡 中 |

### 2.4 查询与反馈能力不足

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| 不支持截图/视图导出 | LLM 无法"看到"当前模型状态 | 🔴 高 |
| 不支持质量属性查询（体积、重量等） | 无法验证建模结果 | 🟡 中 |
| 不支持草图状态查询（是否完全定义） | 草图可能欠定义导致拉伸失败 | 🟡 中 |
| 不支持边/面的详细信息查询 | 射线选面后无法获取面的法向量、面积等 | 🟡 中 |

### 2.5 LLM 规划器不足

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| 单次规划，无多轮反馈机制 | 执行失败后无法自动修正 | 🔴 高 |
| System Prompt 需同步更新 | 新增 API 后需手动更新 prompt | 🟡 中 |
| 不支持条件分支（if/else） | 无法根据查询结果动态调整后续步骤 | 🟡 中 |
| 缺少视觉反馈（截图） | LLM 无法根据当前模型形状做出判断 | 🟡 中 |

### 2.6 工程化问题

| 不足 | 影响 | 严重程度 |
|------|------|----------|
| HTTP API 单线程阻塞 | 一个请求处理中无法处理其他请求 | 🟡 中 |
| 无 API 文档（Swagger/OpenAPI） | 外部集成困难 | 🟡 中 |
| 无日志持久化 | 调试困难 | 🟠 低 |
| 硬编码端口 5100 | 灵活性不足 | 🟠 低 |

---

## 三、功能扩展路线图

### 3.1 路线图总览

```
Phase 1 (基础补齐)     Phase 2 (能力增强)      Phase 3 (高级功能)      Phase 4 (产品化)
─────────────────     ────────────────────    ────────────────────    ─────────────────
✦ 切除拉伸             ✦ 圆角/倒角             ✦ 扫掠/放样             ✦ 装配体支持
✦ 旋转凸台/切除         ✦ 线性/圆周阵列         ✦ 曲面操作              ✦ 工程图自动生成
✦ 草图圆弧/多边形       ✦ 镜像特征              ✦ 钣金功能              ✦ 多模型协同
✦ 文件保存/打开         ✦ 抽壳                  ✦ 模型截图反馈           ✦ 参数化模板库
✦ 撤销/重做             ✦ 草图约束/标注          ✦ 多轮 Agent 闭环       ✦ API 文档化
✦ 拉伸终止条件扩展       ✦ 导出多格式             ✦ 质量属性查询          ✦ 权限/安全
                        ✦ 基准面/基准轴创建       ✦ 视图操作
~2-3 周                 ~3-4 周                  ~4-6 周                ~4-8 周
```

---

### 3.2 Phase 1：基础补齐（优先级最高）

> 目标：补齐最基本的建模能力缺口，使 Copilot 能完成日常 80% 的简单零件建模。

#### 3.2.1 切除拉伸（Feature Cut Extrusion）

**SolidWorks API**: `IFeatureManager.FeatureCut4()`

> 📖 官方文档: `SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IFeatureManager~FeatureCut4`
>
> FeatureCut4 是 FeatureCut3 的升级版本，新增了起始条件、偏移等参数。
> 参数说明（已验证的完整签名）：
> - `Sd` (bool): 是否单向。True = 单向拉伸
> - `Flip` (bool): 是否反转切除方向。默认切除方向与草图法线相反
> - `Dir` (bool): 是否垂直于草图轮廓
> - `T1` (int): 终止条件 1 — `swEndConditions_e` 枚举
> - `T2` (int): 终止条件 2（双向时使用）
> - `D1` (double): 深度 1（米）
> - `D2` (double): 深度 2
> - `Dchk1/Dchk2` (bool): 拔模开关
> - `Ddir1/Ddir2` (bool): 拔模方向反转
> - `Dang1/Dang2` (double): 拔模角度
> - `OffsetReverse1/OffsetReverse2` (bool): 偏移反向
> - `NormalCut` (bool): 法向切除
> - 后续参数为装配体/多实体相关，零件操作中一般设为 false

```csharp
// C# 封装 — 与现有 Extrusion() 方法结构对应
public void CutExtrusion(double depth, string endCondition = "blind", 
                          bool flipDirection = false)
{
    EnsureActiveDoc();
    ActiveDoc.SketchManager.InsertSketch(true); // 退出草图

    // 解析终止条件
    int t1;
    switch (endCondition.ToLower())
    {
        case "through_all": t1 = (int)swEndConditions_e.swEndCondThroughAll; break;
        case "mid_plane":   t1 = (int)swEndConditions_e.swEndCondMidPlane; break;
        case "up_to_next":  t1 = (int)swEndConditions_e.swEndCondUpToNext; break;
        default:            t1 = (int)swEndConditions_e.swEndCondBlind; break;
    }

    ActiveDoc.FeatureManager.FeatureCut4(
        Sd: true, Flip: flipDirection, Dir: false,
        T1: t1, T2: (int)swEndConditions_e.swEndCondBlind,
        D1: depth, D2: 0,
        Dchk1: false, Dchk2: false, Ddir1: false, Ddir2: false,
        Dang1: 0, Dang2: 0,
        OffsetReverse1: false, OffsetReverse2: false,
        TranslateSurface1: false, TranslateSurface2: false,
        NormalCut: true,
        UseFeatScope: false, UseAutoSelect: false,
        AssemblyFeatureScope: false, AutoSelectComponents: false,
        PropagateFeatureToCutBody: false,
        T0: (int)swStartConditions_e.swStartSketchPlane,
        StartOffset: 0, FlipStartOffset: false,
        OptimizeGeometry: false
    );
    ((ModelDoc2)ActiveDoc).ViewZoomtofit2();
}
```

**新增 API 端点**:
```
POST /api/feature/cut-extrusion
  { "depth": 0.01, "endCondition": "blind" | "through_all" | "mid_plane" | "up_to_next", "flipDirection": false }
```

**Python 侧变更**:
- `schemas.py`: 新增 `OperationType.FEATURE_CUT_EXTRUSION`
- `executor.py`: 新增端点映射 `FEATURE_CUT_EXTRUSION → ("POST", "/api/feature/cut-extrusion")`
- `llm_client.py`: 更新 SYSTEM_PROMPT，添加切除拉伸操作说明

**测试用例 1 — 在长方体上挖孔**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 100mm x 80mm x 50mm 的长方体，然后在上表面中心挖一个直径 30mm、深度 20mm 的圆孔"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(index=0)` → 3. `sketch_rectangle(x1=0, y1=0, x2=0.1, y2=0.08)` → 4. `feature_extrusion(depth=0.05)` → 5. `select_face_and_sketch(rx=0.05, ry=0.06, rz=0, dx=0, dy=0, dz=-1)` *(选上表面)* → 6. `sketch_circle(cx=0.05, cy=0.04, radius=0.015)` → 7. `feature_cut_extrusion(depth=0.02)` |
| **预期结果** | SolidWorks 中生成一个长方体，上表面中心有一个 ⌀30mm、深 20mm 的盲孔。特征树包含 `凸台-拉伸1` 和 `切除-拉伸1` |
| **验证方法** | 调用 `GET /api/feature-tree` 确认存在 `"type": "ICut"` 特征；调用 `GET /api/body-bounding-box` 确认尺寸为 0.1 x 0.08 x 0.05 |

**测试用例 2 — 贯穿切除**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 60mm 的正方形板，厚度 10mm，中间打一个 20mm 的圆形贯穿孔"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_rectangle(0,0,0.06,0.06)` → 4. `feature_extrusion(0.01)` → 5. `select_face_and_sketch(...)` → 6. `sketch_circle(0.03,0.03,0.01)` → 7. `feature_cut_extrusion(depth=0.01, endCondition="through_all")` |
| **预期结果** | 正方形薄板中间有一个完全贯穿的圆孔 |

---

#### 3.2.2 旋转凸台/切除（Revolve Boss & Cut）

**SolidWorks API**: `IFeatureManager.FeatureRevolve2()`

> 📖 官方文档: `SolidWorks.Interop.sldworks~SolidWorks.Interop.sldworks.IFeatureManager~FeatureRevolve2`
>
> FeatureRevolve2 替代了已过时的 FeatureRevolveCut2、FeatureRevolveThin 等方法，提供统一的旋转特征接口。
> **完整参数列表**（已从 The CAD Coder 教程验证）：
> - `SingleDir` (bool): True=单向旋转, False=双向
> - `IsSolid` (bool): True=实体旋转（非薄壁）
> - `IsThin` (bool): True=薄壁旋转
> - `IsCut` (bool): True=旋转切除, False=旋转凸台
> - `ReverseDir` (bool): 反向旋转角度
> - `BothDirectionUpToSameEntity` (bool): 双向旋转到同一实体
> - `Dir1Type` (int): 方向1终止条件 — `swEndConditions_e`（0=Blind, 1=ThroughAll, 6=MidPlane）
> - `Dir2Type` (int): 方向2终止条件
> - `Dir1Angle` (double): 方向1旋转角度（**弧度**，360°=2π≈6.2832）
> - `Dir2Angle` (double): 方向2旋转角度
> - `OffsetReverse1/2` (bool): 偏移反向
> - `OffsetDistance1/2` (double): 偏移距离
> - `ThinType` (int): 薄壁类型 — `swThinWallType_e`
> - `ThinThickness1/2` (double): 薄壁厚度
> - `Merge` (bool): 是否合并实体
> - `UseFeatScope` / `UseAutoSelect` (bool): 特征作用范围
>
> **重要**：旋转需要草图中包含一条中心线（Centerline）作为旋转轴，必须同步实现 `sketch/centerline` 操作。

```csharp
public void Revolve(double angleDeg, bool isCut = false, bool isBothDirections = false)
{
    EnsureActiveDoc();
    ActiveDoc.SketchManager.InsertSketch(true);

    double angleRad = angleDeg * Math.PI / 180.0;

    ActiveDoc.FeatureManager.FeatureRevolve2(
        SingleDir: !isBothDirections,
        IsSolid: true,
        IsThin: false,
        IsCut: isCut,
        ReverseDir: false,
        BothDirectionUpToSameEntity: false,
        Dir1Type: (int)swEndConditions_e.swEndCondBlind,
        Dir2Type: (int)swEndConditions_e.swEndCondBlind,
        Dir1Angle: angleRad,
        Dir2Angle: 0,
        OffsetReverse1: false, OffsetReverse2: false,
        OffsetDistance1: 0, OffsetDistance2: 0,
        ThinType: 0,  // swThinWallOneDirection
        ThinThickness1: 0, ThinThickness2: 0,
        Merge: true,
        UseFeatScope: false, UseAutoSelect: true
    );
    ((ModelDoc2)ActiveDoc).ViewZoomtofit2();
}
```

**新增 API 端点**:
```
POST /api/feature/revolve
  { "angle": 360, "isCut": false, "isBothDirections": false }
  注意：angle 参数为角度（度），C# 端内部转换为弧度
```

**测试用例 — 创建简单圆柱体（旋转法）**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"用旋转法创建一个直径 40mm、高度 80mm 的圆柱体"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` *(前视面)* → 3. `sketch_centerline(x1=0, y1=0, x2=0, y2=0.08)` *(垂直中心线)* → 4. `sketch_rectangle(x1=0, y1=0, x2=0.02, y2=0.08)` *(半截面矩形，宽=半径20mm)* → 5. `feature_revolve(angle=360)` |
| **预期结果** | 生成一个 ⌀40mm × 80mm 的实心圆柱体。特征树包含 `旋转-凸台1` 或 `Revolve1` |
| **验证方法** | `GET /api/body-bounding-box` → sizeX≈0.04, sizeY≈0.08, sizeZ≈0.04 |

> 注意：旋转需要草图中包含一条轴线（中心线），需要同步增加 `sketch/centerline` 草图操作。

---

#### 3.2.3 草图元素扩展

> 📖 ISketchManager 接口方法参考（已从 rimptec.com 的 Java Wrapper 文档中验证完整参数签名）

| 新增草图操作 | SolidWorks API 方法 | 参数 | 返回值 |
|-------------|---------------------|------|--------|
| 圆弧 | `SketchManager.CreateArc()` | cx,cy,cz, startX,startY,startZ, endX,endY,endZ, direction(short: 1=顺时针) | ISketchSegment |
| 三点圆弧 | `SketchManager.Create3PointArc()` | startX,Y,Z, midX,Y,Z, endX,Y,Z | ISketchSegment |
| 中心线 | `SketchManager.CreateCenterLine()` | x1,y1,z1, x2,y2,z2 | ISketchSegment |
| 多边形 | `SketchManager.CreatePolygon()` | cx,cy,cz, vx,vy,vz, sides(int) | Object (线段数组) |
| 槽口 | `SketchManager.CreateSketchSlot()` | 类型 + 两端坐标 + 宽度 | ISketchSlot |
| 样条线 | `SketchManager.CreateSpline2()` | 控制点数组(double[]) | ISketchSegment |

**新增 API 端点**:
```
POST /api/sketch/arc
  { "cx": 0, "cy": 0, "startX": 0.02, "startY": 0, "endX": 0, "endY": 0.02, "direction": 1 }
  注意：direction 1=顺时针, -1=逆时针。z 坐标固定为 0（2D 草图）

POST /api/sketch/centerline
  { "x1": 0, "y1": 0, "x2": 0, "y2": 0.1 }
  注意：中心线是构造线，不参与轮廓，仅用于旋转轴

POST /api/sketch/polygon
  { "cx": 0, "cy": 0, "vx": 0.02, "vy": 0, "sides": 6 }
  注意：(cx,cy) 为多边形中心，(vx,vy) 为一个顶点位置，sides 为边数
```

**测试用例 — 中心线 + 旋转创建酒杯轮廓**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个高 100mm 的圆柱体，用旋转法建模"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_centerline(x1=0, y1=0, x2=0, y2=0.1)` → 4. `sketch_line(x1=0, y1=0, x2=0.02, y2=0)` → 5. `sketch_line(x1=0.02, y1=0, x2=0.02, y2=0.1)` → 6. `sketch_line(x1=0.02, y1=0.1, x2=0, y2=0.1)` → 7. `feature_revolve(angle=360)` |
| **预期结果** | 生成一个 ⌀40mm × 100mm 的实心圆柱体 |
| **验证方法** | `GET /api/body-bounding-box` → sizeX≈0.04, sizeY≈0.1 |

**测试用例 — 六角螺母草图**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"在前视面画一个外接圆半径 15mm 的正六边形"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_polygon(cx=0, cy=0, vx=0.015, vy=0, sides=6)` → 4. `close_sketch` |
| **预期结果** | 草图中出现一个以原点为中心的正六边形 |

---

#### 3.2.4 文件保存与打开

**SolidWorks API**:
- 保存: `IModelDoc2.Save3()` — 保存当前文件
- 另存为: `IModelDocExtension.SaveAs3()` — 保存到新路径，支持格式转换
- 打开: `ISldWorks.OpenDoc7()` — 打开已有文件

> 📖 SaveAs3 与已过时的 SaveAs2 的区别：SaveAs3 使用 `IAdvancedSaveAsOptions` 对象来指定高级选项（如零件另存为装配体时的选项）。
> OpenDoc7 的关键参数：FilePath, Type(swDocPART/swDocASSEMBLY/swDocDRAWING), Options, Configuration, Errors, Warnings。

**新增 API 端点**:
```
POST /api/save           { "filePath": "C:/models/box.sldprt" }（可选，不填则保存当前文件）
POST /api/save-as        { "filePath": "C:/models/box.sldprt", "format": "sldprt" | "step" | "stl" }
POST /api/open           { "filePath": "C:/models/existing.sldprt" }
```

**测试用例 — 创建并保存文件**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 50mm 的立方体，然后保存到 C:/temp/cube.sldprt"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_rectangle(0,0,0.05,0.05)` → 4. `feature_extrusion(0.05)` → 5. `save(filePath="C:/temp/cube.sldprt")` |
| **预期结果** | SolidWorks 生成立方体并在 `C:/temp/cube.sldprt` 保存文件，文件存在且可被打开 |
| **验证方法** | 检查文件系统 `C:/temp/cube.sldprt` 是否存在 |

**测试用例 — 打开已有文件并修改**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"打开 C:/temp/cube.sldprt，然后把高度改成 100mm"` |
| **预期 LLM 计划** | 1. `open(filePath="C:/temp/cube.sldprt")` → 2. `feature_tree` → 3. `modify_dimension(featureName="Boss-Extrude1", newValue=0.1)` |
| **预期结果** | 打开文件，立方体的高度从 50mm 变为 100mm |

---

#### 3.2.5 撤销/重做

**SolidWorks API**: `IModelDoc2.EditUndo2()` / `IModelDoc2.EditRedo2()`

> 📖 `EditUndo2(count)` 撤销指定次数的操作，`EditRedo2(count)` 重做。返回 bool 表示是否成功。

**新增 API 端点**:
```
POST /api/undo     { "count": 1 }
POST /api/redo     { "count": 1 }
```

**测试用例 — 撤销拉伸**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"撤销上一步操作"` |
| **预期 LLM 计划** | 1. `undo(count=1)` |
| **预期结果** | 最近一次的 SolidWorks 操作被撤销 |
| **验证方法** | 撤销前后分别调用 `GET /api/feature-tree`，确认特征数减少 |

---

#### 3.2.6 拉伸终止条件扩展

当前 `Extrusion()` 方法（参见 SwCommands.cs 第 392-433 行）仅使用 `swEndCondBlind`（盲孔）和 `FeatureExtrusion3`。需扩展为支持多种终止条件：

| 终止条件 | 枚举值 | 使用场景 |
|---------|--------|---------|
| 盲孔 (Blind) | `swEndCondBlind` | 指定深度 ✅ 已有 |
| 完全贯穿 (Through All) | `swEndCondThroughAll` | 切除贯穿整个实体 |
| 成形到下一面 (Up to Next) | `swEndCondUpToNext` | 自动到下一个面 |
| 成形到一面 (Up to Surface) | `swEndCondUpToSurface` | 到指定面 |
| 中间面 (Mid Plane) | `swEndCondMidPlane` | 双向等距拉伸 |
| 双向 (Both Directions) | 参数组合 | 同时指定两个方向的深度 |

**修改现有端点**:
```
POST /api/feature/extrusion
  {
    "depth": 0.03,
    "endCondition": "blind" | "through_all" | "mid_plane",
    "direction2Depth": 0.01,  // 双向拉伸时第二方向深度
    "flipDirection": false
  }
```

**测试用例 — 中间面拉伸**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 80mm x 60mm 的矩形，以草图平面为中心双向等距拉伸 40mm（即总高度 40mm，上下各 20mm）"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_rectangle(0,0,0.08,0.06)` → 4. `feature_extrusion(depth=0.04, endCondition="mid_plane")` |
| **预期结果** | 生成一个 80mm x 60mm x 40mm 的长方体，以前视面为中心上下对称各 20mm |
| **验证方法** | `GET /api/body-bounding-box` → minZ≈-0.02, maxZ≈0.02, sizeZ≈0.04 |

---

### 3.3 Phase 2：能力增强

> 目标：覆盖中等复杂度零件所需的常用特征操作。

#### 3.3.1 圆角（Fillet）

**SolidWorks API**: `IFeatureManager.FeatureFillet3()` 或 `IModelDoc2.FeatureFillet3()`

> 📖 注意：自 SOLIDWORKS 2020 起，`IFeatureManager.FeatureFillet3()` 被标记为过时（obsolete），
> 建议使用 `IFeatureManager.FeatureFillet()` 或更新的方法来创建圆角。但 FeatureFillet3 在旧版本中仍然可用。
>
> 从博客园文档验证的调用方式：
> ```csharp
> // 先选中边（SelectByID2 + Mark=1），再调用：
> swModel.FeatureManager.FeatureFillet3(195, radius, 0.01, 0, 0, 0, 0, null, null, null, null, null, null, null);
> ```
> 第一个参数 195 是选项组合标志位（Options），radius 是圆角半径。

需要先选中边（Edge），再调用圆角。边的选择通过射线选择实现：`Extension.SelectByRay()` + `swSelEDGES`。

**新增 API 端点**:
```
POST /api/feature/fillet
  {
    "radius": 0.002,                    // 圆角半径（米）
    "edgeSelection": "all" | "ray",     // 选边方式
    "rx","ry","rz","dx","dy","dz"       // 射线选边参数（ray 模式时使用）
  }
```

**实现要点**:
- 需要封装边的选择逻辑（通过射线选边 `SelectByRay` + `swSelEDGES`）
- 支持选择多条边
- 支持变半径圆角（高级功能，Phase 3）

**测试用例 — 长方体加圆角**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 100mm 的立方体，然后对所有边做 5mm 的圆角"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_rectangle(0,0,0.1,0.1)` → 4. `feature_extrusion(0.1)` → 5. `feature_fillet(radius=0.005, edgeSelection="all")` |
| **预期结果** | 生成一个带有圆角的立方体，所有边都有 R5mm 的圆角过渡 |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "Fillet"` 特征 |

---

#### 3.3.2 倒角（Chamfer）

**SolidWorks API**: `IFeatureManager.InsertFeatureChamfer()`

> 📖 InsertFeatureChamfer 支持三种模式：等距倒角、不等距倒角、距离-角度倒角。
> 选边方式与圆角相同：先 SelectByRay + swSelEDGES，再调用倒角。

**新增 API 端点**:
```
POST /api/feature/chamfer
  {
    "type": "equal_distance" | "distance_distance" | "distance_angle",
    "distance1": 0.002,
    "distance2": 0.002,     // distance_distance 模式
    "angle": 45,            // distance_angle 模式（度）
    "rx","ry","rz","dx","dy","dz"  // 射线选边
  }
```

**测试用例 — 长方体加倒角**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 80mm x 60mm x 40mm 的长方体，然后对顶部四条边做 3mm 的等距倒角"` |
| **预期 LLM 计划** | 1~4: 创建长方体 → 5. `feature_chamfer(type="equal_distance", distance1=0.003, ...)` *(选择顶部边)* |
| **预期结果** | 长方体顶部四条边各有 C3mm 的倒角 |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "Chamfer"` 特征 |

---

#### 3.3.3 线性阵列（Linear Pattern）

**SolidWorks API**: `IFeatureManager.FeatureLinearPattern4()`

> 📖 从 3DSwym 论坛验证的参数和使用流程：
> 
> **选择要求（顺序很重要！）**：
> 1. 先选择要阵列的特征 → `SelectByID2(name, "BODYFEATURE", ..., Mark=4)`
> 2. 选择方向1参考（边） → `SelectByID2("", "EDGE", x,y,z, Append=True, Mark=1)`
> 3. 选择方向2参考（边） → `SelectByID2("", "EDGE", x,y,z, Append=True, Mark=2)`
>
> **参数**：D1数量, D1间距, D2数量, D2间距, D1反向, D2反向, 对齐类型x2, UseFeatScope, UseBodyScope, PropagateFeature, GeometryPattern, VarySketch, 其他...

**新增 API 端点**:
```
POST /api/feature/linear-pattern
  {
    "featureName": "Cut-Extrude1",    // 要阵列的特征
    "direction1": { "x": 1, "y": 0, "z": 0 },  // 方向1（用于选择参考边）
    "count1": 5,                       // 方向1数量
    "spacing1": 0.02,                  // 方向1间距（米）
    "direction2": { "x": 0, "y": 1, "z": 0 },  // 方向2（可选）
    "count2": 3,
    "spacing2": 0.02
  }
```

**测试用例 — 螺丝孔阵列**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 200mm x 100mm x 10mm 的板，在左上角打一个 ⌀8mm 的贯穿孔，然后沿 X 方向做 5 个、间距 40mm 的线性阵列"` |
| **预期 LLM 计划** | 1~4: 创建平板 → 5. 选上表面+画圆 → 6. `feature_cut_extrusion(endCondition="through_all")` → 7. `feature_linear_pattern(featureName="Cut-Extrude1", count1=5, spacing1=0.04, direction1={x:1,y:0,z:0})` |
| **预期结果** | 板上有 5 个等间距排列的贯穿圆孔 |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "LPattern"` 特征 |

---

#### 3.3.4 圆周阵列（Circular Pattern）

**SolidWorks API**: `IFeatureManager.FeatureCircularPattern4()`

> 📖 与线性阵列类似，需要先选择要阵列的特征（Mark=4）和旋转轴参考（Mark=1）。
> 旋转轴可以使用临时轴、基准轴或圆柱面的中心轴。

**新增 API 端点**:
```
POST /api/feature/circular-pattern
  {
    "featureName": "Cut-Extrude1",
    "axisType": "y" | "x" | "z" | "custom",
    "count": 6,
    "totalAngle": 360,      // 度
    "equalSpacing": true
  }
```

**测试用例 — 法兰盘螺栓孔**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 ⌀100mm、厚 10mm 的圆盘，在距中心 35mm 处打一个 ⌀8mm 贯穿孔，然后做 6 个均布的圆周阵列"` |
| **预期 LLM 计划** | 1. `new_part` → 2~4: 画圆+拉伸创建圆盘 → 5~7: 在上表面画偏心圆孔+贯穿切除 → 8. `feature_circular_pattern(featureName="Cut-Extrude1", count=6, totalAngle=360, equalSpacing=true, axisType="z")` |
| **预期结果** | 圆盘上有 6 个均匀分布的螺栓孔（每隔 60°） |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "CirPattern"` 特征 |

---

#### 3.3.5 镜像特征（Mirror）

**SolidWorks API**: `IFeatureManager.InsertMirrorFeature2()`

> 📖 镜像需要选择：1) 要镜像的特征（Mark=...）2) 镜像基准面（Mark=...）。
> 支持同时镜像多个特征。

**新增 API 端点**:
```
POST /api/feature/mirror
  {
    "featureNames": ["Cut-Extrude1", "Fillet1"],
    "mirrorPlane": 0          // 基准面索引（0=前视, 1=上视, 2=右视）
  }
```

**测试用例 — 对称零件**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 L 形零件：100mm x 50mm x 20mm 的底板，左侧有一个 50mm x 50mm x 30mm 的竖板。然后以右视基准面为镜像面，镜像竖板特征"` |
| **预期 LLM 计划** | 1~4: 创建底板 → 5~8: 在底板左侧创建竖板 → 9. `feature_mirror(featureNames=["Boss-Extrude2"], mirrorPlane=2)` |
| **预期结果** | L 形零件变为 T 形零件，左右对称 |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "MirrorPattern"` 特征 |

---

#### 3.3.6 抽壳（Shell）

**SolidWorks API**: `IFeatureManager.InsertFeatureShell()`

> 📖 官方 Create Shell Feature Example (C#) 位于：
> `https://help.solidworks.com/2026/english/api/sldworksapi/Create_Shell_Feature_Example_CSharp.htm`
> 
> 抽壳操作需要先选中要移除的面（通常是顶面），然后指定壁厚。
> 选择方式：`SelectByRay` + `swSelFACES`

**新增 API 端点**:
```
POST /api/feature/shell
  {
    "thickness": 0.002,        // 壁厚（米）
    "removeFaceRay": {         // 射线选择要移除的面（如顶面）
      "rx","ry","rz","dx","dy","dz"
    }
  }
```

**测试用例 — 创建盒子（抽壳）**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 80mm x 60mm x 50mm 的长方体，然后抽壳成 2mm 壁厚的盒子（去掉顶面）"` |
| **预期 LLM 计划** | 1~4: 创建长方体 → 5. `body_bounding_box` *(查询顶面位置)* → 6. `feature_shell(thickness=0.002, removeFaceRay={rx:0.04, ry:0.06, rz:0.025, dx:0, dy:-1, dz:0})` |
| **预期结果** | 长方体变为一个开口向上的薄壁盒子，壁厚 2mm |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "Shell"` 特征 |

---

#### 3.3.7 草图约束与标注

**SolidWorks API**: `IModelDoc2.SketchAddConstraints()` / `IModelDoc2.AddDimension2()`

> 📖 `SketchAddConstraints` 接受约束类型字符串，如 "sgHORIZONTAL2D", "sgVERTICAL2D", "sgCOINCIDENT" 等。
> `AddDimension2` 在指定坐标位置添加尺寸标注。

**新增 API 端点**:
```
POST /api/sketch/add-constraint
  { "type": "horizontal" | "vertical" | "coincident" | "tangent" | "concentric" | "equal" }

POST /api/sketch/add-dimension
  { "x": 0.05, "y": 0.05, "value": 0.1 }    // 在(x,y)位置添加尺寸
```

**测试用例 — 完全定义草图**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"在前视面画一个矩形，宽 50mm、高 30mm，添加水平和竖直约束使其完全定义"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(0)` → 3. `sketch_rectangle(0,0,0.05,0.03)` → 4. `sketch_add_constraint(type="horizontal")` → 5. `sketch_add_dimension(x=0.025, y=-0.005, value=0.05)` → 6. `sketch_add_dimension(x=-0.005, y=0.015, value=0.03)` → 7. `close_sketch` |
| **预期结果** | 草图显示为黑色（完全定义状态），不是蓝色（欠定义） |

---

#### 3.3.8 导出多格式

**SolidWorks API**: `IModelDoc2.Extension.SaveAs3()`

> 📖 SaveAs3 通过文件扩展名自动识别导出格式，关键格式对应关系：
> - `.sldprt` → SolidWorks 零件
> - `.step` / `.stp` → STEP AP203/AP214
> - `.stl` → STL（三角面片）
> - `.igs` / `.iges` → IGES
> - `.x_t` → Parasolid
> - `.3mf` → 3MF 增材制造格式
> - `.pdf` → 3D PDF（需要 SolidWorks 专业版以上）
>
> 导出 STL 时可通过 `IModelDoc2.SetUserPreferenceDoubleValue()` 设置精度（偏差/角度），控制网格质量。

**修改/扩展 save-as 端点**:
```
POST /api/save-as
  {
    "filePath": "C:/export/model.step",
    "format": "sldprt" | "step" | "stl" | "iges" | "parasolid" | "3mf" | "pdf"
  }
```

**测试用例 — 导出 STEP 格式**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 50mm 的立方体，然后导出为 STEP 格式保存到 C:/temp/cube.step"` |
| **预期 LLM 计划** | 1~4: 创建立方体 → 5. `save_as(filePath="C:/temp/cube.step", format="step")` |
| **预期结果** | 在 `C:/temp/cube.step` 生成有效的 STEP 文件，可被其他 CAD 软件（如 FreeCAD）导入 |
| **验证方法** | 检查文件存在且大小 > 0；用文本编辑器打开确认首行为 `ISO-10303-21;` |

**测试用例 — 导出 STL 用于 3D 打印**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 ⌀30mm、高 50mm 的圆柱体，导出为 STL 格式"` |
| **预期 LLM 计划** | 1~4: 创建圆柱体 → 5. `save_as(filePath="C:/temp/cylinder.stl", format="stl")` |
| **预期结果** | 在 `C:/temp/cylinder.stl` 生成有效的 STL 文件 |

---

#### 3.3.9 创建基准面/基准轴

**SolidWorks API**: `IFeatureManager.InsertRefPlane()` / `IFeatureManager.InsertRefAxis()`

> 📖 `InsertRefPlane` 参数说明：
> - 第一个参考（`FirstRef`）：通过 `SelectByID2` 选中一个已有的基准面或平面
> - 约束类型参数：`swRefPlaneReferenceConstraint_Distance` (距离偏移)、`swRefPlaneReferenceConstraint_Angle` (角度偏移)
> - 偏移距离单位为米
>
> `InsertRefAxis` 需要先选中两个面或两个基准面，SolidWorks 自动计算它们交线作为基准轴。

**新增 API 端点**:
```
POST /api/feature/ref-plane
  {
    "type": "offset",           // offset | angle | three_points
    "basePlane": 0,             // 基准面索引
    "distance": 0.05            // 偏移距离（米）
  }

POST /api/feature/ref-axis
  {
    "type": "two_planes" | "cylindrical_face",
    "plane1": 0, "plane2": 2   // 两个基准面定义轴
  }
```

**测试用例 — 偏移基准面上画草图**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"在前视面向前偏移 50mm 创建一个新的基准面，然后在新基准面上画一个 ⌀20mm 的圆"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `feature_ref_plane(type="offset", basePlane=0, distance=0.05)` → 3. `select_plane_by_name("Plane1")` → 4. `sketch_circle(cx=0, cy=0, radius=0.01)` → 5. `close_sketch` |
| **预期结果** | 特征树中出现一个新的基准面 `Plane1`，距前视面 50mm，上面有一个 ⌀20mm 的圆形草图 |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "RefPlane"` 的新增特征（除默认的 3 个基准面外） |

---

### 3.4 Phase 3：高级功能

> 目标：支持复杂零件建模、视觉反馈和智能 Agent 闭环。

#### 3.4.1 扫掠（Sweep）

**SolidWorks API**: `IFeatureManager.InsertProtrusionSwept()`

> 📖 扫掠操作需要两个草图：截面轮廓（Profile）和路径（Path）。
> 调用前必须先选择截面草图（Mark=0）和路径草图（Mark=4）。
>
> **操作流程**：
> 1. 在基准面 A 上画截面轮廓（如圆形）→ 关闭草图
> 2. 在基准面 B 上画路径曲线（如样条线/圆弧）→ 关闭草图
> 3. 选择截面草图 + 路径草图 → 调用 InsertProtrusionSwept
>
> 切除版本使用 `InsertCutSwept()`。

```
POST /api/feature/sweep
  {
    "profileSketchName": "Sketch1",   // 截面草图名称
    "pathSketchName": "Sketch2",      // 路径草图名称
    "isCut": false
  }
```

**测试用例 — 创建弯管**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个截面直径 10mm 的弯管：从原点沿 Y 轴直行 50mm，然后以半径 30mm 弯曲 90° 转向 X 轴方向"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(2)` *(右视面画路径)* → 3. `sketch_line(0,0,0,0.05)` → 4. `sketch_arc(cx=0.03, cy=0.05, startX=0, startY=0.05, endX=0.03, endY=0.08, direction=1)` → 5. `close_sketch` → 6. `select_plane(0)` *(前视面画截面)* → 7. `sketch_circle(0, 0, 0.005)` → 8. `close_sketch` → 9. `feature_sweep(profileSketchName="Sketch2", pathSketchName="Sketch1")` |
| **预期结果** | 生成一根沿 L 形路径弯曲的管道，截面为 ⌀10mm |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "Sweep"` 特征 |

---

#### 3.4.2 放样（Loft）

**SolidWorks API**: `IFeatureManager.InsertProtrusionBlend2()`

> 📖 放样操作需要在多个平行平面上分别绘制截面轮廓。
> 调用前必须选择所有截面草图（按顺序选中）。
>
> **操作流程**：
> 1. 在基准面上画截面 1（如正方形）→ 关闭草图
> 2. 创建偏移基准面 → 画截面 2（如圆形）→ 关闭草图
> 3. 选择截面 1 + 截面 2 → 调用 InsertProtrusionBlend2
>
> 切除版本使用 `InsertCutBlend2()`。

```
POST /api/feature/loft
  {
    "profileSketchNames": ["Sketch1", "Sketch2"],  // 截面草图名称列表（按顺序）
    "isCut": false
  }
```

**测试用例 — 方变圆过渡件**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个过渡件：底面是 60mm 的正方形，顶面是 ⌀40mm 的圆形，高度 80mm"` |
| **预期 LLM 计划** | 1. `new_part` → 2. `select_plane(1)` *(上视面)* → 3. `sketch_rectangle(-0.03, -0.03, 0.03, 0.03)` → 4. `close_sketch` → 5. `feature_ref_plane(type="offset", basePlane=1, distance=0.08)` → 6. `select_plane_by_name("Plane1")` → 7. `sketch_circle(0, 0, 0.02)` → 8. `close_sketch` → 9. `feature_loft(profileSketchNames=["Sketch1", "Sketch2"])` |
| **预期结果** | 生成底部为正方形、顶部为圆形的光滑过渡实体，高 80mm |
| **验证方法** | `GET /api/feature-tree` 确认存在 `"type": "Loft"` 特征；`GET /api/body-bounding-box` → sizeY≈0.08 |

---

#### 3.4.3 模型截图（View Capture）

**SolidWorks API**: `IModelDoc2.SaveBMP()` / `IModelView.GetGDIImage()`

> 📖 两种截图方式：
> - `SaveBMP(filePath, width, height)` — 保存当前视图为 BMP 图片文件，支持指定分辨率
> - `IModelView.GetGDIImage()` — 获取当前视图的 GDI 位图句柄，可在内存中转换为其他格式
>
> 建议使用 `SaveBMP` 方式更简单直接。截图前可先调用 `ShowNamedView2("*Isometric", ...)` 切换到等轴测视图以获得最佳展示效果。

```
GET /api/screenshot
  → 返回 { "success": true, "filePath": "...", "base64": "..." }
  参数（可选）:
    ?width=1920&height=1080     // 图片分辨率
    ?view=isometric             // 截图前先切换视图
```

**测试用例 — 截图反馈**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 50mm 的立方体，然后截一张等轴测视图的图片"` |
| **预期 LLM 计划** | 1~4: 创建立方体 → 5. `view(action="standard_view", viewName="isometric")` → 6. `screenshot` |
| **预期结果** | 返回一张等轴测视角的立方体截图（BMP/PNG 格式），可在 Web 前端展示 |
| **验证方法** | 检查返回的 `base64` 字段不为空，或 `filePath` 指向的文件存在且大小 > 0 |

**这是实现视觉反馈闭环的关键能力**：LLM 可以"看到"模型当前状态。

---

#### 3.4.4 质量属性查询

**SolidWorks API**: `IModelDoc2.Extension.CreateMassProperty()` → `IMassProperty2`

> 📖 `IMassProperty2` 接口提供以下属性：
> - `Volume` — 体积（m³）
> - `SurfaceArea` — 表面积（m²）
> - `Mass` — 质量（kg，依赖于材料密度设置）
> - `CenterOfMass` — 质心坐标（double[3]，单位米）
> - `GetMomentOfInertia()` — 转动惯量
>
> 注意：如果未指定材料，SolidWorks 默认使用"普通碳钢"（密度 7800 kg/m³）。

```
GET /api/mass-properties
  → {
      "success": true,
      "volume": 0.000125,          // m³
      "surfaceArea": 0.015,        // m²
      "mass": 0.975,               // kg
      "density": 7800,             // kg/m³
      "centerOfMass": { "x": 0.025, "y": 0.025, "z": 0.025 }
    }
```

**测试用例 — 查询并验证立方体体积**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 50mm 的立方体，然后查询它的体积和质量"` |
| **预期 LLM 计划** | 1~4: 创建 50mm 立方体 → 5. `mass_properties` |
| **预期结果** | 返回 volume ≈ 0.000125 m³ (= 50³ mm³ = 125000 mm³)，mass ≈ 0.975 kg（碳钢密度 7800 kg/m³ × 0.000125 m³） |
| **验证方法** | 检查 `volume` ≈ 0.000125 (允许浮点误差 1e-6)，`centerOfMass` 约为 (0.025, 0.025, 0.025) |

---

#### 3.4.5 多轮 Agent 闭环

```
用户指令 → LLM Plan → 执行 → 截图 → LLM 验证 → (不对?) → 修正 → 再执行
                                        ↑                              |
                                        └──────────────────────────────┘
```

改造 `SwLLMClient`，支持多轮对话上下文，每次执行后：
1. 自动截图当前模型
2. 查询特征树和包围盒
3. 将执行结果 + 截图反馈给 LLM
4. LLM 判断是否需要修正
5. 如需修正，生成后续操作步骤

**实现要点**：
- `SwLLMClient` 维护 `messages: list[dict]` 对话历史
- 每轮执行完成后追加 assistant 消息（包含执行结果 JSON + base64 截图）
- LLM 返回包含 `"needs_correction": bool` 字段
- 最大修正轮次限制（如 3 轮），防止无限循环

**测试用例 — 自动修正尺寸错误**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个 100mm 的立方体"` |
| **模拟场景** | 第一轮 LLM 生成的计划中，`sketch_rectangle` 参数误写为 (0,0,0.01,0.01)（10mm 而非 100mm） |
| **预期 Agent 行为** | 执行后截图 + 查询 bounding box → 发现 sizeX=0.01（10mm）与目标 100mm 不符 → LLM 第二轮判断需要修正 → 生成 `undo + 重新绘制` 或 `modify_dimension` 修正计划 → 再次执行 → 验证通过 |
| **预期最终结果** | 最终生成正确的 100mm 立方体，尽管第一轮有错误 |
| **验证方法** | 检查 Agent 执行了 ≥ 2 轮对话；最终 `GET /api/body-bounding-box` → sizeX≈0.1 |

---

#### 3.4.6 视图操作

**SolidWorks API**: `IModelDoc2.ShowNamedView2()` / `IModelDoc2.ViewZoomtofit2()`

> 📖 `ShowNamedView2(viewName, viewId)` 切换标准视图：
> - viewName 传空字符串时使用 viewId：`swStandardViews_e` 枚举
>   - `swFrontView`=1, `swBackView`=2, `swTopView`=5, `swBottomView`=6
>   - `swLeftView`=3, `swRightView`=4, `swIsometricView`=7, `swTrimetricView`=8
> - `ViewZoomtofit2()` — 自适应缩放，让模型完整显示在视口中
> - `ViewRotateplusx()` / `ViewRotateplusy()` — 旋转视图

```
POST /api/view
  {
    "action": "standard_view" | "zoom_fit" | "rotate",
    "viewName": "isometric" | "front" | "top" | "right" | "back" | "bottom" | "left" | "trimetric",
    "angle": 45        // rotate 时使用（度）
  }
```

**测试用例 — 切换到等轴测视图**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"把当前视图切换到等轴测视图，并自适应缩放"` |
| **预期 LLM 计划** | 1. `view(action="standard_view", viewName="isometric")` → 2. `view(action="zoom_fit")` |
| **预期结果** | SolidWorks 视图切换为等轴测视角，模型完整显示在视口中 |
| **验证方法** | 截图对比：切换前后的视角应有明显变化 |

---

### 3.5 Phase 4：产品化

> 目标：将项目从 Demo 进化为可用的产品原型。

#### 3.5.1 装配体支持

**SolidWorks API**:
- 新建装配体: `ISldWorks.INewDocument2()` 使用装配体模板（`swDocASSEMBLY`）
- 插入零件: `IAssemblyDoc.AddComponent5()`
- 添加配合关系: `IAssemblyDoc.AddMate5()`

> 📖 `AddComponent5` 参数说明：
> - `compPath` (string): 零件文件完整路径
> - `configName` (string): 配置名称，空字符串使用默认配置
> - `Translate` (bool): 是否允许移动
> - `x, y, z` (double): 插入位置坐标（米）
>
> `AddMate5` 支持的配合类型（`swMateType_e`）：
> - `swMateCoincident` (0): 重合
> - `swMateConcentric` (1): 同心
> - `swMateDistance` (5): 距离
> - `swMateAngle` (6): 角度
> - `swMateParallel` (3): 平行

**新增 API 端点**:
```
POST /api/new-assembly     // 新建装配体
POST /api/assembly/add-component
  { "filePath": "C:/parts/bolt.sldprt", "x": 0, "y": 0, "z": 0 }
POST /api/assembly/add-mate
  { "type": "coincident" | "concentric" | "distance", "distance": 0.01 }
```

**测试用例 — 简单装配体**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"创建一个新装配体，插入两个零件 C:/temp/plate.sldprt 和 C:/temp/bolt.sldprt，将螺栓放在板上方"` |
| **预期 LLM 计划** | 1. `new_assembly` → 2. `assembly_add_component(filePath="C:/temp/plate.sldprt", x=0, y=0, z=0)` → 3. `assembly_add_component(filePath="C:/temp/bolt.sldprt", x=0, y=0.02, z=0)` |
| **预期结果** | 装配体中包含两个零件组件，螺栓在板的上方 |
| **验证方法** | `GET /api/feature-tree` 确认存在两个组件节点 |

---

#### 3.5.2 工程图自动生成

**SolidWorks API**:
- 新建工程图: `ISldWorks.INewDocument2()` 使用工程图模板（`swDocDRAWING`）
- 自动投影三视图: `IDrawingDoc.CreateDrawViewFromModelView3()`
- 自动标注尺寸: `IDrawingDoc.InsertModelAnnotations3()`

> 📖 `CreateDrawViewFromModelView3` 参数说明：
> - `modelName` (string): 零件/装配体文件路径
> - `viewName` (string): 视图名称（如 "*Front", "*Top", "*Right", "*Isometric"）
> - `x, y` (double): 视图在图纸上的放置位置
> - `ScaleNum/ScaleDen` (int): 比例尺分子/分母
>
> 三视图投影的标准流程：先创建前视图（主视图），然后从前视图投影生成俯视图和右视图。

**新增 API 端点**:
```
POST /api/new-drawing
  { "modelFilePath": "C:/temp/part.sldprt", "paperSize": "A4" | "A3" }
POST /api/drawing/create-views
  { "viewTypes": ["front", "top", "right", "isometric"], "scale": "1:2" }
POST /api/drawing/auto-dimension
  {}    // 自动标注主要尺寸
```

**测试用例 — 三视图工程图**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"为 C:/temp/cube.sldprt 创建一张 A4 工程图，包含前视图、俯视图和右视图，比例 1:2"` |
| **预期 LLM 计划** | 1. `new_drawing(modelFilePath="C:/temp/cube.sldprt", paperSize="A4")` → 2. `drawing_create_views(viewTypes=["front","top","right"], scale="1:2")` → 3. `drawing_auto_dimension` |
| **预期结果** | 生成一张 A4 工程图，包含标准三视图布局和主要尺寸标注 |
| **验证方法** | SolidWorks 中打开工程图，确认三个视图投影正确、比例为 1:2 |

---

#### 3.5.3 参数化模板库

- 预定义常见零件模板（法兰盘、齿轮、支架等）
- LLM 可以调用模板并填写参数
- 模板以 JSON 配置文件存储，包含参数定义和操作序列

**实现架构**:
```
templates/
├── flange.json        # 法兰盘模板
├── gear_spur.json     # 直齿轮模板
├── bracket_L.json     # L 形支架模板
└── ...
```

每个模板 JSON 结构：
```json
{
  "name": "法兰盘",
  "parameters": {
    "outer_diameter": { "type": "float", "default": 0.1, "unit": "m", "description": "外径" },
    "inner_diameter": { "type": "float", "default": 0.04, "unit": "m", "description": "内径" },
    "thickness": { "type": "float", "default": 0.01, "unit": "m", "description": "厚度" },
    "bolt_count": { "type": "int", "default": 6, "description": "螺栓孔数量" },
    "bolt_diameter": { "type": "float", "default": 0.008, "unit": "m", "description": "螺栓孔直径" }
  },
  "operations": [
    { "type": "new_part" },
    { "type": "select_plane", "params": { "index": 0 } },
    { "type": "sketch_circle", "params": { "cx": 0, "cy": 0, "radius": "${outer_diameter/2}" } },
    "..."
  ]
}
```

**新增 API 端点**:
```
GET  /api/templates                        // 列出可用模板
POST /api/templates/create
  { "templateName": "flange", "parameters": { "outer_diameter": 0.1, "bolt_count": 8 } }
```

**测试用例 — 使用模板创建法兰盘**:

| 项目 | 内容 |
|------|------|
| **用户 Prompt** | `"使用法兰盘模板创建一个外径 120mm、内径 50mm、厚 15mm、8 个 M10 螺栓孔的法兰盘"` |
| **预期 LLM 计划** | 1. `template_create(templateName="flange", parameters={outer_diameter: 0.12, inner_diameter: 0.05, thickness: 0.015, bolt_count: 8, bolt_diameter: 0.01})` |
| **预期结果** | 生成一个标准法兰盘：外圆 ⌀120mm，中心孔 ⌀50mm，厚 15mm，均布 8 个 ⌀10mm 螺栓孔 |
| **验证方法** | `GET /api/feature-tree` 确认包含外圆拉伸、中心切除、螺栓孔切除和圆周阵列特征 |

---

#### 3.5.4 API 文档化

- 为 C# HTTP API 添加 OpenAPI/Swagger 文档
- 自动生成 Python 客户端 SDK

**实现方案**:
- 在 C# 端生成 `openapi.json` 规范文件（手动编写或通过反射自动生成端点描述）
- 提供 `GET /api/openapi.json` 端点返回 API 规范
- 使用 `openapi-generator` 工具从规范文件生成 Python 客户端类
- 同步更新 `CODEBUDDY.md` 中的端点表格

**新增 API 端点**:
```
GET /api/openapi.json      // 返回 OpenAPI 3.0 规范
GET /api/docs              // 返回简易 HTML API 文档页面
```

**测试用例 — API 文档可访问**:

| 项目 | 内容 |
|------|------|
| **验证步骤** | 1. 启动 C# HTTP 服务 → 2. 访问 `http://localhost:5100/api/openapi.json` → 3. 验证返回有效的 OpenAPI JSON |
| **预期结果** | 返回符合 OpenAPI 3.0 规范的 JSON，包含所有已实现端点的路径、参数和响应定义 |
| **验证方法** | 使用 Swagger Editor 导入 JSON，确认所有端点渲染正确且无 validation error |

---

## 四、实施建议

### 4.1 每个新功能的标准开发流程

```
1. C# SwCommands.cs — 新增方法封装 SolidWorks COM 调用
2. C# Program.cs    — 新增 HTTP 端点 + Handler
3. Python schemas.py — 新增 OperationType 枚举值
4. Python executor.py — 新增端点映射
5. Python llm_client.py — 更新 SYSTEM_PROMPT（新增操作说明）
6. 测试 — 手动 curl/Postman 测试 + Python 脚本测试
7. CODEBUDDY.md — 更新文档
```

### 4.2 代码组织建议

当功能增多后，建议对 `SwCommands.cs` 进行拆分：

```
SwCommands/
├── SwCommandsBase.cs         # 基类：ISldWorks 引用、ActiveDoc、辅助方法
├── SwSketchCommands.cs       # 草图操作：Line, Rectangle, Circle, Arc, Polygon...
├── SwFeatureCommands.cs      # 特征操作：Extrusion, Cut, Revolve, Fillet...
├── SwPatternCommands.cs      # 阵列/镜像操作
├── SwQueryCommands.cs        # 查询操作：FeatureTree, BoundingBox, MassProperties...
├── SwDocumentCommands.cs     # 文档操作：NewPart, Save, Open, Export...
└── SwViewCommands.cs         # 视图操作：Screenshot, ZoomFit, StandardView...
```

同样，`Program.cs` 的路由分发可以考虑使用字典映射替代巨大的 switch-case。

### 4.3 LLM System Prompt 管理策略

随着操作类型从 11 种增长到 30+，System Prompt 会变得很长。建议：

1. **分类组织**：按草图/特征/查询/编辑分组，使用 Markdown 格式
2. **参数文档自动生成**：从 `schemas.py` 的 Field description 自动生成 prompt 片段
3. **动态 Prompt**：根据用户指令类型（创建 vs 编辑 vs 查询），只加载相关操作的说明
4. **Few-shot 示例库**：维护 10-20 个常见建模任务的标准操作序列作为 few-shot 示例

### 4.4 优先级排序建议

按照投入产出比（Impact / Effort），建议的开发顺序：

| 优先级 | 功能 | Impact | Effort | 理由 |
|--------|------|--------|--------|------|
| P0 | 切除拉伸 | ⭐⭐⭐⭐⭐ | ⭐⭐ | 最高频需求，代码与拉伸类似 |
| P0 | 文件保存/打开 | ⭐⭐⭐⭐⭐ | ⭐ | 必需功能，API 极简单 |
| P0 | 拉伸终止条件扩展 | ⭐⭐⭐⭐ | ⭐ | 仅需修改现有方法参数 |
| P1 | 圆角 | ⭐⭐⭐⭐ | ⭐⭐ | 高频需求 |
| P1 | 倒角 | ⭐⭐⭐⭐ | ⭐⭐ | 高频需求 |
| P1 | 圆弧草图 | ⭐⭐⭐⭐ | ⭐⭐ | 旋转特征的前置依赖 |
| P1 | 中心线草图 | ⭐⭐⭐ | ⭐ | 旋转特征的前置依赖 |
| P1 | 旋转凸台/切除 | ⭐⭐⭐⭐ | ⭐⭐⭐ | 轴对称零件必需 |
| P2 | 线性阵列 | ⭐⭐⭐ | ⭐⭐⭐ | 选择逻辑较复杂 |
| P2 | 圆周阵列 | ⭐⭐⭐ | ⭐⭐⭐ | 选择逻辑较复杂 |
| P2 | 镜像 | ⭐⭐⭐ | ⭐⭐ | — |
| P2 | 抽壳 | ⭐⭐⭐ | ⭐⭐ | 薄壁件常用 |
| P2 | 草图约束 | ⭐⭐⭐ | ⭐⭐⭐ | 精度控制 |
| P2 | 多格式导出 | ⭐⭐⭐ | ⭐ | SaveAs 参数变化 |
| P3 | 模型截图 | ⭐⭐⭐⭐ | ⭐⭐ | Agent 闭环关键 |
| P3 | 扫掠/放样 | ⭐⭐ | ⭐⭐⭐⭐ | 复杂曲面，实现难度大 |
| P3 | 质量属性 | ⭐⭐ | ⭐ | API 简单 |
| P3 | 多轮 Agent | ⭐⭐⭐⭐⭐ | ⭐⭐⭐⭐⭐ | 质的飞跃，但工作量大 |

---

## 五、参考资源

### SolidWorks API 官方文档

- **API Help 首页**: `https://help.solidworks.com/2025/english/api/sldworksapiprogwelcome/start_here.html`
- **IFeatureManager 方法**: `https://help.solidworks.com/2022/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.ifeaturemanager_methods.html`
- **ISketchManager 方法**: `https://help.solidworks.com/2022/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.isketchmanager_methods.html`
- **IModelDoc2 方法**: `https://help.solidworks.com/2022/english/api/sldworksapi/solidworks.interop.sldworks~solidworks.interop.sldworks.imodeldoc2_methods.html`

### 社区资源

- **SolidWorks API 教程 (GitHub)**: `https://github.com/weianweigan/SolidWorksAPITutorial`
- **CodeStack SolidWorks API**: `https://www.codestack.net/solidworks-api/`
- **SolidWorks API 中文博客**: `https://www.cnblogs.com/solidworksapi/`

### 关键 COM 接口速查

| 接口 | 获取方式 | 核心职责 |
|------|---------|---------|
| `ISldWorks` | `SwApplicationFactory.FromProcess()` → `.Sw` | 应用程序入口 |
| `IModelDoc2` | `ISldWorks.IActiveDoc2` / `.INewDocument2()` | 文档操作核心 |
| `ISketchManager` | `IModelDoc2.SketchManager` | 草图绘制 |
| `IFeatureManager` | `IModelDoc2.FeatureManager` | 特征创建 |
| `ISelectionManager` | `IModelDoc2.ISelectionManager` | 选择管理 |
| `IPartDoc` | `IModelDoc2` 强转 | 零件特有操作 |
| `IAssemblyDoc` | `IModelDoc2` 强转 | 装配体特有操作 |
| `IDrawingDoc` | `IModelDoc2` 强转 | 工程图特有操作 |
| `IMathUtility` | `ISldWorks.GetMathUtility()` | 数学计算 |
| `IBody2` | `IPartDoc.GetBodies2()` | 实体几何操作 |
| `IFace2` | `IBody2.GetFirstFace()` | 面操作 |
| `IEdge` | `IFace2.GetEdges()` | 边操作 |
| `IDimension` | `IFeature.GetFirstDisplayDimension()` | 尺寸操作 |

---

## 六、总结

当前项目已搭建了一套完整的"自然语言 → LLM 规划 → HTTP API → SolidWorks"自动化链路，但 **SolidWorks 端的原子操作覆盖面严重不足**（仅 11 种操作），导致只能完成最简单的长方体/圆柱建模。

**最关键的 3 个扩展方向**：

1. **🔴 切除拉伸 + 拉伸条件扩展**：这是使用频率最高的缺失功能，补齐后可立即支持"挖孔"、"开槽"、"贯穿切除"等高频场景。实现成本极低（代码结构与现有拉伸几乎相同）。

2. **🔴 文件保存/打开**：这是产品化的基本前提，没有保存功能意味着所有建模结果都会丢失。

3. **🔴 圆角 + 倒角**：几乎所有真实零件都需要边缘过渡处理，补齐后零件的"真实感"将大幅提升。

建议按 Phase 1 → Phase 2 的顺序推进，每个 Phase 完成后都能显著提升 Copilot 的可用性和 Demo 效果。
