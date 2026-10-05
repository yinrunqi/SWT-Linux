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
        #region 原子操作 — 查询

        /// <summary>
        /// 获取特征树信息。
        /// 返回包含所有特征名称、类型、索引和尺寸列表的列表。
        /// 对于拉伸等特征，尺寸列表中会包含可编辑的尺寸参数（如拉伸深度）。
        /// </summary>
        public List<Dictionary<string, object>> GetFeatureTree()
        {
            EnsureActiveDoc();

            var features = new List<Dictionary<string, object>>();
            var feat = ActiveDoc.FirstFeature() as IFeature;
            int index = 0;

            while (feat != null)
            {
                var featureInfo = new Dictionary<string, object>
                {
                    { "index", index },
                    { "name", feat.Name },
                    { "type", feat.GetTypeName2() },
                    { "suppressed", feat.IsSuppressed() },
                };

                // 提取该特征的所有尺寸
                var dimensions = new List<Dictionary<string, object>>();
                var dispDim = feat.GetFirstDisplayDimension() as IDisplayDimension;
                while (dispDim != null)
                {
                    var dim = dispDim.GetDimension2(0);
                    if (dim != null)
                    {
                        // 尺寸全名格式: "尺寸名@特征名"
                        // GetSystemValue3 返回 object，实际可能是 double[] 数组
                        object rawVal = dim.GetSystemValue3(
                            (int)swInConfigurationOpts_e.swThisConfiguration, null);
                        double dimValue;
                        if (rawVal is double[])
                        {
                            var arr = (double[])rawVal;
                            dimValue = arr.Length > 0 ? arr[0] : 0;
                        }
                        else if (rawVal is double)
                        {
                            dimValue = (double)rawVal;
                        }
                        else
                        {
                            dimValue = Convert.ToDouble(rawVal);
                        }

                        dimensions.Add(new Dictionary<string, object>
                        {
                            { "name", dim.Name },
                            { "fullName", dim.FullName },
                            { "value", dimValue },
                        });
                    }
                    dispDim = feat.GetNextDisplayDimension(dispDim) as IDisplayDimension;
                }

                if (dimensions.Count > 0)
                    featureInfo["dimensions"] = dimensions;

                features.Add(featureInfo);
                feat = feat.GetNextFeature() as IFeature;
                index++;
            }

            Console.WriteLine($"[SwCommands] 获取特征树，共 {features.Count} 个特征");
            return features;
        }

        /// <summary>
        /// 获取当前零件实体的包围盒。
        /// 返回 { minX, minY, minZ, maxX, maxY, maxZ, sizeX, sizeY, sizeZ } 单位：米。
        /// </summary>
        public Dictionary<string, double> GetBodyBoundingBox()
        {
            EnsureActiveDoc();

            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");

            // 获取所有实体
            var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体（可能还未建模）");

            // ⚠️ 多实体零件：合并所有实体的包围盒
            //   老代码只取 bodies[0]，多实体零件下会漏掉其他实体（例如两个不相连的齿轮）
            //   现在遍历每个实体的 GetBodyBox()，取 min/max 的并集
            double minX = double.MaxValue, minY = double.MaxValue, minZ = double.MaxValue;
            double maxX = double.MinValue, maxY = double.MinValue, maxZ = double.MinValue;
            int bodyCount = 0;
            foreach (var bObj in bodies)
            {
                var b = bObj as IBody2;
                if (b == null) continue;
                var bx = b.GetBodyBox() as double[];
                if (bx == null || bx.Length < 6) continue;
                if (bx[0] < minX) minX = bx[0];
                if (bx[1] < minY) minY = bx[1];
                if (bx[2] < minZ) minZ = bx[2];
                if (bx[3] > maxX) maxX = bx[3];
                if (bx[4] > maxY) maxY = bx[4];
                if (bx[5] > maxZ) maxZ = bx[5];
                bodyCount++;
            }
            if (bodyCount == 0)
                throw new InvalidOperationException("无法获取任何实体的包围盒");

            var box = new double[] { minX, minY, minZ, maxX, maxY, maxZ };
            if (bodyCount > 1)
                Console.WriteLine($"[SwCommands] 检测到 {bodyCount} 个实体，合并包围盒");

            var result = new Dictionary<string, double>
            {
                { "minX", box[0] },
                { "minY", box[1] },
                { "minZ", box[2] },
                { "maxX", box[3] },
                { "maxY", box[4] },
                { "maxZ", box[5] },
                { "sizeX", Math.Abs(box[3] - box[0]) },
                { "sizeY", Math.Abs(box[4] - box[1]) },
                { "sizeZ", Math.Abs(box[5] - box[2]) },
            };

            Console.WriteLine($"[SwCommands] 包围盒: ({result["minX"]:F4}, {result["minY"]:F4}, {result["minZ"]:F4}) -> ({result["maxX"]:F4}, {result["maxY"]:F4}, {result["maxZ"]:F4})");
            Console.WriteLine($"[SwCommands] 尺寸: {result["sizeX"]:F4} x {result["sizeY"]:F4} x {result["sizeZ"]:F4} 米");
            return result;
        }

        /// <summary>
        /// 获取当前零件所有实体的总体积（立方米）。
        /// 用于精确判断切除/凸台效果：切除使体积减小，凸台使体积增大。
        /// </summary>
        public double GetBodyVolume()
        {
            EnsureActiveDoc();

            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");

            var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体（可能还未建模）");

            double totalVolume = 0;
            foreach (object bodyObj in bodies)
            {
                var body = bodyObj as IBody2;
                if (body == null) continue;
                // GetMassProperties: 返回 double[12]，索引 3 是体积（立方米）
                var mp = body.GetMassProperties(0) as double[];
                if (mp != null && mp.Length > 3)
                    totalVolume += mp[3];
            }

            Console.WriteLine($"[SwCommands] 实体总体积: {totalVolume:E6} 立方米 ({totalVolume * 1e9:F1} 立方毫米)");
            return totalVolume;
        }

        public List<Dictionary<string, object>> GetBodyFaces(int maxFaces = 200)
        {
            EnsureActiveDoc();
            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");
            var bodies = partDoc.GetBodies2(
                (int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                return new List<Dictionary<string, object>>();

            var bbox = GetBodyBoundingBox();
            var result = new List<Dictionary<string, object>>();
            foreach (object bodyObj in bodies)
            {
                var body = bodyObj as IBody2;
                var faces = body == null ? null : body.GetFaces() as object[];
                if (faces == null) continue;
                foreach (object faceObj in faces)
                {
                    if (result.Count >= maxFaces) return result;
                    var face = faceObj as IFace2;
                    if (face == null) continue;
                    var surface = face.IGetSurface();
                    bool planar = surface != null && surface.IsPlane();
                    string surfaceType = planar ? "plane" :
                        (surface != null && surface.IsCylinder() ? "cylinder" : "other");
                    double[] faceBox = null;
                    double area = 0;
                    try { faceBox = ((dynamic)face).GetBox() as double[]; }
                    catch { }
                    try { area = Convert.ToDouble(((dynamic)face).GetArea()); }
                    catch { }
                    if (faceBox == null || faceBox.Length < 6)
                        continue;
                    var center = new double[]
                    {
                        (faceBox[0] + faceBox[3]) / 2,
                        (faceBox[1] + faceBox[4]) / 2,
                        (faceBox[2] + faceBox[5]) / 2,
                    };
                    // IFace2.Normal 已考虑面相对曲面的朝向，平面上即实体外法向；
                    // PlaneParams 是曲面法向，可能朝内。
                    var normal = new double[] { 0, 0, 0 };
                    if (planar)
                    {
                        var faceNormal = face.Normal as double[];
                        if (faceNormal != null && faceNormal.Length >= 3)
                        {
                            normal = new double[] { faceNormal[0], faceNormal[1], faceNormal[2] };
                        }
                        else
                        {
                            var pp = (double[])surface.PlaneParams;
                            normal = new double[] { pp[0], pp[1], pp[2] };
                        }
                    }
                    string faceKey = planar
                        ? GetAxisFaceKey(center, normal, bbox)
                        : "";
                    var item = new Dictionary<string, object>
                    {
                        { "index", result.Count },
                        { "faceKey", faceKey },
                        { "surfaceType", surfaceType },
                        { "center", new Dictionary<string, double> {
                            { "x", center[0] }, { "y", center[1] }, { "z", center[2] }
                        }},
                        { "normal", new Dictionary<string, double> {
                            { "x", normal[0] }, { "y", normal[1] }, { "z", normal[2] }
                        }},
                        { "area", area },
                        { "box", new Dictionary<string, double> {
                            { "minX", faceBox[0] }, { "minY", faceBox[1] }, { "minZ", faceBox[2] },
                            { "maxX", faceBox[3] }, { "maxY", faceBox[4] }, { "maxZ", faceBox[5] }
                        }},
                        { "geometricSignature",
                            $"{surfaceType}:{center[0]:F6}:{center[1]:F6}:{center[2]:F6}:{area:F9}" },
                    };
                    if (surfaceType == "cylinder")
                    {
                        try
                        {
                            // CylinderParams: origin(3), axis(3), radius
                            var cp = surface.CylinderParams as double[];
                            if (cp != null && cp.Length >= 7)
                            {
                                item["radius"] = cp[6];
                                item["axisOrigin"] = new Dictionary<string, double> {
                                    { "x", cp[0] }, { "y", cp[1] }, { "z", cp[2] } };
                                item["axis"] = new Dictionary<string, double> {
                                    { "x", cp[3] }, { "y", cp[4] }, { "z", cp[5] } };
                            }
                        }
                        catch { }
                    }
                    try
                    {
                        var persist = ActiveDoc.Extension.GetPersistReference3(face) as byte[];
                        if (persist != null)
                            item["persistRef"] = Convert.ToBase64String(persist);
                    }
                    catch { }
                    result.Add(item);
                }
            }
            return result;
        }

        /// <summary>
        /// 直接读取模型中每个草图的真实几何（不依赖本服务记录的草图元数据），
        /// 因此对外部打开的 SLDPRT 同样有效。
        /// </summary>
        public List<Dictionary<string, object>> GetSketchGeometryStats()
        {
            EnsureActiveDoc();
            var result = new List<Dictionary<string, object>>();
            var feat = ActiveDoc.FirstFeature() as IFeature;
            int featureIndex = 0;
            while (feat != null)
            {
                string typeName = feat.GetTypeName2();
                if (typeName == "ProfileFeature" || typeName == "3DProfileFeature")
                {
                    var sketch = feat.GetSpecificFeature2() as ISketch;
                    if (sketch != null)
                        result.Add(DescribeSketchGeometry(feat, sketch, featureIndex, typeName));
                }
                feat = feat.GetNextFeature() as IFeature;
                featureIndex++;
            }
            return result;
        }

        private static Dictionary<string, object> DescribeSketchGeometry(
            IFeature feat, ISketch sketch, int featureIndex, string typeName)
        {
            int lines = 0, arcs = 0, circles = 0, ellipses = 0, splines = 0, other = 0;
            int constructionLines = 0, constructionOther = 0;
            var lineLengths = new List<double>();
            var segments = sketch.GetSketchSegments() as object[] ?? new object[0];
            foreach (var raw in segments)
            {
                var seg = raw as ISketchSegment;
                if (seg == null)
                    continue;
                var segType = (swSketchSegments_e)seg.GetType();
                if (seg.ConstructionGeometry)
                {
                    if (segType == swSketchSegments_e.swSketchLINE)
                        constructionLines++;
                    else
                        constructionOther++;
                    continue;
                }
                switch (segType)
                {
                    case swSketchSegments_e.swSketchLINE:
                        lines++;
                        lineLengths.Add(seg.GetLength());
                        break;
                    case swSketchSegments_e.swSketchARC:
                        var arc = seg as ISketchArc;
                        if (arc != null && arc.IsCircle() == 1)
                            circles++;
                        else
                            arcs++;
                        break;
                    case swSketchSegments_e.swSketchELLIPSE:
                        ellipses++;
                        break;
                    case swSketchSegments_e.swSketchSPLINE:
                        splines++;
                        break;
                    default:
                        other++;
                        break;
                }
            }

            // 5~12 条等长直线视为一个正多边形（多边形工具的典型产物）。
            bool regularPolygon = false;
            if (lines >= 5 && lines <= 12 && arcs == 0)
            {
                double min = lineLengths.Min(), max = lineLengths.Max();
                regularPolygon = min > 0 && (max - min) / max < 0.01;
            }

            var consumers = new List<Dictionary<string, object>>();
            var children = feat.GetChildren() as object[] ?? new object[0];
            foreach (var child in children)
            {
                var childFeat = child as IFeature;
                if (childFeat == null)
                    continue;
                consumers.Add(new Dictionary<string, object>
                {
                    { "name", childFeat.Name },
                    { "type", childFeat.GetTypeName2() },
                });
            }

            return new Dictionary<string, object>
            {
                { "featureIndex", featureIndex },
                { "name", feat.Name },
                { "type", typeName },
                { "lines", lines },
                { "arcs", arcs },
                { "circles", circles },
                { "ellipses", ellipses },
                { "splines", splines },
                { "otherSegments", other },
                { "constructionLines", constructionLines },
                { "constructionOther", constructionOther },
                { "regularPolygon", regularPolygon },
                { "consumers", consumers },
            };
        }

        public List<Dictionary<string, object>> GetSketchSummaries()
        {
            var result = new List<Dictionary<string, object>>();
            foreach (var pair in _sketchMetadata)
            {
                var source = pair.Value;
                var circles = source["circles"] as List<Dictionary<string, double>>;
                result.Add(new Dictionary<string, object>
                {
                    { "name", pair.Key },
                    { "faceKey", source["faceKey"] },
                    { "circleCount", circles == null ? 0 : circles.Count },
                    { "circles", circles ?? new List<Dictionary<string, double>>() },
                });
            }
            return result;
        }

        /// <summary>
        /// 每个实体的包围盒（米）与体积（mm³）。多于一个实体通常说明特征没有连上主体。
        /// </summary>
        public List<Dictionary<string, object>> GetBodySummaries()
        {
            var result = new List<Dictionary<string, object>>();
            var partDoc = ActiveDoc as IPartDoc;
            var bodies = partDoc?.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            foreach (var bObj in bodies ?? new object[0])
            {
                var body = bObj as IBody2;
                if (body == null) continue;
                var bx = body.GetBodyBox() as double[];
                var mp = body.GetMassProperties(0) as double[];
                var item = new Dictionary<string, object>
                {
                    { "volumeMm3", mp != null && mp.Length > 3 ? mp[3] * 1e9 : 0.0 },
                };
                if (bx != null && bx.Length >= 6)
                {
                    item["box"] = new Dictionary<string, double> {
                        { "minX", bx[0] }, { "minY", bx[1] }, { "minZ", bx[2] },
                        { "maxX", bx[3] }, { "maxY", bx[4] }, { "maxZ", bx[5] },
                    };
                }
                result.Add(item);
            }
            return result;
        }

        public Dictionary<string, object> GetModelObservation(int maxFaces = 200)
        {
            EnsureActiveDoc();
            var features = GetFeatureTree();
            Dictionary<string, double> bbox = null;
            double? volumeMm3 = null;
            var faces = new List<Dictionary<string, object>>();
            var bodies = new List<Dictionary<string, object>>();
            try
            {
                bbox = GetBodyBoundingBox();
                volumeMm3 = GetBodyVolume() * 1e9;
                faces = GetBodyFaces(maxFaces);
                bodies = GetBodySummaries();
            }
            catch (InvalidOperationException ex)
            {
                // New parts have no solid body yet; still return sketch/feature ledger state.
                Console.WriteLine($"[SwCommands] model-observation 无实体: {ex.Message}");
            }
            return new Dictionary<string, object>
            {
                { "features", features },
                { "boundingBox", bbox },
                { "volumeMm3", volumeMm3 },
                { "faces", faces },
                { "faceLimit", maxFaces },
                { "bodyCount", bodies.Count },
                { "bodies", bodies },
                { "sketches", GetSketchSummaries() },
                { "featureSketchLinks", _featureSketchLinks.ToList() },
            };
        }

        #endregion
    }
}
