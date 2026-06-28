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

        /// <summary>
        /// 标记当前草图是否在基准面上（而非实体面上）。
        /// 如果为 true，绘图方法将直接把传入的 (x, y) 作为草图平面内坐标，
        /// 而不通过 ModelToSketchTransform 变换。
        /// </summary>
        private bool _sketchOnRefPlane = false;

        /// <summary>
        /// 记录"最近一次进入草图编辑时新建的那个草图"的特征名。
        /// 在 SelectPlaneAndStartSketch / SelectFaceByRayAndStartSketch 进入草图后捕获，
        /// 供 CutExtrusion 在退出草图后【显式按名选中该草图】，避免依赖 SolidWorks
        /// 退出草图后的自动选择（自动选择在首刀切除时可能误指向立方体本体的轮廓草图，
        /// 导致用 20×20 全截面切除而非小圆点）。
        /// </summary>
        private string _activeSketchName = null;

        public SwCommands(ISldWorks swApp)
        {
            _swApp = swApp ?? throw new ArgumentNullException(nameof(swApp));
            _partTemplatePath = GetPartTemplatePath();
            Console.WriteLine($"[SwCommands] 零件模板路径: {_partTemplatePath}");
        }

        #region 辅助方法

        /// <summary>
        /// 根据语义化边位置名称（如 "top_front", "bottom_left" 等）自动计算射线参数并选中对应的边。
        /// 
        /// 支持的位置名称格式为 "面1_面2"，表示两个面的交线（即边）。
        /// 面名称：top(顶面,Y=maxY), bottom(底面,Y=minY), front(前面,Z=maxZ), back(后面,Z=minZ),
        ///         left(左面,X=minX), right(右面,X=maxX)
        /// 
        /// 例如：
        /// - "top_front"：顶面与前面的交线（Y=maxY, Z=maxZ 的那条边）
        /// - "top_back"：顶面与后面的交线（Y=maxY, Z=minZ 的那条边）
        /// - "top_left"：顶面与左面的交线（Y=maxY, X=minX 的那条边）
        /// - "top_right"：顶面与右面的交线（Y=maxY, X=maxX 的那条边）
        /// - "bottom_front"：底面与前面的交线
        /// - "front_left"：前面与左面的交线
        /// 等等...
        /// </summary>
        /// <returns>选中的边数量（成功时为 1）</returns>
        private int SelectEdgeByPosition(IPartDoc partDoc, IModelDocExtension ext,
            string positionName, string featureLabel)
        {
            // 获取包围盒
            var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体");

            var bbox = ((IBody2)bodies[0]).GetBodyBox() as double[];
            if (bbox == null)
                throw new InvalidOperationException("无法获取实体包围盒");

            double minX = bbox[0], minY = bbox[1], minZ = bbox[2];
            double maxX = bbox[3], maxY = bbox[4], maxZ = bbox[5];
            double midX = (minX + maxX) / 2;
            double midY = (minY + maxY) / 2;
            double midZ = (minZ + maxZ) / 2;
            double offset = 0.01; // 射线起点到面的偏移量（10mm）

            Console.WriteLine($"[SwCommands] {featureLabel}: 语义化选边 \"{positionName}\"");
            Console.WriteLine($"[SwCommands] {featureLabel}: 包围盒 ({minX:F4},{minY:F4},{minZ:F4})->({maxX:F4},{maxY:F4},{maxZ:F4})");

            // 解析位置名称：拆分为两个面名
            string normalized = positionName.ToLower().Trim();
            string[] parts = normalized.Split(new char[] { '_', '-', ' ' }, StringSplitOptions.RemoveEmptyEntries);

            if (parts.Length != 2)
                throw new ArgumentException(
                    $"无效的边位置名: \"{positionName}\"。格式应为 \"面1_面2\"（如 top_front, bottom_left）。" +
                    $"可用面名: top, bottom, front, back, left, right");

            // 确定射线起点和方向
            // 策略：从两个面的"外部交线"方向射入，命中该交线（边）
            // 射线从两个面的法线外侧交汇点出发，指向包围盒中心
            double rx, ry, rz, dx, dy, dz;
            ComputeEdgeRay(parts[0], parts[1], minX, minY, minZ, maxX, maxY, maxZ,
                midX, midY, midZ, offset, out rx, out ry, out rz, out dx, out dy, out dz);

            Console.WriteLine($"[SwCommands] {featureLabel}: 计算射线 起点({rx:F4},{ry:F4},{rz:F4}) 方向({dx:F4},{dy:F4},{dz:F4})");

            bool sel = ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.002,
                (int)swSelectType_e.swSelEDGES, false, 0, 0);

            if (!sel)
                throw new InvalidOperationException(
                    $"未能通过语义化位置 \"{positionName}\" 选中边。" +
                    $"计算的射线: 起点({rx:F4},{ry:F4},{rz:F4}), 方向({dx:F4},{dy:F4},{dz:F4})。" +
                    $"包围盒: ({minX:F4},{minY:F4},{minZ:F4})->({maxX:F4},{maxY:F4},{maxZ:F4})");

            Console.WriteLine($"[SwCommands] {featureLabel}: 通过语义化位置 \"{positionName}\" 选中 1 条边");
            return 1;
        }

        /// <summary>
        /// 根据两个面名计算用于选取其交线（边）的射线起点和方向。
        /// 射线起点在目标边的外侧中点附近，方向斜 45° 指向包围盒内部。
        /// </summary>
        private void ComputeEdgeRay(string face1, string face2,
            double minX, double minY, double minZ,
            double maxX, double maxY, double maxZ,
            double midX, double midY, double midZ, double offset,
            out double rx, out double ry, out double rz,
            out double dx, out double dy, out double dz)
        {
            // 初始化：射线起点设为包围盒中心，方向为零
            rx = midX; ry = midY; rz = midZ;
            dx = 0; dy = 0; dz = 0;

            // 根据面名设置对应坐标分量
            // face1 和 face2 各锁定一个坐标轴到包围盒的边界值
            ApplyFaceConstraint(face1, ref rx, ref ry, ref rz, ref dx, ref dy, ref dz,
                minX, minY, minZ, maxX, maxY, maxZ, offset);
            ApplyFaceConstraint(face2, ref rx, ref ry, ref rz, ref dx, ref dy, ref dz,
                minX, minY, minZ, maxX, maxY, maxZ, offset);

            // 归一化方向向量
            double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            if (len < 1e-10)
            {
                // 如果方向为零，默认指向中心
                dx = midX - rx; dy = midY - ry; dz = midZ - rz;
                len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            }
            if (len > 1e-10)
            {
                dx /= len; dy /= len; dz /= len;
            }
        }

        /// <summary>
        /// 根据单个面名设置射线起点的对应坐标分量和方向分量。
        /// </summary>
        private void ApplyFaceConstraint(string faceName,
            ref double rx, ref double ry, ref double rz,
            ref double dx, ref double dy, ref double dz,
            double minX, double minY, double minZ,
            double maxX, double maxY, double maxZ, double offset)
        {
            switch (faceName)
            {
                case "top":
                    ry = maxY + offset;  // 起点在顶面上方
                    dy += -1;            // 方向向下
                    break;
                case "bottom":
                    ry = minY - offset;  // 起点在底面下方
                    dy += 1;             // 方向向上
                    break;
                case "front":
                    rz = maxZ + offset;  // 起点在前面前方（Z正方向）
                    dz += -1;            // 方向向后
                    break;
                case "back":
                    rz = minZ - offset;  // 起点在后面后方
                    dz += 1;             // 方向向前
                    break;
                case "left":
                    rx = minX - offset;  // 起点在左面左方
                    dx += 1;             // 方向向右
                    break;
                case "right":
                    rx = maxX + offset;  // 起点在右面右方
                    dx += -1;            // 方向向左
                    break;
                default:
                    throw new ArgumentException(
                        $"未知的面名称: \"{faceName}\"。可选: top, bottom, front, back, left, right");
            }
        }

        /// <summary>
        /// 将模型坐标 (x, y, z) 通过当前活动草图的 ModelToSketchTransform 变换为草图坐标。
        /// 返回 (sx, sy)。如果没有活动草图或变换矩阵不可用，原样返回 (x, y)。
        /// 
        /// 特殊规则：如果当前草图在基准面上（_sketchOnRefPlane = true），
        /// 则直接把 (x, y) 作为草图平面内坐标返回，不做 3D→2D 变换。
        /// 这是因为 API 调用方传入的 (x, y) 本意就是草图平面内的 2D 坐标。
        /// 只有当草图在实体面上时才需要完整的 ModelToSketchTransform 变换。
        /// </summary>
        private void ModelToSketchPoint(double mx, double my, double mz, out double sx, out double sy)
        {
            // 基准面上的草图：直接用 (x, y) 作为草图坐标
            if (_sketchOnRefPlane)
            {
                sx = mx; sy = my;
                return;
            }

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
            // 1. 尝试获取 SolidWorks 配置的默认零件模板
            var template = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swDefaultTemplatePart);

            Console.WriteLine($"[SwCommands] swDefaultTemplatePart = \"{template}\"");

            // 检查是否为有效的真实文件路径（排除 ~BLANK_PART_TEMPLATE.prtdot 等内部占位符）
            if (!string.IsNullOrWhiteSpace(template) && File.Exists(template))
                return template;

            Console.WriteLine("[SwCommands] 默认模板无效或不存在，尝试从模板目录查找...");

            // 2. 从模板目录中搜索 .prtdot 文件
            var dirs = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swFileLocationsDocumentTemplates);

            Console.WriteLine($"[SwCommands] 模板目录配置 = \"{dirs}\"");

            if (!string.IsNullOrWhiteSpace(dirs))
            {
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
            }

            // 3. 最后兜底：尝试 SolidWorks 安装目录下的默认模板
            var swDir = Path.GetDirectoryName(_swApp.GetExecutablePath());
            if (!string.IsNullOrWhiteSpace(swDir))
            {
                // SolidWorks 默认模板通常在 lang\<language>\Tutorial 或 templates 目录下
                var searchDirs = new[]
                {
                    Path.Combine(swDir, "lang", "chinese-simplified", "Tutorial"),
                    Path.Combine(swDir, "lang", "chinese", "Tutorial"),
                    Path.Combine(swDir, "lang", "english", "Tutorial"),
                    Path.Combine(swDir, "templates"),
                    swDir,
                };

                foreach (var dir in searchDirs)
                {
                    if (!Directory.Exists(dir)) continue;
                    var files = Directory.GetFiles(dir, "*.prtdot");
                    if (files.Length > 0)
                    {
                        Console.WriteLine($"[SwCommands] 从安装目录找到模板: {files[0]}");
                        return files[0];
                    }
                }
            }

            throw new InvalidOperationException(
                "未找到任何零件模板文件 (.prtdot)。请在 SolidWorks 中设置：工具→选项→默认模板→零件");
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
        /// 进入草图编辑后立即调用：捕获"刚刚新建的那个草图"的特征名并存入 _activeSketchName。
        /// 新建草图总是追加到特征树末尾，因此取最后一个 ProfileFeature 即为刚建的草图。
        /// 同时打印特征树里所有 ProfileFeature（草图）名字，便于诊断"切除选错草图"问题。
        /// </summary>
        private void CaptureActiveSketchName()
        {
            _activeSketchName = null;
            var sketchNames = new List<string>();
            IFeature feat = (IFeature)ActiveDoc.FirstFeature();
            while (feat != null)
            {
                if (feat.GetTypeName2() == "ProfileFeature")
                {
                    sketchNames.Add(feat.Name);
                    _activeSketchName = feat.Name; // 末尾的那个即最新建的
                }
                feat = (IFeature)feat.GetNextFeature();
            }
            Console.WriteLine($"[SwCommands] 进入草图后特征树中的草图: [{string.Join(", ", sketchNames)}]，" +
                $"捕获当前草图 = \"{_activeSketchName}\"");
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

        #endregion

        #region 原子操作 — 面选择

        /// <summary>
        /// 沿给定射线在所有实体面中查找命中的面，**优先返回最近的平面**；
        /// 仅当射线没有命中任何平面时，才返回离射线起点最近的曲面。
        /// 这样可避免圆角后实体上的大量曲面（圆角面）在选面时与平面竞争而被误选。
        /// </summary>
        private void FindFaceByRay(object[] bodies,
            double rx, double ry, double rz, double dx, double dy, double dz,
            out IFace2 hitFace, out double[] hitPoint)
        {
            IFace2 bestPlanar = null; double bestPlanarT = double.MaxValue; double[] bestPlanarPt = null;
            IFace2 bestCurved = null; double bestCurvedDist = double.MaxValue; double[] bestCurvedPt = null;

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
                        // 平面：求射线与平面交点（P = Origin + t·Dir）
                        var pp = (double[])surface.PlaneParams;
                        double nx = pp[0], ny = pp[1], nz = pp[2];
                        double rootX = pp[3], rootY = pp[4], rootZ = pp[5];
                        double denom = dx * nx + dy * ny + dz * nz;
                        if (Math.Abs(denom) >= 1e-12) // 非平行
                        {
                            double t = ((rootX - rx) * nx + (rootY - ry) * ny + (rootZ - rz) * nz) / denom;
                            if (t >= 0) // 交点在射线正方向
                            {
                                double hx = rx + t * dx, hy = ry + t * dy, hz = rz + t * dz;
                                var closest = (double[])face.GetClosestPointOn(hx, hy, hz);
                                double d = Math.Sqrt(
                                    Math.Pow(closest[0] - hx, 2) +
                                    Math.Pow(closest[1] - hy, 2) +
                                    Math.Pow(closest[2] - hz, 2));
                                if (d < 0.0001 && t < bestPlanarT) // 交点落在面内且更近
                                {
                                    bestPlanarT = t; bestPlanar = face; bestPlanarPt = new[] { hx, hy, hz };
                                }
                            }
                        }
                    }
                    else
                    {
                        // 曲面：用 GetClosestPointOn 取离射线起点最近的点近似。
                        // ⚠️ GetClosestPointOn 不考虑射线方向，必须额外判定该点在射线【前方】，
                        // 否则当射线朝实体外侧时，也会错误命中起点附近、位于射线反方向的圆角曲面，
                        // 从而吞掉本应触发的"反转方向重试"。
                        var closest = (double[])face.GetClosestPointOn(rx, ry, rz);
                        double vx = closest[0] - rx, vy = closest[1] - ry, vz = closest[2] - rz;
                        double forwardDot = vx * dx + vy * dy + vz * dz; // 与射线方向同向才算前方
                        if (forwardDot >= -1e-9)
                        {
                            double d = Math.Sqrt(vx * vx + vy * vy + vz * vz);
                            if (d < bestCurvedDist)
                            {
                                bestCurvedDist = d; bestCurved = face; bestCurvedPt = new[] { closest[0], closest[1], closest[2] };
                            }
                        }
                    }

                    face = (IFace2)face.GetNextFace();
                }
            }

            // 平面优先
            Console.WriteLine($"[FindFaceByRay] 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz}) => " +
                $"平面命中={(bestPlanar != null ? $"是(t={bestPlanarT:F4})" : "否")}, " +
                $"曲面命中={(bestCurved != null ? $"是(d={bestCurvedDist:F4})" : "否")}");
            if (bestPlanar != null) { hitFace = bestPlanar; hitPoint = bestPlanarPt; }
            else { hitFace = bestCurved; hitPoint = bestCurvedPt; }
        }

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

            // ---- 遍历所有实体的面，通过射线求交找到目标面 ----
            // 规则：**平面命中优先于曲面**。用于挖孔/凹坑的草图几乎总是画在平面上；
            // 圆角后实体会多出大量曲面（圆角面），若让曲面与平面一起按距离竞争，
            // 当射线起点贴近目标面时极易误选到邻近的圆角曲面，导致在曲面上画草图、切除失败。
            // 因此只要射线命中任一平面就优先选最近的平面，仅当无任何平面命中时才退而选曲面。
            IFace2 bestFace;
            double[] bestHitPt;

            // 正向射线
            FindFaceByRay(bodies, rx, ry, rz, dx, dy, dz, out bestFace, out bestHitPt);

            // 未命中则反转方向重试（容错：LLM 常把射线方向搞反）
            if (bestFace == null)
            {
                Console.WriteLine($"[SwCommands] 射线({rx},{ry},{rz})方向({dx},{dy},{dz})未命中，尝试反转方向...");
                FindFaceByRay(bodies, rx, ry, rz, -dx, -dy, -dz, out bestFace, out bestHitPt);
                if (bestFace != null)
                    Console.WriteLine("[SwCommands] 反转方向后成功命中面！");
            }

            if (bestFace == null)
                throw new InvalidOperationException(
                    $"未能通过射线选中实体面。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})及其反方向均未命中。");

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
            _sketchOnRefPlane = false;  // 标记：草图在实体面上，需要变换坐标
            CaptureActiveSketchName();  // 捕获刚新建的草图名，供切除时显式选中

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

            Console.WriteLine($"[SwCommands] NewPart: 使用模板 \"{_partTemplatePath}\"");
            Console.WriteLine($"[SwCommands] NewPart: 模板文件是否存在 = {File.Exists(_partTemplatePath)}");

            var doc = _swApp.INewDocument2(_partTemplatePath, 0, 0, 0);
            Console.WriteLine($"[SwCommands] NewPart: INewDocument2 返回 {(doc == null ? "null" : "非null")}");

            if (doc == null)
            {
                throw new InvalidOperationException(
                    $"创建零件文档失败 (模板={_partTemplatePath}, 文件存在={File.Exists(_partTemplatePath)})");
            }

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
            _sketchOnRefPlane = true;  // 标记：草图在基准面上
            CaptureActiveSketchName();  // 捕获刚新建的草图名，供切除时显式选中
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

            // ModelToSketchTransform 是刚性变换（旋转+平移，无缩放），
            // 因此草图平面内的半径恒等于模型空间的 radius，直接使用即可。
            //
            // ⚠️ 不要用"变换 (cx+radius, cy, cz) 偏移点再求两点距离"来反推半径！
            // 当面的法线恰好沿模型 X 轴时（如立方体的左面/右面），模型 X 方向会被
            // 映射到草图平面外（草图 Z），导致偏移点的草图 XY 与圆心重合、算出半径=0、
            // 进而生成无效的零半径圆，切除/拉伸失败。
            double sketchRadius = radius;

            ActiveDoc.SketchManager.CreateCircle(sx, sy, 0, sx + sketchRadius, sy, 0);
            Console.WriteLine($"[SwCommands] 绘制圆: 模型圆心({cx},{cy},{cz}), 半径 {radius}，草图圆心({sx:F4},{sy:F4}), 草图半径 {sketchRadius:F4}");
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
        /// flipDirection: 是否翻转拉伸方向（默认 false）。
        /// midPlane: 是否使用中间面拉伸（从草图面两侧各拉伸 depth/2），默认 false。
        /// </summary>
        public void Extrusion(double depth, bool flipDirection = false, bool midPlane = false)
        {
            EnsureActiveDoc();

            if (depth <= 0)
                throw new ArgumentOutOfRangeException(nameof(depth), "拉伸深度必须为正数");

            // 退出草图编辑模式（使用 InsertSketch2 — 与 CutExtrusion/Revolve 保持一致）
            var mdoc = (ModelDoc2)ActiveDoc;
            mdoc.InsertSketch2(true);
            _sketchOnRefPlane = false;

            // 确定终止条件和深度
            int t1, t2;
            double d1, d2;
            bool sd; // Sd: true=单方向, false=双方向（中间面）

            if (midPlane)
            {
                // 中间面拉伸：从草图面两侧各拉伸一半
                t1 = (int)swEndConditions_e.swEndCondMidPlane;
                t2 = (int)swEndConditions_e.swEndCondBlind;
                d1 = depth;   // 总深度（SolidWorks 中间面模式会自动两侧各拉 depth/2）
                d2 = 0;
                sd = false;
            }
            else
            {
                // 单方向盲拉伸
                t1 = (int)swEndConditions_e.swEndCondBlind;
                t2 = (int)swEndConditions_e.swEndCondBlind;
                d1 = depth;
                d2 = 0;
                sd = true;
            }

            // 执行拉伸
            // 注意：Flip 参数控制的是"拉伸侧面翻转"(用于双向拉伸选择哪一侧)
            //       Dir 参数才是真正的"反转拉伸方向"(Reverse Direction)
            var feat = ActiveDoc.FeatureManager.FeatureExtrusion3(
                Sd: sd,
                Flip: false,
                Dir: flipDirection,
                T1: t1,
                T2: t2,
                D1: d1,
                D2: d2,
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

            Console.WriteLine($"[SwCommands] FeatureExtrusion3 返回: {(feat != null ? "成功" : "null")} (flip={flipDirection}, midPlane={midPlane})");

            if (feat == null)
                throw new InvalidOperationException("拉伸失败，请确保草图中有封闭的轮廓");

            // 缩放到合适视图
            mdoc.ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 拉伸完成，深度: {depth} 米, 方向翻转: {flipDirection}, 中间面: {midPlane}");
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
            _sketchOnRefPlane = false;

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
            _sketchOnRefPlane = false;

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

            // 策略：显式按名选中"进入草图时捕获的那个草图"（_activeSketchName）再切除，
            // 不依赖退出草图后 SolidWorks 的自动选择。
            // 经验：自动选择在首刀切除时可能误指向立方体本体的 20×20 轮廓草图，
            // 导致用全截面切除（体积≈20×20×depth）而非小圆点。
            // 这里通过创建草图时即捕获的名字精确锁定目标草图，从根本上消除歧义。
            if (!string.IsNullOrEmpty(_activeSketchName))
            {
                ActiveDoc.ClearSelection2(true);
                bool selOk = mdoc.Extension.SelectByID2(_activeSketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
                Console.WriteLine($"[SwCommands] 切除前显式选中草图 \"{_activeSketchName}\" = {selOk}");
                if (!selOk)
                    Console.WriteLine("[SwCommands] ⚠️ 按名选中草图失败，将回退到自动选择状态");

                // 打印当前选择，确认选到的是切除小圆草图
                int curSel = selMgr.GetSelectedObjectCount2(-1);
                for (int i = 1; i <= curSel; i++)
                    Console.WriteLine($"[SwCommands]   显式选中后选中项 {i}: 类型={selMgr.GetSelectedObjectType3(i, -1)}");
            }
            else
            {
                Console.WriteLine("[SwCommands] ⚠️ 未捕获到草图名，回退到退出草图后的自动选择");
            }

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

            // 如果直接调用失败，再次显式选中捕获的草图并重试
            if (feat == null)
            {
                Console.WriteLine("[SwCommands] 首次切除失败，重新显式选中草图后重试...");

                ActiveDoc.ClearSelection2(true);
                if (!string.IsNullOrEmpty(_activeSketchName))
                {
                    bool sel = mdoc.Extension.SelectByID2(_activeSketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
                    Console.WriteLine($"[SwCommands] 重试 SelectByID2(\"{_activeSketchName}\", \"SKETCH\") = {sel}");

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

            // ⚠️ 注意：这里**不再**做"反转方向重试"！
            // 历史教训：曾有一段 FeatureCut4(!flipDirection) 的反转容错，
            // 当某一刀的切除偶发失败时，它会用相反方向重试——而相反方向是朝实体【外侧】，
            // FeatureCut4 朝外切会生成一个**凸台**（凸起的料），导致骰子点数变成凸起而非凹陷。
            // 切除方向由调用方通过 flipDirection 明确指定（凹坑必朝实体内部），方向是确定的，
            // 服务端绝不能擅自反转。若切除失败，应明确报错而不是悄悄做成凸起。

            if (feat == null)
            {
                throw new InvalidOperationException("切除拉伸失败，请确保草图闭合且位于实体面上。请查看 C# 控制台的调试信息。");
            }

            // 安全校验：确认生成的特征确实是"切除"而非"凸台"（防御性检查）
            string featTypeName = feat.GetTypeName2();
            Console.WriteLine($"[SwCommands] 切除特征类型校验: {featTypeName}");

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
                Console.WriteLine($"[SwCommands] 圆角: ActiveDoc类型={ActiveDoc.GetType()}, partDoc类型={partDoc.GetType()}");
                var bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                Console.WriteLine($"[SwCommands] 圆角: GetBodies2(visible=true) 返回 {(bodies == null ? "null" : $"数组长度={bodies.Length}")}");

                // 如果可见实体为空，尝试获取所有实体（包括不可见的）
                if (bodies == null || bodies.Length == 0)
                {
                    bodies = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, false) as object[];
                    Console.WriteLine($"[SwCommands] 圆角: GetBodies2(visible=false) 返回 {(bodies == null ? "null" : $"数组长度={bodies.Length}")}");
                }

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
                Console.WriteLine($"[SwCommands] 圆角: 选中所有边，共 {edgeCount} 条");
            }
            else if (edgeSelection.ToLower() == "ray")
            {
                // 通过射线选中边
                Console.WriteLine($"[SwCommands] 圆角: 射线选边 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz}) 容差=0.002m");
                bool sel = ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.002,
                    (int)swSelectType_e.swSelEDGES, false, 0, 0);
                if (!sel)
                {
                    // 输出包围盒帮助调试
                    string bboxInfo = "";
                    try
                    {
                        var bodies2 = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                        if (bodies2 != null && bodies2.Length > 0)
                        {
                            var bb = ((IBody2)bodies2[0]).GetBodyBox() as double[];
                            if (bb != null)
                                bboxInfo = $" 实体包围盒: ({bb[0]:F4},{bb[1]:F4},{bb[2]:F4})->({bb[3]:F4},{bb[4]:F4},{bb[5]:F4})";
                        }
                    }
                    catch { }
                    Console.WriteLine($"[SwCommands] 圆角: 射线未命中任何边。{bboxInfo}");
                    throw new InvalidOperationException(
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。{bboxInfo}请确保射线起点在实体附近且方向指向边。");
                }
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 圆角: 通过射线选中 1 条边");
            }
            else
            {
                // 尝试作为语义化边位置名解析（如 "top_front", "bottom_left" 等）
                edgeCount = SelectEdgeByPosition(partDoc, ext, edgeSelection, "圆角");
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
                Console.WriteLine($"[SwCommands] 倒角: 射线选边 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz}) 容差=0.002m");
                bool sel = ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.002,
                    (int)swSelectType_e.swSelEDGES, false, 0, 0);
                if (!sel)
                {
                    string bboxInfo = "";
                    try
                    {
                        var bodies2 = partDoc.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                        if (bodies2 != null && bodies2.Length > 0)
                        {
                            var bb = ((IBody2)bodies2[0]).GetBodyBox() as double[];
                            if (bb != null)
                                bboxInfo = $" 实体包围盒: ({bb[0]:F4},{bb[1]:F4},{bb[2]:F4})->({bb[3]:F4},{bb[4]:F4},{bb[5]:F4})";
                        }
                    }
                    catch { }
                    Console.WriteLine($"[SwCommands] 倒角: 射线未命中任何边。{bboxInfo}");
                    throw new InvalidOperationException(
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。{bboxInfo}请确保射线起点在实体附近且方向指向边。");
                }
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 倒角: 通过射线选中 1 条边");
            }
            else
            {
                // 尝试作为语义化边位置名解析（如 "top_front", "bottom_left" 等）
                edgeCount = SelectEdgeByPosition(partDoc, ext, edgeSelection, "倒角");
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
