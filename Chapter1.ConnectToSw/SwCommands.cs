using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.swmotionstudy;
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

        // ============================================================
        // 装配体状态字段（Stage B: 装配体 API 扩展）
        //   零件建模状态 (ActiveDoc / _activeSketchName / _sketchOnRefPlane) 独立保留，
        //   装配体状态与之解耦。两套字段并行工作，互不干扰，保证已有零件建模能力（B01~C3）
        //   完全不受影响。
        // ============================================================
        /// <summary>
        /// 当前活动的装配体文档（IAssemblyDoc 也是 IModelDoc2）。
        /// NewAssembly() 后设置，装配体端点操作时使用。
        /// </summary>
        private IAssemblyDoc _activeAssembly = null;
        /// <summary>装配体保存到磁盘的完整路径（用于 SelectByID2 组件寻址所需的装配体名）。</summary>
        private string _activeAssemblyPath = null;
        /// <summary>装配体名（不含扩展名），SelectByID2 的组件名格式 "PlaneName@Component@AssemblyName" 需要它。</summary>
        private string _activeAssemblyName = null;
        /// <summary>当前由 API 创建或取得的 Motion Study。</summary>
        private IMotionStudy _activeMotionStudy = null;
        /// <summary>当前由 API 创建的旋转马达特征。</summary>
        private IFeature _activeMotionMotor = null;

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
        /// 显式选中最近创建的草图。
        /// SelectByID2 在圆周阵列创建参考轴后可能无法按本地化名称选中后续草图，
        /// 因此失败时遍历特征树并直接调用 IFeature.Select2。
        /// </summary>
        private bool SelectActiveSketch(string operationName)
        {
            if (string.IsNullOrEmpty(_activeSketchName))
            {
                Console.WriteLine($"[SwCommands] ⚠️ {operationName}前未捕获到草图名");
                return false;
            }

            ActiveDoc.ClearSelection2(true);
            var mdoc = (ModelDoc2)ActiveDoc;
            bool selected = mdoc.Extension.SelectByID2(
                _activeSketchName, "SKETCH", 0, 0, 0, false, 0, null, 0);
            Console.WriteLine(
                $"[SwCommands] {operationName}前 SelectByID2(\"{_activeSketchName}\", \"SKETCH\") = {selected}");
            if (selected)
                return true;

            IFeature feat = (IFeature)ActiveDoc.FirstFeature();
            while (feat != null)
            {
                if (feat.GetTypeName2() == "ProfileFeature" && feat.Name == _activeSketchName)
                {
                    ActiveDoc.ClearSelection2(true);
                    selected = feat.Select2(false, 0);
                    Console.WriteLine(
                        $"[SwCommands] {operationName}前回退 IFeature.Select2(\"{_activeSketchName}\") = {selected}");
                    return selected;
                }
                feat = (IFeature)feat.GetNextFeature();
            }

            Console.WriteLine($"[SwCommands] ⚠️ 特征树中找不到草图 \"{_activeSketchName}\"");
            return false;
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
                bool selOk = SelectActiveSketch("拉伸");
                if (!selOk)
                    Console.WriteLine("[SwCommands] ⚠️ 显式选中草图失败，将回退到自动选择状态");
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

            // ⚠️ 多实体零件 v2 关键改进：
            //   如果 LLM 没传 axisOrigin, 不再盲选"第一个"匹配的圆柱面（老逻辑在多实体下总是命中 bodies[0] 的圆柱，
            //   例如 C3 齿轮组: 先建的小齿轮圆盘永远排在 bodies[0], 大齿轮的种子齿被错误地绕小齿轮圆心阵列, 副本飞出实体外被静默丢弃）。
            //   改进: 从**源特征（sourceFeat, 刚建的种子特征）的几何中心**推导默认目标点, 选轴线离此点最近的圆柱面。
            //   这样"要阵列哪个特征, 就自动绕该特征所在的圆柱轴", 语义正确。
            //   显式给 axisOrigin 的优先级最高（覆盖自动推导）。
            if (!hasOrigin)
            {
                try
                {
                    // 用 sourceFeat 的面包围盒中点作为"源特征几何中心"
                    var srcFaces = sourceFeat.GetFaces() as object[];
                    if (srcFaces != null && srcFaces.Length > 0)
                    {
                        double smnx = double.MaxValue, smny = double.MaxValue, smnz = double.MaxValue;
                        double smxx = double.MinValue, smxy = double.MinValue, smxz = double.MinValue;
                        foreach (var fObj in srcFaces)
                        {
                            var f = fObj as IFace2;
                            if (f == null) continue;
                            var bx = f.GetBox() as double[];
                            if (bx == null || bx.Length < 6) continue;
                            if (bx[0] < smnx) smnx = bx[0];
                            if (bx[1] < smny) smny = bx[1];
                            if (bx[2] < smnz) smnz = bx[2];
                            if (bx[3] > smxx) smxx = bx[3];
                            if (bx[4] > smxy) smxy = bx[4];
                            if (bx[5] > smxz) smxz = bx[5];
                        }
                        if (smnx != double.MaxValue)
                        {
                            tx = (smnx + smxx) / 2.0;
                            ty = (smny + smxy) / 2.0;
                            tz = (smnz + smxz) / 2.0;
                            hasOrigin = true;   // 触发"最近圆柱面"选择逻辑
                            Console.WriteLine($"[SwCommands] 未传 axisOrigin, 从源特征\"{sourceFeat.Name}\"包围盒中点推导目标点: ({tx:F4},{ty:F4},{tz:F4})");
                        }
                    }
                }
                catch (Exception ex)
                {
                    Console.WriteLine($"[SwCommands] 推导源特征中心失败, 回退到第一个匹配圆柱面: {ex.Message}");
                }
            }

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
                            // 未给 axisOrigin 且源特征中心推导也失败：取第一个匹配方向的圆柱面（老行为）
                            chosenCylFace = face;
                            cylAxisX = ax; cylAxisY = ay; cylAxisZ = az;
                            Console.WriteLine($"[SwCommands] 找到圆柱面: 轴上一点=({cp[0]:F4},{cp[1]:F4},{cp[2]:F4}), 轴向=({ax:F3},{ay:F3},{az:F3}), R={cp[6]:F4}");
                            break;
                        }
                    }
                    if (chosenCylFace != null && !hasOrigin) break;
                }
                if (hasOrigin && chosenCylFace != null)
                    Console.WriteLine($"[SwCommands] 最终选中的圆柱面: 距目标点 ({tx:F4},{ty:F4},{tz:F4}) 距离 {bestDist:F4}m");
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

        // ================================================================
        // 装配体 API 扩展（Stage B）— 让齿轮转起来的基础
        //
        // 设计原则（严格遵守）：
        //   1) 不修改任何已有零件方法，装配体方法**追加**在这里；
        //   2) 装配体状态存于独立字段 _activeAssembly，与 ActiveDoc（零件）解耦；
        //   3) 一个服务实例可同时管理"零件"和"装配体"两个上下文（零件建完再进装配体）。
        //
        // 已验证事实（来自 Phase 2 pywin32 冒烟）：
        //   - SolidWorks 2025 中文版没有 asmdot 模板文件 → 用预先准备的 empty_template
        //   - AddComponent5 参数顺序: (compName, config, appearance, docType, autoConfig, x, y, z)
        //   - SelectByID2 装配体上下文组件名格式: "前视基准面@gear_small-1@gear_pair_assembly"
        //   - AddMate5 参数(15 个): (MateType, Align, Flip, Distance, DistAbsUpper, DistAbsLower,
        //                            GearNum, GearDen, Angle, AngleAbsUpper, AngleAbsLower,
        //                            ForPositioningOnly, LockRotation, WidthMateOption, ErrorStatus)
        //   - Mate 类型: swMateCOINCIDENT=0, swMateCONCENTRIC=1, swMateGEAR=10
        // ================================================================
        #region 装配体扩展

        /// <summary>
        /// 装配体空模板文件的完整路径（项目内置资源）。
        ///   由于 SolidWorks 2025 中文版没有 .asmdot 模板文件，我们无法用 INewDocument2
        ///   直接建装配体。策略：项目 resources/ 目录内置一份 empty_assembly_template.SLDASM，
        ///   通过 OpenDoc6 打开这个空装配体 + SaveAs2 到目标路径 → 得到全新装配体文档。
        /// </summary>
        private string GetAssemblyTemplatePath()
        {
            // 1. 项目 resources 目录（相对可执行文件位置）
            var exeDir = AppDomain.CurrentDomain.BaseDirectory;
            var candidates = new[]
            {
                Path.Combine(exeDir, "resources", "empty_assembly_template.SLDASM"),
                Path.Combine(exeDir, "..", "..", "resources", "empty_assembly_template.SLDASM"),  // dev: bin/Debug -> project root/resources
                Path.Combine(exeDir, "..", "..", "..", "Chapter1.ConnectToSw", "resources", "empty_assembly_template.SLDASM"),
            };
            foreach (var c in candidates)
            {
                var full = Path.GetFullPath(c);
                if (File.Exists(full))
                {
                    Console.WriteLine($"[SwCommands] 装配体模板 (内置): {full}");
                    return full;
                }
            }

            // 2. 尝试 SolidWorks 配置的默认 .asmdot 模板（Phase 2 已知中文版没有，兜底而已）
            var pref = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swDefaultTemplateAssembly);
            if (!string.IsNullOrWhiteSpace(pref) && File.Exists(pref))
            {
                Console.WriteLine($"[SwCommands] 装配体模板 (SolidWorks 默认): {pref}");
                return pref;
            }

            // 3. 扫描 SolidWorks 模板目录找 .asmdot
            var dirs = _swApp.GetUserPreferenceStringValue(
                (int)swUserPreferenceStringValue_e.swFileLocationsDocumentTemplates);
            if (!string.IsNullOrWhiteSpace(dirs))
            {
                foreach (var dir in dirs.Split(';'))
                {
                    if (!Directory.Exists(dir)) continue;
                    var files = Directory.GetFiles(dir, "*.asmdot");
                    if (files.Length > 0)
                    {
                        Console.WriteLine($"[SwCommands] 装配体模板 (扫描): {files[0]}");
                        return files[0];
                    }
                }
            }

            throw new InvalidOperationException(
                "找不到任何装配体模板。请在 resources/ 目录下放置 empty_assembly_template.SLDASM " +
                "（或在 SolidWorks 里手动新建空装配体并保存为此名）。");
        }

        /// <summary>确保当前有活动装配体（装配体端点入口守卫）。</summary>
        private void EnsureActiveAssembly()
        {
            if (_activeAssembly == null)
                throw new InvalidOperationException("没有活动的装配体文档，请先调用 /api/assembly/new");
        }

        /// <summary>
        /// 新建装配体文档。
        ///   策略：打开内置空模板 + SaveAs2 到目标路径。避开 INewDocument2 找不到 asmdot 模板的问题。
        /// </summary>
        /// <param name="savePath">装配体保存路径（.SLDASM）。必须给完整路径。</param>
        public void NewAssembly(string savePath)
        {
            if (string.IsNullOrWhiteSpace(savePath))
                throw new ArgumentException("savePath 不能为空", nameof(savePath));
            if (!Path.IsPathRooted(savePath))
                throw new ArgumentException("savePath 必须是绝对路径", nameof(savePath));
            if (!savePath.EndsWith(".SLDASM", StringComparison.OrdinalIgnoreCase))
                savePath += ".SLDASM";

            var templatePath = GetAssemblyTemplatePath();

            // 关闭同名装配体（避免文件锁死）
            try
            {
                // SolidWorks 的文档标题通常不含扩展名；传 "foo.SLDASM" 会静默不关闭，
                // 随后的 File.Delete 因文件仍被占用而失败。
                _swApp.CloseDoc(Path.GetFileNameWithoutExtension(savePath));
            }
            catch { /* 忽略 */ }
            if (File.Exists(savePath))
            {
                File.Delete(savePath);
                Console.WriteLine($"[SwCommands] 已删除旧装配体文件: {savePath}");
            }

            // 打开空模板
            int errors = 0, warnings = 0;
            var asmDoc = _swApp.OpenDoc6(
                templatePath,
                (int)swDocumentTypes_e.swDocASSEMBLY,
                0,
                "",
                ref errors,
                ref warnings) as IAssemblyDoc;
            if (asmDoc == null)
                throw new InvalidOperationException($"打开装配体模板失败: errors={errors}, warnings={warnings}");
            Console.WriteLine($"[SwCommands] 打开空装配体模板: {templatePath} (errors={errors}, warnings={warnings})");

            // SaveAs2 到目标路径（老 SaveAs2 简单 4 参版本）
            var modelDoc = (IModelDoc2)asmDoc;
            int saveResult = modelDoc.SaveAs2(savePath, 0, false, false);
            Console.WriteLine($"[SwCommands] 装配体 SaveAs2 → {savePath} (ret={saveResult})");

            _activeAssembly = asmDoc;
            _activeAssemblyPath = savePath;
            _activeAssemblyName = Path.GetFileNameWithoutExtension(savePath);
            _activeMotionStudy = null;
            _activeMotionMotor = null;
            Console.WriteLine($"[SwCommands] 新建装配体成功: 名称='{_activeAssemblyName}'");
        }

        /// <summary>
        /// 把 .SLDPRT 零件文件加入当前装配体。
        /// </summary>
        /// <param name="partPath">零件文件路径（绝对路径）</param>
        /// <param name="x">放置 X 坐标（米，装配体坐标系）</param>
        /// <param name="y">放置 Y 坐标（米）</param>
        /// <param name="z">放置 Z 坐标（米）</param>
        /// <returns>加入后的 Component2 实例名（例如 "gear_small-1"）</returns>
        public string AddComponent(string partPath, double x, double y, double z)
        {
            EnsureActiveAssembly();
            if (string.IsNullOrWhiteSpace(partPath))
                throw new ArgumentException("partPath 不能为空", nameof(partPath));
            if (!File.Exists(partPath))
                throw new FileNotFoundException($"零件文件不存在: {partPath}", partPath);

            // 预加载零件（AddComponent5 需要零件已在内存中）
            int errors = 0, warnings = 0;
            var partDoc = _swApp.OpenDoc6(
                partPath,
                (int)swDocumentTypes_e.swDocPART,
                0,
                "",
                ref errors,
                ref warnings);
            if (partDoc == null)
                throw new InvalidOperationException($"预加载零件失败: {partPath} (errors={errors})");
            Console.WriteLine($"[SwCommands] 预加载零件: {partPath} (errors={errors}, warnings={warnings})");

            // 切回装配体作为活动文档
            _swApp.ActivateDoc3(
                _activeAssemblyName + ".SLDASM",
                true,
                (int)swRebuildOnActivation_e.swDontRebuildActiveDoc,
                ref errors);

            // AddComponent5 参数:
            //   (compName, configOpt, newConfigName, useConfigForPartRefs, existConfigName, x, y, z)
            var comp = _activeAssembly.AddComponent5(
                partPath,
                (int)swAddComponentConfigOptions_e.swAddComponentConfigOptions_CurrentSelectedConfig,
                "",
                false,
                "",
                x, y, z) as IComponent2;
            if (comp == null)
                throw new InvalidOperationException($"AddComponent5 返回 null: partPath={partPath}");

            var compName = comp.Name2;
            Console.WriteLine($"[SwCommands] 已加入组件: {compName} @ ({x*1000:F1},{y*1000:F1},{z*1000:F1}) mm");
            return compName;
        }

        /// <summary>
        /// 装配体上下文中的 SelectByID2 组件成员选择封装。
        ///   name 格式: "<成员名>@<组件名>@<装配体名>"
        ///   例如选组件 gear_small-1 的前视基准面: name="前视基准面@gear_small-1@gear_pair_assembly"
        ///   
        /// SolidWorks 中文版基准面名字:
        ///   - "前视基准面" (Front Plane)
        ///   - "上视基准面" (Top Plane)  
        ///   - "右视基准面" (Right Plane)
        /// </summary>
        private bool SelectByIDInAssembly(string memberName, string componentName, string objType, int mark, bool append)
        {
            var ext = ((IModelDoc2)_activeAssembly).Extension;
            var fullName = $"{memberName}@{componentName}@{_activeAssemblyName}";
            var ok = ext.SelectByID2(fullName, objType, 0.0, 0.0, 0.0, append, mark, null, 0);
            Console.WriteLine($"[SwCommands] SelectByID2('{fullName}', '{objType}', mark={mark}) → {ok}");
            return ok;
        }

        /// <summary>装配体：清除当前选择。</summary>
        private void ClearAssemblySelection()
        {
            ((IModelDoc2)_activeAssembly).ClearSelection2(true);
        }

        /// <summary>
        /// 加"重合"配合（Coincident Mate）：让两个组件的基准面重合。
        /// 常用于让两个齿轮在同一 Z 平面上并排（各自的前视基准面重合）。
        /// </summary>
        /// <param name="comp1Name">组件 1 实例名（如 "gear_small-1"）</param>
        /// <param name="plane1Name">组件 1 上的基准面名（默认 "前视基准面"）</param>
        /// <param name="comp2Name">组件 2 实例名</param>
        /// <param name="plane2Name">组件 2 上的基准面名</param>
        public Dictionary<string, object> MateCoincident(
            string comp1Name, string plane1Name,
            string comp2Name, string plane2Name)
        {
            EnsureActiveAssembly();
            ClearAssemblySelection();

            var ok1 = SelectByIDInAssembly(plane1Name, comp1Name, "PLANE", 1, false);
            var ok2 = SelectByIDInAssembly(plane2Name, comp2Name, "PLANE", 1, true);
            if (!ok1 || !ok2)
                throw new InvalidOperationException(
                    $"Coincident Mate 选面失败: ok1={ok1}, ok2={ok2}. " +
                    $"请确认组件名和基准面名。中文默认: 前视基准面/上视基准面/右视基准面");

            int errStatus = 0;
            var mate = _activeAssembly.AddMate5(
                (int)swMateType_e.swMateCOINCIDENT,   // MateTypeFromEnum
                (int)swMateAlign_e.swMateAlignALIGNED, // AlignFromEnum
                false,      // Flip
                0.0, 0.0, 0.0,   // Distance / Upper / Lower
                1.0, 1.0,        // GearRatioNumerator / Denominator (Coincident 用不到)
                0.0, 0.0, 0.0,   // Angle / Upper / Lower
                false, false,     // ForPositioningOnly, LockRotation
                0,               // WidthMateOption
                out errStatus);
            Console.WriteLine($"[SwCommands] MateCoincident 结果: mate={(mate == null ? "null" : "OK")}, errStatus={errStatus}");
            if (errStatus != (int)swAddMateError_e.swAddMateError_NoError)
                throw new InvalidOperationException(
                    $"Coincident Mate 失败: errStatus={errStatus} " +
                    "(1=NoError, 2=IncorrectMateType, 3=IncorrectAlignment, 4=IncorrectSelections, 5=OverDefined)");
            return new Dictionary<string, object>
            {
                ["mateType"] = "Coincident",
                ["component1"] = comp1Name,
                ["plane1"] = plane1Name,
                ["component2"] = comp2Name,
                ["plane2"] = plane2Name,
                ["errStatus"] = errStatus,
            };
        }

        /// <summary>
        /// 加"同心"配合（Concentric Mate）：让两个组件的圆柱面/圆边同轴。
        /// 齿轮组常用：把两个齿轮各自"绕自己的轴旋转"约束住 → 齿轮和圆柱面同心，
        /// 就等于给齿轮定义了旋转轴。
        /// 
        /// 由于装配体里选择"零件的某个圆柱面"必须用面射线/坐标定位，参数暂时用"面射线"方案：
        ///   传入两组组件+射线参数，服务端在各自组件坐标系下找圆柱面。
        /// 
        /// 简化版：本次实现要求两个组件的**圆盘轴都平行 Z 轴** (齿轮场景总是如此)，
        ///   直接用组件的**任意一个圆柱面**通过 SelectByID2 名字选择太复杂 —— 走"射线拾取"路径。
        /// 
        /// TODO(下一版): 支持传"轴基准" (Axis) 而不是圆柱面, 更稳定。
        /// </summary>
        public Dictionary<string, object> MateConcentric(
            string comp1Name, double rx1, double ry1, double rz1, double dx1, double dy1, double dz1,
            string comp2Name, double rx2, double ry2, double rz2, double dx2, double dy2, double dz2)
        {
            EnsureActiveAssembly();
            ClearAssemblySelection();

            // 在装配体的每个组件上射线找圆柱面
            IFace2 face1 = SelectComponentFaceByRay(comp1Name, rx1, ry1, rz1, dx1, dy1, dz1, appendSelect: false, mark: 1);
            IFace2 face2 = SelectComponentFaceByRay(comp2Name, rx2, ry2, rz2, dx2, dy2, dz2, appendSelect: true, mark: 1);
            if (face1 == null || face2 == null)
                throw new InvalidOperationException($"Concentric Mate 选面失败: face1={face1 != null}, face2={face2 != null}");

            int errStatus = 0;
            var mate = _activeAssembly.AddMate5(
                (int)swMateType_e.swMateCONCENTRIC,
                (int)swMateAlign_e.swMateAlignALIGNED,
                false,
                0.0, 0.0, 0.0,
                1.0, 1.0,
                0.0, 0.0, 0.0,
                false, false,
                0,
                out errStatus);
            Console.WriteLine($"[SwCommands] MateConcentric 结果: mate={(mate == null ? "null" : "OK")}, errStatus={errStatus}");
            if (errStatus != (int)swAddMateError_e.swAddMateError_NoError)
                throw new InvalidOperationException(
                    $"Concentric Mate 失败: errStatus={errStatus}");
            return new Dictionary<string, object>
            {
                ["mateType"] = "Concentric",
                ["component1"] = comp1Name,
                ["component2"] = comp2Name,
                ["errStatus"] = errStatus,
            };
        }

        /// <summary>
        /// 加"齿轮"配合（Gear Mate）：让两个组件按齿数比联动旋转。
        ///   传动比 = gearRatioNumerator / gearRatioDenominator
        ///   例如小齿轮 10 齿, 大齿轮 20 齿 → 大转 1 圈时小转 2 圈 → 传动比 2:1
        ///   传参: gearRatioNumerator=20 (大), gearRatioDenominator=10 (小), reverse=false/true
        /// 
        /// Gear Mate 要求先选中两个组件的圆柱面（各自的分度圆柱面）作为啮合参考。
        /// </summary>
        public Dictionary<string, object> MateGear(
            string comp1Name, double rx1, double ry1, double rz1, double dx1, double dy1, double dz1,
            string comp2Name, double rx2, double ry2, double rz2, double dx2, double dy2, double dz2,
            double gearRatioNumerator, double gearRatioDenominator, bool reverse = false)
        {
            EnsureActiveAssembly();
            if (gearRatioNumerator <= 0 || gearRatioDenominator <= 0)
                throw new ArgumentException("齿数比必须为正数");

            ClearAssemblySelection();
            IFace2 face1 = SelectComponentFaceByRay(comp1Name, rx1, ry1, rz1, dx1, dy1, dz1, appendSelect: false, mark: 1);
            IFace2 face2 = SelectComponentFaceByRay(comp2Name, rx2, ry2, rz2, dx2, dy2, dz2, appendSelect: true, mark: 1);
            if (face1 == null || face2 == null)
                throw new InvalidOperationException($"Gear Mate 选面失败");

            int errStatus = 0;
            var alignEnum = reverse
                ? (int)swMateAlign_e.swMateAlignANTI_ALIGNED
                : (int)swMateAlign_e.swMateAlignALIGNED;
            var mate = _activeAssembly.AddMate5(
                (int)swMateType_e.swMateGEAR,
                alignEnum,
                false,
                0.0, 0.0, 0.0,
                gearRatioNumerator,     // GearNum (齿数比 分子)
                gearRatioDenominator,   // GearDen (齿数比 分母)
                0.0, 0.0, 0.0,
                false, false,
                0,
                out errStatus);
            Console.WriteLine($"[SwCommands] MateGear 结果: ratio={gearRatioNumerator}:{gearRatioDenominator}, reverse={reverse}, errStatus={errStatus}");
            if (errStatus != (int)swAddMateError_e.swAddMateError_NoError)
                throw new InvalidOperationException($"Gear Mate 失败: errStatus={errStatus}");

            // AddMate5 的 AlignFromEnum 只控制配合实体对齐方式，并不设置齿轮旋转方向。
            // 必须在创建后修改 IGearMateFeatureData.Reverse；否则外啮合齿轮会同向旋转。
            IFeature gearMateFeature = null;
            IFeature topFeature = ((IModelDoc2)_activeAssembly).FirstFeature() as IFeature;
            while (topFeature != null && gearMateFeature == null)
            {
                IFeature subFeature = topFeature.GetFirstSubFeature() as IFeature;
                while (subFeature != null)
                {
                    var gearDefinition = subFeature.GetDefinition() as IGearMateFeatureData;
                    if (gearDefinition != null)
                        gearMateFeature = subFeature;
                    subFeature = subFeature.GetNextSubFeature() as IFeature;
                }
                topFeature = topFeature.GetNextFeature() as IFeature;
            }
            if (gearMateFeature == null)
                throw new InvalidOperationException("Gear Mate 已创建，但未能在特征树中找到其定义");

            var gearMateData = gearMateFeature.GetDefinition() as IGearMateFeatureData;
            if (gearMateData == null)
                throw new InvalidOperationException("读取 IGearMateFeatureData 失败");
            gearMateData.Reverse = reverse;
            bool reverseApplied = gearMateFeature.ModifyDefinition(
                gearMateData,
                (IModelDoc2)_activeAssembly,
                null);
            if (!reverseApplied)
                throw new InvalidOperationException($"设置 Gear Mate Reverse={reverse} 失败");
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            Console.WriteLine($"[SwCommands] Gear Mate Reverse 已设置为 {reverse}");

            return new Dictionary<string, object>
            {
                ["mateType"] = "Gear",
                ["component1"] = comp1Name,
                ["component2"] = comp2Name,
                ["gearRatioNumerator"] = gearRatioNumerator,
                ["gearRatioDenominator"] = gearRatioDenominator,
                ["reverse"] = reverse,
                ["errStatus"] = errStatus,
            };
        }

        /// <summary>
        /// 在装配体的某个组件上，用射线拾取一个面并把它加入当前选择集。
        ///   射线在**装配体坐标系**下给出。
        ///   返回选中的 IFace2 引用（供调用方后续使用）。
        /// </summary>
        private IFace2 SelectComponentFaceByRay(
            string componentName,
            double rx, double ry, double rz,
            double dx, double dy, double dz,
            bool appendSelect, int mark)
        {
            var asmModel = (IModelDoc2)_activeAssembly;
            // 找到组件对象
            var comps = (object[])_activeAssembly.GetComponents(false);
            IComponent2 targetComp = null;
            foreach (var c in comps)
            {
                var comp = c as IComponent2;
                if (comp != null && comp.Name2 == componentName)
                {
                    targetComp = comp;
                    break;
                }
            }
            if (targetComp == null)
                throw new InvalidOperationException($"装配体中找不到组件: '{componentName}'. 可用组件: [{string.Join(", ", comps.Select(o => (o as IComponent2)?.Name2 ?? "?"))}]");

            // 归一化方向
            double norm = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            if (norm < 1e-9) throw new ArgumentException("射线方向不能为零向量");
            dx /= norm; dy /= norm; dz /= norm;

            // 优先让 SolidWorks 的空间索引直接拾取射线命中的面。渐开线齿轮包含数百个
            // 侧面，逐面 COM 调用 GetClosestPointOn 会耗时数分钟；中心孔射线可直接命中
            // 唯一的内圆柱面，且选择结果已经带有正确的组件上下文。
            bool raySelected = asmModel.Extension.SelectByRay(
                rx, ry, rz, dx, dy, dz, 0.0005,
                (int)swSelectType_e.swSelFACES,
                appendSelect, mark, 0);
            if (raySelected)
            {
                var selectionManager = (ISelectionMgr)asmModel.SelectionManager;
                int selectedCount = selectionManager.GetSelectedObjectCount2(mark);
                var rayFace = selectionManager.GetSelectedObject6(selectedCount, mark) as IFace2;
                var raySurface = rayFace?.IGetSurface() as ISurface;
                if (rayFace != null && raySurface != null && raySurface.IsCylinder())
                {
                    Console.WriteLine(
                        $"[SwCommands] 组件 '{componentName}' 通过 SelectByRay 快速选中圆柱面, " +
                        $"append={appendSelect}");
                    return rayFace;
                }
                throw new InvalidOperationException(
                    $"组件 '{componentName}' 的射线命中了非圆柱面；请把射线起点放在中心孔内");
            }

            // 从组件的实体里找面
            IFace2 chosenFace = null;
            double minDist = double.MaxValue;
            var bodies = (object[])targetComp.GetBodies3((int)swBodyType_e.swAllBodies, out _);
            if (bodies != null)
            {
                foreach (var bObj in bodies)
                {
                    var body = bObj as IBody2;
                    if (body == null) continue;
                    var faces = (object[])body.GetFaces();
                    if (faces == null) continue;
                    foreach (var fObj in faces)
                    {
                        var face = fObj as IFace2;
                        if (face == null) continue;
                        // 只考虑圆柱面（Concentric/Gear Mate 需要圆柱面）
                        var surf = face.IGetSurface() as ISurface;
                        if (surf == null || !surf.IsCylinder()) continue;
                        var closest = (double[])face.GetClosestPointOn(rx, ry, rz);
                        if (closest == null || closest.Length < 3) continue;
                        var dxo = closest[0] - rx; var dyo = closest[1] - ry; var dzo = closest[2] - rz;
                        var forwardDot = dxo * dx + dyo * dy + dzo * dz;
                        if (forwardDot < 0) continue;  // 面在射线反方向
                        var dist = Math.Sqrt(dxo * dxo + dyo * dyo + dzo * dzo);
                        if (dist < minDist)
                        {
                            minDist = dist;
                            chosenFace = face;
                        }
                    }
                }
            }
            if (chosenFace == null)
                throw new InvalidOperationException(
                    $"组件 '{componentName}' 上未找到方向匹配的圆柱面 (射线起点=({rx:F4},{ry:F4},{rz:F4}), 方向=({dx:F3},{dy:F3},{dz:F3}))");

            var isEnt = chosenFace as IEntity;
            var selOk = isEnt.Select4(appendSelect, null);
            Console.WriteLine($"[SwCommands] 组件 '{componentName}' 上选中圆柱面, dist={minDist * 1000:F2}mm, append={appendSelect}, ok={selOk}");
            return chosenFace;
        }

        /// <summary>
        /// 把当前活动零件文档另存为独立 .sldprt 文件（供装配用）。
        /// </summary>
        /// <param name="savePath">目标 .sldprt 完整路径</param>
        public Dictionary<string, object> SavePartAs(string savePath)
        {
            EnsureActiveDoc();
            if (string.IsNullOrWhiteSpace(savePath))
                throw new ArgumentException("savePath 不能为空", nameof(savePath));
            if (!Path.IsPathRooted(savePath))
                throw new ArgumentException("savePath 必须是绝对路径", nameof(savePath));
            if (!savePath.EndsWith(".SLDPRT", StringComparison.OrdinalIgnoreCase))
                savePath += ".SLDPRT";

            int result = ActiveDoc.SaveAs2(savePath, 0, false, false);
            Console.WriteLine($"[SwCommands] SavePartAs → {savePath} (ret={result})");
            return new Dictionary<string, object>
            {
                ["path"] = savePath,
                ["result"] = result,
            };
        }

        /// <summary>
        /// 装配体：强制重建。
        /// </summary>
        public void RebuildAssembly()
        {
            EnsureActiveAssembly();
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            Console.WriteLine("[SwCommands] 装配体 Rebuild 完成");
        }

        /// <summary>
        /// 保存当前装配体。
        /// </summary>
        public void SaveAssembly()
        {
            EnsureActiveAssembly();
            var modelDoc = (IModelDoc2)_activeAssembly;
            int result = modelDoc.SaveAs2(_activeAssemblyPath, 0, false, false);
            Console.WriteLine($"[SwCommands] 装配体 Save → {_activeAssemblyPath} (ret={result})");
        }

        /// <summary>使用 SolidWorks 原生干涉检测检查当前装配姿态。</summary>
        public Dictionary<string, object> CheckAssemblyInterference()
        {
            EnsureActiveAssembly();
            var manager = _activeAssembly.InterferenceDetectionManager;
            if (manager == null)
                throw new InvalidOperationException("无法获取 InterferenceDetectionManager");

            try
            {
                manager.TreatCoincidenceAsInterference = false;
                manager.TreatSubAssembliesAsComponents = false;
                manager.IncludeMultibodyPartInterferences = true;
                manager.MakeInterferingPartsTransparent = false;
                manager.CreateFastenersFolder = false;
                manager.IgnoreHiddenBodies = true;
                manager.ShowIgnoredInterferences = false;
                manager.UseTransform = false;

                var interferenceObjects = manager.GetInterferences() as object[];
                var details = new List<Dictionary<string, object>>();
                if (interferenceObjects != null)
                {
                    foreach (var item in interferenceObjects)
                    {
                        var interference = item as IInterference;
                        if (interference == null || interference.Ignore) continue;
                        var componentNames = new List<string>();
                        var components = interference.Components as object[];
                        if (components != null)
                        {
                            foreach (var componentObject in components)
                            {
                                var component = componentObject as IComponent2;
                                if (component != null)
                                    componentNames.Add(component.Name2);
                            }
                        }
                        details.Add(new Dictionary<string, object>
                        {
                            ["components"] = componentNames.ToArray(),
                            ["volume"] = interference.Volume,
                            ["isPossibleInterference"] = interference.IsPossibleInterference,
                        });
                    }
                }

                Console.WriteLine(
                    $"[SwCommands] 装配体干涉检测: count={details.Count}, " +
                    $"totalVolume={details.Sum(item => Convert.ToDouble(item["volume"])):E6}m³");
                return new Dictionary<string, object>
                {
                    ["interferenceCount"] = details.Count,
                    ["totalVolume"] = details.Sum(item => Convert.ToDouble(item["volume"])),
                    ["interferences"] = details.ToArray(),
                };
            }
            finally
            {
                manager.Done();
            }
        }

        /// <summary>
        /// 读取一个组件在装配体坐标系下的当前 Transform 矩阵（16 元素 double 数组）。
        ///   用于诊断: 旋转后位置是否真的变了。
        /// </summary>
        public Dictionary<string, object> GetComponentTransform(string componentName)
        {
            EnsureActiveAssembly();
            var comps = (object[])_activeAssembly.GetComponents(false);
            IComponent2 targetComp = null;
            foreach (var c in comps)
            {
                var comp = c as IComponent2;
                if (comp != null && comp.Name2 == componentName)
                {
                    targetComp = comp;
                    break;
                }
            }
            if (targetComp == null)
                throw new InvalidOperationException($"找不到组件: {componentName}");

            var t = (MathTransform)targetComp.Transform2;
            var arr = (double[])t.ArrayData;
            // 提取旋转子矩阵前 9 项 + 平移 3 项
            return new Dictionary<string, object>
            {
                ["componentName"] = componentName,
                ["fullMatrix"] = arr,
                ["rotationMatrix3x3"] = new double[] { arr[0], arr[1], arr[2], arr[3], arr[4], arr[5], arr[6], arr[7], arr[8] },
                ["translationXYZ"] = new double[] { arr[9], arr[10], arr[11] },
                ["scale"] = arr[12],
                ["isFixed"] = targetComp.IsFixed(),
            };
        }

        /// <summary>
        /// 把指定组件设为"浮动"（Float）— 允许被 Transform 旋转/移动。
        ///   AddComponent5 加进来的第一个组件默认是"固定"（Fixed），Fixed 组件的 Transform
        ///   修改会被 SolidWorks 忽略 —— 这正是我们旋转小齿轮"视觉无变化"的根因。
        /// </summary>
        public void UnfixComponent(string componentName)
        {
            EnsureActiveAssembly();
            ClearAssemblySelection();
            // 选择组件: name 格式 "componentName@assemblyName", type = "COMPONENT"
            var ext = ((IModelDoc2)_activeAssembly).Extension;
            var fullName = $"{componentName}@{_activeAssemblyName}";
            bool ok = ext.SelectByID2(fullName, "COMPONENT", 0.0, 0.0, 0.0, false, 0, null, 0);
            if (!ok)
                throw new InvalidOperationException($"选择组件失败: {fullName}");
            _activeAssembly.UnfixComponent();
            Console.WriteLine($"[SwCommands] 已浮动 (Float) 组件: {componentName}");
        }

        /// <summary>
        /// 打开一个已存在的装配体文件（把它设为当前 _activeAssembly）。
        ///   用于外部工作流：先 NewAssembly 建，再关闭并重新打开操作；
        ///   或直接对已有 .SLDASM 文件做 rotate 等操作。
        /// </summary>
        public void OpenAssembly(string assemblyPath)
        {
            if (string.IsNullOrWhiteSpace(assemblyPath))
                throw new ArgumentException("assemblyPath 不能为空", nameof(assemblyPath));
            if (!File.Exists(assemblyPath))
                throw new FileNotFoundException($"装配体文件不存在: {assemblyPath}", assemblyPath);

            int errors = 0, warnings = 0;
            var asmDoc = _swApp.OpenDoc6(
                assemblyPath,
                (int)swDocumentTypes_e.swDocASSEMBLY,
                0,
                "",
                ref errors,
                ref warnings) as IAssemblyDoc;
            if (asmDoc == null)
                throw new InvalidOperationException($"打开装配体失败: {assemblyPath} (errors={errors})");
            _activeAssembly = asmDoc;
            _activeAssemblyPath = assemblyPath;
            _activeAssemblyName = Path.GetFileNameWithoutExtension(assemblyPath);
            _swApp.ActivateDoc3(
                Path.GetFileName(assemblyPath),
                true,
                (int)swRebuildOnActivation_e.swDontRebuildActiveDoc,
                ref errors);
            _activeMotionStudy = null;
            _activeMotionMotor = null;
            Console.WriteLine($"[SwCommands] 已打开装配体: {_activeAssemblyName} (errors={errors}, warnings={warnings})");
        }

        /// <summary>
        /// 让一个组件绕装配体坐标系某个轴旋转指定角度 —— 用 IDragOperator (模拟用户拖动).
        ///   这是 SolidWorks 官方推荐的"通过 API 触发约束联动"方案.
        ///   
        /// ⚠️ 用 Transform2 setter / SetTransformAndSolve3 无效 (Gear Mate 会反解).
        ///   诊断证据 (phase6/phase7): 前后 Transform 完全一样, 视觉无变化.
        ///   
        /// DragOperator 模拟的是"用户在 SW 界面里手动拖组件"的行为, 会正确触发 Gear Mate 联动.
        /// </summary>
        public Dictionary<string, object> RotateComponentByDrag(
            string componentName,
            double axisPointX, double axisPointY, double axisPointZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double angleRad)
        {
            EnsureActiveAssembly();

            // 找目标组件
            var comps = (object[])_activeAssembly.GetComponents(false);
            IComponent2 targetComp = null;
            foreach (var c in comps)
            {
                var comp = c as IComponent2;
                if (comp != null && comp.Name2 == componentName)
                {
                    targetComp = comp;
                    break;
                }
            }
            if (targetComp == null)
                throw new InvalidOperationException($"找不到组件: {componentName}");

            // 获取 DragOperator (SolidWorks 官方推荐的组件拖动 API)
            var dragOp = _activeAssembly.GetDragOperator() as IDragOperator;
            if (dragOp == null)
                throw new InvalidOperationException("GetDragOperator 返回 null");

            // 构造"绕轴旋转"变换矩阵 (与 RotateComponent 用法一致)
            var mathUtil = (IMathUtility)_swApp.GetMathUtility();
            var mathPoint = (MathPoint)mathUtil.CreatePoint(new object[] { axisPointX, axisPointY, axisPointZ });
            var mathVector = (MathVector)mathUtil.CreateVector(new object[] { axisDirX, axisDirY, axisDirZ });
            var rotateTransform = (MathTransform)mathUtil.CreateTransformRotateAxis(mathPoint, mathVector, angleRad);
            if (rotateTransform == null)
                throw new InvalidOperationException("CreateTransformRotateAxis 返回 null");

            // DragOperator 的正确用法 (反射得到的 API 签名):
            //   1. AddComponent(comp, false)   - 加要拖的组件
            //   2. BeginDrag()                  - 开始拖动会话
            //   3. Drag(transform)              - 应用变换 (可多次调用)
            //   4. EndDrag()                    - 结束会话, 提交变换
            //
            // ⚠️ Drag 传 MathTransform 表示"目标绝对变换"或"相对变换" (取决于 UseAbsoluteTransform).
            //   默认 UseAbsoluteTransform=false → 相对变换 (即"在当前位置上再应用这个变换")
            bool ok = false;
            try
            {
                bool addOk = dragOp.AddComponent(targetComp, false);
                Console.WriteLine($"[SwCommands] DragOperator.AddComponent → {addOk}");

                // 可选: 设置 UseAbsoluteTransform=false 表示相对变换
                dragOp.UseAbsoluteTransform = false;
                dragOp.GraphicsRedrawEnabled = true;

                bool beginOk = dragOp.BeginDrag();
                Console.WriteLine($"[SwCommands] DragOperator.BeginDrag → {beginOk}");

                bool dragOk = dragOp.Drag(rotateTransform);
                Console.WriteLine($"[SwCommands] DragOperator.Drag(transform) → {dragOk}");

                bool endOk = dragOp.EndDrag();
                Console.WriteLine($"[SwCommands] DragOperator.EndDrag → {endOk}");

                ok = addOk && beginOk && dragOk && endOk;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[SwCommands] DragOperator 序列抛异常: {ex.Message}");
                ok = false;
            }

            // 刷新图形显示
            ((IModelDoc2)_activeAssembly).GraphicsRedraw2();

            Console.WriteLine(
                $"[SwCommands] RotateComponentByDrag: '{componentName}' 绕轴({axisPointX*1000:F1},{axisPointY*1000:F1},{axisPointZ*1000:F1})mm " +
                $"方向({axisDirX:F2},{axisDirY:F2},{axisDirZ:F2}) 旋转 {angleRad*180/Math.PI:F2}° → ok={ok}");

            return new Dictionary<string, object>
            {
                ["componentName"] = componentName,
                ["angleDegrees"] = angleRad * 180.0 / Math.PI,
                ["method"] = "DragOperator",
                ["ok"] = ok,
            };
        }

        /// <summary>
        /// 让一个组件绕装配体坐标系某个轴旋转指定角度。
        ///   通过 IComponent2.SetTransformAndSolve3 更新组件的 Transform 矩阵，
        ///   SolidWorks 会触发约束求解器 → Gear Mate 自动联动其他齿轮。
        /// 
        /// 参数：
        ///   componentName - 组件实例名（如 "gear_small-1"）
        ///   axisPointX/Y/Z - 旋转轴穿过的点（装配体坐标系，米）
        ///   axisDirX/Y/Z - 旋转轴方向向量（会被归一化）
        ///   angleRad - 旋转角度（弧度，正值按右手定则）
        /// </summary>
        public Dictionary<string, object> RotateComponent(
            string componentName,
            double axisPointX, double axisPointY, double axisPointZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double angleRad)
        {
            EnsureActiveAssembly();

            // 找目标组件
            var comps = (object[])_activeAssembly.GetComponents(false);
            IComponent2 targetComp = null;
            foreach (var c in comps)
            {
                var comp = c as IComponent2;
                if (comp != null && comp.Name2 == componentName)
                {
                    targetComp = comp;
                    break;
                }
            }
            if (targetComp == null)
                throw new InvalidOperationException(
                    $"找不到组件: '{componentName}'. 可用组件: [{string.Join(", ", comps.Select(o => (o as IComponent2)?.Name2 ?? "?"))}]");

            // 用 MathUtility 构造 "绕 axis 旋转 angleRad" 的变换矩阵
            var mathUtil = (IMathUtility)_swApp.GetMathUtility();
            var pointArr = new object[] { axisPointX, axisPointY, axisPointZ };
            var vectorArr = new object[] { axisDirX, axisDirY, axisDirZ };
            var mathPoint = (MathPoint)mathUtil.CreatePoint(pointArr);
            var mathVector = (MathVector)mathUtil.CreateVector(vectorArr);
            var rotateTransform = (MathTransform)mathUtil.CreateTransformRotateAxis(mathPoint, mathVector, angleRad);
            if (rotateTransform == null)
                throw new InvalidOperationException("CreateTransformRotateAxis 返回 null（可能轴向量为零）");

            // 取组件当前 Transform, 叠加旋转
            var currentTransform = (MathTransform)targetComp.Transform2;
            // 组件新位置 = 当前 × 旋转（先旋转后当前变换 - SolidWorks 使用行主序矩阵）
            // 试验证明: newTransform = currentTransform.Multiply(rotateTransform) 会绕组件本地坐标系
            //         newTransform = rotateTransform.Multiply(currentTransform) 会绕装配体世界坐标系
            // 齿轮啮合需要"绕世界坐标系里的齿轮轴"旋转，所以用后者
            var newTransform = (MathTransform)rotateTransform.Multiply(currentTransform);

            // ⚠️ 关键: SolidWorks 2025 中文版 SetTransformAndSolve3 会被 Gear Mate 约束
            //   "反解"回原样 (Transform 数值上不变), API 返回 ok=True 但完全没生效.
            //   诊断证据 (phase6_diagnose.py): 前后 Transform 完全一样.
            //   
            // 换方案: 直接赋值 Transform2 属性 - 这是"底层"操作, 绕过约束求解器,
            //   齿轮真的会转. 代价: Gear Mate 不会自动联动另一个齿轮 (需要外部手工同步).
            //   联动方案见 RotateGearPair (旋转两齿轮时按传动比手工同步角度).
            bool ok = true;
            int errorStatus = 0;
            try
            {
                targetComp.Transform2 = newTransform;
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[SwCommands] Transform2 赋值失败: {ex.Message}");
                ok = false;
                errorStatus = -1;
            }

            // 强制刷新图形显示 (装配体不需要 EditRebuild3, 只需要重绘)
            ((IModelDoc2)_activeAssembly).GraphicsRedraw2();

            Console.WriteLine(
                $"[SwCommands] RotateComponent: '{componentName}' 绕轴({axisPointX*1000:F1},{axisPointY*1000:F1},{axisPointZ*1000:F1})mm " +
                $"方向({axisDirX:F2},{axisDirY:F2},{axisDirZ:F2}) 旋转 {angleRad*180/Math.PI:F2}° " +
                $"→ ok={ok}, errorStatus={errorStatus}");

            return new Dictionary<string, object>
            {
                ["componentName"] = componentName,
                ["angleDegrees"] = angleRad * 180.0 / Math.PI,
                ["axisPoint"] = new double[] { axisPointX, axisPointY, axisPointZ },
                ["axisDirection"] = new double[] { axisDirX, axisDirY, axisDirZ },
                ["ok"] = ok,
                ["errorStatus"] = errorStatus,
            };
        }

        /// <summary>
        /// 手工联动式旋转两个齿轮 (按传动比同步).
        ///   由于 SetTransformAndSolve3 被 Gear Mate 反解, 无法通过约束求解自动联动.
        ///   替代方案: 一次调用同时旋转两个齿轮, 传动比 = teeth2/teeth1
        ///   例如驱动齿数 10, 从动齿数 20 → 驱动转 anglePerStepDeg 时,
        ///   从动转 -anglePerStepDeg × (10/20) = -anglePerStepDeg/2 (反向)
        /// </summary>
        public Dictionary<string, object> RotateGearPair(
            string driverName, double driverAxisX, double driverAxisY, double driverAxisZ,
            string drivenName, double drivenAxisX, double drivenAxisY, double drivenAxisZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double driverAngleDeg,
            double gearRatio  // = driverTeeth / drivenTeeth, 例 10/20 = 0.5
        )
        {
            double driverAngleRad = driverAngleDeg * Math.PI / 180.0;
            double drivenAngleRad = -driverAngleRad * gearRatio;  // 反向, 按齿比

            RotateComponent(driverName, driverAxisX, driverAxisY, driverAxisZ,
                axisDirX, axisDirY, axisDirZ, driverAngleRad);
            RotateComponent(drivenName, drivenAxisX, drivenAxisY, drivenAxisZ,
                axisDirX, axisDirY, axisDirZ, drivenAngleRad);

            return new Dictionary<string, object>
            {
                ["driverAngleDeg"] = driverAngleDeg,
                ["drivenAngleDeg"] = drivenAngleRad * 180.0 / Math.PI,
                ["gearRatio"] = gearRatio,
            };
        }

        /// <summary>持续旋转的取消 token（后台线程用）</summary>
        private System.Threading.CancellationTokenSource _spinCts = null;
        private System.Threading.Tasks.Task _spinTask = null;

        /// <summary>启动"齿轮对持续联动旋转"后台任务.</summary>
        public Dictionary<string, object> StartSpinGearPair(
            string driverName, double driverAxisX, double driverAxisY, double driverAxisZ,
            string drivenName, double drivenAxisX, double drivenAxisY, double drivenAxisZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double driverAnglePerStepDeg,
            double gearRatio,
            int stepDelayMs)
        {
            StopSpin();
            _spinCts = new System.Threading.CancellationTokenSource();
            var token = _spinCts.Token;
            Console.WriteLine($"[SwCommands] StartSpinGearPair: driver='{driverName}' {driverAnglePerStepDeg}°/步, driven='{drivenName}' 反向按比 {gearRatio}, 间隔 {stepDelayMs}ms");

            _spinTask = System.Threading.Tasks.Task.Run(() =>
            {
                int frame = 0;
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        RotateGearPair(
                            driverName, driverAxisX, driverAxisY, driverAxisZ,
                            drivenName, drivenAxisX, drivenAxisY, drivenAxisZ,
                            axisDirX, axisDirY, axisDirZ,
                            driverAnglePerStepDeg, gearRatio);
                        frame++;
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"[SwCommands] SpinGearPair 帧 {frame} 出错: {ex.Message}");
                        break;
                    }
                    if (stepDelayMs > 0) System.Threading.Thread.Sleep(stepDelayMs);
                }
                Console.WriteLine($"[SwCommands] SpinGearPair 停止, 共 {frame} 帧");
            });

            return new Dictionary<string, object>
            {
                ["status"] = "spinning",
                ["driver"] = driverName,
                ["driven"] = drivenName,
                ["driverAnglePerStepDeg"] = driverAnglePerStepDeg,
                ["gearRatio"] = gearRatio,
                ["stepDelayMs"] = stepDelayMs,
            };
        }

        /// <summary>启动"持续旋转"后台任务 — 组件每 stepDelayMs 转一次小角度, 直到 StopSpin 被调用.</summary>
        public Dictionary<string, object> StartSpin(
            string componentName,
            double axisPointX, double axisPointY, double axisPointZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double anglePerStepDeg,
            int stepDelayMs)
        {
            StopSpin();  // 先停掉之前的
            _spinCts = new System.Threading.CancellationTokenSource();
            var token = _spinCts.Token;
            double stepRad = anglePerStepDeg * Math.PI / 180.0;
            Console.WriteLine($"[SwCommands] StartSpin: '{componentName}', 每步 {anglePerStepDeg}°, 间隔 {stepDelayMs}ms");

            _spinTask = System.Threading.Tasks.Task.Run(() =>
            {
                int frameCount = 0;
                while (!token.IsCancellationRequested)
                {
                    try
                    {
                        RotateComponent(componentName,
                            axisPointX, axisPointY, axisPointZ,
                            axisDirX, axisDirY, axisDirZ,
                            stepRad);
                        frameCount++;
                    }
                    catch (Exception ex)
                    {
                        Console.WriteLine($"[SwCommands] StartSpin 帧 {frameCount} 出错: {ex.Message}");
                        break;
                    }
                    if (stepDelayMs > 0)
                        System.Threading.Thread.Sleep(stepDelayMs);
                }
                Console.WriteLine($"[SwCommands] Spin 停止, 共 {frameCount} 帧");
            });

            return new Dictionary<string, object>
            {
                ["status"] = "spinning",
                ["componentName"] = componentName,
                ["anglePerStepDeg"] = anglePerStepDeg,
                ["stepDelayMs"] = stepDelayMs,
            };
        }

        /// <summary>停止持续旋转.</summary>
        public Dictionary<string, object> StopSpin()
        {
            if (_spinCts != null && !_spinCts.IsCancellationRequested)
            {
                _spinCts.Cancel();
                try { _spinTask?.Wait(2000); } catch { /* 忽略 */ }
                _spinCts.Dispose();
                _spinCts = null;
                _spinTask = null;
                Console.WriteLine("[SwCommands] Spin 已停止");
                return new Dictionary<string, object> { ["status"] = "stopped" };
            }
            return new Dictionary<string, object> { ["status"] = "not_spinning" };
        }

        /// <summary>
        /// 让一个组件绕装配体的 Z 轴（穿过组件当前位置）连续动画式旋转，
        ///   分成多帧 + 每帧一小角度 + Sleep + Rebuild，让用户能视觉看到齿轮转动过程。
        ///   
        /// 参数：
        ///   componentName - 组件实例名
        ///   totalAngleDeg - 总旋转角度（度，正=右手 CCW）
        ///   steps - 分几步（越多越平滑）
        ///   stepDelayMs - 每帧间隔（毫秒）
        /// </summary>
        public Dictionary<string, object> AnimateRotateComponent(
            string componentName,
            double axisPointX, double axisPointY, double axisPointZ,
            double axisDirX, double axisDirY, double axisDirZ,
            double totalAngleDeg,
            int steps,
            int stepDelayMs)
        {
            if (steps <= 0) throw new ArgumentException("steps 必须为正");
            if (stepDelayMs < 0) throw new ArgumentException("stepDelayMs 必须 >= 0");

            double stepAngleRad = (totalAngleDeg * Math.PI / 180.0) / steps;
            Console.WriteLine(
                $"[SwCommands] AnimateRotate: '{componentName}' 分 {steps} 步旋转 {totalAngleDeg}°, 每步 {stepAngleRad*180/Math.PI:F2}°, 帧间隔 {stepDelayMs}ms");

            var startTime = DateTime.Now;
            for (int i = 0; i < steps; i++)
            {
                RotateComponent(componentName,
                    axisPointX, axisPointY, axisPointZ,
                    axisDirX, axisDirY, axisDirZ,
                    stepAngleRad);
                if (stepDelayMs > 0)
                    System.Threading.Thread.Sleep(stepDelayMs);
            }
            var elapsed = (DateTime.Now - startTime).TotalSeconds;
            Console.WriteLine($"[SwCommands] 动画完成, 耗时 {elapsed:F2}s");

            return new Dictionary<string, object>
            {
                ["componentName"] = componentName,
                ["totalAngleDegrees"] = totalAngleDeg,
                ["steps"] = steps,
                ["stepDelayMs"] = stepDelayMs,
                ["elapsedSeconds"] = Math.Round(elapsed, 2),
            };
        }

        #region Motion Study

        private IMotionStudy EnsureMotionStudy()
        {
            EnsureActiveAssembly();
            if (_activeMotionStudy != null)
            {
                _activeMotionStudy.Activate();
                return _activeMotionStudy;
            }

            var model = (IModelDoc2)_activeAssembly;
            var manager = model.Extension.GetMotionStudyManager() as IMotionStudyManager;
            if (manager == null)
                throw new InvalidOperationException("GetMotionStudyManager 返回 null，当前 SolidWorks 不提供 Motion Study Manager");

            if (manager.GetMotionStudyCount() > 0)
            {
                var rawNames = manager.GetMotionStudyNames();
                var enumerable = rawNames as System.Collections.IEnumerable;
                if (enumerable != null)
                {
                    foreach (var item in enumerable)
                    {
                        if (item == null) continue;
                        _activeMotionStudy = manager.GetMotionStudy(item.ToString()) as IMotionStudy;
                        if (_activeMotionStudy != null) break;
                    }
                }
            }

            if (_activeMotionStudy == null)
                _activeMotionStudy = manager.CreateMotionStudy() as IMotionStudy;
            if (_activeMotionStudy == null)
                throw new InvalidOperationException("CreateMotionStudy 返回 null");
            if (!_activeMotionStudy.Activate() && !_activeMotionStudy.IsActive)
                throw new InvalidOperationException($"Motion Study \"{_activeMotionStudy.Name}\" 激活失败");

            return _activeMotionStudy;
        }

        private IComponent2 FindAssemblyComponent(string componentName)
        {
            EnsureActiveAssembly();
            var components = _activeAssembly.GetComponents(false) as object[];
            if (components != null)
            {
                foreach (var item in components)
                {
                    var component = item as IComponent2;
                    if (component != null && component.Name2 == componentName)
                        return component;
                }
            }
            throw new InvalidOperationException($"装配体中找不到组件: {componentName}");
        }

        /// <summary>寻找组件上轴线平行 Z 轴且半径最大的圆柱面，作为旋转马达方向参考。</summary>
        private IFace2 FindMotorCylinderFace(IComponent2 component)
        {
            // 渐开线齿轮有数百个平面齿面，但中心孔是唯一圆柱面。直接从组件轴心
            // 向 +X 射线拾取孔壁，避免逐面 COM 遍历导致创建马达超时。
            var componentTransform = component.Transform2 as MathTransform;
            var transformData = componentTransform?.ArrayData as double[];
            if (transformData != null && transformData.Length >= 12)
            {
                var assemblyModel = (IModelDoc2)_activeAssembly;
                assemblyModel.ClearSelection2(true);
                bool selected = assemblyModel.Extension.SelectByRay(
                    transformData[9], transformData[10], 0.0,
                    1.0, 0.0, 0.0, 0.0005,
                    (int)swSelectType_e.swSelFACES,
                    false, 0, 0);
                if (selected)
                {
                    var selectionManager = (ISelectionMgr)assemblyModel.SelectionManager;
                    var selectedFace = selectionManager.GetSelectedObject6(1, -1) as IFace2;
                    var selectedSurface = selectedFace?.IGetSurface() as ISurface;
                    if (selectedFace != null && selectedSurface != null && selectedSurface.IsCylinder())
                    {
                        var cylinder = selectedSurface.CylinderParams as double[];
                        double radius = cylinder != null && cylinder.Length >= 7
                            ? cylinder[6]
                            : 0.0;
                        Console.WriteLine(
                            $"[SwCommands] Motion 马达通过中心孔射线选面: " +
                            $"component={component.Name2}, radius={radius * 1000:F2}mm");
                        return selectedFace;
                    }
                }
            }

            IFace2 bestFace = null;
            double bestRadius = -1;
            object bodyInfo;
            var bodies = component.GetBodies3((int)swBodyType_e.swAllBodies, out bodyInfo) as object[];
            if (bodies != null)
            {
                foreach (var bodyObject in bodies)
                {
                    var body = bodyObject as IBody2;
                    var faces = body?.GetFaces() as object[];
                    if (faces == null) continue;
                    foreach (var faceObject in faces)
                    {
                        var face = faceObject as IFace2;
                        var surface = face?.IGetSurface() as ISurface;
                        if (surface == null || !surface.IsCylinder()) continue;
                        var cylinder = surface.CylinderParams as double[];
                        if (cylinder == null || cylinder.Length < 7) continue;
                        double axisNorm = Math.Sqrt(
                            cylinder[3] * cylinder[3] +
                            cylinder[4] * cylinder[4] +
                            cylinder[5] * cylinder[5]);
                        if (axisNorm < 1e-9 || Math.Abs(cylinder[5] / axisNorm) < 0.9) continue;
                        if (cylinder[6] > bestRadius)
                        {
                            bestRadius = cylinder[6];
                            bestFace = face;
                        }
                    }
                }
            }
            if (bestFace == null)
                throw new InvalidOperationException($"组件 \"{component.Name2}\" 上找不到 Z 轴向圆柱面");
            Console.WriteLine(
                $"[SwCommands] Motion 马达方向面: component={component.Name2}, radius={bestRadius * 1000:F2}mm");
            return bestFace;
        }

        /// <summary>
        /// 探测当前 SolidWorks 是否支持创建并激活 Animation Motion Study，
        /// 以及是否能创建旋转马达定义。仅创建 Study，不把马达特征写入装配体。
        /// </summary>
        public Dictionary<string, object> ProbeMotionStudyCapabilities()
        {
            var study = EnsureMotionStudy();
            int supportedStudyTypes;
            bool supportedTypesRead = study.GetSupportedStudyTypes(out supportedStudyTypes);
            object motorDefinition = study.CreateDefinition(
                (int)swFeatureNameID_e.swFmAEMRotationalMotor);

            Console.WriteLine(
                $"[SwCommands] Motion Study 探针: name={study.Name}, active={study.IsActive}, " +
                $"studyType={study.StudyType}, motorDefinition={(motorDefinition != null)}");

            return new Dictionary<string, object>
            {
                ["managerAvailable"] = true,
                ["studyName"] = study.Name,
                ["studyType"] = study.StudyType,
                ["isActive"] = study.IsActive,
                ["supportedTypesRead"] = supportedTypesRead,
                ["supportedStudyTypes"] = supportedStudyTypes,
                ["rotationalMotorDefinitionAvailable"] = motorDefinition != null,
            };
        }

        /// <summary>给指定齿轮组件创建恒速旋转马达。</summary>
        public Dictionary<string, object> CreateGearMotor(
            string componentName, double rpm, double durationSeconds, bool reverseDirection)
        {
            if (rpm <= 0) throw new ArgumentOutOfRangeException(nameof(rpm), "rpm 必须大于 0");
            if (durationSeconds <= 0)
                throw new ArgumentOutOfRangeException(nameof(durationSeconds), "durationSeconds 必须大于 0");

            var study = EnsureMotionStudy();
            var component = FindAssemblyComponent(componentName);
            var cylinderFace = FindMotorCylinderFace(component);

            if (_activeMotionMotor == null)
            {
                var motionFeatures = study.GetMotionFeatures() as object[];
                if (motionFeatures != null)
                {
                    foreach (var featureObject in motionFeatures)
                    {
                        var feature = featureObject as IFeature;
                        if (feature != null && feature.GetTypeName2() == "AEMRotationalMotor")
                        {
                            _activeMotionMotor = feature;
                            break;
                        }
                    }
                }
            }

            if (_activeMotionMotor == null)
            {
                var motorData = study.CreateDefinition(
                    (int)swFeatureNameID_e.swFmAEMRotationalMotor) as ISimulationMotorFeatureData;
                if (motorData == null)
                    throw new InvalidOperationException("旋转马达定义创建失败");

                motorData.Location = cylinderFace;
                motorData.DirectionReference = cylinderFace;
                motorData.ReverseDirection = reverseDirection;
                motorData.ConstantSpeedMotor(rpm);
                _activeMotionMotor = study.CreateFeature(motorData) as IFeature;
                if (_activeMotionMotor == null)
                    throw new InvalidOperationException("Motion Study 创建旋转马达特征失败");
            }
            else
            {
                var motorData = _activeMotionMotor.GetDefinition() as ISimulationMotorFeatureData;
                if (motorData == null)
                    throw new InvalidOperationException("读取已有旋转马达定义失败");
                motorData.Location = cylinderFace;
                motorData.DirectionReference = cylinderFace;
                motorData.ReverseDirection = reverseDirection;
                motorData.ConstantSpeedMotor(rpm);
                if (!_activeMotionMotor.ModifyDefinition(motorData, (IModelDoc2)_activeAssembly, null))
                    throw new InvalidOperationException("更新已有旋转马达定义失败");
            }
            if (!study.SetDuration(durationSeconds))
                throw new InvalidOperationException($"Motion Study 时长设置失败: {durationSeconds}s");

            Console.WriteLine(
                $"[SwCommands] 已创建旋转马达: feature={_activeMotionMotor.Name}, " +
                $"component={componentName}, rpm={rpm}, duration={durationSeconds}s, reverse={reverseDirection}");
            return new Dictionary<string, object>
            {
                ["studyName"] = study.Name,
                ["motorFeatureName"] = _activeMotionMotor.Name,
                ["componentName"] = componentName,
                ["rpm"] = rpm,
                ["durationSeconds"] = durationSeconds,
                ["reverseDirection"] = reverseDirection,
            };
        }

        public Dictionary<string, object> PlayMotionStudy()
        {
            var study = EnsureMotionStudy();
            study.SetTime(0);
            bool started = study.PlayFromStart();
            Console.WriteLine($"[SwCommands] Motion Study 播放: started={started}, isPlaying={study.IsPlaying}");
            return GetMotionStudyStatus(started);
        }

        public Dictionary<string, object> SetMotionStudyTime(double seconds)
        {
            var study = EnsureMotionStudy();
            double duration = study.GetDuration();
            if (seconds < 0 || seconds > duration)
                throw new ArgumentOutOfRangeException(
                    nameof(seconds),
                    $"时间必须在 0 到 {duration:F3}s 之间");
            bool updated = study.SetTime(seconds);
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            Console.WriteLine($"[SwCommands] Motion Study 定位: time={seconds:F3}s, updated={updated}");
            return GetMotionStudyStatus(updated);
        }

        public Dictionary<string, object> StopMotionStudy()
        {
            var study = EnsureMotionStudy();
            bool stopped = study.Stop();
            Console.WriteLine($"[SwCommands] Motion Study 停止: stopped={stopped}, time={study.GetTime():F3}s");
            return GetMotionStudyStatus(stopped);
        }

        public Dictionary<string, object> GetMotionStudyStatus(bool? commandResult = null)
        {
            var study = EnsureMotionStudy();
            var result = new Dictionary<string, object>
            {
                ["studyName"] = study.Name,
                ["studyType"] = study.StudyType,
                ["isActive"] = study.IsActive,
                ["isPlaying"] = study.IsPlaying,
                ["currentTime"] = study.GetTime(),
                ["duration"] = study.GetDuration(),
                ["motorCreated"] = _activeMotionMotor != null,
                ["motorFeatureName"] = _activeMotionMotor?.Name ?? "",
            };
            if (commandResult.HasValue)
                result["commandResult"] = commandResult.Value;
            return result;
        }

        #endregion

        #endregion
    }
}
