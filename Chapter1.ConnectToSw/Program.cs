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
            Console.WriteLine("  GET  /api/ping                 - 健康检查");
            Console.WriteLine("  POST /api/new-part             - 新建零件文档");
            Console.WriteLine("  POST /api/select-plane         - 选择基准面并进入草图");
            Console.WriteLine("  POST /api/sketch/line          - 画直线");
            Console.WriteLine("  POST /api/sketch/rectangle     - 画矩形");
            Console.WriteLine("  POST /api/sketch/circle        - 画圆");
            Console.WriteLine("  POST /api/feature/extrusion    - 拉伸");
            Console.WriteLine("  POST /api/close-sketch         - 退出草图");
            Console.WriteLine("  POST /api/create-box           - 组合: 创建长方体");
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
                    catch { /* 忽略响应写入失败 */ }
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

                case "/api/feature/extrusion":
                    RequirePost(method, resp, () => HandleExtrusion(req, resp));
                    break;

                case "/api/close-sketch":
                    RequirePost(method, resp, () => HandleCloseSketch(resp));
                    break;

                case "/api/create-box":
                    RequirePost(method, resp, () => HandleCreateBox(req, resp));
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

                _commands.SketchLine(x1, y1, x2, y2);
                SendJson(resp, 200, new { success = true, message = $"绘制直线: ({x1}, {y1}) -> ({x2}, {y2})" });
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

                _commands.SketchRectangle(x1, y1, x2, y2);
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

                if (radius <= 0)
                    throw new ArgumentOutOfRangeException("radius", "半径必须为正数");

                _commands.SketchCircle(cx, cy, radius);
                SendJson(resp, 200, new { success = true, message = $"绘制圆: 圆心({cx}, {cy}), 半径 {radius}" });
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

                _commands.Extrusion(depth);
                SendJson(resp, 200, new { success = true, message = $"拉伸完成，深度: {depth} 米" });
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
        /// 从字典中获取 int 值，找不到则返回默认值。
        /// </summary>
        private static int GetInt(Dictionary<string, object> body, string key, int defaultValue)
        {
            if (!body.ContainsKey(key))
                return defaultValue;

            return Convert.ToInt32(body[key]);
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
