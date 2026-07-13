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
        /// draftAngleDeg: 拔模角度（度，默认 0=无拔模）。用于生成梯形/漏斗类零件——
        ///                例如键盘键帽底座、花盆、漏斗、锥台等。
        ///                取值范围 (0, 89.99)；0 表示无拔模。
        /// draftOutward: 拔模方向。false（默认）=向内收窄（顶面小于底面，常见如键帽/花盆）；
        ///               true=向外扩张（顶面大于底面，常见如漏斗）。
        /// </summary>
        public void Extrusion(
            double depth,
            bool flipDirection = false,
            bool midPlane = false,
            double draftAngleDeg = 0.0,
            bool draftOutward = false)
        {
            EnsureActiveDoc();

            if (depth <= 0)
                throw new ArgumentOutOfRangeException(nameof(depth), "拉伸深度必须为正数");
            if (draftAngleDeg < 0 || draftAngleDeg >= 90)
                throw new ArgumentOutOfRangeException(nameof(draftAngleDeg), "拔模角必须在 [0, 90) 度范围内");

            // 退出草图编辑模式（使用 InsertSketch2 — 与 CutExtrusion/Revolve 保持一致）
            var mdoc = (ModelDoc2)ActiveDoc;
            mdoc.InsertSketch2(true);
            _sketchOnRefPlane = false;

            // ⚠️ 多轮廓草图关键修复：显式选中"进入草图时捕获的那个草图"，
            // 不依赖退出草图后 SolidWorks 的自动选择。
            //
            // 问题背景：当同一个草图里有多个不相交的封闭轮廓（例如两个齿轮的圆盘同时画在
            // 前视基准面上），退出草图后 SolidWorks 的"自动选中"可能选空或选错，
            // 导致 FeatureExtrusion3 返回 null（错误信息："请确保草图中有封闭的轮廓"）。
            //
            // 修复：与 CutExtrusion 保持一致，用 _activeSketchName 显式按名选中草图。
            // 选中整个草图后 SolidWorks 会自动把草图中的所有封闭轮廓都作为拉伸源。
            if (!string.IsNullOrEmpty(_activeSketchName))
            {
                ActiveDoc.ClearSelection2(true);
                bool selOk = mdoc.Extension.SelectByID2(_activeSketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
                Console.WriteLine($"[SwCommands] 拉伸前显式选中草图 \"{_activeSketchName}\" = {selOk}");
                if (!selOk)
                    Console.WriteLine("[SwCommands] ⚠️ 按名选中草图失败，将回退到自动选择状态");
            }
            else
            {
                Console.WriteLine("[SwCommands] ⚠️ 未捕获到草图名，回退到退出草图后的自动选择");
            }

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

            // 拔模参数（SolidWorks API 用弧度）
            bool enableDraft = draftAngleDeg > 0;
            double draftRad = enableDraft ? draftAngleDeg * Math.PI / 180.0 : 0.0;

            // 执行拉伸
            // 注意：Flip 参数控制的是"拉伸侧面翻转"(用于双向拉伸选择哪一侧)
            //       Dir 参数才是真正的"反转拉伸方向"(Reverse Direction)
            //       Dchk1=true 启用侧一拔模; Ddir1=true 表示拔模方向向外(outward)。
            //       Dang1 用弧度。
            var feat = ActiveDoc.FeatureManager.FeatureExtrusion3(
                Sd: sd,
                Flip: false,
                Dir: flipDirection,
                T1: t1,
                T2: t2,
                D1: d1,
                D2: d2,
                Dchk1: enableDraft,
                Dchk2: false,
                Ddir1: draftOutward,
                Ddir2: false,
                Dang1: draftRad,
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

            Console.WriteLine(
                $"[SwCommands] FeatureExtrusion3 返回: {(feat != null ? "成功" : "null")} " +
                $"(flip={flipDirection}, midPlane={midPlane}, draftDeg={draftAngleDeg}, outward={draftOutward})");

            if (feat == null)
                throw new InvalidOperationException("拉伸失败，请确保草图中有封闭的轮廓");

            // 缩放到合适视图
            mdoc.ViewZoomtofit2();

            Console.WriteLine(
                $"[SwCommands] 拉伸完成，深度: {depth} 米, 方向翻转: {flipDirection}, " +
                $"中间面: {midPlane}, 拔模: {draftAngleDeg}° {(draftOutward ? "外扩" : "内收")}");
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

        /// <summary>
        /// 对最后一个创建的特征（拉伸/切除/圆角等）做圆周阵列，围绕指定坐标轴均匀复制多份。
        /// 常用于：齿轮的齿、法兰盘的螺栓孔、通风口的百叶等。
        ///
        /// 参数：
        ///   count      — 阵列总数（含原始特征本身，count >= 2）
        ///   axis       — 旋转轴，可选 "x"/"y"/"z"，默认 "y"。轴通过坐标原点。
        ///   angleDeg   — 阵列总覆盖角度（度），默认 360（完整圆周均分）；
        ///                若 < 360 则在此角度范围内均分。
        ///   reverseDir — 是否反转阵列方向，默认 false。
        ///
        /// 说明：
        ///   源特征使用当前零件最新创建的特征（自动定位为特征树末尾的非草图特征）。
        ///   使用 SolidWorks 的临时轴（Temporary Axes）作为旋转轴——即三个坐标基准面的交线（X/Y/Z 轴）。
        /// </summary>
        public Dictionary<string, object> CircularPattern(int count, string axis = "y",
            double angleDeg = 360, bool reverseDir = false,
            double? axisOriginX = null, double? axisOriginY = null, double? axisOriginZ = null)
        {
            EnsureActiveDoc();

            if (count < 2)
                throw new ArgumentOutOfRangeException(nameof(count), "阵列数必须 >= 2");
            if (angleDeg <= 0 || angleDeg > 360)
                throw new ArgumentOutOfRangeException(nameof(angleDeg), "阵列角度必须在 (0, 360] 之间");

            var mdoc = (ModelDoc2)ActiveDoc;
            var ext = mdoc.Extension;

            // ---- 1. 找到最后一个可阵列的源特征（跳过草图 ProfileFeature） ----
            IFeature sourceFeat = null;
            {
                IFeature f = (IFeature)mdoc.FirstFeature();
                while (f != null)
                {
                    string tn = f.GetTypeName2();
                    // 阵列源应是实体特征：拉伸、切除、圆角、倒角、旋转等，跳过草图与基准面
                    if (tn != "ProfileFeature" && tn != "RefPlane" && tn != "OriginProfileFeature"
                        && tn != "Sketch" && !tn.StartsWith("Ref"))
                    {
                        sourceFeat = f;
                    }
                    f = (IFeature)f.GetNextFeature();
                }
            }
            if (sourceFeat == null)
                throw new InvalidOperationException("找不到可作为阵列源的特征");

            Console.WriteLine($"[SwCommands] 阵列源特征: {sourceFeat.Name} (type={sourceFeat.GetTypeName2()})");

            // ---- 2. 选中旋转轴参考 ----
            // 策略：遍历实体的所有边，找一条**圆弧/圆形边**（IEdge.IGetCurve.IsCircle()）作为旋转轴——
            //       SolidWorks 圆周阵列可接受圆边的中心线作为轴。
            //       如果没有圆边（如立方体全是直线边），则退化：把源特征的法线方向作为轴——
            //       但目前先只支持"实体上必须有圆边"的场景（齿轮/法兰盘等都天然满足）。
            //       同时校验找到的圆边的法线方向与用户指定的 axis 一致（±0.9 以上余弦）。
            string axisLower = axis.ToLower();
            double[] wantDir;
            switch (axisLower)
            {
                case "x": wantDir = new[] { 1.0, 0.0, 0.0 }; break;
                case "y": wantDir = new[] { 0.0, 1.0, 0.0 }; break;
                case "z": wantDir = new[] { 0.0, 0.0, 1.0 }; break;
                default: throw new ArgumentException($"未知的轴向: {axis}，可选 x/y/z");
            }

            // ---- 2a. 找一个"轴线沿 wantDir 方向"的圆柱面（不是圆边！）----
            //         SolidWorks API 文档明确说圆周阵列的方向对象可以是:
            //           - IRefAxis (参考轴)
            //           - Cylindrical/Conical IFace2 (圆柱/圆锥面)
            //           - Linear IEdge (直线边)
            //         **不接受**普通"圆形 IEdge"作轴——历史代码用圆边选轴，SolidWorks 会
            //         创建"死特征"（树上有阵列节点但几何未生效，静默失败）。
            //         正确做法：找一个圆柱面，用它的轴线做圆周阵列的旋转轴。
            //
            // ⚠️ 多实体零件下的关键改进：
            //   若 axisOrigin(X,Y,Z) 给定，则不选任意一个匹配方向的圆柱面（这会导致在多实体
            //   零件里"绕错了实体"，例如两个齿轮各自的圆盘都有 Z 轴向圆柱面，遍历时先命中的
            //   那一个会被选中——阵列结果绕错圆心，10 个副本飞出实体外，SolidWorks 静默丢弃，
            //   只剩种子。修复：以 axisOrigin 为目标点，选**其轴线离这一点最近**的圆柱面。
            //   若未给 axisOrigin，退化为老行为（第一个匹配方向的圆柱面）。
            IFace2 chosenCylFace = null;
            double cylAxisX = 0, cylAxisY = 0, cylAxisZ = 0;
            bool hasOrigin = axisOriginX.HasValue && axisOriginY.HasValue && axisOriginZ.HasValue;
            double tx = axisOriginX ?? 0.0;
            double ty = axisOriginY ?? 0.0;
            double tz = axisOriginZ ?? 0.0;
            double bestDist = double.MaxValue;
            {
                var bodies = (object[])((IPartDoc)ActiveDoc).GetBodies2((int)swBodyType_e.swAllBodies, false);
                foreach (var bObj in bodies)
                {
                    var body = (IBody2)bObj;
                    var faces = (object[])body.GetFaces();
                    if (faces == null) continue;
                    foreach (var fObj in faces)
                    {
                        var face = (IFace2)fObj;
                        var surf = (ISurface)face.IGetSurface();
                        if (surf == null || !surf.IsCylinder()) continue;
                        // ISurface.CylinderParams 返回 [ox, oy, oz, ax, ay, az, radius]
                        //   (ox,oy,oz) = 圆柱轴上一点, (ax,ay,az) = 轴向量
                        var cp = (double[])surf.CylinderParams;
                        if (cp == null || cp.Length < 7) continue;
                        double ax = cp[3], ay = cp[4], az = cp[5];
                        double norm = Math.Sqrt(ax * ax + ay * ay + az * az);
                        if (norm < 1e-9) continue;
                        ax /= norm; ay /= norm; az /= norm;
                        double dot = Math.Abs(ax * wantDir[0] + ay * wantDir[1] + az * wantDir[2]);
                        if (dot <= 0.9) continue;

                        if (hasOrigin)
                        {
                            // 计算 axisOrigin 到轴线 (ox,oy,oz)+t*(ax,ay,az) 的垂直距离
                            double dx = tx - cp[0], dy = ty - cp[1], dz = tz - cp[2];
                            double proj = dx * ax + dy * ay + dz * az;
                            double px = dx - proj * ax, py = dy - proj * ay, pz = dz - proj * az;
                            double dist = Math.Sqrt(px * px + py * py + pz * pz);
                            if (dist < bestDist)
                            {
                                bestDist = dist;
                                chosenCylFace = face;
                                cylAxisX = ax; cylAxisY = ay; cylAxisZ = az;
                                Console.WriteLine($"[SwCommands] 候选圆柱面: 轴上一点=({cp[0]:F4},{cp[1]:F4},{cp[2]:F4}), 距目标点距离={dist:F4}m, R={cp[6]:F4}");
                            }
                        }
                        else
                        {
                            // 未给 axisOrigin：取第一个匹配方向的圆柱面（老行为）
                            chosenCylFace = face;
                            cylAxisX = ax; cylAxisY = ay; cylAxisZ = az;
                            Console.WriteLine($"[SwCommands] 找到圆柱面: 轴上一点=({cp[0]:F4},{cp[1]:F4},{cp[2]:F4}), 轴向=({ax:F3},{ay:F3},{az:F3}), R={cp[6]:F4}");
                            break;
                        }
                    }
                    if (chosenCylFace != null && !hasOrigin) break;
                }
                if (hasOrigin && chosenCylFace != null)
                    Console.WriteLine($"[SwCommands] 最终选中的圆柱面: 距 axisOrigin ({tx:F4},{ty:F4},{tz:F4}) 距离 {bestDist:F4}m");
            }
            if (chosenCylFace == null)
                throw new InvalidOperationException(
                    $"实体上找不到轴线沿 {axis} 方向的圆柱面。圆周阵列需要一个圆柱面（外圆柱或孔的内圆柱面）作为旋转轴。" +
                    $"典型场景：齿轮圆盘的外圆柱面 / 法兰盘的孔内圆柱面 / 螺栓孔的内壁。");

            // ---- 2b. 选中圆柱面后, 用 InsertAxis2 创建一条参考轴 ----
            //         SolidWorks 会自动识别"选中的是圆柱面"→ 沿其轴线创建参考轴。
            //         这条 axis 会作为独立特征添加到树末尾, 稳定可靠。
            ActiveDoc.ClearSelection2(true);
            bool faceSel = ((Entity)chosenCylFace).Select4(false, null);
            if (!faceSel)
                throw new InvalidOperationException("未能选中圆柱面(用于创建参考轴)");
            Console.WriteLine($"[SwCommands] 已选中圆柱面");

            bool axisCreated = mdoc.InsertAxis2(true);
            if (!axisCreated)
                throw new InvalidOperationException("InsertAxis2 返回 false, 未能创建参考轴");

            // 拿到刚创建的参考轴 = 特征树最后一个特征
            IFeature axisFeat = null;
            {
                IFeature f = (IFeature)mdoc.FirstFeature();
                while (f != null)
                {
                    if (f.GetTypeName2() == "RefAxis") axisFeat = f;
                    f = (IFeature)f.GetNextFeature();
                }
            }
            if (axisFeat == null)
                throw new InvalidOperationException("参考轴创建后未能在特征树中找到");
            Console.WriteLine($"[SwCommands] 已创建参考轴: {axisFeat.Name} (基于圆柱面)");

            // ---- 3. 有序选择: Mark=1 选轴, Mark=4 选源特征 ----
            //         SolidWorks API Remark: "You must select the features or components before selecting the axis"
            //         但也有资料说反过来 —— 我们按官方 CSharp 示例更常见的顺序: 先轴后特征。
            //         关键是两个 Mark 都必须存在, 顺序对 FeatureCircularPattern4/5 影响较小。
            ActiveDoc.ClearSelection2(true);
            if (!axisFeat.Select2(false, 1))
                throw new InvalidOperationException($"未能选中参考轴 {axisFeat.Name} (Mark=1)");
            Console.WriteLine($"[SwCommands] 已选中参考轴: {axisFeat.Name} (Mark=1)");

            if (!sourceFeat.Select2(true, 4))
                throw new InvalidOperationException($"未能选中源特征 {sourceFeat.Name} (Mark=4)");
            Console.WriteLine($"[SwCommands] 已选中源特征: {sourceFeat.Name} (Mark=4)");

            // ---- 4. 调用 FeatureCircularPattern4 (7 参数, 与 SW 2018+ 兼容且稳定) ----
            //   签名: (Number, Spacing, FlipDir, DName, GeometryPattern, EqualSpacing, VaryInstance)
            //   Spacing = 总角度 (弧度), EqualSpacing=true 时 SolidWorks 会自动均分。
            double angleRad = angleDeg * Math.PI / 180.0;

            var patternFeat = mdoc.FeatureManager.FeatureCircularPattern4(
                count,             // Number
                angleRad,          // Spacing (总角度, EqualSpacing=true 时 SW 均分)
                reverseDir,        // FlipDirection
                "NULL",            // DName
                false,             // GeometryPattern (false = 完整特征阵列, 更稳定)
                true,              // EqualSpacing (关键！360°/count 均匀分布)
                false              // VaryInstance
            );

            if (patternFeat == null)
                throw new InvalidOperationException("圆周阵列创建失败");

            mdoc.EditRebuild3();
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 圆周阵列创建成功: 特征={patternFeat.Name}, 数量={count}, 轴={axis}, 角度={angleDeg}°");

            return new Dictionary<string, object>
            {
                { "featureName", patternFeat.Name },
                { "sourceFeature", sourceFeat.Name },
                { "count", count },
                { "axis", axis },
                { "angleDeg", angleDeg },
                { "message", $"圆周阵列创建成功，绕 {axis.ToUpper()} 轴阵列 {count} 份，覆盖角度 {angleDeg}°" },
            };
        }

        /// <summary>
        /// 对最后一个创建的特征做**线性阵列**（Linear Pattern），沿 1 个或 2 个方向复制。
        /// 常用于：键盘按键矩阵、格栅通风孔、蜂窝板、齿条等。
        ///
        /// 参数：
        ///   num1      — 方向 1 的实例总数（含原始特征本身，num1 >= 2）
        ///   spacing1  — 方向 1 每份之间的间距（米，> 0）
        ///   dir1Axis  — 方向 1 的轴，可选 "x"/"y"/"z"，默认 "x"
        ///   num2      — 方向 2 的实例总数（可选，>= 1；=1 表示只做单向阵列）
        ///   spacing2  — 方向 2 每份之间的间距（米，num2>=2 时必填）
        ///   dir2Axis  — 方向 2 的轴，可选 "x"/"y"/"z"，与 dir1Axis 不同
        ///   flip1     — 是否反转方向 1（默认 false）
        ///   flip2     — 是否反转方向 2（默认 false）
        ///
        /// 说明：
        ///   源特征使用当前零件最新创建的实体特征（自动定位为特征树末尾的非草图非基准面特征）。
        ///   方向参考对象用**实体上一条与目标轴平行的直线边**（每个长方体天然有 12 条边）。
        ///   若找不到匹配方向的直线边（如零件只有球面/曲面），会报错。
        /// </summary>
        public Dictionary<string, object> LinearPattern(
            int num1, double spacing1, string dir1Axis,
            int num2, double spacing2, string dir2Axis,
            bool flip1, bool flip2)
        {
            EnsureActiveDoc();

            if (num1 < 2)
                throw new ArgumentOutOfRangeException(nameof(num1), "方向 1 实例数必须 >= 2");
            if (spacing1 <= 0)
                throw new ArgumentOutOfRangeException(nameof(spacing1), "方向 1 间距必须 > 0");
            if (num2 < 1)
                throw new ArgumentOutOfRangeException(nameof(num2), "方向 2 实例数必须 >= 1（=1 表示单向阵列）");
            if (num2 >= 2 && spacing2 <= 0)
                throw new ArgumentOutOfRangeException(nameof(spacing2), "方向 2 实例数 >=2 时间距必须 > 0");
            if (num2 >= 2 && dir1Axis.ToLower() == dir2Axis.ToLower())
                throw new ArgumentException("方向 1 与方向 2 的轴必须不同");

            var mdoc = (ModelDoc2)ActiveDoc;

            // ---- 1. 找源特征（跳过草图/基准面/参考几何） ----
            IFeature sourceFeat = null;
            {
                IFeature f = (IFeature)mdoc.FirstFeature();
                while (f != null)
                {
                    string tn = f.GetTypeName2();
                    if (tn != "ProfileFeature" && tn != "RefPlane" && tn != "OriginProfileFeature"
                        && tn != "Sketch" && !tn.StartsWith("Ref"))
                    {
                        sourceFeat = f;
                    }
                    f = (IFeature)f.GetNextFeature();
                }
            }
            if (sourceFeat == null)
                throw new InvalidOperationException("找不到可作为阵列源的特征");
            Console.WriteLine($"[SwCommands] 线性阵列源特征: {sourceFeat.Name} (type={sourceFeat.GetTypeName2()})");

            // ---- 2. 找方向 1 的参考直线边 ----
            IEdge dir1Edge = FindLinearEdgeAlong(dir1Axis);
            if (dir1Edge == null)
                throw new InvalidOperationException(
                    $"实体上找不到沿 {dir1Axis} 方向的直线边。线性阵列需要至少一条方向参考边。" +
                    $"典型场景：任何长方体/立方体天然有 12 条边（4×X + 4×Y + 4×Z）");
            Console.WriteLine($"[SwCommands] 找到方向 1 参考边（沿 {dir1Axis}）");

            IEdge dir2Edge = null;
            if (num2 >= 2)
            {
                dir2Edge = FindLinearEdgeAlong(dir2Axis);
                if (dir2Edge == null)
                    throw new InvalidOperationException(
                        $"实体上找不到沿 {dir2Axis} 方向的直线边。二维阵列需要两个方向的参考边。");
                Console.WriteLine($"[SwCommands] 找到方向 2 参考边（沿 {dir2Axis}）");
            }

            // ---- 3. 有序选择: Mark=1 方向 1 边, Mark=2 方向 2 边（如果有）, Mark=4 源特征 ----
            //   参考 SolidWorks API 文档: 
            //     IEntity::Select4 with ISelectData::Mark=1 for Direction 1
            //     IEntity::Select4 with ISelectData::Mark=2 and 4 to mark directions
            //     (Note: LinearPattern 的 Mark=2 是 Direction 2, Mark=4 是要阵列的特征)
            ActiveDoc.ClearSelection2(true);

            bool dir1Sel = ((Entity)dir1Edge).Select4(false, MakeSelData(1));
            if (!dir1Sel)
                throw new InvalidOperationException("未能选中方向 1 参考边 (Mark=1)");
            Console.WriteLine($"[SwCommands] 已选中方向 1 参考边 (Mark=1)");

            if (dir2Edge != null)
            {
                bool dir2Sel = ((Entity)dir2Edge).Select4(true, MakeSelData(2));
                if (!dir2Sel)
                    throw new InvalidOperationException("未能选中方向 2 参考边 (Mark=2)");
                Console.WriteLine($"[SwCommands] 已选中方向 2 参考边 (Mark=2)");
            }

            if (!sourceFeat.Select2(true, 4))
                throw new InvalidOperationException($"未能选中源特征 {sourceFeat.Name} (Mark=4)");
            Console.WriteLine($"[SwCommands] 已选中源特征: {sourceFeat.Name} (Mark=4)");

            // ---- 4. 调用 FeatureLinearPattern2（9 参数），完成后通过 ILinearPatternFeatureData 修正 D2PatternSeedOnly ----
            //   ⚠️ 关键坑：SolidWorks 的 LinearPattern 默认 D2PatternSeedOnly=true！
            //       表现为：二维阵列只生成 D1 行 + D2 列 L 形（N1+N2-1 个），而非完整 N1×N2 网格。
            //   FeatureLinearPattern5 虽然有 D2PatternSeedOnly 参数但本地 SW 上传 false 也未生效
            //       （疑似 COM 参数解析问题或版本兼容）。
            //   最可靠方案：先用 v2 建阵列，然后通过 GetDefinition() 拿到 ILinearPatternFeatureData
            //       并显式设 D2PatternSeedOnly=false 后 ModifyDefinition 提交，SolidWorks 会重建阵列。
            var patternFeat = mdoc.FeatureManager.FeatureLinearPattern2(
                num1, spacing1,
                num2, num2 >= 2 ? spacing2 : 0.001,
                flip1, flip2,
                "NULL", "NULL",
                false      // GeometryPattern
            );

            if (patternFeat == null)
                throw new InvalidOperationException("线性阵列创建失败（FeatureLinearPattern2 返回 null）");

            // ---- 5. 事后修正 D2PatternSeedOnly ⇒ 生成完整 N1×N2 网格 ----
            //   SolidWorks 默认 D2PatternSeedOnly=true (仅对种子做 D2 阵列, 忽略 D1 已生成的实例)
            //   → 二维阵列只出 L 形分布 N1+N2-1 个, 而非 N1×N2 完整网格
            //   通过 ILinearPatternFeatureData 显式设 false 后重新提交定义
            if (num2 >= 2)
            {
                try
                {
                    var defObj = patternFeat.GetDefinition();
                    Console.WriteLine($"[SwCommands] patternFeat.GetDefinition() 返回类型: {defObj?.GetType().FullName ?? "null"}");
                    var patternData = defObj as ILinearPatternFeatureData;
                    if (patternData == null)
                    {
                        Console.WriteLine("[SwCommands] ⚠️ GetDefinition 结果无法转为 ILinearPatternFeatureData");
                    }
                    else
                    {
                        Console.WriteLine($"[SwCommands] 修正前 D2PatternSeedOnly={patternData.D2PatternSeedOnly}, D1TotalInstances={patternData.D1TotalInstances}, D2TotalInstances={patternData.D2TotalInstances}");
                        patternData.D2PatternSeedOnly = false;
                        Console.WriteLine($"[SwCommands] 设置后 D2PatternSeedOnly={patternData.D2PatternSeedOnly}");
                        bool ok = patternFeat.ModifyDefinition(patternData, mdoc, null);
                        Console.WriteLine($"[SwCommands] ModifyDefinition 返回 {ok}");
                        // 再拿一次确认
                        var pd2 = patternFeat.GetDefinition() as ILinearPatternFeatureData;
                        if (pd2 != null)
                            Console.WriteLine($"[SwCommands] 修正后重读 D2PatternSeedOnly={pd2.D2PatternSeedOnly}");
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[SwCommands] ⚠️ 修正 D2PatternSeedOnly 异常: {ex.GetType().Name}: {ex.Message}");
                }
            }

            mdoc.EditRebuild3();
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            int totalCount = num1 * Math.Max(num2, 1);
            Console.WriteLine($"[SwCommands] 线性阵列创建成功: 特征={patternFeat.Name}, 方向1={num1}×{spacing1 * 1000:F1}mm, 方向2={num2}×{spacing2 * 1000:F1}mm, 总数={totalCount}");

            return new Dictionary<string, object>
            {
                { "featureName", patternFeat.Name },
                { "sourceFeature", sourceFeat.Name },
                { "num1", num1 },
                { "spacing1", spacing1 },
                { "dir1Axis", dir1Axis },
                { "num2", num2 },
                { "spacing2", spacing2 },
                { "dir2Axis", dir2Axis },
                { "totalCount", totalCount },
                { "message", $"线性阵列创建成功：方向 1 沿 {dir1Axis.ToUpper()} {num1} 份间距 {spacing1 * 1000:F1}mm，方向 2 沿 {dir2Axis.ToUpper()} {num2} 份间距 {spacing2 * 1000:F1}mm，共 {totalCount} 个实例" },
            };
        }

        /// <summary>
        /// 遍历实体所有边，找一条**方向平行于目标轴**（|cos| > 0.95）的**直线边**。
        /// 长方体天然有 12 条这样的边（每个轴向 4 条）；圆盘外圆虽是圆边不算。
        /// </summary>
        private IEdge FindLinearEdgeAlong(string axis)
        {
            double[] wantDir;
            switch (axis.ToLower())
            {
                case "x": wantDir = new[] { 1.0, 0.0, 0.0 }; break;
                case "y": wantDir = new[] { 0.0, 1.0, 0.0 }; break;
                case "z": wantDir = new[] { 0.0, 0.0, 1.0 }; break;
                default: throw new ArgumentException($"未知轴向: {axis}");
            }

            var bodies = (object[])((IPartDoc)ActiveDoc).GetBodies2((int)swBodyType_e.swAllBodies, false);
            if (bodies == null) return null;
            foreach (var bObj in bodies)
            {
                var body = (IBody2)bObj;
                var edges = (object[])body.GetEdges();
                if (edges == null) continue;
                foreach (var eObj in edges)
                {
                    var edge = (IEdge)eObj;
                    var curve = (ICurve)edge.IGetCurve();
                    if (curve == null || !curve.IsLine()) continue;
                    // ICurve.LineParams 返回 [rx, ry, rz, dx, dy, dz]：起点 + 方向单位向量
                    var lp = (double[])curve.LineParams;
                    if (lp == null || lp.Length < 6) continue;
                    double dx = lp[3], dy = lp[4], dz = lp[5];
                    double norm = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                    if (norm < 1e-9) continue;
                    dx /= norm; dy /= norm; dz /= norm;
                    // ⚠️ 关键: 不用 Math.Abs, 只接受"正方向"的边 (即 dot > 0.95)。
                    //         若接受负方向 (Math.Abs), 阵列会沿 -axis 走, 大概率飞出实体外
                    //         导致部分实例不生效。用户可通过 flip1/flip2 显式反向。
                    double dot = dx * wantDir[0] + dy * wantDir[1] + dz * wantDir[2];
                    if (dot > 0.95)
                    {
                        Console.WriteLine($"[SwCommands]   命中 {axis}+ 方向边: 起点=({lp[0]:F4},{lp[1]:F4},{lp[2]:F4}), 方向=({dx:F3},{dy:F3},{dz:F3})");
                        return edge;
                    }
                }
            }
            return null;
        }

        /// <summary>
        /// 创建一个 SelectData 对象并设置 Mark 值——SolidWorks API 里 IEntity.Select4
        /// 必须传 ISelectData，用 Mark 区分参考对象角色。
        /// </summary>
        private SelectData MakeSelData(int mark)
        {
            var sd = (SelectData)((ISelectionMgr)ActiveDoc.SelectionManager).CreateSelectData();
            sd.Mark = mark;
            return sd;
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
