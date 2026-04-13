using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;

namespace Chapter1.ConnectToSw
{
    /// <summary>
    /// SolidWorks 操作封装类。
    /// 持有 ISldWorks 引用和当前活动文档状态，提供原子操作和组合操作方法。
    /// 所有方法均应在 STA 线程上调用（COM 要求）。
    /// </summary>
    public class SwCommands
    {
        private readonly ISldWorks _swApp;
        private string _partTemplatePath;

        /// <summary>
        /// 当前活动的文档。NewPart() 后自动设置。
        /// </summary>
        public IModelDoc2 ActiveDoc { get; private set; }

        public SwCommands(ISldWorks swApp)
        {
            _swApp = swApp ?? throw new ArgumentNullException(nameof(swApp));
            _partTemplatePath = GetPartTemplatePath();
            Console.WriteLine($"[SwCommands] 零件模板路径: {_partTemplatePath}");
        }

        #region 辅助方法

        /// <summary>
        /// 将模型坐标 (x, y, z) 通过当前活动草图的 ModelToSketchTransform 变换为草图坐标。
        /// 返回 (sx, sy)。如果没有活动草图或变换矩阵不可用，原样返回 (x, y)。
        /// 
        /// 这是关键方法：当草图建立在某个面上（如长方体上表面）时，
        /// 草图坐标系与模型坐标系不同，必须做此变换才能在正确位置绘图。
        /// </summary>
        private void ModelToSketchPoint(double mx, double my, double mz, out double sx, out double sy)
        {
            var sketch = ActiveDoc.SketchManager.ActiveSketch;
            if (sketch == null)
            {
                // 没有活动草图，原样返回
                sx = mx; sy = my;
                return;
            }

            var xform = sketch.ModelToSketchTransform;
            if (xform == null)
            {
                sx = mx; sy = my;
                return;
            }

            // 使用 MathUtility 做变换
            var mathUtil = (IMathUtility)_swApp.IGetMathUtility();
            var ptData = new double[] { mx, my, mz };
            var mathPt = (IMathPoint)mathUtil.CreatePoint(ptData);
            var sketchPt = (IMathPoint)mathPt.MultiplyTransform(xform);
            var result = (double[])sketchPt.ArrayData;

            sx = result[0];
            sy = result[1];
            // result[2] 是草图 Z，2D 草图中忽略
        }

        /// <summary>
        /// 获取零件模板路径。优先使用 SolidWorks 默认模板，
        /// 若为空则从模板目录查找第一个 .prtdot 文件。
        /// </summary>
        private string GetPartTemplatePath()
        {
            var template = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swDefaultTemplatePart);

            if (!string.IsNullOrWhiteSpace(template))
                return template;

            Console.WriteLine("[SwCommands] 默认模板为空，尝试从模板目录查找...");

            var dirs = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swFileLocationsDocumentTemplates);

            if (string.IsNullOrWhiteSpace(dirs))
                throw new InvalidOperationException("无法获取 SolidWorks 模板目录配置");

            foreach (var dir in dirs.Split(';'))
            {
                if (!Directory.Exists(dir)) continue;

                var files = Directory.GetFiles(dir, "*.prtdot");
                if (files.Length > 0)
                {
                    Console.WriteLine($"[SwCommands] 找到模板: {files[0]}");
                    return files[0];
                }
            }

            throw new InvalidOperationException("未找到任何零件模板文件 (.prtdot)");
        }

        /// <summary>
        /// 遍历特征树，获取所有基准面特征。
        /// </summary>
        public static List<IFeature> GetRefPlanes(IModelDoc2 doc)
        {
            var planes = new List<IFeature>();
            var feat = doc.FirstFeature() as IFeature;
            while (feat != null)
            {
                if (feat.GetTypeName2() == "RefPlane")
                {
                    planes.Add(feat);
                }
                feat = feat.GetNextFeature() as IFeature;
            }
            return planes;
        }

        /// <summary>
        /// 确保当前有活动文档，否则抛出异常。
        /// </summary>
        private void EnsureActiveDoc()
        {
            if (ActiveDoc == null)
                throw new InvalidOperationException("当前没有活动文档，请先调用 NewPart 创建零件");
        }

        /// <summary>
        /// 从字典中安全获取 string 值。
        /// </summary>
        private static string GetStringOrDefault(Dictionary<string, object> dict, string key, string defaultValue = "")
        {
            if (dict != null && dict.ContainsKey(key))
                return dict[key]?.ToString() ?? defaultValue;
            return defaultValue;
        }

        #endregion

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

            // 使用第一个实体的包围盒
            var body = bodies[0] as IBody2;
            // GetBodyBox 返回 double[6]: { minX, minY, minZ, maxX, maxY, maxZ }
            var box = body.GetBodyBox() as double[];
            if (box == null || box.Length < 6)
                throw new InvalidOperationException("无法获取实体包围盒");

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

        #endregion

        #region 原子操作 — 面选择

        /// <summary>
        /// 通过射线拾取方式选中一个面并进入草图编辑模式。
        /// 给定射线起点(rx, ry, rz)和方向(dx, dy, dz)，找到最近的面并选中它。
        /// 坐标单位：米。
        /// 
        /// 返回中包含 sketchTransform 信息，用于调用方将模型坐标转换为草图坐标。
        /// </summary>
        public Dictionary<string, object> SelectFaceByRayAndStartSketch(
            double rx, double ry, double rz,
            double dx, double dy, double dz)
        {
            EnsureActiveDoc();

            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");

            var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体");

            // ---- 遍历所有实体的面，通过射线求交找到最近的实体面 ----
            // （不使用 SelectByRay，因为它可能选中基准面而非实体面）
            IFace2 bestFace = null;
            double bestDist = double.MaxValue;
            double[] bestHitPt = null;

            foreach (object bodyObj in bodies)
            {
                var body = bodyObj as IBody2;
                if (body == null) continue;

                var face = (IFace2)body.GetFirstFace();
                while (face != null)
                {
                    var surface = face.IGetSurface();
                    if (surface.IsPlane())
                    {
                        // 平面：求射线与平面交点
                        // 获取平面参数：法向量和到原点的距离
                        var planeParams = (double[])surface.PlaneParams;
                        // planeParams: [nx, ny, nz, rootX, rootY, rootZ]
                        double nx = planeParams[0], ny = planeParams[1], nz = planeParams[2];
                        double rootX = planeParams[3], rootY = planeParams[4], rootZ = planeParams[5];

                        // 射线：P = Origin + t * Dir
                        // 平面：dot(P - Root, Normal) = 0
                        // t = dot(Root - Origin, Normal) / dot(Dir, Normal)
                        double denom = dx * nx + dy * ny + dz * nz;
                        if (Math.Abs(denom) < 1e-12) { face = (IFace2)face.GetNextFace(); continue; } // 平行

                        double t = ((rootX - rx) * nx + (rootY - ry) * ny + (rootZ - rz) * nz) / denom;
                        if (t < 0) { face = (IFace2)face.GetNextFace(); continue; } // 交点在射线反方向

                        // 交点
                        double hitX = rx + t * dx, hitY = ry + t * dy, hitZ = rz + t * dz;

                        // 检查交点是否在面的边界内（使用 GetClosestPointOn）
                        var closest = (double[])face.GetClosestPointOn(hitX, hitY, hitZ);
                        double distToFace = Math.Sqrt(
                            Math.Pow(closest[0] - hitX, 2) +
                            Math.Pow(closest[1] - hitY, 2) +
                            Math.Pow(closest[2] - hitZ, 2));

                        if (distToFace < 0.0001 && t < bestDist) // 交点在面内且更近
                        {
                            bestDist = t;
                            bestFace = face;
                            bestHitPt = new[] { hitX, hitY, hitZ };
                        }
                    }
                    else
                    {
                        // 非平面（曲面）：用 GetClosestPointOn 近似
                        var closest = (double[])face.GetClosestPointOn(rx, ry, rz);
                        double distToOrigin = Math.Sqrt(
                            Math.Pow(closest[0] - rx, 2) +
                            Math.Pow(closest[1] - ry, 2) +
                            Math.Pow(closest[2] - rz, 2));

                        if (distToOrigin < bestDist)
                        {
                            bestDist = distToOrigin;
                            bestFace = face;
                            bestHitPt = new[] { closest[0], closest[1], closest[2] };
                        }
                    }

                    face = (IFace2)face.GetNextFace();
                }
            }

            if (bestFace == null)
                throw new InvalidOperationException(
                    $"未能通过射线选中实体面。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。");

            // 输出选中面的详细信息
            var hitSurface = bestFace.IGetSurface();
            string faceType = hitSurface.IsPlane() ? "平面" : "曲面";
            if (hitSurface.IsPlane())
            {
                var pp = (double[])hitSurface.PlaneParams;
                Console.WriteLine($"[SwCommands] 射线命中实体面: {faceType}，法向量=({pp[0]:F3},{pp[1]:F3},{pp[2]:F3})，" +
                    $"面上点=({pp[3]:F4},{pp[4]:F4},{pp[5]:F4})，交点=({bestHitPt[0]:F4},{bestHitPt[1]:F4},{bestHitPt[2]:F4})");
            }

            // 选中该面：通过实体选中（IEntity.Select4 确保选中的是实体面而不是其他类型）
            var faceEntity = (IEntity)bestFace;
            bool selected = faceEntity.Select4(false, null);
            if (!selected)
                throw new InvalidOperationException("选中实体面失败");

            // 进入草图编辑模式
            ActiveDoc.SketchManager.InsertSketch(true);

            // 获取草图坐标变换信息
            var activeSketch = ActiveDoc.SketchManager.ActiveSketch;
            double[] xformData = null;
            if (activeSketch != null)
            {
                // ModelToSketchTransform: 将模型坐标转换为草图坐标
                var xform = activeSketch.ModelToSketchTransform;
                if (xform != null)
                {
                    xformData = (double[])xform.ArrayData;
                    Console.WriteLine($"[SwCommands] 草图变换矩阵 (ModelToSketch): " +
                        $"[{xformData[0]:F3},{xformData[1]:F3},{xformData[2]:F3}," +
                        $" {xformData[3]:F3},{xformData[4]:F3},{xformData[5]:F3}," +
                        $" {xformData[6]:F3},{xformData[7]:F3},{xformData[8]:F3}," +
                        $" {xformData[9]:F4},{xformData[10]:F4},{xformData[11]:F4}]");
                }
            }

            Console.WriteLine($"[SwCommands] 已通过射线选中实体{faceType}并进入草图编辑");

            var result = new Dictionary<string, object>
            {
                { "selected", true },
                { "faceType", faceType },
                { "hitPoint", new Dictionary<string, double> {
                    { "x", bestHitPt[0] }, { "y", bestHitPt[1] }, { "z", bestHitPt[2] }
                }},
                { "message", $"已选中{faceType}并进入草图编辑" },
            };

            // 返回草图变换矩阵，让调用方可以正确计算草图坐标
            if (xformData != null)
            {
                result["sketchTransform"] = xformData;
            }

            return result;
        }

        #endregion

        #region 原子操作 — 文档

        /// <summary>
        /// 新建一个零件文档，设为当前活动文档。
        /// </summary>
        public void NewPart()
        {
            if (string.IsNullOrWhiteSpace(_partTemplatePath))
                throw new InvalidOperationException("零件模板路径未配置");

            var doc = _swApp.INewDocument2(_partTemplatePath, 0, 0, 0);
            if (doc == null)
                throw new InvalidOperationException("创建零件文档失败");

            if (doc.GetType() != (int)swDocumentTypes_e.swDocPART)
                throw new InvalidOperationException("创建的文档不是零件类型");

            ActiveDoc = doc;
            Console.WriteLine("[SwCommands] 新建零件文档成功");
        }

        #endregion

        #region 原子操作 — 草图

        /// <summary>
        /// 选择指定索引的基准面并进入草图编辑模式。
        /// index: 0=前视基准面, 1=上视基准面, 2=右视基准面
        /// </summary>
        public void SelectPlaneAndStartSketch(int planeIndex)
        {
            EnsureActiveDoc();

            var planes = GetRefPlanes(ActiveDoc);
            if (planeIndex < 0 || planeIndex >= planes.Count)
                throw new ArgumentOutOfRangeException(nameof(planeIndex),
                    $"基准面索引 {planeIndex} 超出范围，可用范围: 0 ~ {planes.Count - 1}");

            var plane = planes[planeIndex];
            plane.Select2(false, 0);

            ActiveDoc.SketchManager.InsertSketch(true);
            Console.WriteLine($"[SwCommands] 已选择基准面 [{planeIndex}] 并进入草图编辑");
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
            ActiveDoc.SketchManager.CreateLine(sx1, sy1, 0, sx2, sy2, 0);
            Console.WriteLine($"[SwCommands] 绘制直线: 模型({x1},{y1},{z1}) -> ({x2},{y2},{z2})，草图({sx1:F4},{sy1:F4}) -> ({sx2:F4},{sy2:F4})");
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
            ActiveDoc.SketchManager.CreateCornerRectangle(sx1, sy1, 0, sx2, sy2, 0);
            Console.WriteLine($"[SwCommands] 绘制矩形: 模型({x1},{y1}) -> ({x2},{y2})，草图({sx1:F4},{sy1:F4}) -> ({sx2:F4},{sy2:F4})");
        }

        /// <summary>
        /// 在当前草图中绘制一个圆。
        /// 圆心坐标为模型空间 3D 坐标（单位：米），自动变换到草图坐标系。
        /// 半径在草图平面内保持不变。cz 可选。
        /// </summary>
        public void SketchCircle(double cx, double cy, double radius, double cz = 0)
        {
            EnsureActiveDoc();
            double sx, sy;
            ModelToSketchPoint(cx, cy, cz, out sx, out sy);
            // 圆上一点也需要变换，使用同一平面上偏移 radius 的点
            double sx2, sy2;
            ModelToSketchPoint(cx + radius, cy, cz, out sx2, out sy2);
            double sketchRadius = Math.Sqrt((sx2 - sx) * (sx2 - sx) + (sy2 - sy) * (sy2 - sy));
            ActiveDoc.SketchManager.CreateCircle(sx, sy, 0, sx + sketchRadius, sy, 0);
            Console.WriteLine($"[SwCommands] 绘制圆: 模型圆心({cx},{cy},{cz}), 半径 {radius}，草图圆心({sx:F4},{sy:F4}), 草图半径 {sketchRadius:F4}");
        }

        /// <summary>
        /// 退出当前草图编辑模式（不进行拉伸）。
        /// </summary>
        public void CloseSketch()
        {
            EnsureActiveDoc();
            ActiveDoc.SketchManager.InsertSketch(true);
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
            ActiveDoc.SketchManager.CreateCenterLine(sx1, sy1, 0, sx2, sy2, 0);
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
            ActiveDoc.SketchManager.CreateArc(scx, scy, 0, ssx, ssy, 0, sex, sey, 0, direction);
            Console.WriteLine($"[SwCommands] 绘制圆弧: 圆心({cx},{cy}), 起点({startX},{startY}), 终点({endX},{endY}), 方向={direction}");
        }

        #endregion

        #region 原子操作 — 特征

        /// <summary>
        /// 退出草图并拉伸当前草图。depth 为拉伸深度，单位：米。
        /// </summary>
        public void Extrusion(double depth)
        {
            EnsureActiveDoc();

            if (depth <= 0)
                throw new ArgumentOutOfRangeException(nameof(depth), "拉伸深度必须为正数");

            // 先退出草图编辑模式
            ActiveDoc.SketchManager.InsertSketch(true);

            // 执行拉伸
            ActiveDoc.FeatureManager.FeatureExtrusion3(
                Sd: true,
                Flip: false,
                Dir: false,
                T1: (int)swEndConditions_e.swEndCondBlind,
                T2: (int)swEndConditions_e.swEndCondBlind,
                D1: depth,
                D2: 0,
                Dchk1: false,
                Dchk2: false,
                Ddir1: false,
                Ddir2: false,
                Dang1: 0,
                Dang2: 0,
                OffsetReverse1: false,
                OffsetReverse2: false,
                TranslateSurface1: false,
                TranslateSurface2: false,
                Merge: true,
                UseFeatScope: true,
                UseAutoSelect: true,
                T0: (int)swStartConditions_e.swStartSketchPlane,
                StartOffset: 0,
                FlipStartOffset: false
            );

            // 缩放到合适视图（ViewZoomtofit2 在 ModelDoc2 类上，需强转）
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 拉伸完成，深度: {depth} 米");
        }

        /// <summary>
        /// 退出草图并执行旋转操作（旋转凸台或旋转切除）。
        /// 草图中必须包含一条中心线作为旋转轴。
        /// angleDeg: 旋转角度（度），360 表示完整旋转。
        /// isCut: true=旋转切除, false=旋转凸台。
        /// reverseDir: 是否反转旋转方向。
        /// </summary>
        public void Revolve(double angleDeg, bool isCut = false, bool reverseDir = false)
        {
            EnsureActiveDoc();

            if (angleDeg <= 0 || angleDeg > 360)
                throw new ArgumentOutOfRangeException(nameof(angleDeg), "旋转角度必须在 (0, 360] 之间");

            double angleRad = angleDeg * Math.PI / 180.0;

            var mdoc = (ModelDoc2)ActiveDoc;

            // 退出草图编辑模式（使用 IModelDoc2.InsertSketch2 —— 官方推荐方式）
            // SketchManager.InsertSketch(true) 在某些情况下可能无法正确退出草图，
            // 导致 FeatureRevolve2 返回 null（与 CutExtrusion 遇到的问题一致）
            mdoc.InsertSketch2(true);

            // 执行旋转
            var feat = ActiveDoc.FeatureManager.FeatureRevolve2(
                /* SingleDir */     true,
                /* IsSolid */       true,
                /* IsThin */        false,
                /* IsCut */         isCut,
                /* ReverseDir */    reverseDir,
                /* BothDirectionUpToSameEntity */ false,
                /* Dir1Type */      (int)swEndConditions_e.swEndCondBlind,
                /* Dir2Type */      (int)swEndConditions_e.swEndCondBlind,
                /* Dir1Angle */     angleRad,
                /* Dir2Angle */     0,
                /* OffsetReverse1 */ false,
                /* OffsetReverse2 */ false,
                /* OffsetDistance1 */ 0,
                /* OffsetDistance2 */ 0,
                /* ThinType */      0,
                /* ThinThickness1 */ 0,
                /* ThinThickness2 */ 0,
                /* Merge */         true,
                /* UseFeatScope */  true,
                /* UseAutoSelect */ true
            );

            if (feat == null)
                throw new InvalidOperationException("旋转操作失败，请确保草图中包含有效的轮廓和中心线作为旋转轴。");

            // 缩放到合适视图
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            string type = isCut ? "切除" : "凸台";
            Console.WriteLine($"[SwCommands] 旋转{type}完成，角度: {angleDeg}°");
        }

        /// <summary>
        /// 退出草图并执行切除拉伸（挖孔/开槽等减法操作）。
        /// depth 为切除深度，单位：米。
        /// endCondition: "blind"(盲孔), "through_all"(贯穿), "mid_plane"(中间面), "up_to_next"(到下一面)
        /// flipDirection: 是否反转切除方向
        /// </summary>
        public void CutExtrusion(double depth, string endCondition = "blind", bool flipDirection = false)
        {
            EnsureActiveDoc();

            var mdoc = (ModelDoc2)ActiveDoc;

            // 检查是否在草图编辑模式中
            var activeSketch = ActiveDoc.SketchManager.ActiveSketch;
            Console.WriteLine($"[SwCommands] 切除拉伸 - 当前ActiveSketch: {(activeSketch != null ? "有" : "无")}");

            // 退出草图编辑模式（使用 IModelDoc2.InsertSketch2 —— 官方推荐方式）
            // SketchManager.InsertSketch(true) 在某些情况下（如实体面上的草图）可能无法正确退出，
            // 而 IModelDoc2.InsertSketch2(true) 是官方示例中使用的方法。
            mdoc.InsertSketch2(true);

            // 验证草图是否已退出
            activeSketch = ActiveDoc.SketchManager.ActiveSketch;
            Console.WriteLine($"[SwCommands] 切除拉伸 - InsertSketch2后ActiveSketch: {(activeSketch != null ? "仍在编辑!" : "已退出")}");

            // 如果草图还没退出，尝试再次退出
            if (activeSketch != null)
            {
                Console.WriteLine("[SwCommands] 草图未退出，尝试 SketchManager.InsertSketch(true)...");
                ActiveDoc.SketchManager.InsertSketch(true);
                activeSketch = ActiveDoc.SketchManager.ActiveSketch;
                Console.WriteLine($"[SwCommands] 第二次退出后ActiveSketch: {(activeSketch != null ? "仍在编辑!" : "已退出")}");
            }

            // 检查当前选择状态（调试信息）
            var selMgr = ActiveDoc.ISelectionManager;
            int selCount = selMgr.GetSelectedObjectCount2(-1);
            Console.WriteLine($"[SwCommands] 切除拉伸 - 退出草图后选中对象数: {selCount}");
            for (int i = 1; i <= selCount; i++)
            {
                int selType = selMgr.GetSelectedObjectType3(i, -1);
                Console.WriteLine($"[SwCommands]   选中项 {i}: 类型={selType}");
            }

            // 解析终止条件
            int t1;
            switch (endCondition.ToLower())
            {
                case "through_all": t1 = (int)swEndConditions_e.swEndCondThroughAll; break;
                case "mid_plane": t1 = (int)swEndConditions_e.swEndCondMidPlane; break;
                case "up_to_next": t1 = (int)swEndConditions_e.swEndCondUpToNext; break;
                default: t1 = (int)swEndConditions_e.swEndCondBlind; break;
            }

            Console.WriteLine($"[SwCommands] 切除拉伸参数 - depth={depth}, endCondition={endCondition}(t1={t1}), flip={flipDirection}");

            // 策略：先尝试和 Extrusion 一样直接调用（依赖退出草图后的自动选择），
            // 如果失败再手动选择草图重试。

            // 执行切除拉伸
            var feat = ActiveDoc.FeatureManager.FeatureCut4(
                true,               // 1.  Sd - 单方向
                flipDirection,      // 2.  Flip - 是否反转方向
                false,              // 3.  Dir - 方向2
                t1,                 // 4.  T1 - 终止条件1 (int)
                0,                  // 5.  T2 - 终止条件2（单端切除时不使用）
                depth,              // 6.  D1 - 深度1 (double)
                depth,              // 7.  D2 - 深度2 (double)
                false,              // 8.  Dchk1 - 拔模开关1
                false,              // 9.  Dchk2 - 拔模开关2
                false,              // 10. Ddir1 - 拔模方向1
                false,              // 11. Ddir2 - 拔模方向2
                0.0,                // 12. Dang1 - 拔模角度1 (double)
                0.0,                // 13. Dang2 - 拔模角度2 (double)
                false,              // 14. OffsetReverse1
                false,              // 15. OffsetReverse2
                false,              // 16. TranslateSurface1
                false,              // 17. TranslateSurface2
                false,              // 18. NormalCut - 非钣金法向切除，普通零件用 false
                true,               // 19. UseFeatScope - 使用特征范围
                true,               // 20. UseAutoSelect - 自动选择受影响实体
                false,              // 21. AssemblyFeatureScope
                false,              // 22. AutoSelectComponents
                false,              // 23. PropagateFeatureToCutBody
                (int)swStartConditions_e.swStartSketchPlane,  // 24. T0 - 起始条件 (int)
                0.0,                // 25. StartOffset (double)
                false,              // 26. FlipStartOffset
                false               // 27. OptimizeGeometry
            );

            Console.WriteLine($"[SwCommands] FeatureCut4 返回: {(feat != null ? feat.Name : "null")}");

            // 如果直接调用失败，手动选择草图并重试
            if (feat == null)
            {
                Console.WriteLine("[SwCommands] 首次切除失败，尝试手动选择草图后重试...");
                
                // 清除选择，然后用 SelectByID2 选中草图
                ActiveDoc.ClearSelection2(true);
                
                // 遍历特征树找到最后一个草图
                string lastSketchName = null;
                IFeature curFeat = (IFeature)ActiveDoc.FirstFeature();
                while (curFeat != null)
                {
                    if (curFeat.GetTypeName2() == "ProfileFeature")
                        lastSketchName = curFeat.Name;
                    curFeat = (IFeature)curFeat.GetNextFeature();
                }

                if (lastSketchName != null)
                {
                    // 使用 SelectByID2 选中草图，指定 Mark=1（某些特征创建需要 Mark 值）
                    var ext = mdoc.Extension;
                    bool sel = ext.SelectByID2(lastSketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
                    Console.WriteLine($"[SwCommands] SelectByID2(\"{lastSketchName}\", \"SKETCH\") = {sel}");
                    
                    // 检查选择类型
                    selCount = selMgr.GetSelectedObjectCount2(-1);
                    for (int i = 1; i <= selCount; i++)
                    {
                        int selType = selMgr.GetSelectedObjectType3(i, -1);
                        Console.WriteLine($"[SwCommands]   选中项 {i}: 类型={selType}");
                    }
                }

                feat = ActiveDoc.FeatureManager.FeatureCut4(
                    true, flipDirection, false,
                    t1, 0, depth, depth,
                    false, false, false, false,
                    0.0, 0.0, false, false, false, false,
                    false, true, true, false, false, false,
                    (int)swStartConditions_e.swStartSketchPlane, 0.0, false, false
                );
                Console.WriteLine($"[SwCommands] 第二次 FeatureCut4 返回: {(feat != null ? feat.Name : "null")}");
            }

            // 如果还是失败，尝试反转方向
            if (feat == null)
            {
                Console.WriteLine("[SwCommands] 第二次切除失败，尝试反转方向...");
                
                ActiveDoc.ClearSelection2(true);
                string lastSketchName2 = null;
                IFeature curFeat2 = (IFeature)ActiveDoc.FirstFeature();
                while (curFeat2 != null)
                {
                    if (curFeat2.GetTypeName2() == "ProfileFeature")
                        lastSketchName2 = curFeat2.Name;
                    curFeat2 = (IFeature)curFeat2.GetNextFeature();
                }
                if (lastSketchName2 != null)
                {
                    mdoc.Extension.SelectByID2(lastSketchName2, "SKETCH", 0, 0, 0, false, 0, null, 0);
                }

                feat = ActiveDoc.FeatureManager.FeatureCut4(
                    true, !flipDirection, false,
                    t1, 0, depth, depth,
                    false, false, false, false,
                    0.0, 0.0, false, false, false, false,
                    false, true, true, false, false, false,
                    (int)swStartConditions_e.swStartSketchPlane, 0.0, false, false
                );
                Console.WriteLine($"[SwCommands] 反转方向后 FeatureCut4 返回: {(feat != null ? feat.Name : "null")}");
            }

            if (feat == null)
            {
                throw new InvalidOperationException("切除拉伸失败，请确保草图闭合且位于实体面上。请查看 C# 控制台的调试信息。");
            }

            // 缩放到合适视图
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 切除拉伸完成，深度: {depth} 米, 终止条件: {endCondition}");
        }

        /// <summary>
        /// 修改指定特征的尺寸值。
        /// featureName: 特征名称（如 "Boss-Extrude1" / "凸台-拉伸1"）
        /// dimensionName: 尺寸名称（如 "D1"），如果为空则修改该特征的第一个尺寸
        /// newValue: 新的尺寸值，单位：米
        /// </summary>
        public Dictionary<string, object> ModifyFeatureDimension(string featureName, string dimensionName, double newValue)
        {
            EnsureActiveDoc();

            if (string.IsNullOrWhiteSpace(featureName))
                throw new ArgumentException("特征名称不能为空", nameof(featureName));

            // 遍历特征树找到目标特征
            var feat = ActiveDoc.FirstFeature() as IFeature;
            IFeature targetFeat = null;

            while (feat != null)
            {
                if (feat.Name == featureName)
                {
                    targetFeat = feat;
                    break;
                }
                feat = feat.GetNextFeature() as IFeature;
            }

            if (targetFeat == null)
                throw new InvalidOperationException($"未找到名为 \"{featureName}\" 的特征");

            // 找到目标尺寸
            IDimension targetDim = null;
            string resolvedDimName = "";

            var dispDim = targetFeat.GetFirstDisplayDimension() as IDisplayDimension;
            while (dispDim != null)
            {
                var dim = dispDim.GetDimension2(0);
                if (dim != null)
                {
                    // 如果未指定尺寸名，使用第一个尺寸
                    if (string.IsNullOrWhiteSpace(dimensionName))
                    {
                        targetDim = dim;
                        resolvedDimName = dim.Name;
                        break;
                    }
                    // 按尺寸名匹配
                    if (dim.Name == dimensionName || dim.FullName == dimensionName)
                    {
                        targetDim = dim;
                        resolvedDimName = dim.Name;
                        break;
                    }
                }
                dispDim = targetFeat.GetNextDisplayDimension(dispDim) as IDisplayDimension;
            }

            if (targetDim == null)
            {
                string hint = string.IsNullOrWhiteSpace(dimensionName)
                    ? $"特征 \"{featureName}\" 没有可编辑的尺寸"
                    : $"在特征 \"{featureName}\" 中未找到名为 \"{dimensionName}\" 的尺寸";
                throw new InvalidOperationException(hint);
            }

            // 获取旧值
            // 注意：GetSystemValue3 返回的是 object，实际类型可能是 double[] 数组
            double oldValue;
            object rawValue = targetDim.GetSystemValue3(
                (int)swInConfigurationOpts_e.swThisConfiguration, null);
            if (rawValue is double[])
            {
                var arr = (double[])rawValue;
                oldValue = arr.Length > 0 ? arr[0] : 0;
            }
            else if (rawValue is double)
            {
                oldValue = (double)rawValue;
            }
            else
            {
                oldValue = Convert.ToDouble(rawValue);
            }

            Console.WriteLine($"[SwCommands] 尺寸旧值: {oldValue:F6} 米 (原始类型: {rawValue?.GetType().Name})");

            // 设置新值
            int retCode = targetDim.SetSystemValue3(
                newValue,
                (int)swSetValueInConfiguration_e.swSetValue_InThisConfiguration,
                null);

            Console.WriteLine($"[SwCommands] SetSystemValue3 返回代码: {retCode} (期望: {(int)swSetValueReturnStatus_e.swSetValue_Successful})");

            if (retCode != (int)swSetValueReturnStatus_e.swSetValue_Successful)
                throw new InvalidOperationException(
                    $"设置尺寸值失败，返回代码: {retCode}");

            // 重建模型
            ActiveDoc.ForceRebuild3(true);

            // 缩放到合适视图
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 修改尺寸: {featureName}.{resolvedDimName} = {oldValue:F4} -> {newValue:F4} 米");

            return new Dictionary<string, object>
            {
                { "featureName", featureName },
                { "dimensionName", resolvedDimName },
                { "oldValue", oldValue },
                { "newValue", newValue },
                { "message", $"已修改 {featureName} 的 {resolvedDimName}: {oldValue:F4} -> {newValue:F4} 米" },
            };
        }

        /// <summary>
        /// 创建圆角特征。
        /// 通过射线选中实体的边，再应用圆角。
        /// radius: 圆角半径（米）
        /// 
        /// 选边策略:
        /// - "all": 选中当前实体的所有边
        /// - "ray": 通过射线选中特定边（提供 rx,ry,rz,dx,dy,dz）
        /// </summary>
        public Dictionary<string, object> Fillet(double radius, string edgeSelection,
            double rx = 0, double ry = 0, double rz = 0,
            double dx = 0, double dy = 0, double dz = 0)
        {
            EnsureActiveDoc();

            if (radius <= 0)
                throw new ArgumentOutOfRangeException(nameof(radius), "圆角半径必须为正数");

            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");

            var mdoc = (ModelDoc2)ActiveDoc;
            var ext = mdoc.Extension;

            // 清除当前选择
            ActiveDoc.ClearSelection2(true);

            int edgeCount = 0;

            if (edgeSelection.ToLower() == "all")
            {
                // 选中所有边
                var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                if (bodies == null || bodies.Length == 0)
                    throw new InvalidOperationException("当前零件没有实体");

                foreach (object bodyObj in bodies)
                {
                    var body = bodyObj as IBody2;
                    if (body == null) continue;

                    var edges = body.GetEdges() as object[];
                    if (edges == null) continue;

                    foreach (object edgeObj in edges)
                    {
                        var edge = edgeObj as IEdge;
                        if (edge == null) continue;

                        var entity = (IEntity)edge;
                        // Mark = 1 用于圆角边选择
                        bool sel = entity.Select4(edgeCount > 0, null);
                        if (sel) edgeCount++;
                    }
                }
                Console.WriteLine($"[SwCommands] 圆角: 选中所有边，共 {edgeCount} 条");
            }
            else if (edgeSelection.ToLower() == "ray")
            {
                // 通过射线选中边
                bool sel = ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.001,
                    (int)swSelectType_e.swSelEDGES, false, 0, 0);
                if (!sel)
                    throw new InvalidOperationException(
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})");
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 圆角: 通过射线选中 1 条边");
            }
            else
            {
                throw new ArgumentException($"未知的选边方式: {edgeSelection}，可选 'all' 或 'ray'");
            }

            if (edgeCount == 0)
                throw new InvalidOperationException("未选中任何边");

            // 使用 FeatureFillet3 创建圆角
            // Options = 195 = swFeatureFilletPropagate | swFeatureFilletKeepFeatures | swFeatureFilletUniformRadius 等组合
            // 对于简单恒定半径圆角，后面的数组参数传空即可
            double[] radiiArray = new double[0];
            double[] dist2Array = new double[0];
            double[] conicRhosArray = new double[0];
            double[] setBackArray = new double[0];
            double[] pointRadiusArray = new double[0];
            double[] pointDist2Array = new double[0];
            double[] pointRhoArray = new double[0];

            var feat = (Feature)ActiveDoc.FeatureManager.FeatureFillet3(
                195,                // Options (标准简单圆角选项组合)
                radius,             // R1 - 圆角半径
                0,                  // R2 - 非对称时的第二半径（对称时为 0）
                0,                  // Rho - 圆锥形状值
                0,                  // Ftyp - 0=Simple (简单恒定半径圆角)
                0,                  // OverflowType - 0=Default
                0,                  // ConicRhoType - 0=Circular (圆形截面)
                radiiArray,         // Radii
                dist2Array,         // Dist2Arr
                conicRhosArray,     // RhoArr
                setBackArray,       // SetBackDistances
                pointRadiusArray,   // PointRadiusArray
                pointDist2Array,    // PointDist2Array
                pointRhoArray       // PointRhoArray
            );

            if (feat == null)
                throw new InvalidOperationException("创建圆角失败，请确保选中了有效的边");

            // 缩放到合适视图
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 圆角创建成功，半径: {radius} 米，选中 {edgeCount} 条边");

            return new Dictionary<string, object>
            {
                { "featureName", feat.Name },
                { "edgeCount", edgeCount },
                { "radius", radius },
                { "message", $"圆角创建成功，半径 {radius * 1000:F1}mm，{edgeCount} 条边" },
            };
        }

        /// <summary>
        /// 创建倒角特征。
        /// 通过射线选中实体的边，再应用倒角。
        /// 
        /// chamferType: 
        ///   "equal_distance" - 等距倒角（distance1 = distance2）
        ///   "distance_distance" - 不等距倒角（distance1 和 distance2 不同）
        ///   "distance_angle" - 距离-角度倒角（distance1 和 angle）
        /// 
        /// 选边策略:
        /// - "all": 选中当前实体的所有边
        /// - "ray": 通过射线选中特定边
        /// </summary>
        public Dictionary<string, object> Chamfer(string chamferType, double distance1,
            string edgeSelection,
            double distance2 = 0, double angle = 45,
            double rx = 0, double ry = 0, double rz = 0,
            double dx = 0, double dy = 0, double dz = 0)
        {
            EnsureActiveDoc();

            if (distance1 <= 0)
                throw new ArgumentOutOfRangeException(nameof(distance1), "倒角距离必须为正数");

            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");

            var mdoc = (ModelDoc2)ActiveDoc;
            var ext = mdoc.Extension;

            // 清除当前选择
            ActiveDoc.ClearSelection2(true);

            int edgeCount = 0;

            if (edgeSelection.ToLower() == "all")
            {
                // 选中所有边
                var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                if (bodies == null || bodies.Length == 0)
                    throw new InvalidOperationException("当前零件没有实体");

                foreach (object bodyObj in bodies)
                {
                    var body = bodyObj as IBody2;
                    if (body == null) continue;

                    var edges = body.GetEdges() as object[];
                    if (edges == null) continue;

                    foreach (object edgeObj in edges)
                    {
                        var edge = edgeObj as IEdge;
                        if (edge == null) continue;

                        var entity = (IEntity)edge;
                        bool sel = entity.Select4(edgeCount > 0, null);
                        if (sel) edgeCount++;
                    }
                }
                Console.WriteLine($"[SwCommands] 倒角: 选中所有边，共 {edgeCount} 条");
            }
            else if (edgeSelection.ToLower() == "ray")
            {
                bool sel = ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.001,
                    (int)swSelectType_e.swSelEDGES, false, 0, 0);
                if (!sel)
                    throw new InvalidOperationException(
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})");
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 倒角: 通过射线选中 1 条边");
            }
            else
            {
                throw new ArgumentException($"未知的选边方式: {edgeSelection}，可选 'all' 或 'ray'");
            }

            if (edgeCount == 0)
                throw new InvalidOperationException("未选中任何边");

            // --- 倒角参数计算 ---
            // 使用 IModelDoc2.FeatureChamfer(Width, Angle, Flip) 方法（3参数版）
            // 此方法比 IFeatureManager.InsertFeatureChamfer（8参数版）更可靠
            //
            // Width: 倒角距离（米）
            // Angle: 倒角角度（弧度），45° = π/4 时为等距倒角
            // Flip: false=从第一个面量角度，true=从另一个面量角度
            //
            // 等距倒角: Width=d, Angle=45°(π/4)
            // 距离-角度: Width=d, Angle=指定角度
            // 距离-距离: Width=d1, Angle=atan(d2/d1) — 通过几何关系换算

            double chamferWidth, chamferAngleRad;
            bool flip = false;

            switch (chamferType.ToLower())
            {
                case "equal_distance":
                    // 等距倒角：45° 角，两面等距
                    chamferWidth = distance1;
                    chamferAngleRad = Math.PI / 4.0; // 45°
                    break;
                case "distance_distance":
                    // 不等距倒角：通过 atan(d2/d1) 换算角度
                    double d2 = distance2 > 0 ? distance2 : distance1;
                    chamferWidth = distance1;
                    chamferAngleRad = Math.Atan2(d2, distance1);
                    break;
                case "distance_angle":
                    // 距离-角度倒角
                    chamferWidth = distance1;
                    chamferAngleRad = angle * Math.PI / 180.0;
                    break;
                default:
                    throw new ArgumentException(
                        $"未知的倒角类型: {chamferType}，可选 'equal_distance', 'distance_distance', 'distance_angle'");
            }

            Console.WriteLine($"[SwCommands] 倒角参数: Width={chamferWidth}m ({chamferWidth * 1000:F2}mm), Angle={chamferAngleRad}rad ({chamferAngleRad * 180 / Math.PI:F1}°), Flip={flip}");

            // 记录调用前的特征数量，用于判断倒角是否成功
            int featCountBefore = 0;
            {
                Feature f = (Feature)mdoc.FirstFeature();
                while (f != null)
                {
                    featCountBefore++;
                    f = (Feature)f.GetNextFeature();
                }
            }

            // 使用 IModelDoc2.FeatureChamfer 创建倒角（返回 void）
            mdoc.FeatureChamfer(chamferWidth, chamferAngleRad, flip);

            // 重建模型确保特征生效
            mdoc.EditRebuild3();

            // 通过特征数量变化判断倒角是否成功创建
            string featName = "倒角";
            int featCountAfter = 0;
            {
                Feature f = (Feature)mdoc.FirstFeature();
                Feature lastF = null;
                while (f != null)
                {
                    featCountAfter++;
                    lastF = f;
                    f = (Feature)f.GetNextFeature();
                }
                if (lastF != null && featCountAfter > featCountBefore)
                    featName = lastF.Name;
            }

            if (featCountAfter <= featCountBefore)
                throw new InvalidOperationException("创建倒角失败，特征树中未新增倒角特征。请确保选中了有效的边");

            // 缩放到合适视图
            mdoc.ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 倒角创建成功，类型: {chamferType}，距离1: {distance1} 米，{edgeCount} 条边");

            return new Dictionary<string, object>
            {
                { "featureName", featName },
                { "edgeCount", edgeCount },
                { "chamferType", chamferType },
                { "distance1", distance1 },
                { "message", $"倒角创建成功，类型 {chamferType}，距离 {distance1 * 1000:F1}mm，{edgeCount} 条边" },
            };
        }

        #endregion

        #region 组合操作

        /// <summary>
        /// 组合操作：创建一个长方体。
        /// 新建零件 -> 选前视基准面 -> 画矩形 -> 拉伸。
        /// length/width 为矩形的长和宽，height 为拉伸高度，单位：米。
        /// </summary>
        public void CreateBox(double length, double width, double height)
        {
            if (length <= 0) throw new ArgumentOutOfRangeException(nameof(length), "长度必须为正数");
            if (width <= 0) throw new ArgumentOutOfRangeException(nameof(width), "宽度必须为正数");
            if (height <= 0) throw new ArgumentOutOfRangeException(nameof(height), "高度必须为正数");

            NewPart();
            SelectPlaneAndStartSketch(0); // 前视基准面
            SketchRectangle(0, 0, length, width);
            Extrusion(height);

            Console.WriteLine($"[SwCommands] 长方体创建成功 ({length} x {width} x {height} 米)");
        }

        #endregion
    }
}
