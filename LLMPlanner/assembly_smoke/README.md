# 装配体 API 冒烟测试

本目录是 Chapter1.ConnectToSw 装配体 API 扩展（Stage B）的端到端冒烟测试脚本 + 齿轮组样例数据。

## 目录内容

| 文件 | 用途 |
|---|---|
| `gear_small_involute_bored.SLDPRT` | 小齿轮（模数 4mm、10 齿、30° 压力角、⌀10mm 轴孔）|
| `gear_big_involute_bored.SLDPRT` | 大齿轮（同模数/压力角、20 齿、齿形相位 +9°、⌀10mm 轴孔）|
| `gear_pair_assembly_v4.SLDASM` | 真实渐开线啮合装配体（60mm 理论中心距、外啮合 -2:1 Gear Mate）✅ |
| `gear_pair_no_mate.SLDASM` | 无 Mate 的对照样本（用于验证"完全没有 Mate 时 Transform2 是否生效"）|
| `phase1_build_two_gears.py` | 计算分段渐开线闭合轮廓，并通过 HTTP API 自动建模、保存两个齿轮 |
| `phase3_via_csharp_api.py` | 用装配体 API 搭建 60mm 中心距渐开线齿轮组 |
| `test_gear_geometry.py` | 在一个完整齿周期内采样 73 帧，检查两齿轮轮廓无交叉穿模 |
| `test_motion_study.py` | Motion Study 旋转马达端到端测试：播放、timebar 推进、主动/从动齿轮 Transform 变化 |
| `capture_gear_animation.py` | 定位 Motion timebar，导出 25 帧 PNG/BMP 和 GIF，并逐帧调用 SolidWorks 原生干涉检测 |

## 快速验证：从头搭一个齿轮啮合组

前置：SolidWorks 已启动、C# 服务运行中（`run_service.bat`）。缺少渐开线零件时，先运行 `phase1_build_two_gears.py` 自动重建。

```bash
cd LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\phase3_via_csharp_api.py
```

预期输出：
```
✅ POST /api/assembly/new           → 新建装配体
✅ POST /api/assembly/add-component → gear_small_involute_bored-1
✅ POST /api/assembly/add-component → gear_big_involute_bored-1
✅ POST /api/assembly/mate-coincident → Coincident Mate 完成
✅ POST /api/assembly/mate-gear     → Gear Mate 完成 (比 20:10)
✅ POST /api/assembly/save          → 装配体已保存
```

两齿轮使用相同模数 4mm、压力角 30°。分度圆直径分别为 40/80mm，因此理论中心距为 `(40 + 80) / 2 = 60mm`。30° 压力角使 10 齿小齿轮避开标准 20° 全齿高齿形的根切区；1.25m 齿根深度提供 1mm 顶隙。大齿轮错相半齿距 9°，Gear Mate 保持严格的 `ω大 = -ω小 / 2`。

## ✅ Motion Study 编程驱动齿轮旋转

2026-07-14 已通过 Motion Study API 打通可见齿轮动画：

- `IModelDocExtension.GetMotionStudyManager()` 获取/创建 Animation Study；
- `swFmAEMRotationalMotor` + `ISimulationMotorFeatureData.ConstantSpeedMotor()` 创建旋转马达；
- `IMotionStudy.PlayFromStart()` / `Stop()` 控制播放；
- 小齿轮由马达驱动，大齿轮通过 Gear Mate 按 2:1 **反向**联动；
- 自动检查双齿轮中心距为 60mm；
- 纯几何测试覆盖一个完整小齿轮齿周期，73 个角度采样均无轮廓交叉；
- Motion 测试校验有符号转角（实测 `-64.458° : +32.229° = -2:1`），不再只检查矩阵是否变化；
- 导出的一个完整齿周期 25 帧均通过 SolidWorks `IInterferenceDetectionMgr` 原生零干涉检查；
- Web demo 提供“加载装配体 / 播放 5 圈 / 停止”控件。

自动验证：

```bash
cd LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\test_motion_study.py
venv\Scripts\python.exe -X utf8 assembly_smoke\capture_gear_animation.py
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
