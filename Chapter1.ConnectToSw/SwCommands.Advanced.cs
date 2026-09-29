using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using System;
using System.Collections.Generic;
using System.Linq;

namespace Chapter1.ConnectToSw
{
    public partial class SwCommands
    {
        #region workflow 层 — 终止到面、筋、异形孔、扫描/放样/抽壳/拔模、钣金

        public Dictionary<string, object> ExtrudeOrCutUntil(
            string untilReference, bool flipDirection, bool isCut)
        {
            EnsureActiveDoc();
            var mdoc = (ModelDoc2)ActiveDoc;
            mdoc.InsertSketch2(true);
            _sketchOnRefPlane = false;
            ActiveDoc.ClearSelection2(true);
            if (!SelectActiveSketch(isCut ? "切除到面" : "拉伸到面"))
                throw new InvalidOperationException("无法选中刚画完的草图");
            int endCond;
            if (string.Equals(untilReference, "body", StringComparison.OrdinalIgnoreCase))
            {
                SelectAllSolidBodies(1);
                endCond = (int)swEndConditions_e.swEndCondUpToBody;
            }
            else
            {
                if (!SelectReference(untilReference, true, 1))
                    throw new InvalidOperationException($"无法选中终止面 {untilReference}");
                endCond = (int)swEndConditions_e.swEndCondUpToSurface;
            }

            IFeature feat;
            if (isCut)
            {
                feat = ActiveDoc.FeatureManager.FeatureCut4(
                    true, false, flipDirection, endCond, 0, 0.001, 0.001,
                    false, false, false, false, 0, 0, false, false, false, false,
                    false, true, true, false, false, false,
                    (int)swStartConditions_e.swStartSketchPlane, 0, false, false) as IFeature;
            }
            else
            {
                feat = ActiveDoc.FeatureManager.FeatureExtrusion3(
                    true, false, flipDirection, endCond, 0, 0.001, 0,
                    false, false, false, false, 0, 0, false, false, false, false,
                    true, true, true,
                    (int)swStartConditions_e.swStartSketchPlane, 0, false) as IFeature;
            }
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException(isCut ? "拉伸切除到面失败" : "拉伸到面失败");
            RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), isCut ? "cut" : "extrusion", 0);
            mdoc.ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] {(isCut ? "切除" : "拉伸")}到 {untilReference}: {feat.Name}");
            return FeatureResult(feat);
        }

        public Dictionary<string, object> InsertRib(double thickness, bool bothSides, bool flip)
        {
            EnsureActiveDoc();
            var mdoc = (ModelDoc2)ActiveDoc;
            mdoc.InsertSketch2(true);
            _sketchOnRefPlane = false;
            ActiveDoc.ClearSelection2(true);
            if (!SelectActiveSketch("筋"))
                throw new InvalidOperationException("无法选中筋的草图（需要一条开放轮廓）");
            IFeature feat = null;
            foreach (var flags in new[]
            {
                Tuple.Create(bothSides, flip, false),
                Tuple.Create(bothSides, !flip, false),
                Tuple.Create(bothSides, flip, true),
                Tuple.Create(false, flip, false),
            })
            {
                ActiveDoc.ClearSelection2(true);
                if (!SelectSketchSegments(_activeSketchName))
                    continue;
                ActiveDoc.FeatureManager.InsertRib(
                    flags.Item1, flags.Item2, thickness, 0, flags.Item3, false, false, 0, true, false);
                feat = LastUserFeature();
                if (feat != null && feat.GetTypeName2() == "Rib")
                    break;
                feat = null;
            }
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException("创建筋失败：开放线段须同时碰到两个已有实体面");
            RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), "rib", thickness);
            mdoc.ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 筋 {feat.Name}: 厚 {thickness}m, 双侧={bothSides}");
            return FeatureResult(feat);
        }

        public Dictionary<string, object> InsertSweep(bool isCut)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            var sketches = RecentSketchNames(2);
            if (sketches.Count < 2)
                throw new InvalidOperationException("扫描需要两张草图：先画路径，再画截面");
            string pathName = sketches[0];
            string profileName = sketches[1];
            IFeature feat = null;
            foreach (var marks in new[] { Tuple.Create(1, 4), Tuple.Create(4, 1), Tuple.Create(1, 2) })
            {
                ActiveDoc.ClearSelection2(true);
                if (!SelectSketchByName(profileName, false, marks.Item1))
                    continue;
                if (!SelectSketchByName(pathName, true, marks.Item2))
                    continue;
                feat = isCut
                    ? ActiveDoc.FeatureManager.InsertCutSwept4(
                        false, false, 0, false, false, 0, 0, false, 0, 0, 0, 0,
                        true, true, 0, false, false, true, false) as IFeature
                    : ActiveDoc.FeatureManager.InsertProtrusionSwept4(
                        false, false, 0, false, false, 0, 0, false, 0, 0, 0, 0,
                        true, true, true, 0, false, false, 0, 0) as IFeature;
                if (feat != null)
                    break;
            }
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException("扫描失败：截面须在路径起点处且大致垂直于路径，路径须开放");
            RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), isCut ? "sweep_cut" : "sweep", 0);
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 扫描 {feat.Name}: 截面 {profileName}, 路径 {pathName}, cut={isCut}");
            return FeatureResult(feat);
        }

        public Dictionary<string, object> InsertLoft(int sectionCount, bool isCut)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            if (sectionCount < 2)
                throw new ArgumentOutOfRangeException(nameof(sectionCount), "放样至少需要 2 个截面");
            var sketches = RecentSketchNames(sectionCount);
            if (sketches.Count < sectionCount)
                throw new InvalidOperationException($"放样需要 {sectionCount} 张截面草图，当前只有 {sketches.Count} 张");
            ActiveDoc.ClearSelection2(true);
            for (int i = 0; i < sketches.Count; i++)
            {
                if (!SelectSketchByName(sketches[i], i > 0, 1))
                    throw new InvalidOperationException($"无法选中截面 {sketches[i]}");
            }
            IFeature feat = isCut
                ? ActiveDoc.FeatureManager.InsertCutBlend(
                    false, true, false, 0.0001, 0, 0, false, 0, 0, 0, true, true) as IFeature
                : ActiveDoc.FeatureManager.InsertProtrusionBlend2(
                    false, true, false, 0.0001, 0, 0, 0, 0, false, false, false, 0, 0, 0,
                    true, true, true, 0) as IFeature;
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException("放样失败：每个截面应是闭合轮廓，且草图平面沿 loft 方向排列");
            RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), isCut ? "loft_cut" : "loft", 0);
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 放样 {feat.Name}: {string.Join(" → ", sketches)}, cut={isCut}");
            return FeatureResult(feat);
        }

        public Dictionary<string, object> InsertShell(double thickness, List<string> openFaces, bool outward)
        {
            EnsureActiveDoc();
            if (thickness <= 0)
                throw new ArgumentOutOfRangeException(nameof(thickness), "壁厚必须为正");
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (openFaces != null)
            {
                foreach (var reference in openFaces)
                {
                    if (!SelectReference(reference, true, 1))
                        throw new InvalidOperationException($"无法选中抽壳开口面 {reference}");
                }
            }
            var mdoc = (ModelDoc2)ActiveDoc;
            try
            {
                mdoc.InsertFeatureShell(thickness, outward);
            }
            catch (Exception ex)
            {
                throw new InvalidOperationException("抽壳失败：开口面应为实体外表面，壁厚不能大于最小半厚。 " + ex.Message);
            }
            ActiveDoc.ClearSelection2(true);
            var feat = LastUserFeature();
            if (feat != null)
                RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), "shell", thickness);
            mdoc.ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 抽壳: 厚 {thickness}m, 开口 {openFaces?.Count ?? 0}");
            return feat != null ? FeatureResult(feat) : new Dictionary<string, object> { { "featureName", "" } };
        }

        public Dictionary<string, object> InsertDraft(double angleDeg, string neutral, List<string> faces, bool flip)
        {
            EnsureActiveDoc();
            if (angleDeg <= 0 || angleDeg >= 45)
                throw new ArgumentOutOfRangeException(nameof(angleDeg), "拔模角应在 (0, 45) 度");
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference(neutral, false, 1))
                throw new InvalidOperationException($"无法选中中性面 {neutral}");
            if (faces == null || faces.Count == 0)
                throw new ArgumentException("draft 需要给出要拔模的面");
            foreach (var reference in faces)
            {
                if (!SelectReference(reference, true, 2))
                    throw new InvalidOperationException($"无法选中拔模面 {reference}");
            }
            var feat = ActiveDoc.FeatureManager.InsertMultiFaceDraft(
                angleDeg * Math.PI / 180.0, flip, false, 0, false, false) as IFeature;
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException("拔模失败：中性面应与拔模面相邻");
            ((ModelDoc2)ActiveDoc).ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 拔模 {angleDeg}° 中性面 {neutral}");
            return feat != null ? FeatureResult(feat) : new Dictionary<string, object> { { "featureName", "" } };
        }

        public Dictionary<string, object> InsertSheetMetalBase(double thickness)
        {
            EnsureActiveDoc();
            if (thickness <= 0)
                throw new ArgumentOutOfRangeException(nameof(thickness), "板厚必须为正");
            var mdoc = (ModelDoc2)ActiveDoc;
            mdoc.InsertSketch2(true);
            _sketchOnRefPlane = false;
            ActiveDoc.ClearSelection2(true);
            if (!SelectActiveSketch("钣金基体法兰"))
                throw new InvalidOperationException("无法选中钣金轮廓草图");
            double radius = Math.Max(thickness, 0.0005);
            IFeature feat = ActiveDoc.FeatureManager.InsertSheetMetalBaseFlange2(
                thickness, false, radius, 0, 0, false, 0, 0, 0, null,
                true, 0, 0, 0, 0.5, true, false, true, true) as IFeature;
            if (feat == null)
            {
                ActiveDoc.ClearSelection2(true);
                SelectActiveSketch("钣金基体法兰");
                feat = ActiveDoc.FeatureManager.InsertSheetMetalBaseFlange(
                    thickness, false, radius, 0, 0, false, 0, 0, 0, null,
                    true, 0, 0, 0, 0.5, true) as IFeature;
            }
            if (feat == null)
            {
                ActiveDoc.ClearSelection2(true);
                SelectActiveSketch("钣金基体法兰");
                Extrusion(thickness, false, false, 0, false);
                bool converted = ActiveDoc.FeatureManager.InsertConvertToSheetMetal2(
                    thickness, false, false, radius, 0, 0, 0.5, 0, 0, false);
                feat = LastUserFeature();
                if (!converted && (feat == null || feat.GetTypeName2() != "SheetMetal" && feat.GetTypeName2() != "SMBaseFlange"))
                    throw new InvalidOperationException("钣金基体法兰失败：轮廓应闭合且当前零件尚未是实体零件");
            }
            ActiveDoc.ClearSelection2(true);
            RecordFeatureSketchLink(feat.Name, feat.GetTypeName2(), "sheet_metal", thickness);
            mdoc.ViewZoomtofit2();
            Console.WriteLine($"[SwCommands] 钣金基体法兰 {feat.Name}: 厚 {thickness}m");
            return FeatureResult(feat);
        }

        public Dictionary<string, object> InsertCosmeticThread(string faceReference, double diameter, double depth)
        {
            EnsureActiveDoc();
            ExitSketchIfActive();
            ActiveDoc.ClearSelection2(true);
            if (!SelectReference(faceReference, false, 0))
                throw new InvalidOperationException($"无法选中螺纹所在圆柱面 {faceReference}");
            var feat = ActiveDoc.FeatureManager.InsertCosmeticThread3(
                0, "", "", diameter, 0, depth, "") as IFeature;
            ActiveDoc.ClearSelection2(true);
            if (feat == null)
                throw new InvalidOperationException("装饰螺纹失败：请选圆柱面，diameter 为公称直径");
            Console.WriteLine($"[SwCommands] 装饰螺纹: Ø{diameter}m 深 {depth}m");
            return feat != null ? FeatureResult(feat) : new Dictionary<string, object> { { "featureName", "" } };
        }

        public Dictionary<string, object> CloseSketchNamed()
        {
            EnsureActiveDoc();
            string name = _activeSketchName ?? "";
            ((ModelDoc2)ActiveDoc).InsertSketch2(true);
            _sketchOnRefPlane = false;
            return new Dictionary<string, object> { { "sketchName", name } };
        }

        private void SelectAllSolidBodies(int mark)
        {
            var bodies = ((IPartDoc)ActiveDoc).GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体");
            var selectionManager = (ISelectionMgr)ActiveDoc.SelectionManager;
            foreach (object bodyObj in bodies)
            {
                var data = (SelectData)selectionManager.CreateSelectData();
                data.Mark = mark;
                if (!((IBody2)bodyObj).Select2(true, data))
                    throw new InvalidOperationException("无法选中实体");
            }
        }

        private List<string> RecentSketchNames(int count)
        {
            var names = new List<string>();
            var feat = ActiveDoc.FirstFeature() as IFeature;
            while (feat != null)
            {
                string type = feat.GetTypeName2();
                if (type == "ProfileFeature" || type == "3DProfileFeature")
                    names.Add(feat.Name);
                feat = feat.GetNextFeature() as IFeature;
            }
            if (names.Count <= count)
                return names;
            return names.GetRange(names.Count - count, count);
        }

        private bool SelectSketchSegments(string sketchName)
        {
            if (string.IsNullOrEmpty(sketchName))
                return SelectActiveSketch("筋");
            var feature = ((IPartDoc)ActiveDoc).FeatureByName(sketchName) as IFeature;
            var sketch = feature?.GetSpecificFeature2() as ISketch;
            var segments = sketch?.GetSketchSegments() as object[];
            if (segments == null || segments.Length == 0)
                return SelectActiveSketch("筋");
            ActiveDoc.ClearSelection2(true);
            bool any = false;
            foreach (object item in segments)
            {
                var segment = item as ISketchSegment;
                if (segment != null && segment.Select4(true, null))
                    any = true;
            }
            return any || SelectActiveSketch("筋");
        }

        private bool SelectSketchByName(string name, bool append, int mark)
        {
            var feat = ((IPartDoc)ActiveDoc).FeatureByName(name) as IFeature;
            return feat != null && feat.Select2(append, mark);
        }

        private IFeature LastUserFeature()
        {
            IFeature last = null;
            var feat = ActiveDoc.FirstFeature() as IFeature;
            while (feat != null)
            {
                string type = feat.GetTypeName2();
                if (type != "OriginProfileFeature" && type != "RefPlane" && type != "MaterialFolder"
                    && type != "CommentsFolder" && type != "FavoriteFolder" && type != "HistoryFolder"
                    && type != "SelectionSetFolder" && type != "SensorFolder" && type != "EnvFolder"
                    && type != "EqnFolder")
                    last = feat;
                feat = feat.GetNextFeature() as IFeature;
            }
            return last;
        }

        private static Dictionary<string, object> FeatureResult(IFeature feat)
        {
            return new Dictionary<string, object>
            {
                { "featureName", feat.Name },
                { "featureType", feat.GetTypeName2() },
            };
        }

        #endregion
    }
}
