# Chapter1.ConnectToSw — SolidWorks HTTP API 服务

通过命令行（无需打开 Visual Studio）构建并运行连接 SolidWorks 的 C# HTTP API 服务。
服务启动后监听 `http://localhost:5100/`，外部程序（如 LLMPlanner）可通过 HTTP 请求远程控制 SolidWorks 自动化建模。

---

## 一、前置条件

1. **本机已安装 SolidWorks**（项目依赖其 COM 注册）。
2. **运行服务前必须先启动 SolidWorks**，否则服务会提示 “SolidWorks 未启动” 并退出。
3. **NuGet 包已还原**：包位于解决方案根目录的 `packages\` 文件夹下。本项目用传统 `packages.config` 模式，包齐全时无需额外 restore，直接 MSBuild 即可。

---

## 二、快速开始（推荐：用根目录的批处理脚本）

仓库根目录已提供两个脚本，封装好了 MSBuild 路径与项目路径：

```bat
cd c:\codes\sw\SolidWorksTutorial

REM 1. 构建（修改过 .cs 代码后执行；先关闭正在运行的服务窗口）
build_service.bat

REM 2. 启动服务（确保 SolidWorks 已打开）
run_service.bat
```

- `build_service.bat` — 自动定位 MSBuild、检测 exe 是否被占用、执行 Rebuild。
- `run_service.bat` — 检查 exe 是否存在、提醒 SolidWorks 是否启动、启动服务。

服务启动成功后会打印：

```
[OK] 已连接到 SolidWorks
[OK] HTTP API 服务已启动，监听: http://localhost:5100/
按 Ctrl+C 停止服务...
```

按 `Ctrl+C` 可停止服务。

---

## 三、手动命令行操作（不依赖脚本）

### 1. 构建

MSBuild **不在系统 PATH 中**，需使用完整路径。本机实际路径为
（注意 Visual Studio 的版本目录是 `18`，不是 `2022`）：

```bat
"C:\Program Files\Microsoft Visual Studio\18\Community\MSBuild\Current\Bin\MSBuild.exe" "c:\codes\sw\SolidWorksTutorial\Chapter1.ConnectToSw\Chapter1.ConnectToSw.csproj" /p:Configuration=Debug /t:Rebuild
```

> 若上述路径不存在，用以下命令查找 MSBuild：
> ```bat
> where /r "C:\Program Files\Microsoft Visual Studio" MSBuild.exe
> ```

构建成功输出：

```
Chapter1.ConnectToSw -> ...\bin\Debug\Chapter1.ConnectToSw.exe
```

### 2. 运行

```bat
c:\codes\sw\SolidWorksTutorial\Chapter1.ConnectToSw\bin\Debug\Chapter1.ConnectToSw.exe
```

### 3. 验证服务是否正常（另开一个终端）

```powershell
powershell -NoProfile -Command "Invoke-RestMethod -Uri 'http://localhost:5100/api/ping' -TimeoutSec 10"
```

正常返回：

```json
{ "success": true, "message": "SolidWorks API 服务运行中" }
```

> 注意：Windows 自带的 `curl` 在某些重定向环境下会报 `Input redirection is not supported`，
> 建议用上面的 PowerShell `Invoke-RestMethod` 来测试。

---

## 四、重要：权限匹配问题（COM 连接失败的常见根因）

### 现象

服务启动后日志报错并立即退出：

```
System.Exception: Cannot access SOLIDWORKS application at process <PID>
   在 Xarial.XCad.SolidWorks.SwApplicationFactory.FromProcess(...)
```

### 原因

Windows COM 跨进程访问要求**两端的「完整性级别 / 权限」必须一致**：

- 若 **SolidWorks 以管理员权限运行**，而**命令行是普通权限** → COM 访问被拒绝。
- 反之亦然。

> 这也是为什么以前在 Visual Studio 里能跑通：当 VS 以管理员启动时，它运行的服务进程也是管理员，
> 恰好和管理员权限的 SolidWorks 匹配。

### 排查：SolidWorks 是不是被偷偷设成了「以管理员运行」

即使你只是双击图标打开，SolidWorks 也可能因兼容性标志 `RUNASADMIN` 而**始终以管理员启动**。
检查注册表（普通权限即可读）：

```powershell
powershell -NoProfile -Command "$k='HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'; if(Test-Path $k){ (Get-ItemProperty $k).PSObject.Properties | Where-Object { $_.Name -like '*SLDWORKS*' } | ForEach-Object { $_.Name + ' = ' + $_.Value } }"
```

若输出中含 `RUNASADMIN`，例如：

```
C:\...\SLDWORKS.exe = ~ RUNASADMIN WIN8RTM
```

说明 SolidWorks 被强制以管理员启动。

### 解决方法（任选其一）

**方法 A：让 SolidWorks 以普通权限运行（推荐，本项目即采用此方案）**

移除该 `RUNASADMIN` 兼容性标志（`HKCU` 下，普通权限即可修改）：

```powershell
powershell -NoProfile -Command "$k='HKCU:\Software\Microsoft\Windows NT\CurrentVersion\AppCompatFlags\Layers'; $name='C:\Program Files\softwares\solidworks2025\Corp\SOLIDWORKS\SLDWORKS.exe'; Remove-ItemProperty -Path $k -Name $name; '已移除 RUNASADMIN，SLDWORKS 将以普通权限启动'"
```

> 注意：把 `$name` 换成你本机实际的 `SLDWORKS.exe` 完整路径（可在上一步排查命令的输出里看到）。

移除后**重新双击打开 SolidWorks**（这次为普通权限），再用普通命令行跑服务即可连上。

**方法 B：让命令行以管理员权限运行**

保持 SolidWorks 管理员运行，改为**以管理员身份打开命令行**再跑服务：

1. `Win` 键 → 输入 `cmd` → 右键「以管理员身份运行」（UAC 弹窗点「是」）。
2. 在管理员终端执行 `run_service.bat`。

> 提权必须由你在本机交互式点击 UAC 确认，无法通过 PIN/密码由程序自动完成。

**核心原则：SolidWorks 与连接它的程序，必须处于同一权限级别（要么都普通，要么都管理员）。**

---

## 五、常见问题

| 问题 | 原因 / 解决 |
|---|---|
| 构建报「exe 被占用 / 无法复制到输出目录」 | 服务正在运行，exe 被锁定。**先关闭运行中的服务窗口**再构建。 |
| `Cannot access SOLIDWORKS application at process` | 权限不匹配，见第四节。 |
| `SolidWorks 未启动` | 运行服务前先打开 SolidWorks。 |
| `无法启动 HTTP 监听` | 端口被占用或权限不足；可按提示执行 `netsh http add urlacl url=http://localhost:5100/ user=Everyone`。 |
| `curl` 报 `Input redirection is not supported` | 改用 PowerShell `Invoke-RestMethod` 测试。 |

---

## 六、调试技巧：把服务输出重定向到日志

服务用 `start /min` 后台启动时看不到控制台输出，可重定向到日志文件排查启动失败原因：

```bat
cd c:\codes\sw\SolidWorksTutorial\Chapter1.ConnectToSw\bin\Debug
start "SwHttpApi" /min cmd /c "Chapter1.ConnectToSw.exe > service_log.txt 2>&1"
```

随后查看：

```bat
type c:\codes\sw\SolidWorksTutorial\Chapter1.ConnectToSw\bin\Debug\service_log.txt
```

---

## 七、HTTP API 端点速览

服务监听 `http://localhost:5100/`，所有端点返回 `{ "success": bool, "message": "..." }` JSON。

| 方法 | 端点 | 说明 |
|---|---|---|
| GET  | `/api/ping` | 健康检查 |
| POST | `/api/new-part` | 新建零件文档 |
| POST | `/api/select-plane` | 选基准面并进入草图（0=前视, 1=上视, 2=右视） |
| POST | `/api/sketch/line` | 画直线（米） |
| POST | `/api/sketch/rectangle` | 画矩形（米） |
| POST | `/api/sketch/circle` | 画圆（米） |
| POST | `/api/sketch/centerline` | 画中心线/构造线（旋转轴） |
| POST | `/api/sketch/arc` | 画圆弧 |
| POST | `/api/sketch/ellipse` | 画椭圆 |
| POST | `/api/feature/extrusion` | 拉伸当前草图 |
| POST | `/api/feature/revolve` | 旋转凸台/切除 |
| POST | `/api/feature/cut-extrusion` | 切除拉伸 |
| POST | `/api/close-sketch` | 退出草图编辑 |
| POST | `/api/create-box` | 组合操作：创建长方体 |
| GET  | `/api/feature-tree` | 获取特征树 |
| GET  | `/api/body-bounding-box` | 获取实体包围盒 |
| POST | `/api/select-face-and-sketch` | 射线拾取面并进入草图 |
| POST | `/api/modify-dimension` | 修改已有特征的尺寸值 |
| POST | `/api/feature/fillet` | 圆角特征 |
| POST | `/api/feature/chamfer` | 倒角特征 |

> 所有长度单位均为**米**（如 `0.01` 表示 10mm）。
