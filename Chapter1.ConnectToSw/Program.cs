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

                _commands.SelectPlaneAndStartSketch(index);
                SendJson(resp, 200, new { success = true, message = $"已选择基准面 [{index}] 并进入草图编辑" });
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

                _commands.Extrusion(depth, flipDirection, midPlane);
                SendJson(resp, 200, new { success = true, message = $"拉伸完成，深度: {depth} 米, 方向翻转: {flipDirection}, 中间面: {midPlane}" });
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

                var result = _commands.CircularPattern(count, axis, angleDeg, reverseDir);
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

                _commands.CutExtrusion(depth, endCondition, flipDirection);
                SendJson(resp, 200, new { success = true, message = $"切除拉伸完成，深度: {depth} 米, 终止条件: {endCondition}" });
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
