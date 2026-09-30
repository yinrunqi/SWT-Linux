using SolidWorks.Interop.sldworks;
using SolidWorks.Interop.swconst;
using SolidWorks.Interop.swmotionstudy;
using System;
using System.Collections;
using System.Collections.Generic;
using System.Linq;

namespace Chapter1.ConnectToSw
{
    /// <summary>
    /// 装配 workflow 的通用原语：按组件内的面编号 / 默认基准面引用选取实体并加配合，
    /// 读取组件几何、配合与约束状态，以及由 Motion Study 马达驱动的逐帧采样。
    /// 所有长度单位为米，角度单位为度。路由统一为 /api/asmwf/&lt;op&gt;。
    /// </summary>
    public partial class SwCommands
    {
        private static readonly Dictionary<string, string> DefaultPlaneNames = new Dictionary<string, string>
        {
            { "front", "前视基准面" },
            { "top", "上视基准面" },
            { "right", "右视基准面" },
        };

        public Dictionary<string, object> AssemblyWorkflowCall(string op, Dictionary<string, object> body)
        {
            EnsureActiveAssembly();
            switch (op)
            {
                case "components":
                    return new Dictionary<string, object> { ["components"] = DescribeComponents() };
                case "component-faces":
                    return GetComponentFaces(
                        Str(body, "component"),
                        (int)Num(body, "maxFaces", 400),
                        Flag(body, "includeOther", false),
                        Num(body, "radius", -1));
                case "mates":
                    return new Dictionary<string, object> { ["mates"] = DescribeMates() };
                case "mate":
                    return AddWorkflowMate(body);
                case "delete-mate":
                    return DeleteMateByName(Str(body, "name"));
                case "delete-component":
                    return DeleteComponentByName(Str(body, "component"));
                case "motion-drive":
                    return CreateRotaryDrive(
                        Str(body, "component"),
                        body.ContainsKey("ref") ? Convert.ToString(body["ref"]) : null,
                        Num(body, "rpm", 10),
                        Num(body, "durationSeconds", 6),
                        Flag(body, "reverse", false));
                case "motion-sample":
                    return SampleMotion(
                        Nums(body, "times"),
                        Flag(body, "interference", true),
                        Flag(body, "resetToZero", true));
                case "motion-delete":
                    return DeleteMotionStudies();
                default:
                    throw new ArgumentException($"未知的装配 workflow 操作: {op}");
            }
        }

        // ------------------------------------------------------------ 参数解析

        private static string Str(Dictionary<string, object> body, string key)
        {
            if (!body.ContainsKey(key) || body[key] == null)
                throw new ArgumentException($"缺少参数: {key}");
            return Convert.ToString(body[key]);
        }

        private static double Num(Dictionary<string, object> body, string key, double fallback)
        {
            return body.ContainsKey(key) && body[key] != null ? Convert.ToDouble(body[key]) : fallback;
        }

        private static bool Flag(Dictionary<string, object> body, string key, bool fallback)
        {
            return body.ContainsKey(key) && body[key] != null ? Convert.ToBoolean(body[key]) : fallback;
        }

        private static double[] Nums(Dictionary<string, object> body, string key)
        {
            var raw = body.ContainsKey(key) ? body[key] as IEnumerable : null;
            if (raw == null || raw is string)
                throw new ArgumentException($"{key} 必须是数组");
            return raw.Cast<object>().Select(Convert.ToDouble).ToArray();
        }

        private static Dictionary<string, object> Obj(Dictionary<string, object> body, string key)
        {
            var value = body.ContainsKey(key) ? body[key] as Dictionary<string, object> : null;
            if (value == null)
                throw new ArgumentException($"{key} 必须是对象 {{component, ref}}");
            return value;
        }

        // ------------------------------------------------------------ 组件与几何

        private List<IFace2> ComponentFaceList(IComponent2 component)
        {
            var faces = new List<IFace2>();
            object bodyInfo;
            var bodies = component.GetBodies3((int)swBodyType_e.swSolidBody, out bodyInfo) as object[];
            foreach (var bodyObject in bodies ?? new object[0])
            {
                var list = (bodyObject as IBody2)?.GetFaces() as object[];
                if (list == null) continue;
                foreach (var faceObject in list)
                {
                    var face = faceObject as IFace2;
                    if (face != null) faces.Add(face);
                }
            }
            return faces;
        }

        private static double[] ComponentTransformArray(IComponent2 component)
        {
            var values = (component.Transform2 as IMathTransform)?.ArrayData as double[];
            return values ?? new double[] { 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0 };
        }

        private List<Dictionary<string, object>> DescribeComponents()
        {
            var result = new List<Dictionary<string, object>>();
            foreach (var item in _activeAssembly.GetComponents(true) as object[] ?? new object[0])
            {
                var component = item as IComponent2;
                if (component == null) continue;
                int status = 1;
                try { status = component.GetConstrainedStatus(); } catch { }
                result.Add(new Dictionary<string, object>
                {
                    ["name"] = component.Name2,
                    ["path"] = component.GetPathName() ?? "",
                    ["isFixed"] = component.IsFixed(),
                    ["constrainedStatus"] = status,
                    ["transform"] = ComponentTransformArray(component),
                });
            }
            return result;
        }

        /// <summary>
        /// 组件的面列表。index 是该组件实体上的全局面序号（与零件文档 observation.faces 一致），
        /// 几何值原样来自 SolidWorks；transform 为组件的 Transform2（旋转 9 项、平移 3 项、比例 1 项）。
        /// </summary>
        public Dictionary<string, object> GetComponentFaces(
            string componentName, int maxFaces, bool includeOther, double radiusFilter = -1)
        {
            var component = FindAssemblyComponent(componentName);
            var faces = ComponentFaceList(component);
            var cylinders = new List<Dictionary<string, object>>();
            var planes = new List<Dictionary<string, object>>();
            var others = new List<Dictionary<string, object>>();
            for (int index = 0; index < faces.Count; index++)
            {
                var face = faces[index];
                var surface = face.IGetSurface();
                bool planar = surface != null && surface.IsPlane();
                bool cylinder = surface != null && surface.IsCylinder();
                if (!planar && !cylinder && !includeOther) continue;
                var item = new Dictionary<string, object>
                {
                    ["index"] = index,
                    ["surfaceType"] = planar ? "plane" : (cylinder ? "cylinder" : "other"),
                };
                try { item["area"] = Convert.ToDouble(face.GetArea()); } catch { }
                try
                {
                    var box = face.GetBox() as double[];
                    if (box != null && box.Length >= 6) item["box"] = box.Take(6).ToArray();
                }
                catch { }
                if (planar)
                {
                    if (radiusFilter >= 0) continue;
                    var normal = face.Normal as double[];
                    if (normal != null && normal.Length >= 3) item["normal"] = normal.Take(3).ToArray();
                    var plane = surface.PlaneParams as double[];
                    if (plane != null && plane.Length >= 6) item["point"] = plane.Skip(3).Take(3).ToArray();
                    planes.Add(item);
                }
                else if (cylinder)
                {
                    var cp = surface.CylinderParams as double[];
                    if (cp != null && cp.Length >= 7)
                    {
                        item["axisOrigin"] = cp.Take(3).ToArray();
                        item["axis"] = cp.Skip(3).Take(3).ToArray();
                        item["radius"] = cp[6];
                    }
                    if (radiusFilter >= 0 && item.ContainsKey("radius")
                        && Math.Abs(Convert.ToDouble(item["radius"]) - radiusFilter) > 2e-4)
                        continue;
                    cylinders.Add(item);
                    if (radiusFilter >= 0 && cylinders.Count >= Math.Max(maxFaces, 1))
                        break;
                }
                else if (radiusFilter >= 0)
                {
                    continue;
                }
                else
                {
                    others.Add(item);
                }
            }
            // 齿轮有大量齿面；圆柱面（轴孔）必须优先返回，否则配合选不到中心孔。
            var items = cylinders.Concat(planes).Concat(others).Take(Math.Max(maxFaces, 1)).ToList();
            return new Dictionary<string, object>
            {
                ["component"] = componentName,
                ["faceCount"] = faces.Count,
                ["transform"] = ComponentTransformArray(component),
                ["faces"] = items,
            };
        }

        // ------------------------------------------------------------ 配合

        /// <summary>
        /// 选中组件上的实体：face:&lt;i&gt;（组件面序号）或 plane:front|top|right（组件默认基准面）。
        /// </summary>
        private void SelectComponentRef(string componentName, string reference, bool append, int mark)
        {
            var component = FindAssemblyComponent(componentName);
            string text = (reference ?? "").Trim();
            int colon = text.IndexOf(':');
            if (colon <= 0)
                throw new ArgumentException($"引用 {reference} 应写成 face:i、plane:front|top|right 或 bore:auto");
            string kind = text.Substring(0, colon).ToLowerInvariant();
            string value = text.Substring(colon + 1).Trim().ToLowerInvariant();
            if (kind == "bore")
            {
                FindMotorCylinderFace(component, append);
                return;
            }
            var model = (IModelDoc2)_activeAssembly;
            if (kind == "face")
            {
                var faces = ComponentFaceList(component);
                int index = int.Parse(value);
                if (index < 0 || index >= faces.Count)
                    throw new ArgumentException($"组件 {componentName} 没有面 {index}（共 {faces.Count} 个）");
                var selectData = ((ISelectionMgr)model.SelectionManager).CreateSelectData();
                selectData.Mark = mark;
                if (!((IEntity)faces[index]).Select4(append, selectData))
                    throw new InvalidOperationException($"选择 {componentName} 的 face:{index} 失败");
                return;
            }
            if (kind == "plane")
            {
                if (!DefaultPlaneNames.ContainsKey(value))
                    throw new ArgumentException($"未知基准面 {value}，可选 front/top/right");
                if (!SelectByIDInAssembly(DefaultPlaneNames[value], componentName, "PLANE", mark, append))
                    throw new InvalidOperationException($"选择 {componentName} 的基准面 {value} 失败");
                return;
            }
            throw new ArgumentException($"未知引用类型 {kind}");
        }

        private List<IFeature> MateFeatures()
        {
            var result = new List<IFeature>();
            var feature = ((IModelDoc2)_activeAssembly).FirstFeature() as IFeature;
            while (feature != null)
            {
                if (feature.GetTypeName2() == "MateGroup")
                {
                    var sub = feature.GetFirstSubFeature() as IFeature;
                    while (sub != null)
                    {
                        result.Add(sub);
                        sub = sub.GetNextSubFeature() as IFeature;
                    }
                }
                feature = feature.GetNextFeature() as IFeature;
            }
            return result;
        }

        private List<Dictionary<string, object>> DescribeMates()
        {
            var result = new List<Dictionary<string, object>>();
            foreach (var feature in MateFeatures())
            {
                var item = new Dictionary<string, object>
                {
                    ["name"] = feature.Name,
                    ["type"] = feature.GetTypeName2(),
                    ["suppressed"] = feature.IsSuppressed(),
                };
                try
                {
                    bool warning;
                    item["errorCode"] = feature.GetErrorCode2(out warning);
                    item["warning"] = warning;
                }
                catch { }
                var mate = feature.GetSpecificFeature2() as IMate2;
                if (mate != null)
                {
                    var components = new List<string>();
                    for (int i = 0; i < mate.GetMateEntityCount(); i++)
                    {
                        var entity = mate.MateEntity(i);
                        var owner = entity?.ReferenceComponent;
                        if (owner != null) components.Add(owner.Name2);
                    }
                    item["components"] = components.ToArray();
                    item["mateType"] = mate.Type;
                }
                var gear = feature.GetDefinition() as IGearMateFeatureData;
                if (gear != null)
                {
                    item["gearRatioNumerator"] = gear.GearRatioNumerator;
                    item["gearRatioDenominator"] = gear.GearRatioDenominator;
                    item["reverse"] = gear.Reverse;
                }
                result.Add(item);
            }
            return result;
        }

        private static readonly Dictionary<string, swMateType_e> MateTypes = new Dictionary<string, swMateType_e>
        {
            { "coincident", swMateType_e.swMateCOINCIDENT },
            { "concentric", swMateType_e.swMateCONCENTRIC },
            { "parallel", swMateType_e.swMatePARALLEL },
            { "perpendicular", swMateType_e.swMatePERPENDICULAR },
            { "distance", swMateType_e.swMateDISTANCE },
            { "angle", swMateType_e.swMateANGLE },
            { "gear", swMateType_e.swMateGEAR },
        };

        public Dictionary<string, object> AddWorkflowMate(Dictionary<string, object> body)
        {
            string typeName = Str(body, "type").ToLowerInvariant();
            if (!MateTypes.ContainsKey(typeName))
                throw new ArgumentException($"不支持的配合类型 {typeName}，可选 {string.Join("/", MateTypes.Keys)}");
            var a = Obj(body, "a");
            var b = Obj(body, "b");
            string alignText = body.ContainsKey("align") ? Convert.ToString(body["align"]).ToLowerInvariant() : "closest";
            int align = alignText == "aligned" ? 0 : alignText == "anti" ? 1 : 2;
            bool flip = Flag(body, "flip", false);
            double distance = Num(body, "distance", 0.0);
            double angle = Num(body, "angleDeg", 0.0) * Math.PI / 180.0;
            double numerator = Num(body, "ratioNumerator", 1.0);
            double denominator = Num(body, "ratioDenominator", 1.0);
            bool reverse = Flag(body, "reverse", false);

            var before = new HashSet<string>(MateFeatures().Select(f => f.Name));
            ClearAssemblySelection();
            SelectComponentRef(Str(a, "component"), Str(a, "ref"), false, 1);
            SelectComponentRef(Str(b, "component"), Str(b, "ref"), true, 1);
            int errStatus = 0;
            _activeAssembly.AddMate5(
                (int)MateTypes[typeName], align, flip,
                distance, distance, distance,
                numerator, denominator,
                angle, angle, angle,
                false, false, 0, out errStatus);
            ClearAssemblySelection();
            var added = MateFeatures().Where(f => !before.Contains(f.Name)).ToList();
            if (errStatus != (int)swAddMateError_e.swAddMateError_NoError || added.Count == 0)
            {
                foreach (var feature in added) DeleteMateByName(feature.Name);
                string reason = errStatus == 3 ? "对齐方式不可行" : errStatus == 4 ? "所选实体不能构成该配合"
                    : errStatus == 5 ? "装配过定义（与已有配合冲突）" : errStatus == 6 ? "齿轮比不合法" : "未知原因";
                throw new InvalidOperationException($"{typeName} 配合失败: errStatus={errStatus}（{reason}）");
            }
            var mateFeature = added[added.Count - 1];
            if (typeName == "gear")
            {
                var data = mateFeature.GetDefinition() as IGearMateFeatureData;
                if (data != null && data.Reverse != reverse)
                {
                    data.Reverse = reverse;
                    mateFeature.ModifyDefinition(data, (IModelDoc2)_activeAssembly, null);
                }
            }
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            bool warning = false;
            int errorCode = 0;
            try { errorCode = mateFeature.GetErrorCode2(out warning); } catch { }
            if (errorCode != 0)
            {
                string name = mateFeature.Name;
                DeleteMateByName(name);
                throw new InvalidOperationException($"{typeName} 配合建立后求解失败 (errorCode={errorCode})，已撤销");
            }
            return new Dictionary<string, object>
            {
                ["name"] = mateFeature.Name,
                ["type"] = typeName,
                ["warning"] = warning,
            };
        }

        public Dictionary<string, object> DeleteMateByName(string name)
        {
            var feature = MateFeatures().FirstOrDefault(f => f.Name == name);
            if (feature == null)
                throw new InvalidOperationException($"找不到配合 {name}");
            ClearAssemblySelection();
            feature.Select2(false, 0);
            bool ok = ((IModelDoc2)_activeAssembly).Extension.DeleteSelection2(
                (int)swDeleteSelectionOptions_e.swDelete_Absorbed);
            ClearAssemblySelection();
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            return new Dictionary<string, object> { ["deleted"] = name, ["ok"] = ok };
        }

        public Dictionary<string, object> DeleteComponentByName(string componentName)
        {
            var component = FindAssemblyComponent(componentName);
            ClearAssemblySelection();
            component.Select4(false, null, false);
            bool ok = ((IModelDoc2)_activeAssembly).Extension.DeleteSelection2(
                (int)swDeleteSelectionOptions_e.swDelete_Absorbed);
            ClearAssemblySelection();
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            return new Dictionary<string, object> { ["deleted"] = componentName, ["ok"] = ok };
        }

        // ------------------------------------------------------------ 运动

        /// <summary>
        /// 在组件上放一个恒速旋转马达。ref 为 face:i 时以该圆柱面（或圆边所在面）为位置与方向参考，
        /// 否则回退到组件上最大的 Z 向圆柱面。已有马达会被改为新的定义。
        /// </summary>
        public Dictionary<string, object> CreateRotaryDrive(
            string componentName, string reference, double rpm, double durationSeconds, bool reverse)
        {
            if (rpm <= 0 || durationSeconds <= 0)
                throw new ArgumentException("rpm 与 durationSeconds 必须为正");
            var component = FindAssemblyComponent(componentName);
            IFace2 face;
            if (!string.IsNullOrWhiteSpace(reference) && !reference.Trim().StartsWith("bore"))
            {
                var faces = ComponentFaceList(component);
                string text = reference.Trim();
                if (!text.StartsWith("face:"))
                    throw new ArgumentException("马达参考必须是 face:i 或 bore:auto");
                int index = int.Parse(text.Substring(5));
                if (index < 0 || index >= faces.Count)
                    throw new ArgumentException($"组件 {componentName} 没有面 {index}");
                face = faces[index];
                var surface = face.IGetSurface();
                if (surface == null || !surface.IsCylinder())
                    throw new ArgumentException($"face:{index} 不是圆柱面，不能作为旋转马达参考");
            }
            else
            {
                face = FindMotorCylinderFace(component);
            }

            var study = EnsureMotionStudy();
            if (_activeMotionMotor == null)
            {
                foreach (var featureObject in study.GetMotionFeatures() as object[] ?? new object[0])
                {
                    var feature = featureObject as IFeature;
                    if (feature != null && feature.GetTypeName2() == "AEMRotationalMotor")
                    {
                        _activeMotionMotor = feature;
                        break;
                    }
                }
            }
            ISimulationMotorFeatureData data;
            if (_activeMotionMotor == null)
            {
                data = study.CreateDefinition((int)swFeatureNameID_e.swFmAEMRotationalMotor) as ISimulationMotorFeatureData;
                if (data == null) throw new InvalidOperationException("旋转马达定义创建失败");
                data.Location = face;
                data.DirectionReference = face;
                data.ReverseDirection = reverse;
                data.ConstantSpeedMotor(rpm);
                _activeMotionMotor = study.CreateFeature(data) as IFeature;
                if (_activeMotionMotor == null) throw new InvalidOperationException("创建旋转马达特征失败");
            }
            else
            {
                data = _activeMotionMotor.GetDefinition() as ISimulationMotorFeatureData;
                if (data == null) throw new InvalidOperationException("读取已有旋转马达定义失败");
                data.Location = face;
                data.DirectionReference = face;
                data.ReverseDirection = reverse;
                data.ConstantSpeedMotor(rpm);
                if (!_activeMotionMotor.ModifyDefinition(data, (IModelDoc2)_activeAssembly, null))
                    throw new InvalidOperationException("更新旋转马达定义失败");
            }
            if (!study.SetDuration(durationSeconds))
                throw new InvalidOperationException($"Motion Study 时长设置失败: {durationSeconds}s");
            return new Dictionary<string, object>
            {
                ["studyName"] = study.Name,
                ["motor"] = _activeMotionMotor.Name,
                ["component"] = componentName,
                ["rpm"] = rpm,
                ["durationSeconds"] = durationSeconds,
                ["reverse"] = reverse,
            };
        }

        /// <summary>按给定时间点定位 Motion Study，逐帧读取全部组件 Transform，可选逐帧原生干涉检测。</summary>
        public Dictionary<string, object> SampleMotion(double[] times, bool interference, bool resetToZero)
        {
            var study = EnsureMotionStudy();
            double duration = study.GetDuration();
            var frames = new List<Dictionary<string, object>>();
            var model = (IModelDoc2)_activeAssembly;
            foreach (double time in times)
            {
                if (time < 0 || time > duration + 1e-9)
                    throw new ArgumentException($"时间 {time} 超出 Motion Study 时长 {duration}");
                bool updated = study.SetTime(time);
                model.EditRebuild3();
                var transforms = new Dictionary<string, object>();
                foreach (var item in _activeAssembly.GetComponents(true) as object[] ?? new object[0])
                {
                    var component = item as IComponent2;
                    if (component != null) transforms[component.Name2] = ComponentTransformArray(component);
                }
                var frame = new Dictionary<string, object>
                {
                    ["time"] = time,
                    ["studyTime"] = study.GetTime(),
                    ["updated"] = updated,
                    ["transforms"] = transforms,
                };
                if (interference) frame["interference"] = CheckAssemblyInterference();
                frames.Add(frame);
            }
            if (resetToZero)
            {
                study.SetTime(0);
                model.EditRebuild3();
            }
            return new Dictionary<string, object>
            {
                ["studyName"] = study.Name,
                ["duration"] = duration,
                ["frames"] = frames,
            };
        }

        /// <summary>删除全部 Motion Study（回到纯模型状态，便于继续编辑配合）。</summary>
        public Dictionary<string, object> DeleteMotionStudies()
        {
            var manager = ((IModelDoc2)_activeAssembly).Extension.GetMotionStudyManager() as IMotionStudyManager;
            var deleted = new List<string>();
            if (manager != null && manager.GetMotionStudyCount() > 0)
            {
                var names = (manager.GetMotionStudyNames() as IEnumerable)?.Cast<object>().Select(n => n.ToString()).ToList()
                    ?? new List<string>();
                foreach (var name in names)
                {
                    try { if (manager.DeleteMotionStudy(name)) deleted.Add(name); } catch { }
                }
            }
            _activeMotionStudy = null;
            _activeMotionMotor = null;
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            return new Dictionary<string, object> { ["deleted"] = deleted.ToArray() };
        }
    }
}
