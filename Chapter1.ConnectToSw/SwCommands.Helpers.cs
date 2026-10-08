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
        #region 辅助方法

        /// <summary>
        /// 射线选边：取射线正方向上第一个命中的面，选中该面上离命中点最近的边（命中点落在边上时就是这条边）。
        /// SelectByRay 按“离射线 2mm 内的边”选，射线贴着别的面掠过或两条边离射线一样近时会选错，
        /// 例如沿杆身射向螺栓头底面会选中杆的另一端圆边。射线没命中实体时才退回 SelectByRay。
        /// </summary>
        private bool SelectEdgeByRayHit(IModelDocExtension ext, double rx, double ry, double rz,
            double dx, double dy, double dz, bool append)
        {
            var bodies = (ActiveDoc as IPartDoc)?.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            double len = Math.Sqrt(dx * dx + dy * dy + dz * dz);
            if (bodies != null && bodies.Length > 0 && len > 1e-12)
            {
                double ux = dx / len, uy = dy / len, uz = dz / len;
                var wrapped = bodies.Select(b => new System.Runtime.InteropServices.DispatchWrapper(b)).ToArray();
                int n = ext.RayIntersections(wrapped, new[] { rx, ry, rz }, new[] { ux, uy, uz },
                    (int)(swRayPtsOpts_e.swRayPtsOptsTOPOLS | swRayPtsOpts_e.swRayPtsOptsNORMALS), 1e-7, 0, false);
                var points = n > 0 ? ActiveDoc.GetRayIntersectionsPoints() as double[] : null;
                var topology = n > 0 ? ActiveDoc.GetRayIntersectionsTopology() as object[] : null;
                int best = -1;
                double bestT = double.MaxValue;
                for (int i = 0; points != null && topology != null && i < n && i < topology.Length && 9 * i + 5 < points.Length; i++)
                {
                    double t = (points[9 * i + 3] - rx) * ux + (points[9 * i + 4] - ry) * uy + (points[9 * i + 5] - rz) * uz;
                    if (t >= -1e-9 && t < bestT)
                    {
                        bestT = t;
                        best = i;
                    }
                }
                if (best >= 0)
                {
                    double hx = points[9 * best + 3], hy = points[9 * best + 4], hz = points[9 * best + 5];
                    IEdge edge = topology[best] as IEdge;
                    if (edge == null && topology[best] is IFace2 face)
                        edge = NearestEdge(face.GetEdges() as object[], hx, hy, hz);
                    if (edge == null && topology[best] is IVertex vertex)
                        edge = NearestEdge(vertex.GetEdges() as object[], hx, hy, hz);
                    if (edge != null && ((IEntity)edge).Select4(append, null))
                    {
                        Console.WriteLine($"[SwCommands] 射线命中点 ({hx * 1000:F2},{hy * 1000:F2},{hz * 1000:F2})mm，选中离它最近的边");
                        return true;
                    }
                }
            }
            return ext.SelectByRay(rx, ry, rz, dx, dy, dz, 0.002, (int)swSelectType_e.swSelEDGES, append, 0, 0);
        }

        private static IEdge NearestEdge(object[] edges, double x, double y, double z)
        {
            IEdge best = null;
            double bestD = double.MaxValue;
            foreach (object item in edges ?? new object[0])
            {
                var edge = item as IEdge;
                var p = edge?.GetClosestPointOn(x, y, z) as double[];
                if (p == null || p.Length < 3)
                    continue;
                double d = (p[0] - x) * (p[0] - x) + (p[1] - y) * (p[1] - y) + (p[2] - z) * (p[2] - z);
                if (d < bestD)
                {
                    bestD = d;
                    best = edge;
                }
            }
            return best;
        }

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

            bool sel = SelectEdgeByRayHit(ext, rx, ry, rz, dx, dy, dz, false);

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

        private void RegisterActiveSketch(string faceKey)
        {
            _activeFaceKey = faceKey;
            if (string.IsNullOrEmpty(_activeSketchName))
                return;
            _sketchMetadata[_activeSketchName] = new Dictionary<string, object>
            {
                { "name", _activeSketchName },
                { "faceKey", faceKey ?? "" },
                { "circles", new List<Dictionary<string, double>>() },
            };
        }

        private void RecordSketchCircle(
            double cx, double cy, double cz, double radius)
        {
            if (string.IsNullOrEmpty(_activeSketchName))
                return;
            Dictionary<string, object> metadata;
            if (!_sketchMetadata.TryGetValue(_activeSketchName, out metadata))
            {
                RegisterActiveSketch(_activeFaceKey);
                metadata = _sketchMetadata[_activeSketchName];
            }
            var circles = metadata["circles"] as List<Dictionary<string, double>>;
            circles.Add(new Dictionary<string, double>
            {
                { "cx", cx }, { "cy", cy }, { "cz", cz }, { "radius", radius },
            });
        }

        private void RecordFeatureSketchLink(
            string featureName,
            string featureType,
            string operationKind,
            double depth = 0)
        {
            if (string.IsNullOrEmpty(featureName))
                return;
            _featureSketchLinks.Add(new Dictionary<string, object>
            {
                { "featureName", featureName },
                { "featureType", featureType },
                { "sketchName", _activeSketchName ?? "" },
                { "faceKey", _activeFaceKey ?? "" },
                { "operationKind", operationKind ?? "" },
                { "depth", depth },
            });
        }

        private static string GetAxisFaceKey(
            double[] point,
            double[] normal,
            Dictionary<string, double> bbox)
        {
            double ax = Math.Abs(normal[0]);
            double ay = Math.Abs(normal[1]);
            double az = Math.Abs(normal[2]);
            int axis = ax >= ay && ax >= az ? 0 : (ay >= az ? 1 : 2);
            string axisName = axis == 0 ? "X" : (axis == 1 ? "Y" : "Z");
            double center = axis == 0
                ? (bbox["minX"] + bbox["maxX"]) / 2
                : axis == 1
                    ? (bbox["minY"] + bbox["maxY"]) / 2
                    : (bbox["minZ"] + bbox["maxZ"]) / 2;
            return (point[axis] >= center ? "+" : "-") + axisName;
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
    }
}
