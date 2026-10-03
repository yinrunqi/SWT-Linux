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
        #region 原子操作 — 文档

        /// <summary>
        /// 关闭当前 SolidWorks 会话中的所有文档，不保存测试产生的临时修改。
        /// 评测用例之间调用，避免零件/装配体窗口累积导致内存和 COM 负载持续增长。
        /// </summary>
        public List<string> CloseAllDocuments()
        {
            var titles = new List<string>();
            var documents = _swApp.GetDocuments() as object[];
            if (documents != null)
            {
                foreach (var item in documents)
                {
                    var document = item as IModelDoc2;
                    var title = document?.GetTitle();
                    if (!string.IsNullOrWhiteSpace(title))
                        titles.Add(title);
                }
            }

            foreach (var title in titles)
                _swApp.CloseDoc(title);

            ActiveDoc = null;
            _activeAssembly = null;
            _activeAssemblyPath = null;
            _activeAssemblyName = null;
            _activeMotionStudy = null;
            _activeMotionMotor = null;
            _activeSketchName = null;
            _activeFaceKey = null;
            _sketchOnRefPlane = false;
            _sketchMetadata.Clear();
            _featureSketchLinks.Clear();

            Console.WriteLine($"[SwCommands] 已关闭 {titles.Count} 个文档");
            return titles;
        }

        /// <summary>
        /// 把文档切到标准视向并缩放到全屏，供重放录屏使用。
        /// target：auto=当前活动窗口，part=零件建模文档，assembly=装配建模文档（先激活其窗口，插入零件可能把零件窗口顶到前面）。
        /// view：isometric / trimetric / dimetric / front / back / left / right / top / bottom / none（none 只缩放）。
        /// </summary>
        public Dictionary<string, object> FitView(string target, string view)
        {
            IModelDoc2 doc;
            switch ((target ?? "auto").ToLowerInvariant())
            {
                case "part":
                    doc = ActiveDoc;
                    break;
                case "assembly":
                    doc = _activeAssembly == null ? null : (IModelDoc2)_activeAssembly;
                    if (doc != null)
                    {
                        int errors = 0;
                        _swApp.ActivateDoc3(doc.GetTitle(), false, (int)swRebuildOnActivation_e.swDontRebuildActiveDoc, ref errors);
                    }
                    break;
                default:
                    doc = _swApp.IActiveDoc2;
                    break;
            }
            if (doc == null)
                throw new InvalidOperationException($"没有可调整视图的文档（target={target}）");

            var views = new Dictionary<string, swStandardViews_e>(StringComparer.OrdinalIgnoreCase)
            {
                ["front"] = swStandardViews_e.swFrontView,
                ["back"] = swStandardViews_e.swBackView,
                ["left"] = swStandardViews_e.swLeftView,
                ["right"] = swStandardViews_e.swRightView,
                ["top"] = swStandardViews_e.swTopView,
                ["bottom"] = swStandardViews_e.swBottomView,
                ["isometric"] = swStandardViews_e.swIsometricView,
                ["trimetric"] = swStandardViews_e.swTrimetricView,
                ["dimetric"] = swStandardViews_e.swDimetricView,
            };
            string viewName = string.IsNullOrWhiteSpace(view) ? "isometric" : view;
            if (!string.Equals(viewName, "none", StringComparison.OrdinalIgnoreCase))
            {
                if (!views.TryGetValue(viewName, out var standard))
                    throw new ArgumentException($"未知视向 {viewName}");
                doc.ShowNamedView2("", (int)standard);
            }
            doc.ViewZoomtofit2();
            doc.GraphicsRedraw2();
            return new Dictionary<string, object> { ["title"] = doc.GetTitle(), ["view"] = viewName };
        }

        /// <summary>
        /// 关闭除当前活动文档以外的窗口，不保存临时修改。
        /// 装配完成后用于保留最终装配体，同时释放预加载零件窗口。
        /// </summary>
        public List<string> CloseInactiveDocuments()
        {
            var active = _swApp.IActiveDoc2;
            var activeTitle = active?.GetTitle();
            var titles = new List<string>();
            var documents = _swApp.GetDocuments() as object[];
            if (documents != null)
            {
                foreach (var item in documents)
                {
                    var document = item as IModelDoc2;
                    var title = document?.GetTitle();
                    if (!string.IsNullOrWhiteSpace(title)
                        && !string.Equals(
                            title,
                            activeTitle,
                            StringComparison.OrdinalIgnoreCase))
                    {
                        titles.Add(title);
                    }
                }
            }
            foreach (var title in titles)
                _swApp.CloseDoc(title);
            Console.WriteLine(
                $"[SwCommands] 已保留活动文档 '{activeTitle}'，" +
                $"关闭 {titles.Count} 个非活动文档");
            return titles;
        }

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
            _activeSketchName = null;
            _activeFaceKey = null;
            _sketchMetadata.Clear();
            _featureSketchLinks.Clear();
            Console.WriteLine("[SwCommands] 新建零件文档成功");
        }

        /// <summary>
        /// 打开磁盘上已有的 .SLDPRT 并设为当前活动零件，用于读取外部模型的特征树与几何。
        /// </summary>
        public Dictionary<string, object> OpenPart(string partPath)
        {
            if (string.IsNullOrWhiteSpace(partPath))
                throw new ArgumentException("partPath 不能为空", nameof(partPath));
            if (!File.Exists(partPath))
                throw new FileNotFoundException($"零件文件不存在: {partPath}", partPath);

            int errors = 0, warnings = 0;
            var doc = _swApp.OpenDoc6(
                partPath,
                (int)swDocumentTypes_e.swDocPART,
                (int)swOpenDocOptions_e.swOpenDocOptions_Silent,
                "",
                ref errors,
                ref warnings);
            if (doc == null)
                throw new InvalidOperationException(
                    $"打开零件失败: {partPath} (errors={errors}, warnings={warnings})");

            _swApp.ActivateDoc3(
                doc.GetTitle(),
                true,
                (int)swRebuildOnActivation_e.swDontRebuildActiveDoc,
                ref errors);
            ActiveDoc = doc;
            _activeSketchName = null;
            _activeFaceKey = null;
            _sketchOnRefPlane = false;
            _sketchMetadata.Clear();
            _featureSketchLinks.Clear();
            Console.WriteLine($"[SwCommands] 已打开零件: {partPath} (errors={errors}, warnings={warnings})");
            return new Dictionary<string, object>
            {
                ["path"] = partPath,
                ["title"] = doc.GetTitle(),
                ["openErrors"] = errors,
                ["openWarnings"] = warnings,
            };
        }

        /// <summary>
        /// 把当前活动零件另存一份 STEP（不改变活动文档的路径），供离线几何评测使用。
        /// </summary>
        public Dictionary<string, object> ExportStep(string stepPath)
        {
            if (ActiveDoc == null)
                throw new InvalidOperationException("没有活动零件文档");
            if (string.IsNullOrWhiteSpace(stepPath))
                throw new ArgumentException("stepPath 不能为空", nameof(stepPath));
            var extension = Path.GetExtension(stepPath).ToLowerInvariant();
            if (extension != ".step" && extension != ".stp")
                throw new ArgumentException("stepPath 必须以 .step 或 .stp 结尾", nameof(stepPath));
            var directory = Path.GetDirectoryName(stepPath);
            if (!string.IsNullOrEmpty(directory))
                Directory.CreateDirectory(directory);

            int errors = 0, warnings = 0;
            bool saved = ActiveDoc.Extension.SaveAs(
                stepPath,
                (int)swSaveAsVersion_e.swSaveAsCurrentVersion,
                (int)(swSaveAsOptions_e.swSaveAsOptions_Silent | swSaveAsOptions_e.swSaveAsOptions_Copy),
                null,
                ref errors,
                ref warnings);
            if (!saved || !File.Exists(stepPath))
                throw new InvalidOperationException(
                    $"导出 STEP 失败: {stepPath} (errors={errors}, warnings={warnings})");
            return new Dictionary<string, object>
            {
                ["path"] = stepPath,
                ["bytes"] = new FileInfo(stepPath).Length,
            };
        }

        #endregion
    }
}
