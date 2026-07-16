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
            Console.WriteLine("  POST /api/new-part                - 新建零件文档");
            Console.WriteLine("  POST /api/select-plane            - 选择基准面并进入草图");
            Console.WriteLine("  POST /api/sketch/line             - 画直线");
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

                case "/api/new-part":
                    RequirePost(method, resp, () => HandleNewPart(resp));
                    break;

                case "/api/select-plane":
                    RequirePost(method, resp, () => HandleSelectPlane(req, resp));
                    break;

                case "/api/sketch/line":
                    RequirePost(method, resp, () => HandleSketchLine(req, resp));
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
                    HandleModelObservation(resp);
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
                    SendJson(resp, 404, new { success = false, message = $"未知端点: {path}" });
                    break;
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

                _commands.SketchCircle(cx, cy, radius, cz);
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
                var result = _commands.SketchCircles(circles);
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

                var result = _commands.CircularPattern(count, axis, angleDeg, reverseDir, axisOriginX, axisOriginY, axisOriginZ);
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

        private static void HandleModelObservation(HttpListenerResponse resp)
        {
            try
            {
                var snapshot = _commands.GetModelObservation();
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
                SendJson(resp, 200, new { success = true, message = $"已加入组件: {compName}", componentName = compName });
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
                double rx1 = GetDouble(body, "rx1"); double ry1 = GetDouble(body, "ry1"); double rz1 = GetDouble(body, "rz1");
                double dx1 = GetDouble(body, "dx1"); double dy1 = GetDouble(body, "dy1"); double dz1 = GetDouble(body, "dz1");
                string comp2 = GetString(body, "component2");
                double rx2 = GetDouble(body, "rx2"); double ry2 = GetDouble(body, "ry2"); double rz2 = GetDouble(body, "rz2");
                double dx2 = GetDouble(body, "dx2"); double dy2 = GetDouble(body, "dy2"); double dz2 = GetDouble(body, "dz2");
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
