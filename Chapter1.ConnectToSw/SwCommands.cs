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
    public partial class SwCommands
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
        private string _activeFaceKey = null;
        private readonly Dictionary<string, Dictionary<string, object>> _sketchMetadata =
            new Dictionary<string, Dictionary<string, object>>();
        private readonly List<Dictionary<string, object>> _featureSketchLinks =
            new List<Dictionary<string, object>>();

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
    }
}
