using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System;
using System.Collections.Generic;

namespace Chapter1.ConnectToSw
{
    public partial class SwCommands
    {
        #region workflow 层 — 3D 草图路径、草图点

        /// <summary>
        /// 新建一张 3D 草图作为扫描路径 / 放样引导线，画完即退出草图。坐标为模型坐标（米）。
        /// spline=false：折线，相邻段端点合并；cornerRadius &gt; 0 时在每个内部转角加草图圆角。
        /// spline=true：一条通过全部点的样条（螺旋线等由调用方采样）。
        /// </summary>
        public Dictionary<string, object> Sketch3DPath(List<double[]> points, bool spline, double cornerRadius)
        {
            EnsureActiveDoc();
            if (points == null || points.Count < 2)
                throw new ArgumentException("3D 路径至少需要 2 个点");
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            var sketchManager = ActiveDoc.SketchManager;
            sketchManager.Insert3DSketch(true);
            if (sketchManager.ActiveSketch == null)
                throw new InvalidOperationException("无法新建 3D 草图");
            _activeSketchName = (LastFeatureOfType("3DProfileFeature") ?? "");
            try
            {
                return Draw3DPath(points, spline, cornerRadius);
            }
            finally
            {
                if (sketchManager.ActiveSketch != null)
                    sketchManager.Insert3DSketch(true);
                ActiveDoc.ClearSelection2(true);
            }
        }

        private Dictionary<string, object> Draw3DPath(List<double[]> points, bool spline, double cornerRadius)
        {
            var sketchManager = ActiveDoc.SketchManager;
            var created = new List<ISketchSegment>();
            int filleted = 0;
            bool previousAddToDb = sketchManager.AddToDB;
            bool previousDisplay = sketchManager.DisplayWhenAdded;
            try
            {
                sketchManager.AddToDB = true;
                sketchManager.DisplayWhenAdded = false;
                if (spline)
                {
                    var data = new double[points.Count * 3];
                    for (int i = 0; i < points.Count; i++)
                    {
                        data[3 * i] = points[i][0];
                        data[3 * i + 1] = points[i][1];
                        data[3 * i + 2] = points[i][2];
                    }
                    var item = sketchManager.CreateSpline2(data, true) as ISketchSegment;
                    if (item == null)
                        throw new InvalidOperationException("样条创建失败（点可能重合）");
                    created.Add(item);
                }
                else
                {
                    for (int i = 0; i + 1 < points.Count; i++)
                    {
                        var a = points[i];
                        var b = points[i + 1];
                        var item = sketchManager.CreateLine(a[0], a[1], a[2], b[0], b[1], b[2]) as ISketchSegment;
                        if (item == null)
                            throw new InvalidOperationException($"第 {i} 段直线创建失败（两点可能重合）");
                        created.Add(item);
                    }
                }
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
                sketchManager.DisplayWhenAdded = previousDisplay;
            }

            int merged = 0;
            var corners = new List<ISketchPoint>();
            for (int i = 0; i + 1 < created.Count; i++)
            {
                var a = EndpointNear3D(created[i], points[i + 1]);
                var b = EndpointNear3D(created[i + 1], points[i + 1]);
                if (a == null || b == null) continue;
                ActiveDoc.ClearSelection2(true);
                if (!a.Select4(false, null) || !b.Select4(true, null)) continue;
                ActiveDoc.SketchAddConstraints("sgMERGEPOINTS");
                merged++;
                corners.Add(EndpointNear3D(created[i], points[i + 1]));
            }
            if (!spline && cornerRadius > 0)
            {
                foreach (var corner in corners)
                {
                    ActiveDoc.ClearSelection2(true);
                    if (corner == null || !corner.Select4(false, null)) continue;
                    if (sketchManager.CreateFillet(
                            cornerRadius, (int)swConstrainedCornerAction_e.swConstrainedCornerKeepGeometry) != null)
                        filleted++;
                }
            }
            if (!spline && cornerRadius > 0 && filleted < corners.Count)
                throw new InvalidOperationException(
                    $"3D 路径转角圆角只成功 {filleted}/{corners.Count} 个：圆角半径可能大于相邻线段长度");
            Console.WriteLine(
                $"[SwCommands] 3D 路径 {_activeSketchName}: {(spline ? "样条" : "折线")} {points.Count} 点，" +
                $"合并 {merged}，圆角 {filleted}");
            return new Dictionary<string, object>
            {
                { "sketchName", _activeSketchName },
                { "segmentCount", created.Count },
                { "mergedJoints", merged },
                { "filleted", filleted },
            };
        }

        /// <summary>在当前草图中画一个草图点（模型坐标，米），用作放样收口的点截面。</summary>
        public Dictionary<string, object> SketchPoint(double x, double y, double z)
        {
            EnsureActiveDoc();
            if (ActiveDoc.SketchManager.ActiveSketch == null)
                throw new InvalidOperationException("当前没有打开的草图");
            double sx, sy;
            ModelToSketchPoint(x, y, z, out sx, out sy);
            var point = ActiveDoc.SketchManager.CreatePoint(sx, sy, 0);
            if (point == null)
                throw new InvalidOperationException("草图点创建失败");
            return new Dictionary<string, object> { { "sketchName", _activeSketchName ?? "" } };
        }

        private string LastFeatureOfType(string typeName)
        {
            string last = null;
            var feat = ActiveDoc.FirstFeature() as IFeature;
            while (feat != null)
            {
                if (feat.GetTypeName2() == typeName)
                    last = feat.Name;
                feat = feat.GetNextFeature() as IFeature;
            }
            return last;
        }

        private static ISketchPoint EndpointNear3D(ISketchSegment segment, double[] point)
        {
            ISketchPoint start = null, end = null;
            int type = segment.GetType();
            if (type == (int)swSketchSegments_e.swSketchLINE)
            {
                var line = (ISketchLine)segment;
                start = line.GetStartPoint2() as ISketchPoint;
                end = line.GetEndPoint2() as ISketchPoint;
            }
            else if (type == (int)swSketchSegments_e.swSketchARC)
            {
                var arc = (ISketchArc)segment;
                start = arc.GetStartPoint2() as ISketchPoint;
                end = arc.GetEndPoint2() as ISketchPoint;
            }
            if (start == null) return end;
            if (end == null) return start;
            Func<ISketchPoint, double> distance = p =>
                Math.Pow(p.X - point[0], 2) + Math.Pow(p.Y - point[1], 2) + Math.Pow(p.Z - point[2], 2);
            return distance(start) <= distance(end) ? start : end;
        }

        #endregion
    }
}
