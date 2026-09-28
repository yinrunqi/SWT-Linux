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
                    var normal = new double[] { 0, 0, 0 };
                    if (planar)
                    {
                        var pp = (double[])surface.PlaneParams;
                        normal = new double[] { pp[0], pp[1], pp[2] };
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
                        { "geometricSignature",
                            $"{surfaceType}:{center[0]:F6}:{center[1]:F6}:{center[2]:F6}:{area:F9}" },
                    };
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

        public Dictionary<string, object> GetModelObservation()
        {
            EnsureActiveDoc();
            var features = GetFeatureTree();
            Dictionary<string, double> bbox = null;
            double? volumeMm3 = null;
            var faces = new List<Dictionary<string, object>>();
            try
            {
                bbox = GetBodyBoundingBox();
                volumeMm3 = GetBodyVolume() * 1e9;
                faces = GetBodyFaces();
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
                { "sketches", GetSketchSummaries() },
                { "featureSketchLinks", _featureSketchLinks.ToList() },
            };
        }

        #endregion
    }
}
