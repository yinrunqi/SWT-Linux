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

            var body = bodies[0] as IBody2;

            // 使用 IModelDoc2.RayIntersections 获取射线与实体的交点
            // 参数：Body, RayOrigin(x,y,z), RayDirection(x,y,z), Options, HitRadius
            // 使用 GetFirstFace / GetNextFace 遍历面，通过射线方向判断命中
            // 更好的方式：直接用 SelectByRay 方法
            bool selected = ((IModelDoc2)ActiveDoc).Extension.SelectByRay(
                rx, ry, rz,    // 射线起点
                dx, dy, dz,    // 射线方向
                0.001,         // 命中半径（1mm 容差）
                (int)swSelectType_e.swSelFACES,  // 选择类型：面
                false,         // Append: false 表示不追加选择
                0,             // Mark
                0              // Action: 0 = select
            );

            if (!selected)
                throw new InvalidOperationException(
                    $"未能通过射线选中面。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。" +
                    "请确保射线方向正确，且起点在实体外部。");

            // 获取选中面的信息
            var selMgr = ActiveDoc.ISelectionManager;
            int selCount = selMgr.GetSelectedObjectCount2(-1);
            string faceName = "";
            if (selCount > 0)
            {
                var selFace = selMgr.GetSelectedObject6(1, -1) as IFace2;
                if (selFace != null)
                {
                    var surface = selFace.IGetSurface();
                    bool isPlane = surface.IsPlane();
                    faceName = isPlane ? "平面" : "曲面";
                }
            }

            // 进入草图编辑模式
            ActiveDoc.SketchManager.InsertSketch(true);
            Console.WriteLine($"[SwCommands] 已通过射线选中{faceName}并进入草图编辑");

            return new Dictionary<string, object>
            {
                { "selected", true },
                { "faceType", faceName },
                { "message", $"已选中{faceName}并进入草图编辑" },
            };
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
        /// 在当前草图中绘制一条直线。坐标单位：米。
        /// </summary>
        public void SketchLine(double x1, double y1, double x2, double y2)
        {
            EnsureActiveDoc();
            // z 坐标为 0（2D 草图）
            ActiveDoc.SketchManager.CreateLine(x1, y1, 0, x2, y2, 0);
            Console.WriteLine($"[SwCommands] 绘制直线: ({x1}, {y1}) -> ({x2}, {y2})");
        }

        /// <summary>
        /// 在当前草图中绘制一个矩形（对角线两点）。坐标单位：米。
        /// </summary>
        public void SketchRectangle(double x1, double y1, double x2, double y2)
        {
            EnsureActiveDoc();
            ActiveDoc.SketchManager.CreateCornerRectangle(x1, y1, 0, x2, y2, 0);
            Console.WriteLine($"[SwCommands] 绘制矩形: ({x1}, {y1}) -> ({x2}, {y2})");
        }

        /// <summary>
        /// 在当前草图中绘制一个圆。坐标和半径单位：米。
        /// </summary>
        public void SketchCircle(double cx, double cy, double radius)
        {
            EnsureActiveDoc();
            // CreateSketchArc 或 CreateCircleByRadius
            // 使用 CreateCircle 方法：圆心 + 圆上一点
            ActiveDoc.SketchManager.CreateCircle(cx, cy, 0, cx + radius, cy, 0);
            Console.WriteLine($"[SwCommands] 绘制圆: 圆心({cx}, {cy}), 半径 {radius}");
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
