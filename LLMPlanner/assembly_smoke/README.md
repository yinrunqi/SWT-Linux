# 装配体 API 冒烟测试

本目录是 Chapter1.ConnectToSw 装配体 API 扩展（Stage B）的端到端冒烟测试脚本 + 齿轮组样例数据。

## 目录内容

| 文件 | 用途 |
|---|---|
| `gear_small.SLDPRT` | 小齿轮零件（分度圆 ⌀40mm × 厚 15mm，10 个矩形齿）— 由 phase1 脚本 + 手动 SaveAs 生成 |
| `gear_big.SLDPRT` | 大齿轮零件（分度圆 ⌀80mm × 厚 15mm，20 个矩形齿）— 同上 |
| `gear_pair_assembly_v3.SLDASM` | 齿轮啮合装配体（Gear Mate 齿数比 20:10）— 由 phase3 脚本生成，**手动拖动小齿轮时大齿轮会按 2:1 反向联动** ✅ |
| `gear_pair_no_mate.SLDASM` | 无 Mate 的对照样本（用于验证"完全没有 Mate 时 Transform2 是否生效"）|
| `phase1_build_two_gears.py` | 用现有零件建模 HTTP API 建两个齿轮零件（用户需手动 SaveAs 保存为 .SLDPRT）|
| `phase3_via_csharp_api.py` | 用装配体 API 端到端搭齿轮啮合组（新建装配体 + AddComponent × 2 + Coincident Mate + Gear Mate + Save）|

## 快速验证：从头搭一个齿轮啮合组

前置：SolidWorks 已启动、C# 服务运行中（`run_service.bat`）、`gear_small.SLDPRT` + `gear_big.SLDPRT` 已存在（否则用 phase1 重建）。

```bash
cd LLMPlanner
venv\Scripts\python.exe -X utf8 assembly_smoke\phase3_via_csharp_api.py
```

预期输出：
```
✅ POST /api/assembly/new           → 新建装配体
✅ POST /api/assembly/add-component → gear_small-1
✅ POST /api/assembly/add-component → gear_big-1
✅ POST /api/assembly/mate-coincident → Coincident Mate 完成
✅ POST /api/assembly/mate-gear     → Gear Mate 完成 (比 20:10)
✅ POST /api/assembly/save          → 装配体已保存
```

之后在 SolidWorks 里打开 `gear_pair_assembly_v3.SLDASM`，用**装配体 → 移动零部件**拖动小齿轮，大齿轮会按 2:1 反向联动旋转。

## ⚠️ 已知问题：编程驱动组件旋转

**装配体 API 全部功能都打通**，但**通过 API 编程驱动组件旋转（模拟"用户拖"）在 SolidWorks 2025 中文版上未成功**。

尝试过的方案（全部 API 返回 `success=true`，但组件 Transform 前后完全不变，视觉无变化）：

| 方案 | API 调用 | 结果 |
|---|---|---|
| SetTransformAndSolve3 | `IComponent2.SetTransformAndSolve3(newTransform, false)` | 返回 True，Transform 不变 |
| Transform2 直接赋值 | `component.Transform2 = newTransform` | 无异常，Transform 不变 |
| DragOperator 序列 | `AddComponent + BeginDrag + Drag(xform) + EndDrag` | 每步都返回 True，Transform 不变 |

对比：**手动在 SolidWorks 界面拖小齿轮 → 大齿轮联动正常**，说明装配体本身（含 Gear Mate）100% 正确，是**编程 API 层面的问题**。

**后续攻克方向**（未来的独立 issue）：
1. **Motion Study API**（`IModelDoc2.GetMotionStudyManager`）— SolidWorks 官方的运动算例，专门做齿轮动画。这是最有希望的路径。
2. **`SetPositions2` 或 `MoveOrCopyBodies2`** — 底层几何变换 API。
3. 咨询 Xarial.XCad 作者或 SW API 论坛，看是否有 SW2025 特定的组件旋转 API。

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
| POST `/api/save-part-as` | 零件另存为独立 .SLDPRT | ✅ |
