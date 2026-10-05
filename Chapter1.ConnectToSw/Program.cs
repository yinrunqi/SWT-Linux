using SolidWorks.Interop.sldworks;
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Text;
using System.Web.Script.Serialization;
using Xarial.XCad.SolidWorks;

namespace Chapter1.ConnectToSw
{
    internal class Program
    {
        private static SwCommands _commands;
        private static readonly JavaScriptSerializer _json = new JavaScriptSerializer();
        private static bool _running = true;

        private const string ListenUrl = "http://localhost:5100/";

        [STAThread]
        static void Main(string[] args)
        {
            Console.WriteLine("=== SolidWorks HTTP API 服务 ===");
            Console.WriteLine();

            // 1. 连接 SolidWorks
            var swProcess = Process.GetProcessesByName("SLDWORKS");
            if (!swProcess.Any())
            {
                Console.WriteLine("[错误] SolidWorks 未启动，请先打开 SolidWorks 后再运行本程序。");
                Console.WriteLine("按任意键退出...");
                Console.ReadKey();
                return;
            }

            var swApp = SwApplicationFactory.FromProcess(swProcess.First());
            Console.WriteLine("[OK] 已连接到 SolidWorks");

            // 2. 初始化 SwCommands
            try
            {
                _commands = new SwCommands(swApp.Sw);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 初始化 SwCommands 失败: {ex.Message}");
                Console.WriteLine("按任意键退出...");
                Console.ReadKey();
                return;
            }

            // 3. 启动 HTTP 服务
            var listener = new HttpListener();
            listener.Prefixes.Add(ListenUrl);

            try
            {
                listener.Start();
            }
            catch (HttpListenerException ex)
            {
                Console.WriteLine($"[错误] 无法启动 HTTP 监听: {ex.Message}");
                Console.WriteLine($"提示: 可能需要以管理员身份运行，或执行: netsh http add urlacl url={ListenUrl} user=Everyone");
                Console.WriteLine("按任意键退出...");
                Console.ReadKey();
                return;
            }

            Console.WriteLine($"[OK] HTTP API 服务已启动，监听: {ListenUrl}");
            Console.WriteLine();
            Console.WriteLine("可用端点:");
            Console.WriteLine("  GET  /api/ping                    - 健康检查");
            Console.WriteLine("  POST /api/close-all-documents     - 关闭全部测试文档（不保存）");
            Console.WriteLine("  POST /api/close-inactive-documents - 保留活动文档并关闭其余窗口");
            Console.WriteLine("  POST /api/new-part                - 新建零件文档");
            Console.WriteLine("  POST /api/select-plane            - 选择基准面并进入草图");
            Console.WriteLine("  POST /api/sketch/line             - 画直线");
            Console.WriteLine("  POST /api/sketch/polyline         - 批量画连续折线/闭合轮廓");
            Console.WriteLine("  POST /api/sketch/rectangle        - 画矩形");
            Console.WriteLine("  POST /api/sketch/circle           - 画圆");
            Console.WriteLine("  POST /api/sketch/centerline       - 画中心线（旋转轴）");
            Console.WriteLine("  POST /api/sketch/arc              - 画圆弧");
            Console.WriteLine("  POST /api/sketch/ellipse           - 画椭圆");
            Console.WriteLine("  POST /api/sketch/polygon           - 画正 n 边形（n=3~64）");
            Console.WriteLine("  POST /api/sketch/slot              - 画直线型长圆孔/腰形孔");
            Console.WriteLine("  POST /api/feature/circular-pattern - 圆周阵列（对最后一个特征均布 N 份）");
            Console.WriteLine("  POST /api/feature/linear-pattern   - 线性阵列（1D/2D，最后一个特征沿轴向复制 N₁×N₂ 份）");
            Console.WriteLine("  POST /api/feature/extrusion       - 拉伸");
            Console.WriteLine("  POST /api/feature/revolve         - 旋转凸台/切除");
            Console.WriteLine("  POST /api/feature/cut-extrusion   - 切除拉伸（挖孔/开槽）");
            Console.WriteLine("  POST /api/close-sketch            - 退出草图");
            Console.WriteLine("  POST /api/create-box              - 组合: 创建长方体");
            Console.WriteLine("  --- 查询 & 编辑 ---");
            Console.WriteLine("  GET  /api/feature-tree            - 获取特征树（含尺寸）");
            Console.WriteLine("  GET  /api/body-bounding-box       - 获取实体包围盒");
            Console.WriteLine("  POST /api/select-face-and-sketch  - 射线选面并进入草图");
            Console.WriteLine("  POST /api/select-observed-face-and-sketch - 按 observation 面索引进入草图");
            Console.WriteLine("  POST /api/modify-dimension        - 修改特征尺寸值");
            Console.WriteLine("  POST /api/feature/fillet           - 圆角");
            Console.WriteLine("  POST /api/feature/chamfer          - 倒角");
            Console.WriteLine();
            Console.WriteLine("按 Ctrl+C 停止服务...");
            Console.WriteLine();

            // Ctrl+C 优雅退出
            Console.CancelKeyPress += (s, e) =>
            {
                e.Cancel = true;
                _running = false;
                listener.Stop();
                Console.WriteLine("\n[停止] 正在关闭服务...");
            };

            // 4. 主循环：同步处理请求（保证 STA 线程）
            while (_running)
            {
                HttpListenerContext context;
                try
                {
                    context = listener.GetContext();
                }
                catch (HttpListenerException)
                {
                    // listener 被停止时会抛出异常
                    break;
                }

                try
                {
                    HandleRequest(context);
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[异常] 处理请求时发生未捕获的错误: {ex.Message}");
                    try
                    {
                        SendJson(context.Response, 500, new { success = false, message = $"服务器内部错误: {ex.Message}" });
                    }
                    catch
                    {
                        // 响应可能已部分发送（客户端超时断开），中止连接
                        try { context.Response.Abort(); } catch { }
                    }
                }
            }

            Console.WriteLine("[已停止] 服务已关闭。");
        }

        #region 请求路由

        private static void HandleRequest(HttpListenerContext context)
        {
            var req = context.Request;
            var resp = context.Response;

            // CORS 头
            resp.Headers.Add("Access-Control-Allow-Origin", "*");
            resp.Headers.Add("Access-Control-Allow-Methods", "GET, POST, OPTIONS");
            resp.Headers.Add("Access-Control-Allow-Headers", "Content-Type");

            // 处理 OPTIONS 预检请求
            if (req.HttpMethod == "OPTIONS")
            {
                resp.StatusCode = 204;
                resp.Close();
                return;
            }

            var path = req.Url.AbsolutePath.TrimEnd('/').ToLower();
            var method = req.HttpMethod.ToUpper();

            Console.WriteLine($"[请求] {method} {path}");

            switch (path)
            {
                case "/api/ping":
                    HandlePing(resp);
                    break;

                case "/api/close-all-documents":
                    RequirePost(method, resp, () => HandleCloseAllDocuments(resp));
                    break;
                case "/api/close-inactive-documents":
                    RequirePost(method, resp, () => HandleCloseInactiveDocuments(resp));
                    break;

                case "/api/new-part":
                    RequirePost(method, resp, () => HandleNewPart(resp));
                    break;

                case "/api/open-part":
                    RequirePost(method, resp, () => HandleOpenPart(req, resp));
                    break;

                case "/api/delete-feature":
                    RequirePost(method, resp, () => HandleDeleteFeature(req, resp));
                    break;

                case "/api/export-step":
                    RequirePost(method, resp, () => HandleExportStep(req, resp));
                    break;

                case "/api/sketch/profile":
                    RequirePost(method, resp, () => HandleSketchProfile(req, resp));
                    break;

                case "/api/ref-plane/offset":
                    RequirePost(method, resp, () => HandleCreateOffsetPlane(req, resp));
                    break;

                case "/api/ref-axis/planes":
                    RequirePost(method, resp, () => HandleCreateAxisFromPlanes(req, resp));
                    break;

                case "/api/select-ref-plane-and-sketch":
                    RequirePost(method, resp, () => HandleSelectRefPlaneAndSketch(req, resp));
                    break;

                case "/api/feature/mirror":
                    RequirePost(method, resp, () => HandleMirror(req, resp));
                    break;
                case "/api/feature/until":
                    RequirePost(method, resp, () => HandleUntil(req, resp));
                    break;
                case "/api/feature/rib":
                    RequirePost(method, resp, () => HandleRib(req, resp));
                    break;
                case "/api/feature/sweep":
                    RequirePost(method, resp, () => HandleSweep(req, resp));
                    break;
                case "/api/feature/loft":
                    RequirePost(method, resp, () => HandleLoft(req, resp));
                    break;
                case "/api/feature/shell":
                    RequirePost(method, resp, () => HandleShell(req, resp));
                    break;
                case "/api/feature/draft":
                    RequirePost(method, resp, () => HandleDraft(req, resp));
                    break;
                case "/api/feature/sheet-metal-base":
                    RequirePost(method, resp, () => HandleSheetMetalBase(req, resp));
                    break;
                case "/api/feature/cosmetic-thread":
                    RequirePost(method, resp, () => HandleCosmeticThread(req, resp));
                    break;
                case "/api/close-sketch-named":
                    RequirePost(method, resp, () => HandleCloseSketchNamed(resp));
                    break;

                case "/api/select-plane":
                    RequirePost(method, resp, () => HandleSelectPlane(req, resp));
                    break;

                case "/api/sketch/line":
                    RequirePost(method, resp, () => HandleSketchLine(req, resp));
                    break;

                case "/api/sketch/polyline":
                    RequirePost(method, resp, () => HandleSketchPolyline(req, resp));
                    break;

                case "/api/sketch/rectangle":
                    RequirePost(method, resp, () => HandleSketchRectangle(req, resp));
                    break;

                case "/api/sketch/circle":
                    RequirePost(method, resp, () => HandleSketchCircle(req, resp));
                    break;

                case "/api/sketch/circles":
                    RequirePost(method, resp, () => HandleSketchCircles(req, resp));
                    break;

                case "/api/sketch/centerline":
                    RequirePost(method, resp, () => HandleSketchCenterLine(req, resp));
                    break;

                case "/api/sketch/arc":
                    RequirePost(method, resp, () => HandleSketchArc(req, resp));
                    break;

                case "/api/sketch/ellipse":
                    RequirePost(method, resp, () => HandleSketchEllipse(req, resp));
                    break;

                case "/api/sketch/polygon":
                    RequirePost(method, resp, () => HandleSketchPolygon(req, resp));
                    break;

                case "/api/sketch/slot":
                    RequirePost(method, resp, () => HandleSketchSlot(req, resp));
                    break;

                case "/api/feature/circular-pattern":
                    RequirePost(method, resp, () => HandleCircularPattern(req, resp));
                    break;

                case "/api/feature/linear-pattern":
                    RequirePost(method, resp, () => HandleLinearPattern(req, resp));
                    break;

                case "/api/feature/extrusion":
                    RequirePost(method, resp, () => HandleExtrusion(req, resp));
                    break;

                case "/api/feature/revolve":
                    RequirePost(method, resp, () => HandleRevolve(req, resp));
                    break;

                case "/api/feature/cut-extrusion":
                    RequirePost(method, resp, () => HandleCutExtrusion(req, resp));
                    break;

                case "/api/close-sketch":
                    RequirePost(method, resp, () => HandleCloseSketch(resp));
                    break;

                case "/api/create-box":
                    RequirePost(method, resp, () => HandleCreateBox(req, resp));
                    break;

                case "/api/feature-tree":
                    HandleFeatureTree(resp);
                    break;

                case "/api/model-observation":
                    HandleModelObservation(req, resp);
                    break;

                case "/api/sketch-stats":
                    HandleSketchStats(resp);
                    break;

                case "/api/body-bounding-box":
                    HandleBodyBoundingBox(resp);
                    break;

                case "/api/body-volume":
                    HandleBodyVolume(resp);
                    break;

                case "/api/select-face-and-sketch":
                    RequirePost(method, resp, () => HandleSelectFaceAndSketch(req, resp));
                    break;

                case "/api/select-observed-face-and-sketch":
                    RequirePost(
                        method,
                        resp,
                        () => HandleSelectObservedFaceAndSketch(req, resp));
                    break;

                case "/api/modify-dimension":
                    RequirePost(method, resp, () => HandleModifyDimension(req, resp));
                    break;

                case "/api/feature/fillet":
                    RequirePost(method, resp, () => HandleFillet(req, resp));
                    break;

                case "/api/feature/chamfer":
                    RequirePost(method, resp, () => HandleChamfer(req, resp));
                    break;

                // ========== 装配体端点（Stage B: 装配体 API 扩展）==========
                case "/api/assembly/new":
                    RequirePost(method, resp, () => HandleAssemblyNew(req, resp));
                    break;
                case "/api/assembly/add-component":
                    RequirePost(method, resp, () => HandleAssemblyAddComponent(req, resp));
                    break;
                case "/api/assembly/mate-coincident":
                    RequirePost(method, resp, () => HandleAssemblyMateCoincident(req, resp));
                    break;
                case "/api/assembly/mate-concentric":
                    RequirePost(method, resp, () => HandleAssemblyMateConcentric(req, resp));
                    break;
                case "/api/assembly/mate-gear":
                    RequirePost(method, resp, () => HandleAssemblyMateGear(req, resp));
                    break;
                case "/api/assembly/save":
                    RequirePost(method, resp, () => HandleAssemblySave(resp));
                    break;
                case "/api/assembly/rebuild":
                    RequirePost(method, resp, () => HandleAssemblyRebuild(resp));
                    break;
                case "/api/assembly/check-interference":
                    RequirePost(method, resp, () => HandleAssemblyInterference(resp));
                    break;
                case "/api/assembly/open":
                    RequirePost(method, resp, () => HandleAssemblyOpen(req, resp));
                    break;
                case "/api/assembly/unfix-component":
                    RequirePost(method, resp, () => HandleUnfixComponent(req, resp));
                    break;
                case "/api/assembly/fix-component":
                    RequirePost(method, resp, () => HandleFixComponent(req, resp));
                    break;
                case "/api/assembly/observation":
                    HandleAssemblyObservation(resp);
                    break;
                case "/api/assembly/rotate-component":
                    RequirePost(method, resp, () => HandleRotateComponent(req, resp));
                    break;
                case "/api/assembly/rotate-component-by-drag":
                    RequirePost(method, resp, () => HandleRotateComponentByDrag(req, resp));
                    break;
                case "/api/assembly/animate-rotate-component":
                    RequirePost(method, resp, () => HandleAnimateRotateComponent(req, resp));
                    break;
                case "/api/assembly/get-component-transform":
                    RequirePost(method, resp, () => HandleGetComponentTransform(req, resp));
                    break;
                case "/api/assembly/set-component-pose":
                    RequirePost(method, resp, () => HandleSetComponentPose(req, resp));
                    break;
                case "/api/assembly/mechanism/crank-slider-pose":
                    RequirePost(method, resp, () => HandleCrankSliderPose(req, resp));
                    break;
                case "/api/assembly/mechanism/synchronized-rotary-pose":
                    RequirePost(method, resp, () => HandleSynchronizedRotaryPose(req, resp));
                    break;
                case "/api/assembly/mechanism/four-bar-pose":
                    RequirePost(method, resp, () => HandleFourBarPose(req, resp));
                    break;
                case "/api/assembly/mechanism/cam-follower-pose":
                    RequirePost(method, resp, () => HandleCamFollowerPose(req, resp));
                    break;
                case "/api/assembly/mechanism/planar-chain-pose":
                    RequirePost(method, resp, () => HandlePlanarChainPose(req, resp));
                    break;
                case "/api/assembly/mechanism/radial-deployable-pose":
                    RequirePost(method, resp, () => HandleRadialDeployablePose(req, resp));
                    break;
                case "/api/assembly/mechanism/planetary-train-pose":
                    RequirePost(method, resp, () => HandlePlanetaryTrainPose(req, resp));
                    break;
                case "/api/assembly/mechanism/radial-sliders-pose":
                    RequirePost(method, resp, () => HandleRadialSlidersPose(req, resp));
                    break;
                case "/api/assembly/mechanism/indexed-rotary-pose":
                    RequirePost(method, resp, () => HandleIndexedRotaryPose(req, resp));
                    break;
                case "/api/assembly/start-spin":
                    RequirePost(method, resp, () => HandleStartSpin(req, resp));
                    break;
                case "/api/assembly/start-spin-gear-pair":
                    RequirePost(method, resp, () => HandleStartSpinGearPair(req, resp));
                    break;
                case "/api/assembly/stop-spin":
                    RequirePost(method, resp, () => HandleStopSpin(resp));
                    break;
                case "/api/assembly/motion/probe":
                    RequirePost(method, resp, () => HandleMotionStudyProbe(resp));
                    break;
                case "/api/assembly/motion/create-gear-motor":
                    RequirePost(method, resp, () => HandleCreateGearMotor(req, resp));
                    break;
                case "/api/assembly/motion/play":
                    RequirePost(method, resp, () => HandlePlayMotionStudy(resp));
                    break;
                case "/api/assembly/motion/set-time":
                    RequirePost(method, resp, () => HandleSetMotionStudyTime(req, resp));
                    break;
                case "/api/assembly/motion/stop":
                    RequirePost(method, resp, () => HandleStopMotionStudy(resp));
                    break;
                case "/api/assembly/motion/status":
                    HandleMotionStudyStatus(resp);
                    break;
                case "/api/save-part-as":
                    RequirePost(method, resp, () => HandleSavePartAs(req, resp));
                    break;

                default:
                    if (path.StartsWith("/api/asmwf/"))
                    {
                        RequirePost(method, resp, () => HandleAssemblyWorkflow(path.Substring("/api/asmwf/".Length), req, resp));
                        break;
                    }
                    SendJson(resp, 404, new { success = false, message = $"未知端点: {path}" });
                    break;
            }
        }

        private static void HandleAssemblyWorkflow(string op, HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.AssemblyWorkflowCall(op, ReadJsonBody(req));
                SendJson(resp, 200, new { success = true, message = op, result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] asmwf/{op} 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void RequirePost(string method, HttpListenerResponse resp, Action handler)
        {
            if (method != "POST")
            {
                SendJson(resp, 405, new { success = false, message = "此端点仅接受 POST 请求" });
                return;
            }
            handler();
        }

        #endregion

        #region 端点处理

        private static void HandlePing(HttpListenerResponse resp)
        {
            SendJson(resp, 200, new { success = true, message = "SolidWorks API 服务运行中" });
        }

        private static void HandleCloseAllDocuments(HttpListenerResponse resp)
        {
            try
            {
                var closed = _commands.CloseAllDocuments();
                SendJson(resp, 200, new
                {
                    success = true,
                    closedCount = closed.Count,
                    closedDocuments = closed,
                    message = $"已关闭 {closed.Count} 个测试文档"
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 关闭文档失败: {ex}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"关闭文档失败: {ex.Message}"
                });
            }
        }

        private static void HandleCloseInactiveDocuments(HttpListenerResponse resp)
        {
            try
            {
                var closed = _commands.CloseInactiveDocuments();
                SendJson(resp, 200, new
                {
                    success = true,
                    closedCount = closed.Count,
                    closedDocuments = closed,
                    message = $"已关闭 {closed.Count} 个非活动文档"
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 关闭非活动文档失败: {ex}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"关闭非活动文档失败: {ex.Message}"
                });
            }
        }

        private static void HandleNewPart(HttpListenerResponse resp)
        {
            try
            {
                _commands.NewPart();
                SendJson(resp, 200, new { success = true, message = "新建零件文档成功" });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 新建零件失败: {ex.Message}");
                Console.WriteLine($"[错误] 异常类型: {ex.GetType().FullName}");
                Console.WriteLine($"[错误] 堆栈: {ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"新建零件失败: {ex.Message}" });
            }
        }

        private static void HandleSelectPlane(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                int index = GetInt(body, "index", 0);

                string sketchName = _commands.SelectPlaneAndStartSketch(index);
                SendJson(resp, 200, new {
                    success = true,
                    message = $"已选择基准面 [{index}] 并进入草图编辑",
                    sketchName = sketchName,
                    faceKey = $"PLANE:{index}",
                });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"选择基准面失败: {ex.Message}" });
            }
        }

        private static void HandleSketchLine(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double x1 = GetDouble(body, "x1");
                double y1 = GetDouble(body, "y1");
                double x2 = GetDouble(body, "x2");
                double y2 = GetDouble(body, "y2");
                double z1 = GetDoubleOrDefault(body, "z1", 0);
                double z2 = GetDoubleOrDefault(body, "z2", 0);

                _commands.SketchLine(x1, y1, x2, y2, z1, z2);
                SendJson(resp, 200, new { success = true, message = $"绘制直线: ({x1}, {y1}, {z1}) -> ({x2}, {y2}, {z2})" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制直线失败: {ex.Message}" });
            }
        }

        private static void HandleSketchPolyline(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                if (!body.ContainsKey("points"))
                    throw new KeyNotFoundException("缺少必填字段: points");
                var raw = body["points"] as System.Collections.IEnumerable;
                if (raw == null)
                    throw new ArgumentException("points 必须是数组");
                var points = new List<Dictionary<string, object>>();
                foreach (object item in raw)
                {
                    var point = item as Dictionary<string, object>;
                    if (point == null)
                        throw new ArgumentException("points 中每项必须是对象");
                    points.Add(point);
                }
                bool closed = !body.ContainsKey("closed")
                    || Convert.ToBoolean(body["closed"]);
                var result = _commands.SketchPolyline(points, closed);
                result["success"] = true;
                result["message"] =
                    $"批量绘制 {points.Count} 点折线，closed={closed}";
                SendJson(resp, 200, result);
            }
            catch (Exception ex)
            {
                SendJson(resp, 400, new
                {
                    success = false,
                    message = ex.Message
                });
            }
        }

        private static void HandleSketchRectangle(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double x1 = GetDouble(body, "x1");
                double y1 = GetDouble(body, "y1");
                double x2 = GetDouble(body, "x2");
                double y2 = GetDouble(body, "y2");
                double z1 = GetDoubleOrDefault(body, "z1", 0);
                double z2 = GetDoubleOrDefault(body, "z2", 0);

                _commands.SketchRectangle(x1, y1, x2, y2, z1, z2);
                SendJson(resp, 200, new { success = true, message = $"绘制矩形: ({x1}, {y1}) -> ({x2}, {y2})" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制矩形失败: {ex.Message}" });
            }
        }

        private static void HandleSketchCircle(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double cx = GetDouble(body, "cx");
                double cy = GetDouble(body, "cy");
                double radius = GetDouble(body, "radius");
                double cz = GetDoubleOrDefault(body, "cz", 0);

                if (radius <= 0)
                    throw new ArgumentOutOfRangeException("radius", "半径必须为正数");

                _commands.SketchCircle(cx, cy, radius, cz, GetBoolOrDefault(body, "noInference", false));
                SendJson(resp, 200, new { success = true, message = $"绘制圆: 圆心({cx}, {cy}, {cz}), 半径 {radius}" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制圆失败: {ex.Message}" });
            }
        }

        private static void HandleSketchCircles(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                if (!body.ContainsKey("circles"))
                    throw new KeyNotFoundException("缺少必填字段: circles");
                var raw = body["circles"] as System.Collections.IEnumerable;
                if (raw == null)
                    throw new ArgumentException("circles 必须是数组");
                var circles = new List<Dictionary<string, object>>();
                foreach (object item in raw)
                {
                    var circle = item as Dictionary<string, object>;
                    if (circle == null)
                        throw new ArgumentException("circles 中每项必须是对象");
                    circles.Add(circle);
                }
                bool noInference = body.ContainsKey("noInference") && Convert.ToBoolean(body["noInference"]);
                var result = _commands.SketchCircles(circles, noInference);
                result["success"] = true;
                result["message"] = $"批量绘制 {circles.Count} 个圆";
                SendJson(resp, 200, result);
            }
            catch (Exception ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
        }

        private static void HandleExtrusion(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double depth = GetDouble(body, "depth");
                bool flipDirection = false;
                if (body.ContainsKey("flipDirection"))
                    flipDirection = Convert.ToBoolean(body["flipDirection"]);
                bool midPlane = false;
                if (body.ContainsKey("midPlane"))
                    midPlane = Convert.ToBoolean(body["midPlane"]);
                // 新增：拔模拉伸参数（键帽/花盆/漏斗等梯形零件用）
                double draftAngleDeg = 0.0;
                if (body.ContainsKey("draftAngleDeg"))
                    draftAngleDeg = Convert.ToDouble(body["draftAngleDeg"]);
                bool draftOutward = false;
                if (body.ContainsKey("draftOutward"))
                    draftOutward = Convert.ToBoolean(body["draftOutward"]);

                string featureName = _commands.Extrusion(
                    depth, flipDirection, midPlane, draftAngleDeg, draftOutward);
                SendJson(resp, 200, new {
                    success = true,
                    message = $"拉伸完成，深度: {depth} 米, 方向翻转: {flipDirection}, 中间面: {midPlane}, 拔模: {draftAngleDeg}° {(draftOutward ? "外扩" : "内收")}",
                    featureName = featureName,
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"拉伸失败: {ex.Message}" });
            }
        }

        private static void HandleSketchCenterLine(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double x1 = GetDouble(body, "x1");
                double y1 = GetDouble(body, "y1");
                double x2 = GetDouble(body, "x2");
                double y2 = GetDouble(body, "y2");
                double z1 = GetDoubleOrDefault(body, "z1", 0);
                double z2 = GetDoubleOrDefault(body, "z2", 0);

                _commands.SketchCenterLine(x1, y1, x2, y2, z1, z2);
                SendJson(resp, 200, new { success = true, message = $"绘制中心线: ({x1}, {y1}, {z1}) -> ({x2}, {y2}, {z2})" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制中心线失败: {ex.Message}" });
            }
        }

        private static void HandleSketchArc(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double cx = GetDouble(body, "cx");
                double cy = GetDouble(body, "cy");
                double startX = GetDouble(body, "startX");
                double startY = GetDouble(body, "startY");
                double endX = GetDouble(body, "endX");
                double endY = GetDouble(body, "endY");
                short direction = (short)GetInt(body, "direction", 1);
                double cz = GetDoubleOrDefault(body, "cz", 0);
                double startZ = GetDoubleOrDefault(body, "startZ", 0);
                double endZ = GetDoubleOrDefault(body, "endZ", 0);

                _commands.SketchArc(cx, cy, startX, startY, endX, endY, direction, cz, startZ, endZ);
                SendJson(resp, 200, new { success = true, message = $"绘制圆弧: 圆心({cx},{cy}), 起点({startX},{startY}), 终点({endX},{endY}), 方向={direction}" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制圆弧失败: {ex.Message}" });
            }
        }

        private static void HandleSketchEllipse(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double cx = GetDouble(body, "cx");
                double cy = GetDouble(body, "cy");
                double majorRadius = GetDouble(body, "majorRadius");
                double minorRadius = GetDouble(body, "minorRadius");
                double cz = GetDoubleOrDefault(body, "cz", 0);

                if (majorRadius <= 0)
                    throw new ArgumentOutOfRangeException("majorRadius", "长轴半径必须为正数");
                if (minorRadius <= 0)
                    throw new ArgumentOutOfRangeException("minorRadius", "短轴半径必须为正数");

                _commands.SketchEllipse(cx, cy, majorRadius, minorRadius, cz);
                SendJson(resp, 200, new { success = true, message = $"绘制椭圆: 中心({cx}, {cy}, {cz}), 长轴半径 {majorRadius}, 短轴半径 {minorRadius}" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制椭圆失败: {ex.Message}" });
            }
        }

        private static void HandleSketchPolygon(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double cx = GetDouble(body, "cx");
                double cy = GetDouble(body, "cy");
                double circumradius = GetDouble(body, "circumradius");
                if (!body.ContainsKey("sides"))
                    throw new KeyNotFoundException("缺少必填字段: sides（边数，3~64 的整数）");
                int sides = Convert.ToInt32(body["sides"]);
                double startAngleDeg = GetDoubleOrDefault(body, "startAngleDeg", 0);
                double cz = GetDoubleOrDefault(body, "cz", 0);

                _commands.SketchPolygon(cx, cy, circumradius, sides, startAngleDeg, cz);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"绘制正{sides}边形: 中心({cx}, {cy}, {cz}), 外接圆半径 {circumradius}, 起始角 {startAngleDeg}°"
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制正多边形失败: {ex.Message}" });
            }
        }

        private static void HandleSketchSlot(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double cx = GetDouble(body, "cx");
                double cy = GetDouble(body, "cy");
                double length = GetDouble(body, "length");
                double width = GetDouble(body, "width");
                double angleDeg = GetDoubleOrDefault(body, "angleDeg", 0);
                double cz = GetDoubleOrDefault(body, "cz", 0);

                _commands.SketchSlot(cx, cy, length, width, angleDeg, cz);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"绘制长圆孔: 中心({cx}, {cy}, {cz}), 长={length}, 宽={width}, 旋转角={angleDeg}°"
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"绘制长圆孔失败: {ex.Message}" });
            }
        }

        private static void HandleCircularPattern(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                if (!body.ContainsKey("count"))
                    throw new KeyNotFoundException("缺少必填字段: count（阵列数量，≥2 的整数）");
                int count = Convert.ToInt32(body["count"]);
                string axis = body.ContainsKey("axis") ? body["axis"].ToString() : "y";
                double angleDeg = GetDoubleOrDefault(body, "angleDeg", 360);
                bool reverseDir = body.ContainsKey("reverseDir") && Convert.ToBoolean(body["reverseDir"]);
                // 可选：显式指定旋转轴心（多实体零件中用于避开歧义，选轴线最近此点的圆柱面）
                double? axisOriginX = body.ContainsKey("axisOriginX") ? (double?)Convert.ToDouble(body["axisOriginX"]) : null;
                double? axisOriginY = body.ContainsKey("axisOriginY") ? (double?)Convert.ToDouble(body["axisOriginY"]) : null;
                double? axisOriginZ = body.ContainsKey("axisOriginZ") ? (double?)Convert.ToDouble(body["axisOriginZ"]) : null;

                string axisFeature = body.ContainsKey("axisFeature") ? body["axisFeature"]?.ToString() : null;
                var result = _commands.CircularPattern(
                    count, axis, angleDeg, reverseDir, axisOriginX, axisOriginY, axisOriginZ, axisFeature);
                var response = new Dictionary<string, object> { { "success", true } };
                foreach (var kv in result) response[kv.Key] = kv.Value;
                SendJson(resp, 200, response);
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"圆周阵列失败: {ex.Message}" });
            }
        }

        private static void HandleLinearPattern(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                if (!body.ContainsKey("num1"))
                    throw new KeyNotFoundException("缺少必填字段: num1（方向 1 实例数，≥2 的整数）");
                if (!body.ContainsKey("spacing1"))
                    throw new KeyNotFoundException("缺少必填字段: spacing1（方向 1 间距，米，> 0）");

                int num1 = Convert.ToInt32(body["num1"]);
                double spacing1 = Convert.ToDouble(body["spacing1"]);
                string dir1Axis = body.ContainsKey("dir1Axis") ? body["dir1Axis"].ToString() : "x";

                int num2 = body.ContainsKey("num2") ? Convert.ToInt32(body["num2"]) : 1;
                double spacing2 = body.ContainsKey("spacing2") ? Convert.ToDouble(body["spacing2"]) : 0.0;
                string dir2Axis = body.ContainsKey("dir2Axis") ? body["dir2Axis"].ToString() : "y";

                bool flip1 = body.ContainsKey("flip1") && Convert.ToBoolean(body["flip1"]);
                bool flip2 = body.ContainsKey("flip2") && Convert.ToBoolean(body["flip2"]);

                var result = _commands.LinearPattern(num1, spacing1, dir1Axis, num2, spacing2, dir2Axis, flip1, flip2);
                var response = new Dictionary<string, object> { { "success", true } };
                foreach (var kv in result) response[kv.Key] = kv.Value;
                SendJson(resp, 200, response);
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"线性阵列失败: {ex.Message}" });
            }
        }

        private static void HandleRevolve(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double angle = GetDoubleOrDefault(body, "angle", 360);
                bool isCut = false;
                if (body.ContainsKey("isCut"))
                    isCut = Convert.ToBoolean(body["isCut"]);
                bool reverseDir = false;
                if (body.ContainsKey("reverseDir"))
                    reverseDir = Convert.ToBoolean(body["reverseDir"]);

                _commands.Revolve(angle, isCut, reverseDir);
                string type = isCut ? "切除" : "凸台";
                SendJson(resp, 200, new { success = true, message = $"旋转{type}完成，角度: {angle}°" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"旋转失败: {ex.Message}" });
            }
        }

        private static void HandleCutExtrusion(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double depth = GetDouble(body, "depth");
                string endCondition = GetStringOrDefault(body, "endCondition", "blind");
                bool flipDirection = false;
                if (body.ContainsKey("flipDirection"))
                    flipDirection = Convert.ToBoolean(body["flipDirection"]);

                string featureName = _commands.CutExtrusion(
                    depth, endCondition, flipDirection);
                SendJson(resp, 200, new {
                    success = true,
                    message = $"切除拉伸完成，深度: {depth} 米, 终止条件: {endCondition}",
                    featureName = featureName,
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"切除拉伸失败: {ex.Message}" });
            }
        }

        private static void HandleCloseSketch(HttpListenerResponse resp)
        {
            try
            {
                _commands.CloseSketch();
                SendJson(resp, 200, new { success = true, message = "已退出草图编辑" });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"退出草图失败: {ex.Message}" });
            }
        }

        private static void HandleCreateBox(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double length = GetDouble(body, "length");
                double width = GetDouble(body, "width");
                double height = GetDouble(body, "height");

                _commands.CreateBox(length, width, height);
                SendJson(resp, 200, new { success = true, message = $"长方体创建成功 ({length} x {width} x {height} 米)" });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建长方体失败: {ex.Message}" });
            }
        }

        private static void HandleSketchStats(HttpListenerResponse resp)
        {
            try
            {
                var sketches = _commands.GetSketchGeometryStats();
                SendJson(resp, 200, new { success = true, sketches });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"获取草图统计失败: {ex.Message}" });
            }
        }

        private static void HandleFeatureTree(HttpListenerResponse resp)
        {
            try
            {
                var features = _commands.GetFeatureTree();
                SendJson(resp, 200, new { success = true, features = features });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"获取特征树失败: {ex.Message}" });
            }
        }

        private static void HandleModelObservation(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                int maxFaces = 200;
                int requested;
                if (int.TryParse(req.QueryString["maxFaces"], out requested) && requested > 0)
                    maxFaces = Math.Min(requested, 5000);
                var snapshot = _commands.GetModelObservation(maxFaces);
                snapshot["success"] = true;
                SendJson(resp, 200, snapshot);
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new {
                    success = false,
                    message = $"获取模型观察失败: {ex.Message}",
                });
            }
        }

        private static void HandleBodyVolume(HttpListenerResponse resp)
        {
            try
            {
                double vol = _commands.GetBodyVolume();
                SendJson(resp, 200, new { success = true, volume = vol, volumeMm3 = vol * 1e9 });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"获取体积失败: {ex.Message}" });
            }
        }

        private static void HandleBodyBoundingBox(HttpListenerResponse resp)
        {
            try
            {
                var box = _commands.GetBodyBoundingBox();
                SendJson(resp, 200, new { success = true, boundingBox = box });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"获取包围盒失败: {ex.Message}" });
            }
        }

        private static void HandleSelectFaceAndSketch(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                // 射线起点
                double rx = GetDouble(body, "rx");
                double ry = GetDouble(body, "ry");
                double rz = GetDouble(body, "rz");
                // 射线方向
                double dx = GetDouble(body, "dx");
                double dy = GetDouble(body, "dy");
                double dz = GetDouble(body, "dz");

                var result = _commands.SelectFaceByRayAndStartSketch(rx, ry, rz, dx, dy, dz);
                var respData = new Dictionary<string, object>
                {
                    { "success", true },
                    { "message", result["message"] },
                    { "faceType", result["faceType"] },
                };
                if (result.ContainsKey("hitPoint"))
                    respData["hitPoint"] = result["hitPoint"];
                if (result.ContainsKey("sketchTransform"))
                    respData["sketchTransform"] = result["sketchTransform"];
                if (result.ContainsKey("sketchName"))
                    respData["sketchName"] = result["sketchName"];
                if (result.ContainsKey("faceKey"))
                    respData["faceKey"] = result["faceKey"];
                if (result.ContainsKey("normal"))
                    respData["normal"] = result["normal"];
                if (result.ContainsKey("persistRef"))
                    respData["persistRef"] = result["persistRef"];
                SendJson(resp, 200, respData);
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"选面并进入草图失败: {ex.Message}" });
            }
        }

        private static void HandleSelectObservedFaceAndSketch(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                if (!body.ContainsKey("faceIndex"))
                    throw new KeyNotFoundException(
                        "缺少必要参数: faceIndex");
                int faceIndex = Convert.ToInt32(body["faceIndex"]);
                bool exact = body.ContainsKey("exact") && Convert.ToBoolean(body["exact"]);
                var result = _commands.SelectObservedFaceAndStartSketch(
                    faceIndex, exact);
                var respData = new Dictionary<string, object>
                {
                    { "success", true },
                    { "message", result["message"] },
                    { "faceType", result["faceType"] },
                };
                foreach (string key in new[] {
                    "hitPoint",
                    "sketchTransform",
                    "sketchName",
                    "faceKey",
                    "normal",
                    "persistRef",
                })
                {
                    if (result.ContainsKey(key))
                        respData[key] = result[key];
                }
                SendJson(resp, 200, respData);
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(
                    resp,
                    400,
                    new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(
                    resp,
                    400,
                    new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(
                    resp,
                    400,
                    new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new {
                    success = false,
                    message = $"按观察面索引选面失败: {ex.Message}",
                });
            }
        }

        private static void HandleModifyDimension(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string featureName = GetString(body, "featureName");
                string dimensionName = GetStringOrDefault(body, "dimensionName", "");
                double newValue = GetDouble(body, "newValue");

                var result = _commands.ModifyFeatureDimension(featureName, dimensionName, newValue);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = result["message"],
                    featureName = result["featureName"],
                    dimensionName = result["dimensionName"],
                    oldValue = result["oldValue"],
                    newValue = result["newValue"],
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"修改尺寸失败: {ex.Message}" });
            }
        }

        private static void HandleFillet(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double radius = GetDouble(body, "radius");
                string edgeSelection = GetStringOrDefault(body, "edgeSelection", "all");
                double rx = GetDoubleOrDefault(body, "rx", 0);
                double ry = GetDoubleOrDefault(body, "ry", 0);
                double rz = GetDoubleOrDefault(body, "rz", 0);
                double dx = GetDoubleOrDefault(body, "dx", 0);
                double dy = GetDoubleOrDefault(body, "dy", 0);
                double dz = GetDoubleOrDefault(body, "dz", 0);

                var result = _commands.Fillet(radius, edgeSelection, rx, ry, rz, dx, dy, dz);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = result["message"],
                    featureName = result["featureName"],
                    edgeCount = result["edgeCount"],
                    radius = result["radius"],
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建圆角失败: {ex.Message}" });
            }
        }

        private static void HandleChamfer(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string chamferType = GetStringOrDefault(body, "type", "equal_distance");
                double distance1 = GetDouble(body, "distance1");
                string edgeSelection = GetStringOrDefault(body, "edgeSelection", "all");
                double distance2 = GetDoubleOrDefault(body, "distance2", 0);
                double angle = GetDoubleOrDefault(body, "angle", 45);
                double rx = GetDoubleOrDefault(body, "rx", 0);
                double ry = GetDoubleOrDefault(body, "ry", 0);
                double rz = GetDoubleOrDefault(body, "rz", 0);
                double dx = GetDoubleOrDefault(body, "dx", 0);
                double dy = GetDoubleOrDefault(body, "dy", 0);
                double dz = GetDoubleOrDefault(body, "dz", 0);

                var result = _commands.Chamfer(chamferType, distance1, edgeSelection,
                    distance2, angle, rx, ry, rz, dx, dy, dz);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = result["message"],
                    featureName = result["featureName"],
                    edgeCount = result["edgeCount"],
                    chamferType = result["chamferType"],
                    distance1 = result["distance1"],
                });
            }
            catch (KeyNotFoundException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentOutOfRangeException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (ArgumentException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (InvalidOperationException ex)
            {
                SendJson(resp, 400, new { success = false, message = ex.Message });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建倒角失败: {ex.Message}" });
            }
        }

        // ================================================================
        // 装配体端点 handlers（Stage B）
        //   POST /api/assembly/new           { savePath }                         → 新建装配体
        //   POST /api/assembly/add-component { partPath, x, y, z }               → 加零件
        //   POST /api/assembly/mate-coincident {comp1,plane1,comp2,plane2}       → 重合配合
        //   POST /api/assembly/mate-concentric {comp1,rx1,ry1,rz1,dx1,dy1,dz1,
        //                                       comp2,rx2,ry2,rz2,dx2,dy2,dz2}   → 同心配合
        //   POST /api/assembly/mate-gear     {上同 + gearRatioNumerator/
        //                                     Denominator/reverse}                → 齿轮联动
        //   POST /api/assembly/save          {}                                   → 保存装配体
        //   POST /api/assembly/rebuild       {}                                   → 强制重建
        //   POST /api/save-part-as           { savePath }                         → 零件另存为
        // ================================================================
        private static void HandleAssemblyNew(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string savePath = GetString(body, "savePath");
                _commands.NewAssembly(savePath);
                SendJson(resp, 200, new { success = true, message = $"新建装配体成功: {savePath}" });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 新建装配体失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"新建装配体失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyAddComponent(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string partPath = GetString(body, "partPath");
                double x = GetDoubleOrDefault(body, "x", 0);
                double y = GetDoubleOrDefault(body, "y", 0);
                double z = GetDoubleOrDefault(body, "z", 0);
                string compName = _commands.AddComponent(partPath, x, y, z);
                var closedDocuments = _commands.CloseInactiveDocuments();
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"已加入组件: {compName}",
                    componentName = compName,
                    closedDocuments,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] AddComponent 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"加入组件失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyMateCoincident(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp1 = GetString(body, "component1");
                string plane1 = GetStringOrDefault(body, "plane1", "前视基准面");
                string comp2 = GetString(body, "component2");
                string plane2 = GetStringOrDefault(body, "plane2", "前视基准面");
                var result = _commands.MateCoincident(comp1, plane1, comp2, plane2);
                SendJson(resp, 200, new { success = true, message = "Coincident Mate 完成", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] MateCoincident 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"重合配合失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyMateConcentric(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp1 = GetString(body, "component1");
                double rx1 = GetDouble(body, "rx1"); double ry1 = GetDouble(body, "ry1"); double rz1 = GetDouble(body, "rz1");
                double dx1 = GetDouble(body, "dx1"); double dy1 = GetDouble(body, "dy1"); double dz1 = GetDouble(body, "dz1");
                string comp2 = GetString(body, "component2");
                double rx2 = GetDouble(body, "rx2"); double ry2 = GetDouble(body, "ry2"); double rz2 = GetDouble(body, "rz2");
                double dx2 = GetDouble(body, "dx2"); double dy2 = GetDouble(body, "dy2"); double dz2 = GetDouble(body, "dz2");
                var result = _commands.MateConcentric(
                    comp1, rx1, ry1, rz1, dx1, dy1, dz1,
                    comp2, rx2, ry2, rz2, dx2, dy2, dz2);
                SendJson(resp, 200, new { success = true, message = "Concentric Mate 完成", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] MateConcentric 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"同心配合失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyMateGear(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp1 = GetString(body, "component1");
                // Legacy ray fields remain accepted for protocol compatibility,
                // but SwCommands now resolves center-bore faces deterministically.
                double rx1 = GetDoubleOrDefault(body, "rx1", 0); double ry1 = GetDoubleOrDefault(body, "ry1", 0); double rz1 = GetDoubleOrDefault(body, "rz1", 0);
                double dx1 = GetDoubleOrDefault(body, "dx1", 0); double dy1 = GetDoubleOrDefault(body, "dy1", 0); double dz1 = GetDoubleOrDefault(body, "dz1", 0);
                string comp2 = GetString(body, "component2");
                double rx2 = GetDoubleOrDefault(body, "rx2", 0); double ry2 = GetDoubleOrDefault(body, "ry2", 0); double rz2 = GetDoubleOrDefault(body, "rz2", 0);
                double dx2 = GetDoubleOrDefault(body, "dx2", 0); double dy2 = GetDoubleOrDefault(body, "dy2", 0); double dz2 = GetDoubleOrDefault(body, "dz2", 0);
                double num = GetDouble(body, "gearRatioNumerator");
                double den = GetDouble(body, "gearRatioDenominator");
                bool reverse = body.ContainsKey("reverse") && Convert.ToBoolean(body["reverse"]);
                var result = _commands.MateGear(
                    comp1, rx1, ry1, rz1, dx1, dy1, dz1,
                    comp2, rx2, ry2, rz2, dx2, dy2, dz2,
                    num, den, reverse);
                SendJson(resp, 200, new { success = true, message = $"Gear Mate 完成 (比 {num}:{den})", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] MateGear 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"齿轮配合失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblySave(HttpListenerResponse resp)
        {
            try
            {
                _commands.SaveAssembly();
                SendJson(resp, 200, new { success = true, message = "装配体已保存" });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"保存装配体失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyRebuild(HttpListenerResponse resp)
        {
            try
            {
                _commands.RebuildAssembly();
                SendJson(resp, 200, new { success = true, message = "装配体已重建" });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"重建失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyInterference(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.CheckAssemblyInterference();
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"干涉数量: {result["interferenceCount"]}",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 装配体干涉检测失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleOpenPart(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.OpenPart(GetString(body, "partPath"));
                SendJson(resp, 200, new { success = true, message = $"零件已打开: {result["path"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"打开零件失败: {ex.Message}" });
            }
        }

        private static void HandleDeleteFeature(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.DeleteFeature(GetString(body, "featureName"));
                SendJson(resp, 200, new { success = true, message = $"已删除特征 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"删除特征失败: {ex.Message}" });
            }
        }

        private static List<Dictionary<string, object>> GetObjectList(Dictionary<string, object> body, string key)
        {
            if (!body.ContainsKey(key))
                throw new KeyNotFoundException($"缺少必填字段: {key}");
            var raw = body[key] as System.Collections.IEnumerable;
            if (raw == null || body[key] is string)
                throw new ArgumentException($"{key} 必须是数组");
            var items = new List<Dictionary<string, object>>();
            foreach (object item in raw)
            {
                var entry = item as Dictionary<string, object>;
                if (entry == null)
                    throw new ArgumentException($"{key} 中每项必须是对象");
                items.Add(entry);
            }
            return items;
        }

        private static void HandleSketchProfile(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.SketchProfile(
                    GetObjectList(body, "segments"), GetBoolOrDefault(body, "closed", true));
                result["success"] = true;
                result["message"] = $"绘制轮廓 {result["segmentCount"]} 段";
                SendJson(resp, 200, result);
            }
            catch (Exception ex)
            {
                SendJson(resp, 400, new { success = false, message = $"绘制轮廓失败: {ex.Message}" });
            }
        }

        private static void HandleCreateOffsetPlane(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.CreateOffsetPlane(
                    GetString(body, "reference"), GetDouble(body, "distance"), GetBoolOrDefault(body, "flip", false));
                SendJson(resp, 200, new { success = true, message = $"已创建基准面 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建基准面失败: {ex.Message}" });
            }
        }

        private static void HandleCreateAxisFromPlanes(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.CreateAxisFromPlanes(GetString(body, "first"), GetString(body, "second"));
                SendJson(resp, 200, new { success = true, message = $"已创建参考轴 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建参考轴失败: {ex.Message}" });
            }
        }

        private static void HandleSelectRefPlaneAndSketch(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.SelectRefPlaneAndStartSketch(GetString(body, "planeName"));
                result["success"] = true;
                result["message"] = $"已在基准面上开草图 {result["sketchName"]}";
                SendJson(resp, 200, result);
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"在基准面上开草图失败: {ex.Message}" });
            }
        }

        private static void HandleMirror(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var names = new List<string>();
                if (body.ContainsKey("features") && body["features"] is System.Collections.IEnumerable raw
                    && !(body["features"] is string))
                {
                    foreach (object item in raw)
                        names.Add(Convert.ToString(item));
                }
                var result = _commands.MirrorFeatures(
                    names, GetString(body, "plane"), GetBoolOrDefault(body, "mirrorBody", false));
                SendJson(resp, 200, new { success = true, message = $"已镜像 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"镜像失败: {ex.Message}" });
            }
        }

        private static void HandleUntil(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.ExtrudeOrCutUntil(
                    GetString(body, "until"),
                    GetBoolOrDefault(body, "flipDirection", false),
                    GetBoolOrDefault(body, "cut", false));
                SendJson(resp, 200, new { success = true, message = $"已创建到面特征 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"拉伸/切除到面失败: {ex.Message}" });
            }
        }

        private static void HandleRib(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.InsertRib(
                    GetDouble(body, "thickness"),
                    GetBoolOrDefault(body, "bothSides", true),
                    GetBoolOrDefault(body, "flip", false));
                SendJson(resp, 200, new { success = true, message = $"已创建筋 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"创建筋失败: {ex.Message}" });
            }
        }

        private static void HandleSweep(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.InsertSweep(GetBoolOrDefault(body, "cut", false));
                SendJson(resp, 200, new { success = true, message = $"已扫描 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"扫描失败: {ex.Message}" });
            }
        }

        private static void HandleLoft(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.InsertLoft(
                    body.ContainsKey("sectionCount") ? Convert.ToInt32(body["sectionCount"]) : 2,
                    GetBoolOrDefault(body, "cut", false));
                SendJson(resp, 200, new { success = true, message = $"已放样 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"放样失败: {ex.Message}" });
            }
        }

        private static void HandleShell(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var faces = new List<string>();
                if (body.ContainsKey("openFaces") && body["openFaces"] is System.Collections.IEnumerable raw
                    && !(body["openFaces"] is string))
                {
                    foreach (object item in raw)
                        faces.Add(Convert.ToString(item));
                }
                var result = _commands.InsertShell(
                    GetDouble(body, "thickness"), faces, GetBoolOrDefault(body, "outward", false));
                SendJson(resp, 200, new { success = true, message = $"已抽壳 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"抽壳失败: {ex.Message}" });
            }
        }

        private static void HandleDraft(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var faces = new List<string>();
                if (body.ContainsKey("faces") && body["faces"] is System.Collections.IEnumerable raw
                    && !(body["faces"] is string))
                {
                    foreach (object item in raw)
                        faces.Add(Convert.ToString(item));
                }
                var result = _commands.InsertDraft(
                    GetDouble(body, "angleDeg"), GetString(body, "neutral"), faces,
                    GetBoolOrDefault(body, "flip", false));
                SendJson(resp, 200, new { success = true, message = $"已拔模 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"拔模失败: {ex.Message}" });
            }
        }

        private static void HandleSheetMetalBase(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.InsertSheetMetalBase(GetDouble(body, "thickness"));
                SendJson(resp, 200, new { success = true, message = $"已创建钣金基体 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"钣金基体法兰失败: {ex.Message}" });
            }
        }

        private static void HandleCosmeticThread(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.InsertCosmeticThread(
                    GetString(body, "face"), GetDouble(body, "diameter"), GetDouble(body, "depth"));
                SendJson(resp, 200, new { success = true, message = $"已添加装饰螺纹 {result["featureName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"装饰螺纹失败: {ex.Message}" });
            }
        }

        private static void HandleCloseSketchNamed(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.CloseSketchNamed();
                SendJson(resp, 200, new { success = true, message = $"已退出草图 {result["sketchName"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"退出草图失败: {ex.Message}" });
            }
        }

        private static void HandleExportStep(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.ExportStep(GetString(body, "stepPath"));
                SendJson(resp, 200, new { success = true, message = $"STEP 已导出: {result["path"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"导出 STEP 失败: {ex.Message}" });
            }
        }

        private static void HandleSavePartAs(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string savePath = GetString(body, "savePath");
                var result = _commands.SavePartAs(savePath);
                SendJson(resp, 200, new { success = true, message = $"零件已另存为: {result["path"]}", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = $"零件另存为失败: {ex.Message}" });
            }
        }

        private static void HandleGetComponentTransform(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                var result = _commands.GetComponentTransform(comp);
                SendJson(resp, 200, new { success = true, message = "OK", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] GetComponentTransform 失败: {ex.Message}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleSetComponentPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var result = _commands.SetComponentPose(
                    GetString(body, "componentName"),
                    GetDouble(body, "x"),
                    GetDouble(body, "y"),
                    GetDouble(body, "z"),
                    GetDoubleOrDefault(body, "axisX", 0),
                    GetDoubleOrDefault(body, "axisY", 0),
                    GetDoubleOrDefault(body, "axisZ", 1),
                    GetDoubleOrDefault(body, "angleDeg", 0));
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "组件姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] SetComponentPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"设置组件姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleCrankSliderPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var synchronized =
                    new List<Dictionary<string, object>>();
                if (body.ContainsKey("synchronizedComponents"))
                {
                    var raw = body["synchronizedComponents"]
                        as System.Collections.IEnumerable;
                    if (raw == null)
                        throw new ArgumentException(
                            "synchronizedComponents 必须是数组");
                    foreach (var item in raw)
                    {
                        var entry = item as Dictionary<string, object>;
                        if (entry == null)
                            throw new ArgumentException(
                                "同步组件配置必须是对象");
                        synchronized.Add(entry);
                    }
                }
                var result = _commands.SetCrankSliderPose(
                    GetString(body, "crankComponent"),
                    GetString(body, "rodComponent"),
                    GetString(body, "sliderComponent"),
                    GetDoubleOrDefault(body, "centerX", 0),
                    GetDoubleOrDefault(body, "centerY", 0),
                    GetDoubleOrDefault(body, "centerZ", 0),
                    GetDouble(body, "crankRadius"),
                    GetDouble(body, "rodLength"),
                    GetDoubleOrDefault(body, "sliderAxisX", 0),
                    GetDoubleOrDefault(body, "sliderAxisY", 1),
                    GetDoubleOrDefault(body, "sliderAxisZ", 0),
                    GetDoubleOrDefault(body, "rotationAxisX", 0),
                    GetDoubleOrDefault(body, "rotationAxisY", 0),
                    GetDoubleOrDefault(body, "rotationAxisZ", 1),
                    body.ContainsKey("crankAxisOffset")
                        ? GetDouble(body, "crankAxisOffset")
                        : double.NaN,
                    body.ContainsKey("rodAxisOffset")
                        ? GetDouble(body, "rodAxisOffset")
                        : double.NaN,
                    body.ContainsKey("sliderAxisOffset")
                        ? GetDouble(body, "sliderAxisOffset")
                        : double.NaN,
                    GetDouble(body, "angleDeg"),
                    synchronized);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "曲柄滑块姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] CrankSliderPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"曲柄滑块姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleSynchronizedRotaryPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string[], object> valueOf = keys =>
                {
                    foreach (string key in keys)
                        if (body.ContainsKey(key))
                            return body[key];
                    return null;
                };
                Func<string[], double, double> numberOrDefault =
                    (keys, defaultValue) =>
                    {
                        object value = valueOf(keys);
                        return value == null
                            ? defaultValue
                            : Convert.ToDouble(value);
                    };
                object rawRotors = valueOf(
                    new string[] { "rotors", "components" });
                var enumerable =
                    rawRotors as System.Collections.IEnumerable;
                if (enumerable == null || rawRotors is string)
                    throw new ArgumentException(
                        "rotors 必须是对象数组");
                var rotors = new List<Dictionary<string, object>>();
                foreach (var item in enumerable)
                {
                    var rotor = item as Dictionary<string, object>;
                    if (rotor == null)
                        throw new ArgumentException(
                            "rotors 的每一项必须是对象");
                    rotors.Add(rotor);
                }
                object rawInput = valueOf(
                    new string[] { "inputAngleDeg", "angleDeg" });
                if (rawInput == null)
                    throw new KeyNotFoundException(
                        "缺少必要参数: inputAngleDeg");
                var result = _commands.SetSynchronizedRotaryPose(
                    Convert.ToDouble(rawInput),
                    numberOrDefault(
                        new string[] { "pivotX", "centerX" }, 0),
                    numberOrDefault(
                        new string[] { "pivotY", "centerY" }, 0),
                    numberOrDefault(
                        new string[] { "pivotZ", "centerZ" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisX", "axisX" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisY", "axisY" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisZ", "axisZ" }, 1),
                    rotors);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "同步旋转机构姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] SynchronizedRotaryPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"同步旋转机构姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleFourBarPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string[], object> valueOf = keys =>
                {
                    foreach (string key in keys)
                        if (body.ContainsKey(key))
                            return body[key];
                    return null;
                };
                Func<string[], double> requiredNumber = keys =>
                {
                    object value = valueOf(keys);
                    if (value == null)
                        throw new KeyNotFoundException(
                            $"缺少必要参数: {keys[0]}");
                    return Convert.ToDouble(value);
                };
                Func<string[], double, double> numberOrDefault =
                    (keys, defaultValue) =>
                    {
                        object value = valueOf(keys);
                        return value == null
                            ? defaultValue
                            : Convert.ToDouble(value);
                    };
                Func<string[], string> requiredString = keys =>
                {
                    object value = valueOf(keys);
                    if (value == null)
                        throw new KeyNotFoundException(
                            $"缺少必要参数: {keys[0]}");
                    return Convert.ToString(value);
                };
                Func<string[], string, string> stringOrDefault =
                    (keys, defaultValue) =>
                    {
                        object value = valueOf(keys);
                        return value == null
                            ? defaultValue
                            : Convert.ToString(value);
                    };
                var pointA = valueOf(new string[] { "groundA", "pivotA" })
                    as Dictionary<string, object>;
                var pointB = valueOf(new string[] { "groundB", "pivotB" })
                    as Dictionary<string, object>;
                Func<Dictionary<string, object>, string, double, double>
                    pointCoordinate =
                        (point, key, fallback) =>
                        {
                            if (point == null || !point.ContainsKey(key))
                                return fallback;
                            return Convert.ToDouble(point[key]);
                        };
                double ax = pointCoordinate(
                    pointA,
                    "x",
                    numberOrDefault(
                        new string[] { "groundAX", "pivotAX", "aX" }, 0));
                double ay = pointCoordinate(
                    pointA,
                    "y",
                    numberOrDefault(
                        new string[] { "groundAY", "pivotAY", "aY" }, 0));
                double az = pointCoordinate(
                    pointA,
                    "z",
                    numberOrDefault(
                        new string[] { "groundAZ", "pivotAZ", "aZ" }, 0));
                double bx;
                if (pointB != null && pointB.ContainsKey("x"))
                    bx = Convert.ToDouble(pointB["x"]);
                else
                    bx = requiredNumber(
                        new string[] { "groundBX", "pivotBX", "bX" });
                double by = pointCoordinate(
                    pointB,
                    "y",
                    numberOrDefault(
                        new string[] { "groundBY", "pivotBY", "bY" }, 0));
                double bz = pointCoordinate(
                    pointB,
                    "z",
                    numberOrDefault(
                        new string[] { "groundBZ", "pivotBZ", "bZ" }, 0));
                var couplerPoint = valueOf(
                    new string[] { "couplerPoint" })
                    as Dictionary<string, object>;
                var pointComponents =
                    new List<Dictionary<string, object>>();
                object rawPointComponents = valueOf(
                    new string[]
                    {
                        "couplerPointComponents",
                        "pointComponents",
                    });
                if (rawPointComponents == null && couplerPoint != null
                    && couplerPoint.ContainsKey("components"))
                    rawPointComponents = couplerPoint["components"];
                if (rawPointComponents != null)
                {
                    var enumerable = rawPointComponents
                        as System.Collections.IEnumerable;
                    if (enumerable == null
                        || rawPointComponents is string)
                        throw new ArgumentException(
                            "couplerPointComponents 必须是数组");
                    foreach (var item in enumerable)
                    {
                        var entry =
                            item as Dictionary<string, object>;
                        if (entry == null)
                            throw new ArgumentException(
                                "couplerPointComponents 的每一项必须是对象");
                        pointComponents.Add(entry);
                    }
                }
                var result = _commands.SetFourBarPose(
                    requiredString(
                        new string[]
                            { "inputComponent", "crankComponent" }),
                    requiredString(
                        new string[] { "couplerComponent", "rodComponent" }),
                    requiredString(
                        new string[]
                            { "outputComponent", "rockerComponent" }),
                    ax, ay, az,
                    bx, by, bz,
                    requiredNumber(
                        new string[] { "inputLength", "crankLength" }),
                    requiredNumber(
                        new string[] { "couplerLength", "rodLength" }),
                    requiredNumber(
                        new string[] { "outputLength", "rockerLength" }),
                    requiredNumber(
                        new string[] { "inputAngleDeg", "angleDeg" }),
                    stringOrDefault(
                        new string[] { "assemblyMode", "mode" },
                        "open"),
                    numberOrDefault(
                        new string[] { "rotationAxisX", "axisX" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisY", "axisY" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisZ", "axisZ" }, 1),
                    couplerPoint,
                    pointComponents);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "四杆机构姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] FourBarPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"四杆机构姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleCamFollowerPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string[], object> valueOf = keys =>
                {
                    foreach (string key in keys)
                        if (body.ContainsKey(key))
                            return body[key];
                    return null;
                };
                Func<string[], double> requiredNumber = keys =>
                {
                    object value = valueOf(keys);
                    if (value == null)
                        throw new KeyNotFoundException(
                            $"缺少必要参数: {keys[0]}");
                    return Convert.ToDouble(value);
                };
                Func<string[], double, double> numberOrDefault =
                    (keys, defaultValue) =>
                    {
                        object value = valueOf(keys);
                        return value == null
                            ? defaultValue
                            : Convert.ToDouble(value);
                    };
                Func<string[], string> requiredString = keys =>
                {
                    object value = valueOf(keys);
                    if (value == null)
                        throw new KeyNotFoundException(
                            $"缺少必要参数: {keys[0]}");
                    return Convert.ToString(value);
                };
                var synchronized =
                    new List<Dictionary<string, object>>();
                object rawSynchronized = valueOf(
                    new string[]
                    {
                        "synchronizedFollowerComponents",
                        "synchronizedComponents",
                        "followerComponents",
                    });
                if (rawSynchronized != null)
                {
                    var enumerable = rawSynchronized
                        as System.Collections.IEnumerable;
                    if (enumerable == null || rawSynchronized is string)
                        throw new ArgumentException(
                            "synchronizedFollowerComponents 必须是数组");
                    foreach (var item in enumerable)
                    {
                        var entry =
                            item as Dictionary<string, object>;
                        if (entry == null)
                            throw new ArgumentException(
                                "同步从动件配置必须是对象");
                        synchronized.Add(entry);
                    }
                }
                object rawProfile = valueOf(
                    new string[] { "profile", "camType", "type" });
                var result = _commands.SetCamFollowerPose(
                    requiredString(
                        new string[]
                            { "driverComponent", "camComponent" }),
                    requiredString(
                        new string[] { "followerComponent", "sliderComponent" }),
                    numberOrDefault(
                        new string[] { "centerX", "pivotX" }, 0),
                    numberOrDefault(
                        new string[] { "centerY", "pivotY" }, 0),
                    numberOrDefault(
                        new string[] { "centerZ", "pivotZ" }, 0),
                    requiredNumber(
                        new string[] { "baseRadius", "radius" }),
                    requiredNumber(
                        new string[] { "lift", "liftDistance" }),
                    numberOrDefault(
                        new string[] { "phaseDeg", "phase" }, 0),
                    requiredNumber(
                        new string[] { "inputAngleDeg", "angleDeg" }),
                    rawProfile == null
                        ? "harmonic"
                        : Convert.ToString(rawProfile),
                    numberOrDefault(
                        new string[] { "rotationAxisX", "axisX" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisY", "axisY" }, 0),
                    numberOrDefault(
                        new string[] { "rotationAxisZ", "axisZ" }, 1),
                    numberOrDefault(
                        new string[] { "followerAxisX", "travelAxisX" }, 1),
                    numberOrDefault(
                        new string[] { "followerAxisY", "travelAxisY" }, 0),
                    numberOrDefault(
                        new string[] { "followerAxisZ", "travelAxisZ" }, 0),
                    synchronized);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "凸轮从动机构姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] CamFollowerPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"凸轮从动机构姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandlePlanarChainPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string, bool, List<Dictionary<string, object>>> readList =
                    (key, required) =>
                    {
                        var entries =
                            new List<Dictionary<string, object>>();
                        if (!body.ContainsKey(key))
                        {
                            if (required)
                                throw new KeyNotFoundException(
                                    $"缺少必要参数: {key}");
                            return entries;
                        }
                        var raw = body[key]
                            as System.Collections.IEnumerable;
                        if (raw == null)
                            throw new ArgumentException(
                                $"{key} 必须是数组");
                        foreach (var item in raw)
                        {
                            var entry =
                                item as Dictionary<string, object>;
                            if (entry == null)
                                throw new ArgumentException(
                                    $"{key} 的每一项必须是对象");
                            entries.Add(entry);
                        }
                        return entries;
                    };
                var result = _commands.SetPlanarChainPose(
                    GetDoubleOrDefault(body, "baseX", 0),
                    GetDoubleOrDefault(body, "baseY", 0),
                    GetDoubleOrDefault(body, "baseZ", 0),
                    GetDoubleOrDefault(body, "rotationAxisX", 0),
                    GetDoubleOrDefault(body, "rotationAxisY", 0),
                    GetDoubleOrDefault(body, "rotationAxisZ", 1),
                    GetDoubleOrDefault(body, "baseAngleDeg", 0),
                    readList("segments", true),
                    readList("jointComponents", false),
                    readList("endComponents", false));
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "平面多关节连杆姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] PlanarChainPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"平面多关节连杆姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleRadialDeployablePose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var bays = new List<Dictionary<string, object>>();
                var rawBays = body.ContainsKey("bays")
                    ? body["bays"] as System.Collections.IEnumerable
                    : null;
                if (rawBays == null)
                    throw new ArgumentException("bays 必须是对象数组");
                foreach (var item in rawBays)
                {
                    var bay = item as Dictionary<string, object>;
                    if (bay == null)
                        throw new ArgumentException(
                            "bays 的每一项必须是对象");
                    bays.Add(bay);
                }
                var result = _commands.SetRadialDeployablePose(
                    GetDoubleOrDefault(body, "centerX", 0),
                    GetDoubleOrDefault(body, "centerY", 0),
                    GetDoubleOrDefault(body, "centerZ", 0),
                    GetDoubleOrDefault(body, "deploymentAxisX", 0),
                    GetDoubleOrDefault(body, "deploymentAxisY", 0),
                    GetDoubleOrDefault(body, "deploymentAxisZ", 1),
                    GetDoubleOrDefault(body, "referenceRadialX", 1),
                    GetDoubleOrDefault(body, "referenceRadialY", 0),
                    GetDoubleOrDefault(body, "referenceRadialZ", 0),
                    GetDouble(body, "hubRadius"),
                    GetDouble(body, "runnerRadius"),
                    GetDouble(body, "ribLength"),
                    GetDouble(body, "ribAttachmentDistance"),
                    GetDouble(body, "stretcherLength"),
                    GetDouble(body, "ribDownAngleDeg"),
                    GetString(body, "runnerComponent"),
                    bays);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "径向展开连杆姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] RadialDeployablePose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"径向展开连杆姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandlePlanetaryTrainPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string, bool, List<Dictionary<string, object>>> readList =
                    (key, required) =>
                    {
                        var entries =
                            new List<Dictionary<string, object>>();
                        var raw = body.ContainsKey(key)
                            ? body[key] as System.Collections.IEnumerable
                            : null;
                        if (raw == null)
                        {
                            if (required)
                                throw new ArgumentException(
                                    $"{key} 必须是对象数组");
                            return entries;
                        }
                        foreach (var item in raw)
                        {
                            var entry =
                                item as Dictionary<string, object>;
                            if (entry == null)
                                throw new ArgumentException(
                                    $"{key} 的每一项必须是对象");
                            entries.Add(entry);
                        }
                        return entries;
                    };
                var result = _commands.SetPlanetaryTrainPose(
                    GetDoubleOrDefault(body, "centerX", 0),
                    GetDoubleOrDefault(body, "centerY", 0),
                    GetDoubleOrDefault(body, "centerZ", 0),
                    GetDoubleOrDefault(body, "axisX", 1),
                    GetDoubleOrDefault(body, "axisY", 0),
                    GetDoubleOrDefault(body, "axisZ", 0),
                    GetDoubleOrDefault(body, "referenceRadialX", 0),
                    GetDoubleOrDefault(body, "referenceRadialY", 1),
                    GetDoubleOrDefault(body, "referenceRadialZ", 0),
                    GetDouble(body, "inputAngleDeg"),
                    readList("stages", true),
                    readList("outputComponents", false));
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "多级行星轮系姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] PlanetaryTrainPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"多级行星轮系姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleRadialSlidersPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                var sliders = new List<Dictionary<string, object>>();
                var rawSliders = body.ContainsKey("sliders")
                    ? body["sliders"] as System.Collections.IEnumerable
                    : null;
                if (rawSliders == null)
                    throw new ArgumentException(
                        "sliders 必须是对象数组");
                foreach (var item in rawSliders)
                {
                    var slider = item as Dictionary<string, object>;
                    if (slider == null)
                        throw new ArgumentException(
                            "sliders 的每一项必须是对象");
                    sliders.Add(slider);
                }
                var result =
                    _commands.SetSynchronizedRadialSlidersPose(
                        GetDoubleOrDefault(body, "centerX", 0),
                        GetDoubleOrDefault(body, "centerY", 0),
                        GetDoubleOrDefault(body, "centerZ", 0),
                        GetDoubleOrDefault(body, "axisX", 1),
                        GetDoubleOrDefault(body, "axisY", 0),
                        GetDoubleOrDefault(body, "axisZ", 0),
                        GetDoubleOrDefault(
                            body, "referenceRadialX", 0),
                        GetDoubleOrDefault(
                            body, "referenceRadialY", 1),
                        GetDoubleOrDefault(
                            body, "referenceRadialZ", 0),
                        GetDouble(body, "radialDistance"),
                        sliders);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "同步径向滑块姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] RadialSlidersPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"同步径向滑块姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleIndexedRotaryPose(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                Func<string, List<Dictionary<string, object>>> readList =
                    key =>
                    {
                        var entries =
                            new List<Dictionary<string, object>>();
                        if (!body.ContainsKey(key))
                            return entries;
                        var raw = body[key]
                            as System.Collections.IEnumerable;
                        if (raw == null)
                            throw new ArgumentException(
                                $"{key} 必须是数组");
                        foreach (var item in raw)
                        {
                            var entry =
                                item as Dictionary<string, object>;
                            if (entry == null)
                                throw new ArgumentException(
                                    $"{key} 的每一项必须是对象");
                            entries.Add(entry);
                        }
                        return entries;
                    };
                Func<string, double> optionalCoordinate = key =>
                    body.ContainsKey(key)
                        ? GetDouble(body, key)
                        : double.NaN;
                var result = _commands.SetIndexedRotaryPose(
                    GetString(body, "driverComponent"),
                    GetString(body, "indexedComponent"),
                    optionalCoordinate("driverX"),
                    optionalCoordinate("driverY"),
                    optionalCoordinate("driverZ"),
                    optionalCoordinate("indexedX"),
                    optionalCoordinate("indexedY"),
                    optionalCoordinate("indexedZ"),
                    GetDoubleOrDefault(body, "axisX", 0),
                    GetDoubleOrDefault(body, "axisY", 0),
                    GetDoubleOrDefault(body, "axisZ", 1),
                    GetDouble(body, "inputAngleDeg"),
                    body.ContainsKey("slotCount")
                        ? Convert.ToInt32(body["slotCount"])
                        : 4,
                    GetDoubleOrDefault(
                        body,
                        "engagementStartDeg",
                        120),
                    GetDoubleOrDefault(
                        body,
                        "engagementEndDeg",
                        240),
                    GetBoolOrDefault(body, "reverseOutput", false),
                    readList("continuousComponents"),
                    readList("indexedComponents"));
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "间歇分度机构姿态已更新",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine(
                    $"[错误] IndexedRotaryPose 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"间歇分度机构姿态失败: {ex.Message}",
                });
            }
        }

        private static void HandleFixComponent(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                _commands.FixComponent(comp);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"组件已固定: {comp}",
                    componentName = comp,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] FixComponent 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"固定组件失败: {ex.Message}",
                });
            }
        }

        private static void HandleAssemblyObservation(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.GetAssemblyObservation();
                SendJson(resp, 200, new
                {
                    success = true,
                    message = "装配体观察完成",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] AssemblyObservation 失败: {ex.Message}");
                SendJson(resp, 500, new
                {
                    success = false,
                    message = $"装配体观察失败: {ex.Message}",
                });
            }
        }

        private static void HandleStartSpin(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                double px = GetDoubleOrDefault(body, "axisPointX", 0);
                double py = GetDoubleOrDefault(body, "axisPointY", 0);
                double pz = GetDoubleOrDefault(body, "axisPointZ", 0);
                double dx = GetDoubleOrDefault(body, "axisDirX", 0);
                double dy = GetDoubleOrDefault(body, "axisDirY", 0);
                double dz = GetDoubleOrDefault(body, "axisDirZ", 1);
                double stepDeg = GetDoubleOrDefault(body, "anglePerStepDeg", 5);
                int delay = GetInt(body, "stepDelayMs", 100);
                var result = _commands.StartSpin(comp, px, py, pz, dx, dy, dz, stepDeg, delay);
                SendJson(resp, 200, new { success = true, message = $"'{comp}' 开始持续旋转", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] StartSpin 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"启动旋转失败: {ex.Message}" });
            }
        }

        private static void HandleStartSpinGearPair(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string driver = GetString(body, "driverName");
                string driven = GetString(body, "drivenName");
                double dvAxX = GetDouble(body, "driverAxisX");
                double dvAxY = GetDouble(body, "driverAxisY");
                double dvAxZ = GetDoubleOrDefault(body, "driverAxisZ", 0);
                double dnAxX = GetDouble(body, "drivenAxisX");
                double dnAxY = GetDouble(body, "drivenAxisY");
                double dnAxZ = GetDoubleOrDefault(body, "drivenAxisZ", 0);
                double dirX = GetDoubleOrDefault(body, "axisDirX", 0);
                double dirY = GetDoubleOrDefault(body, "axisDirY", 0);
                double dirZ = GetDoubleOrDefault(body, "axisDirZ", 1);
                double stepDeg = GetDoubleOrDefault(body, "driverAnglePerStepDeg", 5);
                double ratio = GetDouble(body, "gearRatio");
                int delay = GetInt(body, "stepDelayMs", 100);
                var result = _commands.StartSpinGearPair(
                    driver, dvAxX, dvAxY, dvAxZ,
                    driven, dnAxX, dnAxY, dnAxZ,
                    dirX, dirY, dirZ,
                    stepDeg, ratio, delay);
                SendJson(resp, 200, new { success = true, message = $"齿轮对开始持续联动旋转", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] StartSpinGearPair 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleStopSpin(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.StopSpin();
                SendJson(resp, 200, new { success = true, message = "已停止", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleMotionStudyProbe(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.ProbeMotionStudyCapabilities();
                SendJson(resp, 200, new { success = true, message = "Motion Study 能力探测完成", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] Motion Study 探针失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleCreateGearMotor(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string componentName = GetString(body, "componentName");
                double rpm = GetDoubleOrDefault(body, "rpm", 60);
                double durationSeconds = GetDoubleOrDefault(body, "durationSeconds", 5);
                bool reverseDirection = GetBoolOrDefault(body, "reverseDirection", false);
                var result = _commands.CreateGearMotor(
                    componentName, rpm, durationSeconds, reverseDirection);
                SendJson(resp, 200, new { success = true, message = "齿轮旋转马达已创建", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 创建齿轮马达失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandlePlayMotionStudy(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.PlayMotionStudy();
                SendJson(resp, 200, new { success = true, message = "Motion Study 已开始播放", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 播放 Motion Study 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleSetMotionStudyTime(
            HttpListenerRequest req,
            HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                double seconds = GetDouble(body, "seconds");
                var result = _commands.SetMotionStudyTime(seconds);
                SendJson(resp, 200, new
                {
                    success = true,
                    message = $"Motion Study 已定位到 {seconds:F3}s",
                    result,
                });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] Motion Study 定位失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleStopMotionStudy(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.StopMotionStudy();
                SendJson(resp, 200, new { success = true, message = "Motion Study 已停止", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 停止 Motion Study 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleMotionStudyStatus(HttpListenerResponse resp)
        {
            try
            {
                var result = _commands.GetMotionStudyStatus();
                SendJson(resp, 200, new { success = true, message = "OK", result });
            }
            catch (Exception ex)
            {
                SendJson(resp, 500, new { success = false, message = ex.Message });
            }
        }

        private static void HandleUnfixComponent(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                _commands.UnfixComponent(comp);
                SendJson(resp, 200, new { success = true, message = $"'{comp}' 已浮动 (Float)" });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] UnfixComponent 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"浮动组件失败: {ex.Message}" });
            }
        }

        private static void HandleAssemblyOpen(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string assemblyPath = GetString(body, "assemblyPath");
                _commands.OpenAssembly(assemblyPath);
                SendJson(resp, 200, new { success = true, message = $"装配体已打开: {assemblyPath}" });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] 打开装配体失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"打开装配体失败: {ex.Message}" });
            }
        }

        private static void HandleRotateComponent(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                double px = GetDoubleOrDefault(body, "axisPointX", 0);
                double py = GetDoubleOrDefault(body, "axisPointY", 0);
                double pz = GetDoubleOrDefault(body, "axisPointZ", 0);
                // 默认绕装配体 Z 轴
                double dx = GetDoubleOrDefault(body, "axisDirX", 0);
                double dy = GetDoubleOrDefault(body, "axisDirY", 0);
                double dz = GetDoubleOrDefault(body, "axisDirZ", 1);
                double angleDeg = GetDoubleOrDefault(body, "angleDegrees", 0);
                double angleRad = angleDeg * Math.PI / 180.0;
                var result = _commands.RotateComponent(comp, px, py, pz, dx, dy, dz, angleRad);
                SendJson(resp, 200, new { success = true, message = $"'{comp}' 已旋转 {angleDeg}°", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] RotateComponent 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"旋转组件失败: {ex.Message}" });
            }
        }

        private static void HandleRotateComponentByDrag(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                double px = GetDoubleOrDefault(body, "axisPointX", 0);
                double py = GetDoubleOrDefault(body, "axisPointY", 0);
                double pz = GetDoubleOrDefault(body, "axisPointZ", 0);
                double dx = GetDoubleOrDefault(body, "axisDirX", 0);
                double dy = GetDoubleOrDefault(body, "axisDirY", 0);
                double dz = GetDoubleOrDefault(body, "axisDirZ", 1);
                double angleDeg = GetDoubleOrDefault(body, "angleDegrees", 0);
                double angleRad = angleDeg * Math.PI / 180.0;
                var result = _commands.RotateComponentByDrag(comp, px, py, pz, dx, dy, dz, angleRad);
                SendJson(resp, 200, new { success = true, message = $"'{comp}' 已通过 DragOperator 旋转 {angleDeg}°", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] RotateComponentByDrag 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"DragOperator 旋转失败: {ex.Message}" });
            }
        }

        private static void HandleAnimateRotateComponent(HttpListenerRequest req, HttpListenerResponse resp)
        {
            try
            {
                var body = ReadJsonBody(req);
                string comp = GetString(body, "componentName");
                double px = GetDoubleOrDefault(body, "axisPointX", 0);
                double py = GetDoubleOrDefault(body, "axisPointY", 0);
                double pz = GetDoubleOrDefault(body, "axisPointZ", 0);
                double dx = GetDoubleOrDefault(body, "axisDirX", 0);
                double dy = GetDoubleOrDefault(body, "axisDirY", 0);
                double dz = GetDoubleOrDefault(body, "axisDirZ", 1);
                double totalDeg = GetDoubleOrDefault(body, "totalAngleDegrees", 360);
                int steps = GetInt(body, "steps", 36);
                int delay = GetInt(body, "stepDelayMs", 50);
                var result = _commands.AnimateRotateComponent(comp, px, py, pz, dx, dy, dz, totalDeg, steps, delay);
                SendJson(resp, 200, new { success = true, message = $"'{comp}' 动画旋转完成 ({totalDeg}° / {steps} 步)", result });
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[错误] AnimateRotateComponent 失败: {ex.Message}\n{ex.StackTrace}");
                SendJson(resp, 500, new { success = false, message = $"动画旋转失败: {ex.Message}" });
            }
        }

        #endregion

        #region JSON 辅助方法

        /// <summary>
        /// 读取请求体并反序列化为 Dictionary。
        /// </summary>
        private static Dictionary<string, object> ReadJsonBody(HttpListenerRequest req)
        {
            if (!req.HasEntityBody)
                return new Dictionary<string, object>();

            using (var reader = new StreamReader(req.InputStream, req.ContentEncoding))
            {
                var json = reader.ReadToEnd();
                if (string.IsNullOrWhiteSpace(json))
                    return new Dictionary<string, object>();

                var result = _json.Deserialize<Dictionary<string, object>>(json);
                return result ?? new Dictionary<string, object>();
            }
        }

        /// <summary>
        /// 从字典中获取 double 值，找不到则抛出 KeyNotFoundException。
        /// </summary>
        private static double GetDouble(Dictionary<string, object> body, string key)
        {
            if (!body.ContainsKey(key))
                throw new KeyNotFoundException($"缺少必要参数: {key}");

            return Convert.ToDouble(body[key]);
        }

        /// <summary>
        /// 从字典中获取 double 值，找不到则返回默认值。
        /// </summary>
        private static double GetDoubleOrDefault(Dictionary<string, object> body, string key, double defaultValue)
        {
            if (!body.ContainsKey(key))
                return defaultValue;

            return Convert.ToDouble(body[key]);
        }

        /// <summary>
        /// 从字典中获取 int 值，找不到则返回默认值。
        /// </summary>
        private static int GetInt(Dictionary<string, object> body, string key, int defaultValue)
        {
            if (!body.ContainsKey(key))
                return defaultValue;

            return Convert.ToInt32(body[key]);
        }

        /// <summary>
        /// 从字典中获取 bool 值，找不到则返回默认值。
        /// </summary>
        private static bool GetBoolOrDefault(Dictionary<string, object> body, string key, bool defaultValue)
        {
            if (!body.ContainsKey(key))
                return defaultValue;

            return Convert.ToBoolean(body[key]);
        }

        /// <summary>
        /// 从字典中获取 string 值，找不到则抛出 KeyNotFoundException。
        /// </summary>
        private static string GetString(Dictionary<string, object> body, string key)
        {
            if (!body.ContainsKey(key))
                throw new KeyNotFoundException($"缺少必要参数: {key}");

            return body[key]?.ToString() ?? "";
        }

        /// <summary>
        /// 从字典中获取 string 值，找不到则返回默认值。
        /// </summary>
        private static string GetStringOrDefault(Dictionary<string, object> body, string key, string defaultValue)
        {
            if (!body.ContainsKey(key))
                return defaultValue;

            return body[key]?.ToString() ?? defaultValue;
        }

        /// <summary>
        /// 发送 JSON 响应。
        /// </summary>
        private static void SendJson(HttpListenerResponse resp, int statusCode, object data)
        {
            resp.StatusCode = statusCode;
            resp.ContentType = "application/json; charset=utf-8";

            var json = _json.Serialize(data);
            var buffer = Encoding.UTF8.GetBytes(json);

            resp.ContentLength64 = buffer.Length;
            resp.OutputStream.Write(buffer, 0, buffer.Length);
            resp.Close();
        }

        #endregion
    }
}
