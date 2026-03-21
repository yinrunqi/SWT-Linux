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
