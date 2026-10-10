using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System;
using System.Collections.Generic;

namespace Chapter1.ConnectToSw
{
    public partial class SwCommands
    {
        #region workflow 层 — 闭合轮廓、参考基准面、镜像

        /// <summary>
        /// 绘制由直线和三点圆弧首尾相接组成的轮廓，并把相邻段的端点合并。
        /// 分别创建的直线/圆弧端点是独立的草图点，浮点误差下 SolidWorks 不认为轮廓闭合，
        /// 因此逐个接头添加「合并点」关系。坐标规则与其它草图接口一致（模型坐标，米）。
        /// segment: {type:"line", x1,y1,z1, x2,y2,z2} 或
        ///          {type:"arc",  x1,y1,z1(起点), x2,y2,z2(终点), xm,ym,zm(弧上一点)} 或
        ///          {type:"spline", x1,y1,z1, x2,y2,z2, points:[{x,y,z}, ...]}（通过全部点，首尾即起终点）
        /// </summary>
        public Dictionary<string, object> SketchProfile(
            List<Dictionary<string, object>> segments, bool closed = true)
        {
            EnsureActiveDoc();
            if (segments == null || segments.Count == 0)
                throw new ArgumentException("segments 不能为空");
            if (ActiveDoc.SketchManager.ActiveSketch == null)
                throw new InvalidOperationException("当前没有打开的草图");

            var sketchManager = ActiveDoc.SketchManager;
            var created = new List<ISketchSegment>();
            var junctions = new List<double[]>();
            bool previousAddToDb = sketchManager.AddToDB;
            bool previousDisplayWhenAdded = sketchManager.DisplayWhenAdded;
            int arcCount = 0, splineCount = 0;
            try
            {
                sketchManager.AddToDB = true;
                sketchManager.DisplayWhenAdded = false;
                foreach (var segment in segments)
                {
                    string type = segment.ContainsKey("type")
                        ? Convert.ToString(segment["type"]).ToLowerInvariant() : "line";
                    double[] start = SketchPointOf(segment, "x1", "y1", "z1");
                    double[] end = SketchPointOf(segment, "x2", "y2", "z2");
                    ISketchSegment item;
                    if (type == "arc")
                    {
                        double[] mid = SketchPointOf(segment, "xm", "ym", "zm");
                        item = sketchManager.Create3PointArc(
                            start[0], start[1], 0, end[0], end[1], 0, mid[0], mid[1], 0) as ISketchSegment;
                        arcCount++;
                    }
                    else if (type == "spline")
                    {
                        var raw = segment.ContainsKey("points") ? segment["points"] as System.Collections.IEnumerable : null;
                        if (raw == null)
                            throw new ArgumentException($"第 {created.Count} 段 spline 缺少 points");
                        var data = new List<double>();
                        foreach (var entry in raw)
                        {
                            var point = entry as Dictionary<string, object>;
                            if (point == null)
                                throw new ArgumentException("spline 的 points 每项须为 {x,y,z}");
                            double[] sketchPoint = SketchPointOf(point, "x", "y", "z");
                            data.Add(sketchPoint[0]);
                            data.Add(sketchPoint[1]);
                            data.Add(0);
                        }
                        if (data.Count < 6)
                            throw new ArgumentException($"第 {created.Count} 段 spline 至少需要 2 个点");
                        item = sketchManager.CreateSpline2(data.ToArray(), true) as ISketchSegment;
                        splineCount++;
                    }
                    else
                    {
                        item = sketchManager.CreateLine(
                            start[0], start[1], 0, end[0], end[1], 0) as ISketchSegment;
                    }
                    if (item == null)
                        throw new InvalidOperationException(
                            $"第 {created.Count} 段 {type} 创建失败（可能退化为零长度或三点共线）");
                    created.Add(item);
                    junctions.Add(end);
                }
            }
            finally
            {
                sketchManager.AddToDB = previousAddToDb;
                sketchManager.DisplayWhenAdded = previousDisplayWhenAdded;
            }

            int merged = 0;
            int joints = closed ? created.Count : created.Count - 1;
            for (int i = 0; i < joints; i++)
            {
                var a = EndpointNear(created[i], junctions[i]);
                var b = EndpointNear(created[(i + 1) % created.Count], junctions[i]);
                if (a == null || b == null) continue;
                ActiveDoc.ClearSelection2(true);
                if (!a.Select4(false, null) || !b.Select4(true, null)) continue;
                ActiveDoc.SketchAddConstraints("sgMERGEPOINTS");
                merged++;
            }
            ActiveDoc.ClearSelection2(true);

            Console.WriteLine(
                $"[SwCommands] 绘制轮廓: {created.Count} 段（圆弧 {arcCount}，样条 {splineCount}），合并端点 {merged}/{joints}");
            return new Dictionary<string, object>
            {
                { "sketchName", _activeSketchName ?? "" },
                { "segmentCount", created.Count },
                { "arcCount", arcCount },
                { "splineCount", splineCount },
                { "mergedJoints", merged },
                { "joints", joints },
            };
        }

        private double[] SketchPointOf(Dictionary<string, object> segment, string kx, string ky, string kz)
        {
            if (!segment.ContainsKey(kx) || !segment.ContainsKey(ky))
                throw new ArgumentException($"轮廓段缺少 {kx}/{ky}");
            double x = Convert.ToDouble(segment[kx]);
            double y = Convert.ToDouble(segment[ky]);
            double z = segment.ContainsKey(kz) ? Convert.ToDouble(segment[kz]) : 0;
            double sx, sy;
            ModelToSketchPoint(x, y, z, out sx, out sy);
            return new[] { sx, sy };
        }

        private static ISketchPoint EndpointNear(ISketchSegment segment, double[] point)
        {
            ISketchPoint start = null, end = null;
            if (segment.GetType() == (int)swSketchSegments_e.swSketchLINE)
            {
                var line = (ISketchLine)segment;
                start = line.GetStartPoint2() as ISketchPoint;
                end = line.GetEndPoint2() as ISketchPoint;
            }
            else if (segment.GetType() == (int)swSketchSegments_e.swSketchARC)
            {
                var arc = (ISketchArc)segment;
                start = arc.GetStartPoint2() as ISketchPoint;
                end = arc.GetEndPoint2() as ISketchPoint;
            }
            else if (segment.GetType() == (int)swSketchSegments_e.swSketchSPLINE)
            {
                var points = ((ISketchSpline)segment).GetPoints2() as object[];
                if (points != null && points.Length > 0)
                {
                    start = points[0] as ISketchPoint;
                    end = points[points.Length - 1] as ISketchPoint;
                }
            }
            if (start == null) return end;
            if (end == null) return start;
            double ds = Math.Pow(start.X - point[0], 2) + Math.Pow(start.Y - point[1], 2);
            double de = Math.Pow(end.X - point[0], 2) + Math.Pow(end.Y - point[1], 2);
            return ds <= de ? start : end;
        }

        private void ExitSketchIfActive()
        {
            if (ActiveDoc.SketchManager.ActiveSketch != null)
                ((ModelDoc2)ActiveDoc).InsertSketch2(true);
            _sketchOnRefPlane = false;
        }

        private IFace2 GetObservedFace(int faceIndex)
        {
            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");
            var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            int currentIndex = 0;
            foreach (object bodyObj in bodies ?? new object[0])
            {
                var faces = (bodyObj as IBody2)?.GetFaces() as object[];
                if (faces == null) continue;
                foreach (object faceObj in faces)
                {
                    if (currentIndex == faceIndex)
                        return faceObj as IFace2;
                    currentIndex++;
                }
            }
            throw new InvalidOperationException($"观察面索引 {faceIndex} 不存在；请重新读取 observation.faces");
        }

        /// <summary>
        /// 按引用选中基准面或实体平面：plane:&lt;0..2&gt;（默认基准面）、refplane:&lt;特征名&gt;、face:&lt;观察面索引&gt;。
        /// </summary>
        private bool SelectReference(string reference, bool append, int mark)
        {
            string text = (reference ?? "").Trim();
            int colon = text.IndexOf(':');
            if (colon <= 0)
                throw new ArgumentException($"引用 {reference} 应写成 plane:i / refplane:名称 / face:i");
            string kind = text.Substring(0, colon).ToLowerInvariant();
            string value = text.Substring(colon + 1).Trim();
            switch (kind)
            {
                case "plane":
                    var planes = GetRefPlanes(ActiveDoc);
                    int index = int.Parse(value);
                    if (index < 0 || index >= planes.Count)
                        throw new ArgumentOutOfRangeException(nameof(reference), $"基准面索引 {index} 超出范围");
                    return planes[index].Select2(append, mark);
                case "refplane":
                    var feature = ((IPartDoc)ActiveDoc).FeatureByName(value) as IFeature;
                    if (feature == null)
                        throw new InvalidOperationException($"找不到基准面 {value}");
                    return feature.Select2(append, mark);
                case "face":
                    var face = GetObservedFace(int.Parse(value));
                    var selectionManager = (ISelectionMgr)ActiveDoc.SelectionManager;
                    var data = (SelectData)selectionManager.CreateSelectData();
                    data.Mark = mark;
                    return ((IEntity)face).Select4(append, data);
                default:
                    throw new ArgumentException($"未知引用类型 {kind}");
            }
        }

        /// <summary>
        /// 创建与参考平面平行、相距 distance（米）的基准面。flip 切换到另一侧。
        /// 返回新基准面的特征名以及其局部坐标系（原点在平面上）。
        /// </summary>
        public Dictionary<string, object> CreateOffsetPlane(string reference, double distance, bool flip)
        {
            EnsureActiveDoc();
            if (distance < 0)
                throw new ArgumentOutOfRangeException(nameof(distance), "distance 不能为负，用 flip 切换方向");
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference(reference, false, 0))
                throw new InvalidOperationException($"无法选中参考 {reference}");

            int constraint = (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_Distance;
            if (flip)
                constraint |= (int)swRefPlaneReferenceConstraints_e.swRefPlaneReferenceConstraint_OptionFlip;
            var feature = ActiveDoc.FeatureManager.InsertRefPlane(constraint, distance, 0, 0, 0, 0) as IFeature;
            ActiveDoc.ClearSelection2(true);
            if (feature == null)
                throw new InvalidOperationException("SolidWorks 未能创建基准面");

            var refPlane = feature.GetSpecificFeature2() as IRefPlane;
            double[] transform = (double[])(refPlane?.Transform?.ArrayData ?? new double[16]);
            Console.WriteLine(
                $"[SwCommands] 创建基准面 {feature.Name}: 参考 {reference}, 距离 {distance}, flip={flip}");
            return new Dictionary<string, object>
            {
                { "featureName", feature.Name },
                { "rotation", new[] { transform[0], transform[1], transform[2], transform[3], transform[4],
                                       transform[5], transform[6], transform[7], transform[8] } },
                { "origin", new[] { transform[9], transform[10], transform[11] } },
            };
        }

        /// <summary>
        /// 两个基准面（plane:i / refplane:名称）的交线作为参考轴，返回轴的特征名。
        /// </summary>
        public Dictionary<string, object> CreateAxisFromPlanes(string first, string second)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference(first, false, 0) || !SelectReference(second, true, 0))
                throw new InvalidOperationException($"无法选中 {first} 和 {second}");
            bool created = ((ModelDoc2)ActiveDoc).InsertAxis2(true);
            ActiveDoc.ClearSelection2(true);
            if (!created)
                throw new InvalidOperationException("InsertAxis2 返回 false：两个平面可能平行");
            IFeature axisFeat = null;
            var f = ActiveDoc.FirstFeature() as IFeature;
            while (f != null)
            {
                if (f.GetTypeName2() == "RefAxis") axisFeat = f;
                f = f.GetNextFeature() as IFeature;
            }
            if (axisFeat == null)
                throw new InvalidOperationException("参考轴创建后未能在特征树中找到");
            Console.WriteLine($"[SwCommands] 由 {first} 与 {second} 创建参考轴 {axisFeat.Name}");
            return new Dictionary<string, object> { { "featureName", axisFeat.Name } };
        }

        /// <summary>
        /// 在指定名称的基准面上开草图。坐标按模型坐标传入，由 ModelToSketchTransform 换算。
        /// </summary>
        public Dictionary<string, object> SelectRefPlaneAndStartSketch(string planeName)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference($"refplane:{planeName}", false, 0))
                throw new InvalidOperationException($"无法选中基准面 {planeName}");
            ActiveDoc.SketchManager.InsertSketch(true);
            _sketchOnRefPlane = false;
            CaptureActiveSketchName();
            RegisterActiveSketch($"REFPLANE:{planeName}");
            Console.WriteLine($"[SwCommands] 在基准面 {planeName} 上开草图 {_activeSketchName}");
            return new Dictionary<string, object> { { "sketchName", _activeSketchName ?? "" } };
        }

        /// <summary>
        /// 关于平面镜像特征（featureNames）或整个实体（mirrorBody=true，结果合并）。
        /// </summary>
        public Dictionary<string, object> MirrorFeatures(
            List<string> featureNames, string planeReference, bool mirrorBody)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference(planeReference, false, 2))
                throw new InvalidOperationException($"无法选中镜像平面 {planeReference}");

            var selectionManager = (ISelectionMgr)ActiveDoc.SelectionManager;
            if (mirrorBody)
            {
                var bodies = ((IPartDoc)ActiveDoc).GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                if (bodies == null || bodies.Length == 0)
                    throw new InvalidOperationException("当前零件没有实体");
                foreach (object bodyObj in bodies)
                {
                    var data = (SelectData)selectionManager.CreateSelectData();
                    data.Mark = 256;
                    if (!((IBody2)bodyObj).Select2(true, data))
                        throw new InvalidOperationException("无法选中实体");
                }
            }
            else
            {
                if (featureNames == null || featureNames.Count == 0)
                    throw new ArgumentException("features 不能为空");
                foreach (var name in featureNames)
                {
                    var feature = ((IPartDoc)ActiveDoc).FeatureByName(name) as IFeature;
                    if (feature == null)
                        throw new InvalidOperationException($"找不到特征 {name}");
                    if (!feature.Select2(true, 1))
                        throw new InvalidOperationException($"无法选中特征 {name}");
                }
            }

            var mirror = ActiveDoc.FeatureManager.InsertMirrorFeature2(
                mirrorBody, false, mirrorBody, false,
                (int)swFeatureScope_e.swFeatureScope_AllBodies) as IFeature;
            ActiveDoc.ClearSelection2(true);
            if (mirror == null)
                throw new InvalidOperationException("SolidWorks 未能创建镜像特征");
            Console.WriteLine($"[SwCommands] 镜像 {mirror.Name}: 平面 {planeReference}, body={mirrorBody}");
            return new Dictionary<string, object>
            {
                { "featureName", mirror.Name },
                { "featureType", mirror.GetTypeName2() },
            };
        }

        #endregion
    }
}
