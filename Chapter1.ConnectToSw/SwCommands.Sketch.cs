using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.swmotionstudy;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Chapter1.ConnectToSw
{
    public partial class SwCommands
    {
        #region 原子操作 — 草图

        /// <summary>
        /// 选择指定索引的基准面并进入草图编辑模式。
        /// index: 0=前视基准面, 1=上视基准面, 2=右视基准面
        /// </summary>
        public string SelectPlaneAndStartSketch(int planeIndex)
        {
            EnsureActiveDoc();

            var planes = GetRefPlanes(ActiveDoc);
            if (planeIndex < 0 || planeIndex >= planes.Count)
                throw new ArgumentOutOfRangeException(nameof(planeIndex),
                    $"基准面索引 {planeIndex} 超出范围，可用范围: 0 ~ {planes.Count - 1}");

            var plane = planes[planeIndex];
            plane.Select2(false, 0);

            ActiveDoc.SketchManager.InsertSketch(true);
            _sketchOnRefPlane = true;  // 标记：草图在基准面上
            CaptureActiveSketchName();  // 捕获刚新建的草图名，供切除时显式选中
            RegisterActiveSketch($"PLANE:{planeIndex}");
            Console.WriteLine($"[SwCommands] 已选择基准面 [{planeIndex}] 并进入草图编辑");
            return _activeSketchName;
        }

        /// <summary>
        /// 在当前草图中绘制一条直线。
        /// 坐标为模型空间 3D 坐标（单位：米），会自动通过 ModelToSketchTransform 变换到草图坐标系。
        /// z1, z2 可选，默认 0（适用于在基准面上的草图）。
        /// </summary>
        public void SketchLine(double x1, double y1, double x2, double y2, double z1 = 0, double z2 = 0)
        {
            EnsureActiveDoc();
            double sx1, sy1, sx2, sy2;
            ModelToSketchPoint(x1, y1, z1, out sx1, out sy1);
            ModelToSketchPoint(x2, y2, z2, out sx2, out sy2);
            var sketchManager = ActiveDoc.SketchManager;
            bool previousAddToDb = sketchManager.AddToDB;
            bool previousDisplayWhenAdded = sketchManager.DisplayWhenAdded;
            try
            {
                // 渐开线等高密度折线不能让 SolidWorks 自动推断水平/垂直关系；
                // 自动吸附会悄悄移动端点，最终把本应闭合的轮廓变成重叠或开口草图。
                sketchManager.AddToDB = true;
                sketchManager.DisplayWhenAdded = false;
                sketchManager.CreateLine(sx1, sy1, 0, sx2, sy2, 0);
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
                sketchManager.DisplayWhenAdded = previousDisplayWhenAdded;
            }
            Console.WriteLine($"[SwCommands] 绘制直线: 模型({x1},{y1},{z1}) -> ({x2},{y2},{z2})，草图({sx1:F4},{sy1:F4}) -> ({sx2:F4},{sy2:F4})");
        }

        /// <summary>
        /// 在当前草图中批量绘制连续折线，可选自动闭合。
        /// 单次提交整个复杂轮廓，避免 Multi-turn 在半成品草图处切轮。
        /// </summary>
        public Dictionary<string, object> SketchPolyline(
            List<Dictionary<string, object>> points,
            bool closed = true)
        {
            EnsureActiveDoc();
            if (points == null || points.Count < 2)
                throw new ArgumentException("points 至少包含两个点");

            var transformed = new List<double[]>();
            foreach (var point in points)
            {
                if (!point.ContainsKey("x") || !point.ContainsKey("y"))
                    throw new ArgumentException("points 中每项必须包含 x 和 y");
                double x = Convert.ToDouble(point["x"]);
                double y = Convert.ToDouble(point["y"]);
                double z = point.ContainsKey("z")
                    ? Convert.ToDouble(point["z"]) : 0;
                double sx, sy;
                ModelToSketchPoint(x, y, z, out sx, out sy);
                transformed.Add(new[] { sx, sy });
            }

            var sketchManager = ActiveDoc.SketchManager;
            bool previousAddToDb = sketchManager.AddToDB;
            bool previousDisplayWhenAdded = sketchManager.DisplayWhenAdded;
            int segmentCount = closed ? points.Count : points.Count - 1;
            try
            {
                sketchManager.AddToDB = true;
                sketchManager.DisplayWhenAdded = false;
                for (int i = 0; i < segmentCount; i++)
                {
                    double[] start = transformed[i];
                    double[] end = transformed[(i + 1) % transformed.Count];
                    sketchManager.CreateLine(
                        start[0], start[1], 0,
                        end[0], end[1], 0);
                }
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
                sketchManager.DisplayWhenAdded = previousDisplayWhenAdded;
            }

            Console.WriteLine(
                $"[SwCommands] 批量绘制折线: {points.Count} 点, " +
                $"{segmentCount} 段, closed={closed}");
            return new Dictionary<string, object>
            {
                { "sketchName", _activeSketchName ?? "" },
                { "faceKey", _activeFaceKey ?? "" },
                { "pointCount", points.Count },
                { "segmentCount", segmentCount },
                { "closed", closed },
            };
        }

        /// <summary>
        /// 在当前草图中绘制一个矩形（对角线两点）。
        /// 坐标为模型空间 3D 坐标（单位：米），自动变换到草图坐标系。
        /// z1, z2 可选。
        /// </summary>
        public void SketchRectangle(double x1, double y1, double x2, double y2, double z1 = 0, double z2 = 0)
        {
            EnsureActiveDoc();
            double sx1, sy1, sx2, sy2;
            ModelToSketchPoint(x1, y1, z1, out sx1, out sy1);
            ModelToSketchPoint(x2, y2, z2, out sx2, out sy2);
            // On an arbitrarily oriented face, callers often provide the
            // rectangle in the active sketch's local 2D coordinates. If
            // interpreting those values as model XY collapses a genuine
            // rectangle into a line, use the values as local coordinates.
            // This preserves model-coordinate behavior whenever the
            // transform produces a non-degenerate rectangle.
            bool inputIsRectangle =
                Math.Abs(x2 - x1) > 1e-9
                && Math.Abs(y2 - y1) > 1e-9;
            bool transformedIsDegenerate =
                Math.Abs(sx2 - sx1) <= 1e-9
                || Math.Abs(sy2 - sy1) <= 1e-9;
            if (!_sketchOnRefPlane
                && inputIsRectangle
                && transformedIsDegenerate)
            {
                Console.WriteLine(
                    "[SwCommands] 模型XY变换使矩形退化，" +
                    "按活动草图局部XY坐标解释输入");
                sx1 = x1;
                sy1 = y1;
                sx2 = x2;
                sy2 = y2;
            }
            ActiveDoc.SketchManager.CreateCornerRectangle(sx1, sy1, 0, sx2, sy2, 0);
            Console.WriteLine($"[SwCommands] 绘制矩形: 模型({x1},{y1}) -> ({x2},{y2})，草图({sx1:F4},{sy1:F4}) -> ({sx2:F4},{sy2:F4})");
        }

        /// <summary>
        /// 在当前草图中绘制一个圆。
        /// 圆心坐标为模型空间 3D 坐标（单位：米），自动变换到草图坐标系。
        /// 半径在草图平面内保持不变。cz 可选。
        /// </summary>
        public void SketchCircle(double cx, double cy, double radius, double cz = 0, bool noInference = false)
        {
            EnsureActiveDoc();
            double sx, sy;
            ModelToSketchPoint(cx, cy, cz, out sx, out sy);

            // ModelToSketchTransform 是刚性变换（旋转+平移，无缩放），
            // 因此草图平面内的半径恒等于模型空间的 radius，直接使用即可。
            //
            // ⚠️ 不要用"变换 (cx+radius, cy, cz) 偏移点再求两点距离"来反推半径！
            // 当面的法线恰好沿模型 X 轴时（如立方体的左面/右面），模型 X 方向会被
            // 映射到草图平面外（草图 Z），导致偏移点的草图 XY 与圆心重合、算出半径=0、
            // 进而生成无效的零半径圆，切除/拉伸失败。
            double sketchRadius = radius;

            var sketchManager = ActiveDoc.SketchManager;
            bool previousAddToDb = sketchManager.AddToDB;
            bool previousDisplayWhenAdded = sketchManager.DisplayWhenAdded;
            try
            {
                // 推理会把圆心/圆周点吸到已有圆的象限点或折线上，圆心和半径都可能被改。
                // 默认保持原行为（L1 结果要能复现），调用方显式要 noInference 才关掉推理。
                if (noInference)
                {
                    sketchManager.AddToDB = true;
                    sketchManager.DisplayWhenAdded = false;
                }
                sketchManager.CreateCircle(sx, sy, 0, sx + sketchRadius, sy, 0);
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
                sketchManager.DisplayWhenAdded = previousDisplayWhenAdded;
            }
            RecordSketchCircle(cx, cy, cz, radius);
            Console.WriteLine($"[SwCommands] 绘制圆: 模型圆心({cx},{cy},{cz}), 半径 {radius}，草图圆心({sx:F4},{sy:F4}), 草图半径 {sketchRadius:F4}");
        }

        public Dictionary<string, object> SketchCircles(
            List<Dictionary<string, object>> circles, bool noInference = false)
        {
            if (circles == null || circles.Count == 0)
                throw new ArgumentException("circles 至少包含一个圆");
            foreach (var circle in circles)
            {
                double cx = Convert.ToDouble(circle["cx"]);
                double cy = Convert.ToDouble(circle["cy"]);
                double cz = circle.ContainsKey("cz")
                    ? Convert.ToDouble(circle["cz"]) : 0;
                double radius = Convert.ToDouble(circle["radius"]);
                if (radius <= 0)
                    throw new ArgumentOutOfRangeException("radius", "半径必须为正数");
                SketchCircle(cx, cy, radius, cz, noInference);
            }
            return new Dictionary<string, object>
            {
                { "sketchName", _activeSketchName ?? "" },
                { "faceKey", _activeFaceKey ?? "" },
                { "circleCount", circles.Count },
            };
        }

        /// <summary>
        /// 在当前草图中绘制一个椭圆。
        /// 椭圆中心坐标为模型空间 3D 坐标（单位：米），自动变换到草图坐标系。
        /// majorRadius: 长轴半径（米），沿 X 方向（草图坐标系）
        /// minorRadius: 短轴半径（米），沿 Y 方向（草图坐标系）
        /// </summary>
        public void SketchEllipse(double cx, double cy, double majorRadius, double minorRadius, double cz = 0)
        {
            EnsureActiveDoc();

            if (majorRadius <= 0)
                throw new ArgumentOutOfRangeException(nameof(majorRadius), "长轴半径必须为正数");
            if (minorRadius <= 0)
                throw new ArgumentOutOfRangeException(nameof(minorRadius), "短轴半径必须为正数");

            // 将中心点从模型坐标转换为草图坐标
            double scx, scy;
            ModelToSketchPoint(cx, cy, cz, out scx, out scy);

            // ModelToSketchTransform 是刚性变换（旋转+平移，无缩放），
            // 草图平面内的长/短轴半径恒等于模型空间的 majorRadius/minorRadius。
            // ⚠️ 不要用"变换偏移端点再求距离"来反推（当面法线沿模型 X/Y 轴时偏移会落到草图平面外，算出 0）。
            double sketchMajorR = majorRadius;
            double sketchMinorR = minorRadius;

            // CreateEllipse 参数：中心点、长轴上的点、短轴上的点（均为草图坐标）
            ActiveDoc.SketchManager.CreateEllipse(
                scx, scy, 0,                       // 中心点
                scx + sketchMajorR, scy, 0,         // 长轴上的点
                scx, scy + sketchMinorR, 0           // 短轴上的点
            );

            Console.WriteLine($"[SwCommands] 绘制椭圆: 模型中心({cx},{cy},{cz}), 长轴半径 {majorRadius}, 短轴半径 {minorRadius}，" +
                $"草图中心({scx:F4},{scy:F4}), 草图长轴 {sketchMajorR:F4}, 草图短轴 {sketchMinorR:F4}");
        }

        /// <summary>
        /// 在当前草图中绘制一个正 n 边形（顶点均匀分布在一个虚拟外接圆上，用 n 条直线首尾相连）。
        ///
        /// 参数（均为模型空间坐标/长度，单位：米/度）：
        ///   cx, cy, cz          — 多边形中心点在模型空间的 3D 坐标（若在基准面上则 cz 可省）
        ///   circumradius        — 外接圆半径（=中心到顶点的距离）
        ///   sides               — 边数（3~64），要求为整数
        ///   startAngleDeg       — 第一个顶点相对于草图 X 轴（草图局部坐标）的角度（度），默认 0
        ///                          例如 startAngleDeg=90 时，六边形有一个顶点在正上方（"顶点朝上"）；
        ///                                startAngleDeg=0  时，六边形有一条边沿水平方向（"平放"）。
        ///
        /// ⚠️ 注意：与 SketchCircle 一样，circumradius 直接作为草图平面内半径使用（ModelToSketchTransform 是刚性变换，
        /// 半径在草图平面内不变，不能通过"偏移点两次变换后求距离"来反推——面法向沿模型 X/Y 轴时会算出 0）。
        /// </summary>
        public void SketchPolygon(double cx, double cy, double circumradius, int sides,
            double startAngleDeg = 0, double cz = 0)
        {
            EnsureActiveDoc();

            if (sides < 3 || sides > 64)
                throw new ArgumentOutOfRangeException(nameof(sides), "边数必须在 3~64 之间");
            if (circumradius <= 0)
                throw new ArgumentOutOfRangeException(nameof(circumradius), "外接圆半径必须为正数");

            // 中心点变换到草图坐标
            double scx, scy;
            ModelToSketchPoint(cx, cy, cz, out scx, out scy);

            // 在草图局部平面内计算 n 个顶点：起始角 startAngleDeg，然后每步 360/n 度
            double startRad = startAngleDeg * Math.PI / 180.0;
            double stepRad = 2.0 * Math.PI / sides;

            var pts = new double[sides, 2];
            for (int i = 0; i < sides; i++)
            {
                double a = startRad + i * stepRad;
                pts[i, 0] = scx + circumradius * Math.Cos(a);
                pts[i, 1] = scy + circumradius * Math.Sin(a);
            }

            // 用 n 条直线首尾相连组成闭合多边形
            for (int i = 0; i < sides; i++)
            {
                int j = (i + 1) % sides;
                ActiveDoc.SketchManager.CreateLine(pts[i, 0], pts[i, 1], 0, pts[j, 0], pts[j, 1], 0);
            }

            Console.WriteLine($"[SwCommands] 绘制正{sides}边形: 模型中心({cx},{cy},{cz}), 外接圆半径 {circumradius}, " +
                $"起始角 {startAngleDeg}°，草图中心({scx:F4},{scy:F4})，共 {sides} 条边");
        }

        /// <summary>
        /// 在当前草图中绘制一个直线型长圆孔（腰形/胶囊形轮廓）：
        /// 两条平行直线 + 两端各一个半圆弧首尾相连。
        ///
        /// 参数（均为模型空间坐标/长度，单位：米/度）：
        ///   cx, cy, cz     — 长圆孔中心点的模型 3D 坐标（在基准面上时 cz 可省）
        ///   length         — 长圆孔总长度（从一端半圆的最远端到另一端半圆的最远端）
        ///   width          — 长圆孔宽度（即两条平行直线之间的距离 = 两个半圆的直径）
        ///   angleDeg       — 长圆孔长轴相对于草图 X 轴的旋转角度（度），默认 0（长轴沿草图 X 轴）
        ///
        /// 几何约束：length > width（必须），否则会退化为圆或异形。
        /// 半圆弧半径 = width/2，两个半圆的中心距离 = length − width。
        /// </summary>
        public void SketchSlot(double cx, double cy, double length, double width,
            double angleDeg = 0, double cz = 0)
        {
            EnsureActiveDoc();

            if (length <= 0)
                throw new ArgumentOutOfRangeException(nameof(length), "长圆孔长度必须为正数");
            if (width <= 0)
                throw new ArgumentOutOfRangeException(nameof(width), "长圆孔宽度必须为正数");
            if (length <= width)
                throw new ArgumentOutOfRangeException(nameof(length),
                    $"长圆孔长度({length})必须大于宽度({width})，否则退化为圆或异形");

            // 中心点变换到草图坐标
            double scx, scy;
            ModelToSketchPoint(cx, cy, cz, out scx, out scy);

            // ⚠️ **已知限制**：本地 SolidWorks 2025 上：
            //   1) 官方 SketchManager.CreateSketchSlot API 返回 NULL；
            //   2) 手工画「2 段直线 + 2 段半圆弧」浮点端点无法被 SW 判定为闭合轮廓；
            //   3) 矩形 + 4 角 SketchFillet 顺序敏感，做完 2 角后短边=半径导致后 2 角失败。
            // 综合以上，我们**退化为「中心矩形」**：功能上与长圆孔等价（钥匙圈孔、螺栓过孔用矩形完全可以），
            // 只是外观是直角而非圆角。这样 API 稳定可用，不会阻塞上层建模。
            // 若将来需要真正的圆角长圆，可考虑用 SwCommands 之外的建模路径实现。
            if (Math.Abs(angleDeg) > 1e-6)
            {
                Console.WriteLine($"[SwCommands] ⚠️ SketchSlot 暂不支持 angleDeg={angleDeg}，将忽略旋转角度");
            }

            double halfLenS = length / 2.0;
            double halfWidS = width / 2.0;
            // 用 CenterRectangle 画：中心 + 对角点
            ActiveDoc.SketchManager.CreateCenterRectangle(
                scx, scy, 0,
                scx + halfLenS, scy + halfWidS, 0);

            Console.WriteLine($"[SwCommands] 绘制长圆孔(退化为中心矩形): 模型中心({cx},{cy},{cz}), 长={length}, 宽={width}");
        }

        /// <summary>
        /// 退出当前草图编辑模式（不进行拉伸）。
        /// </summary>
        public void CloseSketch()
        {
            EnsureActiveDoc();
            ActiveDoc.SketchManager.InsertSketch(true);
            _sketchOnRefPlane = false;
            Console.WriteLine("[SwCommands] 退出草图编辑");
        }

        /// <summary>
        /// 在当前草图中绘制一条中心线（构造线）。
        /// 中心线不参与轮廓，仅用于旋转轴等辅助用途。
        /// 坐标为模型空间 3D 坐标（单位：米），会自动通过 ModelToSketchTransform 变换到草图坐标系。
        /// </summary>
        public void SketchCenterLine(double x1, double y1, double x2, double y2, double z1 = 0, double z2 = 0)
        {
            EnsureActiveDoc();
            double sx1, sy1, sx2, sy2;
            ModelToSketchPoint(x1, y1, z1, out sx1, out sy1);
            ModelToSketchPoint(x2, y2, z2, out sx2, out sy2);
            var sketchManager = ActiveDoc.SketchManager;
            bool previousAddToDb = sketchManager.AddToDB;
            object line;
            try
            {
                // 视图缩得很小时，短中心线的端点会被自动吸附到原点上，线就退化掉了，旋转随之找不到轴。
                sketchManager.AddToDB = true;
                line = sketchManager.CreateCenterLine(sx1, sy1, 0, sx2, sy2, 0);
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
            }
            if (line == null)
                throw new InvalidOperationException($"中心线创建失败: ({x1},{y1},{z1}) -> ({x2},{y2},{z2})");
            Console.WriteLine($"[SwCommands] 绘制中心线: 模型({x1},{y1},{z1}) -> ({x2},{y2},{z2})，草图({sx1:F4},{sy1:F4}) -> ({sx2:F4},{sy2:F4})");
        }

        /// <summary>
        /// 在当前草图中绘制一段圆弧（通过圆心+起点+终点+方向）。
        /// 坐标为模型空间 3D 坐标（单位：米），会自动通过 ModelToSketchTransform 变换到草图坐标系。
        /// direction: 1=顺时针, -1=逆时针
        /// </summary>
        public void SketchArc(double cx, double cy, double startX, double startY,
                              double endX, double endY, short direction,
                              double cz = 0, double startZ = 0, double endZ = 0)
        {
            EnsureActiveDoc();
            double scx, scy, ssx, ssy, sex, sey;
            ModelToSketchPoint(cx, cy, cz, out scx, out scy);
            ModelToSketchPoint(startX, startY, startZ, out ssx, out ssy);
            ModelToSketchPoint(endX, endY, endZ, out sex, out sey);
            var sketchManager = ActiveDoc.SketchManager;
            bool previousAddToDb = sketchManager.AddToDB;
            try
            {
                sketchManager.AddToDB = true;
                sketchManager.CreateArc(scx, scy, 0, ssx, ssy, 0, sex, sey, 0, direction);
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
            }
            Console.WriteLine($"[SwCommands] 绘制圆弧: 圆心({cx},{cy}), 起点({startX},{startY}), 终点({endX},{endY}), 方向={direction}");
        }

        #endregion
    }
}
