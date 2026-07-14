# 装配体 API 冒烟测试

本目录是 Chapter1.ConnectToSw 装配体 API 扩展（Stage B）的端到端冒烟测试脚本 + 齿轮组样例数据。

## 目录内容

| 文件 | 用途 |
|---|---|
| `gear_small.SLDPRT` | 小齿轮零件（分度圆 ⌀40mm × 厚 15mm，10 个矩形齿）— 由 phase1 脚本 + 手动 SaveAs 生成 |
| `gear_big_phased.SLDPRT` | 大齿轮零件（分度圆 ⌀80mm × 厚 15mm，20 个矩形齿，齿形相位 +9°）— 相位直接建入零件，避免装配体 Transform 静默失效 |
| `gear_pair_assembly_v3.SLDASM` | 齿轮啮合装配体（64mm 中心距、20:10 Gear Mate）— 由 phase3 脚本生成，**手动拖动小齿轮时大齿轮会按 2:1 反向联动** ✅ |
| `gear_pair_no_mate.SLDASM` | 无 Mate 的对照样本（用于验证"完全没有 Mate 时 Transform2 是否生效"）|
| `phase1_build_two_gears.py` | 用现有零件建模 HTTP API 建两个齿轮零件（用户需手动 SaveAs 保存为 .SLDPRT）|
| `phase3_via_csharp_api.py` | 用装配体 API 端到端搭齿轮啮合组（64mm 中心距 + 大齿轮 9° 错相 + Coincident/Gear Mate + Save）|
| `test_motion_study.py` | Motion Study 旋转马达端到端测试：播放、timebar 推进、主动/从动齿轮 Transform 变化 |

## 快速验证：从头搭一个齿轮啮合组

前置：SolidWorks 已启动、C# 服务运行中（`run_service.bat`）、`gear_small.SLDPRT` + `gear_big_phased.SLDPRT` 已存在（否则用 phase1 重建）。

```bash
cd LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\phase3_via_csharp_api.py
```

预期输出：
```
✅ POST /api/assembly/new           → 新建装配体
✅ POST /api/assembly/add-component → gear_small-1
✅ POST /api/assembly/add-component → gear_big_phased-1
✅ POST /api/assembly/mate-coincident → Coincident Mate 完成
✅ POST /api/assembly/mate-gear     → Gear Mate 完成 (比 20:10)
✅ POST /api/assembly/save          → 装配体已保存
```

64mm 来自简化齿形的接触条件：小齿轮齿顶半径 24mm + 大齿轮圆盘半径 40mm。大齿轮再错相半个齿距（`360° / 20 / 2 = 9°`），使齿顶对准另一齿轮的齿槽。之后在 SolidWorks 里打开 `gear_pair_assembly_v3.SLDASM`，用**装配体 → 移动零部件**拖动小齿轮，大齿轮会按 2:1 反向联动旋转。

## ✅ Motion Study 编程驱动齿轮旋转

2026-07-14 已通过 Motion Study API 打通可见齿轮动画：

- `IModelDocExtension.GetMotionStudyManager()` 获取/创建 Animation Study；
- `swFmAEMRotationalMotor` + `ISimulationMotorFeatureData.ConstantSpeedMotor()` 创建旋转马达；
- `IMotionStudy.PlayFromStart()` / `Stop()` 控制播放；
- 小齿轮由马达驱动，大齿轮通过 Gear Mate 按 2:1 联动；
- 自动检查双齿轮中心距为 64mm，避免旧版 70mm 中心距造成齿顶悬空；
- Web demo 提供“加载装配体 / 播放 5 圈 / 停止”控件。

自动验证：

```bash
cd LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\test_motion_study.py
```

实测：Motion timebar 推进到 1.25s，小齿轮矩阵变化 1.325782，大齿轮矩阵变化 0.693351。

⚠️ `SetTransformAndSolve3`、`Transform2` setter 和 `IDragOperator.Drag` 三条旧路径仍会出现“返回成功但 Transform 不变”，保留用于历史诊断，不作为动画实现。

## 已提供的 12 个装配体 HTTP 端点

| 端点 | 用途 | 状态 |
|---|---|---|
| POST `/api/assembly/new` | 新建装配体（用内置 empty_template）| ✅ |
| POST `/api/assembly/open` | 打开已有 .SLDASM | ✅ |
| POST `/api/assembly/add-component` | 加零件到装配体指定位置 | ✅ |
| POST `/api/assembly/unfix-component` | 组件从 Fixed 变 Float | ✅ |
| POST `/api/assembly/mate-coincident` | 重合配合（两个基准面）| ✅ |
| POST `/api/assembly/mate-concentric` | 同心配合（两圆柱面，射线选面）| ✅ |
| POST `/api/assembly/mate-gear` | 齿轮联动配合（含齿数比）| ✅ |
| POST `/api/assembly/save` | 保存装配体 | ✅ |
| POST `/api/assembly/rebuild` | 强制重建 | ✅ |
| POST `/api/assembly/get-component-transform` | 读组件当前变换矩阵（调试用）| ✅ |
| POST `/api/assembly/rotate-component` | **编程旋转组件** | ⚠️ 视觉无变化 |
| POST `/api/assembly/rotate-component-by-drag` | **DragOperator 旋转** | ⚠️ 视觉无变化 |
| POST `/api/assembly/motion/probe` | Motion Study 能力探针 | ✅ |
| POST `/api/assembly/motion/create-gear-motor` | 创建或更新旋转马达 | ✅ |
| POST `/api/assembly/motion/play` | 播放运动算例 | ✅ |
| POST `/api/assembly/motion/stop` | 停止运动算例 | ✅ |
| GET `/api/assembly/motion/status` | 查询 timebar / 播放状态 | ✅ |
| POST `/api/save-part-as` | 零件另存为独立 .SLDPRT | ✅ |
