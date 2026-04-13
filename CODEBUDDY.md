# CODEBUDDY.md This file provides guidance to CodeBuddy when working with code in this repository.

## 项目简介

SolidWorks 二次开发项目，使用 C# 通过 Xarial.XCad 封装层和 SolidWorks COM Interop 与 SolidWorks 进行交互。Chapter1 已改造为 HTTP API 服务，外部程序（Python/LLM Agent）可通过 HTTP 请求远程控制 SolidWorks 自动化建模。

## 构建与运行

### 前置条件

本机必须已安装 SolidWorks（项目依赖其 COM 注册），且运行示例前需先启动 SolidWorks 进程。

### NuGet 包还原

项目使用传统 `packages.config` 模式，NuGet 包存放在解决方案根目录的 `packages/` 文件夹中。还原命令：

```
nuget restore SolidWorksTutorial.slnx
```

### 构建

```
msbuild SolidWorksTutorial.slnx /p:Configuration=Debug
```

或在 Visual Studio 中直接打开 `SolidWorksTutorial.slnx` 构建。

### 运行

构建后直接运行对应 Chapter 的 exe，例如：

```
.\Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe
```

运行前确保 SolidWorks 已启动。Chapter1 启动后会在 `http://localhost:5100/` 监听 HTTP API 请求，Ctrl+C 停止服务。

## 架构

### 解决方案结构

解决方案（`SolidWorksTutorial.slnx`，新版 slnx 格式）按教程章节组织，每章一个独立的 .NET Framework 4.7.2 控制台项目（传统 csproj，非 SDK 风格）。当前包含：

- **Chapter1.ConnectToSw** — HTTP API 服务，接收网络请求控制 SolidWorks 建模

新增章节时，在解决方案根目录创建 `ChapterN.xxx/` 项目，并添加到 slnx 中。

### Chapter1 文件结构

- `Program.cs` — HTTP 服务入口：Main 启动流程 + HttpListener 监听循环 + 路由分发 + 各端点 handler
- `SwCommands.cs` — SolidWorks 操作封装类：持有 ISldWorks 引用和当前文档状态，提供原子操作（NewPart、SelectPlaneAndStartSketch、SketchLine/Rectangle/Circle/CenterLine/Arc、Extrusion、Revolve、CutExtrusion、Fillet、Chamfer、CloseSketch）、查询操作（GetFeatureTree、GetBodyBoundingBox）、面选择操作（SelectFaceByRayAndStartSketch）和组合操作（CreateBox）

### HTTP API 端点

服务监听 `http://localhost:5100/`，所有端点返回 `{ "success": bool, "message": "..." }` JSON 格式。

| 方法 | 端点 | 请求参数 | 说明 |
|---|---|---|---|
| GET | `/api/ping` | 无 | 健康检查 |
| POST | `/api/new-part` | 无 | 新建零件文档 |
| POST | `/api/select-plane` | `{ "index": 0 }` | 选基准面并进入草图（0=前视, 1=上视, 2=右视） |
| POST | `/api/sketch/line` | `{ "x1", "y1", "x2", "y2" }` | 画直线（米） |
| POST | `/api/sketch/rectangle` | `{ "x1", "y1", "x2", "y2" }` | 画矩形（米） |
| POST | `/api/sketch/circle` | `{ "cx", "cy", "radius" }` | 画圆（米） |
| POST | `/api/sketch/centerline` | `{ "x1", "y1", "x2", "y2" }` | 画中心线/构造线（米），用于旋转轴 |
| POST | `/api/sketch/arc` | `{ "cx", "cy", "startX", "startY", "endX", "endY", "direction" }` | 画圆弧（米），direction: 1=顺时针, -1=逆时针 |
| POST | `/api/feature/extrusion` | `{ "depth" }` | 拉伸当前草图（米） |
| POST | `/api/feature/revolve` | `{ "angle"(可选,默认360), "isCut"(可选), "reverseDir"(可选) }` | 旋转凸台/切除（度），草图需含中心线 |
| POST | `/api/feature/cut-extrusion` | `{ "depth", "endCondition"(可选), "flipDirection"(可选) }` | 切除拉伸（米），endCondition: blind/through_all/mid_plane/up_to_next |
| POST | `/api/close-sketch` | 无 | 退出草图编辑 |
| POST | `/api/create-box` | `{ "length", "width", "height" }` | 组合操作：创建长方体（米） |
| GET | `/api/feature-tree` | 无 | 获取特征树（返回 features 数组） |
| GET | `/api/body-bounding-box` | 无 | 获取实体包围盒（返回 boundingBox 对象） |
| POST | `/api/select-face-and-sketch` | `{ "rx", "ry", "rz", "dx", "dy", "dz" }` | 射线拾取面并进入草图编辑 |
| POST | `/api/modify-dimension` | `{ "featureName", "dimensionName"(可选), "newValue" }` | 修改已有特征的尺寸值（米） |
| POST | `/api/feature/fillet` | `{ "radius", "edgeSelection"(可选), "rx","ry","rz","dx","dy","dz"(射线模式) }` | 圆角特征（米） |
| POST | `/api/feature/chamfer` | `{ "type"(可选), "distance1", "distance2"(可选), "angle"(可选), "edgeSelection"(可选), 射线参数 }` | 倒角特征（米） |

### 核心依赖

| 包 | 版本 | 用途 |
|---|---|---|
| Xarial.XCad | 0.8.3 | CAD 应用抽象层 |
| Xarial.XCad.SolidWorks | 0.8.3 | SolidWorks 特定实现，含 COM Interop 程序集 |
| Xarial.XCad.Toolkit | 0.8.3 | XCad 工具集 |

SolidWorks COM Interop 程序集（`SolidWorks.Interop.sldworks`、`SolidWorks.Interop.swconst`、`SolidWorks.Interop.swpublished`）由 XCad.SolidWorks 包提供，位于 `packages/Xarial.XCad.SolidWorks.0.8.3/lib/net461/`。

### SolidWorks API 交互模式

Chapter1 展示的典型调用链路是此项目的核心编程模式：

1. **进程连接**：通过 `SwApplicationFactory.FromProcess()` 连接到已运行的 SolidWorks 实例，获取 `ISwApplication` 对象，再通过 `.Sw` 属性访问底层 `ISldWorks` COM 接口。

2. **文档操作**：通过 `ISldWorks.INewDocument2()` 创建文档，使用 `swUserPreferenceStringValue_e` 枚举查询默认模板路径。文档对象 `IModelDoc2` 是后续所有操作的核心句柄。

3. **特征树遍历**：通过 `IModelDoc2.FirstFeature()` 获取首个特征，然后 `IFeature.GetNextFeature()` 链式遍历，用 `GetTypeName2()` 判断特征类型（如 `"RefPlane"` 为基准面）。

4. **草图与特征创建**：先选中基准面（`IFeature.Select2()`），通过 `SketchManager.InsertSketch(true)` 进入草图编辑，使用 `CreateLine`/`CreateCenterRectangle` 等方法绘制几何，再通过 `FeatureManager.FeatureExtrusion3()` 执行拉伸等建模操作。

5. **选择管理**：`ISelectionManager` 提供 `GetSelectedObjectCount2`、`GetSelectedObjectType3`、`GetSelectedObject6`、`GetSelectionPoint2` 等方法查询当前选择的实体信息。

6. **自定义属性**：`IModelDocExtension.CustomPropertyManager[""]` 访问通用属性管理器，支持 `GetAll3` 批量读取、`Add3` 添加/覆盖、`Delete` 删除操作。

7. **方程式管理**：`IModelDoc2.GetEquationMgr()` 获取方程式管理器，遍历和管理尺寸方程式。

### 关键类型映射

- `ISwApplication` (XCad) → `.Sw` → `ISldWorks` (COM)
- `ISldWorks` → `.IActiveDoc2` / `.INewDocument2()` → `IModelDoc2`
- `IModelDoc2` → `.SketchManager` / `.FeatureManager` / `.ISelectionManager` / `.Extension.CustomPropertyManager`
- 常量枚举在 `SolidWorks.Interop.swconst` 命名空间下，如 `swEndConditions_e`、`swDocumentTypes_e`、`swStartConditions_e`

## 项目约定

- **Git 跟踪策略**：`.gitignore` 配置为仅跟踪 `*.cs` 文件，所有二进制产物、NuGet 包、配置文件均不纳入版本控制。新增非 .cs 文件如需跟踪，须修改 `.gitignore`。
- **命名空间**：每个 Chapter 项目使用 `ChapterN.项目名` 作为根命名空间（如 `Chapter1.ConnectToSw`）。
- **代码语言**：注释使用中文，面向中文开发者的教程项目。
- **SolidWorks 坐标单位**：API 中所有长度单位为**米**（如 `0.01` 表示 10mm）。
