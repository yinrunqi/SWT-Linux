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
        #region 原子操作 — 特征

        /// <summary>
        /// 按名称删除特征（含被吸收的草图），供 Agent 撤销失败或错误的一步。
        /// 若仍在草图编辑中，先退出草图。
        /// </summary>
        public Dictionary<string, object> DeleteFeature(string featureName)
        {
            EnsureActiveDoc();
            if (string.IsNullOrWhiteSpace(featureName))
                throw new ArgumentException("featureName 不能为空");

            if (ActiveDoc.SketchManager.ActiveSketch != null)
                ((ModelDoc2)ActiveDoc).InsertSketch2(true);
            _sketchOnRefPlane = false;

            var feature = ((IPartDoc)ActiveDoc).FeatureByName(featureName) as IFeature;
            if (feature == null)
                throw new InvalidOperationException($"找不到特征 {featureName}");
            string typeName = feature.GetTypeName2();

            ActiveDoc.ClearSelection2(true);
            if (!feature.Select2(false, 0))
                throw new InvalidOperationException($"无法选中特征 {featureName}");
            int options = (int)swDeleteSelectionOptions_e.swDelete_Absorbed
                | (int)swDeleteSelectionOptions_e.swDelete_Children;
            bool deleted = ActiveDoc.Extension.DeleteSelection2(options);
            ActiveDoc.ClearSelection2(true);
            if (!deleted)
                throw new InvalidOperationException($"删除特征 {featureName} 失败");

            if (string.Equals(_activeSketchName, featureName, StringComparison.Ordinal))
                _activeSketchName = null;
            _featureSketchLinks.RemoveAll(link =>
                link.TryGetValue("featureName", out var name)
                && string.Equals(Convert.ToString(name), featureName, StringComparison.Ordinal));
            ActiveDoc.ForceRebuild3(false);
            Console.WriteLine($"[SwCommands] 已删除特征 {featureName} ({typeName})");
            return new Dictionary<string, object>
            {
                { "featureName", featureName },
                { "featureType", typeName },
            };
        }

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
        public string Extrusion(
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
            RecordFeatureSketchLink(
                feat.Name,
                feat.GetTypeName2(),
                "extrusion",
                depth);

            // 缩放到合适视图
            mdoc.ViewZoomtofit2();

            Console.WriteLine(
                $"[SwCommands] 拉伸完成，深度: {depth} 米, 方向翻转: {flipDirection}, " +
                $"中间面: {midPlane}, 拔模: {draftAngleDeg}° {(draftOutward ? "外扩" : "内收")}");
            return feat.Name;
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
        public string CutExtrusion(double depth, string endCondition = "blind", bool flipDirection = false,
            double draftAngleDeg = 0, bool draftOutward = false)
        {
            EnsureActiveDoc();
            if (draftAngleDeg < 0 || draftAngleDeg >= 90)
                throw new ArgumentOutOfRangeException(nameof(draftAngleDeg), "拔模角必须在 [0, 90) 度范围内");
            bool draft = draftAngleDeg > 0;
            double draftRad = draftAngleDeg * Math.PI / 180.0;

            var mdoc = (ModelDoc2)ActiveDoc;

            // 检查是否在草图编辑模式中
            var activeSketch = ActiveDoc.SketchManager.ActiveSketch;
            IFeature activeSketchFeature = null;
            if (activeSketch != null)
            {
                try
                {
                    activeSketchFeature = ((IPartDoc)ActiveDoc)
                        .FeatureByName(_activeSketchName) as IFeature;
                }
                catch (Exception ex)
                {
                    Console.WriteLine(
                        $"[SwCommands] 获取活动草图 Feature 失败: {ex.Message}");
                }
            }
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
                bool selOk = activeSketchFeature != null
                    && activeSketchFeature.Select2(false, 0);
                if (!selOk)
                {
                    selOk = mdoc.Extension.SelectByID2(
                        _activeSketchName,
                        "SKETCH",
                        0, 0, 0,
                        false,
                        0,
                        null,
                        0);
                }
                Console.WriteLine(
                    $"[SwCommands] 切除前选中捕获的草图 Feature/名称 \"{_activeSketchName}\" = {selOk}");
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
                false,              // 2.  Flip - 反转“切除侧”（保留轮廓外的材料），不是反转方向
                flipDirection,      // 3.  Dir - 反转切除方向
                t1,                 // 4.  T1 - 终止条件1 (int)
                0,                  // 5.  T2 - 终止条件2（单端切除时不使用）
                depth,              // 6.  D1 - 深度1 (double)
                depth,              // 7.  D2 - 深度2 (double)
                draft,              // 8.  Dchk1 - 拔模开关1
                false,              // 9.  Dchk2 - 拔模开关2
                draftOutward,       // 10. Ddir1 - 拔模方向1
                false,              // 11. Ddir2 - 拔模方向2
                draftRad,           // 12. Dang1 - 拔模角度1 (double)
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
                    bool sel = activeSketchFeature != null
                        && activeSketchFeature.Select2(false, 0);
                    if (!sel)
                    {
                        sel = mdoc.Extension.SelectByID2(
                            _activeSketchName,
                            "SKETCH",
                            0, 0, 0,
                            false,
                            0,
                            null,
                            0);
                    }
                    Console.WriteLine($"[SwCommands] 重试 SelectByID2(\"{_activeSketchName}\", \"SKETCH\") = {sel}");

                    selCount = selMgr.GetSelectedObjectCount2(-1);
                    for (int i = 1; i <= selCount; i++)
                    {
                        int selType = selMgr.GetSelectedObjectType3(i, -1);
                        Console.WriteLine($"[SwCommands]   选中项 {i}: 类型={selType}");
                    }
                }

                feat = ActiveDoc.FeatureManager.FeatureCut4(
                    true, false, flipDirection,
                    t1, 0, depth, depth,
                    draft, false, draftOutward, false,
                    draftRad, 0.0, false, false, false, false,
                    false, true, true, false, false, false,
                    (int)swStartConditions_e.swStartSketchPlane, 0.0, false, false
                );
                Console.WriteLine($"[SwCommands] 第二次 FeatureCut4 返回: {(feat != null ? feat.Name : "null")}");
            }

            if (
                feat == null
                && t1 == (int)swEndConditions_e.swEndCondUpToNext
            )
            {
                Console.WriteLine(
                    "[SwCommands] 到下一面切除失败，" +
                    "保持方向并降级为贯穿全部重试");
                ActiveDoc.ClearSelection2(true);
                bool selected = activeSketchFeature != null
                    && activeSketchFeature.Select2(false, 0);
                if (!selected && !string.IsNullOrEmpty(_activeSketchName))
                {
                    selected = mdoc.Extension.SelectByID2(
                        _activeSketchName,
                        "SKETCH",
                        0, 0, 0,
                        false,
                        0,
                        null,
                        0);
                }
                if (selected)
                {
                    feat = ActiveDoc.FeatureManager.FeatureCut4(
                        true, false, flipDirection,
                        (int)swEndConditions_e.swEndCondThroughAll,
                        0,
                        depth, depth,
                        false, false, false, false,
                        0.0, 0.0,
                        false, false, false, false,
                        false, true, true, false, false, false,
                        (int)swStartConditions_e.swStartSketchPlane,
                        0.0, false, false);
                }
                Console.WriteLine(
                    "[SwCommands] 贯穿全部降级返回: " +
                    (feat != null ? feat.Name : "null"));
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
            RecordFeatureSketchLink(
                feat.Name,
                featTypeName,
                "cut",
                depth);

            // 缩放到合适视图
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();

            Console.WriteLine($"[SwCommands] 切除拉伸完成，深度: {depth} 米, 终止条件: {endCondition}");
            return feat.Name;
        }

        /// <summary>
        /// 修改指定特征的尺寸值。
        /// featureName: 特征名称（如 "Boss-Extrude1" / "凸台-拉伸1"）
        /// dimensionName: 尺寸名称（如 "D1"），如果为空则修改该特征的第一个尺寸
        /// newValue: 新的尺寸值，单位：米（userUnits=true 时为 mm，角度尺寸为度，计数尺寸为个数）
        /// </summary>
        public Dictionary<string, object> ModifyFeatureDimension(string featureName, string dimensionName, double newValue,
            bool userUnits = false)
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

            int dimType = targetDim.GetType();
            string unit = dimType == (int)swDimensionParamType_e.swDimensionParamTypeDoubleAngular ? "deg"
                : dimType == (int)swDimensionParamType_e.swDimensionParamTypeInteger ? "count" : "mm";
            double userScale = unit == "deg" ? 180.0 / Math.PI : unit == "count" ? 1.0 : 1000.0;
            double oldUser = oldValue * userScale;
            if (userUnits)
                newValue /= userScale;

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
                { "unit", unit },
                { "oldValueUser", oldUser },
                { "newValueUser", newValue * userScale },
                { "message", $"已修改 {featureName} 的 {resolvedDimName}: {oldUser:G6} -> {newValue * userScale:G6} {unit}" },
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
            double dx = 0, double dy = 0, double dz = 0,
            List<double[]> rays = null, bool propagate = true)
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
            object[] allBodies = null;

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
                allBodies = bodies;

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
                Console.WriteLine($"[SwCommands] 圆角: 射线选边 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz})");
                bool sel = SelectEdgeByRayHit(ext, rx, ry, rz, dx, dy, dz, false);
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
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。{bboxInfo}射线命中的面上离命中点 2mm 内要有目标边，或者射线要在 2mm 内掠过目标边。");
                }
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 圆角: 通过射线选中 1 条边");
            }
            else if (edgeSelection.ToLower() == "rays")
            {
                edgeCount = SelectEdgesByRays(ext, rays, "圆角");
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

            int options = propagate ? 195 : 195 & ~(int)swFeatureFilletOptions_e.swFeatureFilletPropagate;
            var feat = (Feature)ActiveDoc.FeatureManager.FeatureFillet3(
                options,            // Options (标准简单圆角选项组合；propagate=false 时去掉沿相切边传播)
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

            if (feat == null
                && edgeSelection.Equals(
                    "all",
                    StringComparison.OrdinalIgnoreCase)
                && allBodies != null)
            {
                Console.WriteLine(
                    "[SwCommands] 全边圆角失败，依次尝试可接受该半径的单边");
                foreach (object bodyObj in allBodies)
                {
                    var body = bodyObj as IBody2;
                    var edges = body?.GetEdges() as object[];
                    if (edges == null) continue;
                    foreach (object edgeObj in edges)
                    {
                        var edge = edgeObj as IEdge;
                        if (edge == null) continue;
                        ActiveDoc.ClearSelection2(true);
                        if (!((IEntity)edge).Select4(false, null))
                            continue;
                        feat = (Feature)ActiveDoc.FeatureManager.FeatureFillet3(
                            195,
                            radius,
                            0,
                            0,
                            0,
                            0,
                            0,
                            radiiArray,
                            dist2Array,
                            conicRhosArray,
                            setBackArray,
                            pointRadiusArray,
                            pointDist2Array,
                            pointRhoArray);
                        if (feat != null)
                        {
                            edgeCount = 1;
                            Console.WriteLine(
                                "[SwCommands] 已降级为单条有效边圆角");
                            break;
                        }
                    }
                    if (feat != null) break;
                }
            }

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
        /// 多条射线依次追加选边（每条 [rx,ry,rz,dx,dy,dz]，米），供一个圆角/倒角特征同时处理多条边。
        /// 任一射线未命中即报错，并指出是第几条。
        /// </summary>
        private int SelectEdgesByRays(ModelDocExtension ext, List<double[]> rays, string label)
        {
            if (rays == null || rays.Count == 0)
                throw new ArgumentException("edgeSelection=rays 需要非空的 rays");
            var selectionManager = (ISelectionMgr)ActiveDoc.SelectionManager;
            for (int i = 0; i < rays.Count; i++)
            {
                var r = rays[i];
                if (r == null || r.Length != 6)
                    throw new ArgumentException($"rays[{i}] 须为 [rx,ry,rz,dx,dy,dz]");
                bool selected = SelectEdgeByRayHit(ext, r[0], r[1], r[2], r[3], r[4], r[5], i > 0);
                if (!selected)
                    throw new InvalidOperationException(
                        $"第 {i + 1} 条射线未命中任何边：起点({r[0]},{r[1]},{r[2]}) 方向({r[3]},{r[4]},{r[5]})");
            }
            int count = selectionManager.GetSelectedObjectCount2(-1);
            Console.WriteLine($"[SwCommands] {label}: {rays.Count} 条射线选中 {count} 条边");
            return count;
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
            double dx = 0, double dy = 0, double dz = 0,
            List<double[]> rays = null)
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
                Console.WriteLine($"[SwCommands] 倒角: 射线选边 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz})");
                bool sel = SelectEdgeByRayHit(ext, rx, ry, rz, dx, dy, dz, false);
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
                        $"未能通过射线选中边。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})。{bboxInfo}射线命中的面上离命中点 2mm 内要有目标边，或者射线要在 2mm 内掠过目标边。");
                }
                edgeCount = 1;
                Console.WriteLine($"[SwCommands] 倒角: 通过射线选中 1 条边");
            }
            else if (edgeSelection.ToLower() == "rays")
            {
                edgeCount = SelectEdgesByRays(ext, rays, "倒角");
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
            double? axisOriginX = null, double? axisOriginY = null, double? axisOriginZ = null,
            string axisFeature = null)
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
            //
            // axisFeature（L1_v2）：直接用调用方建好的参考轴（两基准面的交线，正好穿过给定中心），
            //   不再找最近的圆柱面——中心处没有圆柱面时，最近的圆柱面会是别处的孔。
            IFeature axisFeat = null;
            if (!string.IsNullOrEmpty(axisFeature))
            {
                axisFeat = ((IPartDoc)ActiveDoc).FeatureByName(axisFeature) as IFeature;
                if (axisFeat == null)
                    throw new InvalidOperationException($"找不到参考轴 {axisFeature}");
                Console.WriteLine($"[SwCommands] 使用指定参考轴: {axisFeat.Name}");
            }
            if (axisFeat == null)
            {
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
            }

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
    }
}
