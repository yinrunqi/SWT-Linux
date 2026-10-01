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
            // 进程外 API 每次调用后 SolidWorks 都会刷新界面；逐面读几何时这会让一次查询拖到几分钟。
            bool wasInProgress = _swApp.CommandInProgress;
            _swApp.CommandInProgress = true;
            try
            {
                return AssemblyWorkflowDispatch(op, body);
            }
            finally
            {
                _swApp.CommandInProgress = wasInProgress;
            }
        }

        private Dictionary<string, object> AssemblyWorkflowDispatch(string op, Dictionary<string, object> body)
        {
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
                case "component-edges":
                    return GetComponentEdges(
                        Str(body, "component"),
                        (int)Num(body, "maxEdges", 200),
                        body.ContainsKey("kind") ? Convert.ToString(body["kind"]) : "all");
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
                        Flag(body, "reverse", false),
                        StudyTypeCode(body.ContainsKey("studyType") ? Convert.ToString(body["studyType"]) : "animation"));
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

        private static int StudyTypeCode(string name)
        {
            switch ((name ?? "animation").Trim().ToLowerInvariant())
            {
                case "animation": return (int)swMotionStudyType_e.swMotionStudyTypeAssembly;
                case "basic": return (int)swMotionStudyType_e.swMotionStudyTypePhysicalSimulation;
                case "motion": return (int)swMotionStudyType_e.swMotionStudyTypeCosmosMotion;
                default: throw new ArgumentException($"studyType 只能是 animation / basic / motion: {name}");
            }
        }

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

        /// <summary>
        /// 组件的实体。优先取零件文档里的实体：在装配体上下文逐面读几何的 COM 往返极慢
        /// （几百个面的齿轮要几分钟），零件上下文则很快；面/边序号两者一致。
        /// 要在装配体里选中或作为马达参考时，用 InAssembly 换算成装配体上下文实体。
        /// </summary>
        private static object[] ComponentBodies(IComponent2 component)
        {
            if (component.GetModelDoc2() is IPartDoc part)
            {
                var partBodies = part.GetBodies2((int)swBodyType_e.swSolidBody, true) as object[];
                if (partBodies != null && partBodies.Length > 0) return partBodies;
            }
            object bodyInfo;
            return component.GetBodies3((int)swBodyType_e.swSolidBody, out bodyInfo) as object[] ?? new object[0];
        }

        private static T InAssembly<T>(IComponent2 component, T entity) where T : class
        {
            return component.GetCorrespondingEntity(entity) as T ?? entity;
        }

        private List<IFace2> ComponentFaceList(IComponent2 component)
        {
            var faces = new List<IFace2>();
            var bodies = ComponentBodies(component);
            foreach (var bodyObject in bodies)
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

        private List<IEdge> ComponentEdgeList(IComponent2 component)
        {
            var edges = new List<IEdge>();
            foreach (var bodyObject in ComponentBodies(component))
            {
                var list = (bodyObject as IBody2)?.GetEdges() as object[];
                if (list == null) continue;
                foreach (var edgeObject in list)
                {
                    var edge = edgeObject as IEdge;
                    if (edge != null) edges.Add(edge);
                }
            }
            return edges;
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
                var watch = System.Diagnostics.Stopwatch.StartNew();
                try { status = component.GetConstrainedStatus(); } catch { }
                if (watch.ElapsedMilliseconds > 1000)
                    Console.WriteLine($"[SwCommands] GetConstrainedStatus {component.Name2}: {watch.ElapsedMilliseconds} ms");
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
            var watch = System.Diagnostics.Stopwatch.StartNew();
            var component = FindAssemblyComponent(componentName);
            var faces = ComponentFaceList(component);
            long listed = watch.ElapsedMilliseconds;
            var cylinders = new List<Dictionary<string, object>>();
            var planes = new List<Dictionary<string, object>>();
            var others = new List<Dictionary<string, object>>();
            for (int index = 0; index < faces.Count; index++)
            {
                var face = faces[index];
                var surface = face.IGetSurface();
                bool planar = surface != null && surface.IsPlane();
                bool cylinder = surface != null && surface.IsCylinder();
                bool sphere = surface != null && !planar && !cylinder && surface.IsSphere();
                if (!planar && !cylinder && !sphere && !includeOther) continue;
                bool other = !planar && !cylinder && !sphere;
                // 平面与其他面排在圆柱/球面之后，超过 maxFaces 的必然被截掉，不再逐面读几何（齿轮有上千个齿面）。
                if ((planar || other) && radiusFilter >= 0) continue;
                if (planar && planes.Count >= Math.Max(maxFaces, 1)) continue;
                if (other && others.Count >= Math.Max(maxFaces, 1)) continue;
                var item = new Dictionary<string, object>
                {
                    ["index"] = index,
                    ["surfaceType"] = planar ? "plane" : cylinder ? "cylinder" : sphere ? "sphere" : "other",
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
                else if (sphere)
                {
                    var sp = surface.SphereParams as double[];
                    if (sp != null && sp.Length >= 4)
                    {
                        item["center"] = sp.Take(3).ToArray();
                        item["radius"] = sp[3];
                    }
                    if (radiusFilter >= 0 && item.ContainsKey("radius")
                        && Math.Abs(Convert.ToDouble(item["radius"]) - radiusFilter) > 2e-4)
                        continue;
                    cylinders.Add(item);
                }
                else
                {
                    others.Add(item);
                }
            }
            // 齿轮有大量齿面；圆柱面（轴孔）必须优先返回，否则配合选不到中心孔。
            var items = cylinders.Concat(planes).Concat(others).Take(Math.Max(maxFaces, 1)).ToList();
            Console.WriteLine(
                $"[SwCommands] component-faces {componentName}: {faces.Count} faces, list {listed} ms, total {watch.ElapsedMilliseconds} ms");
            return new Dictionary<string, object>
            {
                ["component"] = componentName,
                ["faceCount"] = faces.Count,
                ["transform"] = ComponentTransformArray(component),
                ["faces"] = items,
            };
        }

        /// <summary>
        /// 组件的边列表。index 是组件实体上的全局边序号；line 给出端点与单位方向，circle 给出圆心、轴向与半径。
        /// kind 为 line / circle / all。坐标为零件坐标系（与 faces 相同，需乘 transform 才是装配坐标）。
        /// </summary>
        public Dictionary<string, object> GetComponentEdges(string componentName, int maxEdges, string kind)
        {
            var component = FindAssemblyComponent(componentName);
            var edges = ComponentEdgeList(component);
            string filter = (kind ?? "all").ToLowerInvariant();
            var items = new List<Dictionary<string, object>>();
            for (int index = 0; index < edges.Count && items.Count < Math.Max(maxEdges, 1); index++)
            {
                var curve = edges[index].GetCurve() as ICurve;
                if (curve == null) continue;
                bool line = curve.IsLine();
                bool circle = !line && curve.IsCircle();
                if (filter == "line" && !line) continue;
                if (filter == "circle" && !circle) continue;
                if (filter == "all" && !line && !circle) continue;
                var item = new Dictionary<string, object>
                {
                    ["index"] = index,
                    ["curveType"] = line ? "line" : "circle",
                };
                if (line)
                {
                    var start = (edges[index].GetStartVertex() as IVertex)?.GetPoint() as double[];
                    var end = (edges[index].GetEndVertex() as IVertex)?.GetPoint() as double[];
                    if (start != null && end != null && start.Length >= 3 && end.Length >= 3)
                    {
                        double dx = end[0] - start[0], dy = end[1] - start[1], dz = end[2] - start[2];
                        double length = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                        item["start"] = start.Take(3).ToArray();
                        item["end"] = end.Take(3).ToArray();
                        item["length"] = length;
                        if (length > 1e-12)
                            item["direction"] = new[] { dx / length, dy / length, dz / length };
                    }
                }
                else
                {
                    var cp = curve.CircleParams as double[];
                    if (cp != null && cp.Length >= 7)
                    {
                        item["center"] = cp.Take(3).ToArray();
                        item["axis"] = cp.Skip(3).Take(3).ToArray();
                        item["radius"] = cp[6];
                    }
                }
                items.Add(item);
            }
            return new Dictionary<string, object>
            {
                ["component"] = componentName,
                ["edgeCount"] = edges.Count,
                ["transform"] = ComponentTransformArray(component),
                ["edges"] = items,
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
            var model = (IModelDoc2)_activeAssembly;
            if (text == "" || text.ToLowerInvariant() == "component")
            {
                var componentSelect = ((ISelectionMgr)model.SelectionManager).CreateSelectData();
                componentSelect.Mark = mark;
                if (!component.Select4(append, componentSelect, false))
                    throw new InvalidOperationException($"选择组件 {componentName} 失败");
                return;
            }
            int colon = text.IndexOf(':');
            if (colon <= 0)
                throw new ArgumentException(
                    $"引用 {reference} 应写成 face:i、edge:i、plane:front|top|right、bore:auto 或 component");
            string kind = text.Substring(0, colon).ToLowerInvariant();
            string value = text.Substring(colon + 1).Trim().ToLowerInvariant();
            if (kind == "bore")
            {
                FindMotorCylinderFace(component, append);
                return;
            }
            if (kind == "edge")
            {
                var edges = ComponentEdgeList(component);
                int edgeIndex = int.Parse(value);
                if (edgeIndex < 0 || edgeIndex >= edges.Count)
                    throw new ArgumentException($"组件 {componentName} 没有边 {edgeIndex}（共 {edges.Count} 条）");
                var edgeSelect = ((ISelectionMgr)model.SelectionManager).CreateSelectData();
                edgeSelect.Mark = mark;
                if (!((IEntity)InAssembly(component, edges[edgeIndex])).Select4(append, edgeSelect))
                    throw new InvalidOperationException($"选择 {componentName} 的 edge:{edgeIndex} 失败");
                return;
            }
            if (kind == "face")
            {
                var faces = ComponentFaceList(component);
                int index = int.Parse(value);
                if (index < 0 || index >= faces.Count)
                    throw new ArgumentException($"组件 {componentName} 没有面 {index}（共 {faces.Count} 个）");
                var selectData = ((ISelectionMgr)model.SelectionManager).CreateSelectData();
                selectData.Mark = mark;
                if (!((IEntity)InAssembly(component, faces[index])).Select4(append, selectData))
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

        /// <summary>
        /// 把引用解析为装配体上下文中的实体对象（供 CreateMateData 的 EntitiesToMate 使用）：
        /// 先按 SelectComponentRef 选中，再从选择集取回，保证拿到的是组件实例上的代理实体。
        /// </summary>
        private object ResolveComponentEntity(string componentName, string reference)
        {
            ClearAssemblySelection();
            SelectComponentRef(componentName, reference, false, 0);
            var manager = (ISelectionMgr)((IModelDoc2)_activeAssembly).SelectionManager;
            int count = manager.GetSelectedObjectCount2(-1);
            object entity = count > 0 ? manager.GetSelectedObject6(count, -1) : null;
            ClearAssemblySelection();
            if (entity == null)
                throw new InvalidOperationException($"无法取得 {componentName} 上的实体 {reference}");
            return entity;
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
                    var entities = new List<Dictionary<string, object>>();
                    for (int i = 0; i < mate.GetMateEntityCount(); i++)
                    {
                        var entity = mate.MateEntity(i);
                        var owner = entity?.ReferenceComponent;
                        if (owner != null) components.Add(owner.Name2);
                        if (entity == null) continue;
                        // swMateEntity2ReferenceType_e；EntityParams 为装配坐标下的 点(3) 方向(3) 半径1 半径2（米）
                        var described = new Dictionary<string, object> { ["component"] = owner?.Name2 };
                        try { described["refType"] = entity.ReferenceType; } catch { }
                        try { described["params"] = entity.EntityParams as double[]; } catch { }
                        entities.Add(described);
                    }
                    item["components"] = components.ToArray();
                    item["entities"] = entities.ToArray();
                    item["mateType"] = mate.Type;
                }
                var gear = feature.GetDefinition() as IGearMateFeatureData;
                if (gear != null)
                {
                    // 与 mate 操作同一约定（见 AddWorkflowMate）：读回的分子/分母与 AddMate5 相反，Reverse=false 才是反向
                    item["gearRatioNumerator"] = gear.GearRatioDenominator;
                    item["gearRatioDenominator"] = gear.GearRatioNumerator;
                    item["reverse"] = !gear.Reverse;
                }
                var definition = feature.GetDefinition();
                if (definition is IRackPinionMateFeatureData rack)
                {
                    item["kind"] = "rackpinion";
                    item["diameterType"] = rack.DiameterType;
                    item["diameterValue"] = rack.DiameterVal;
                    item["reverse"] = rack.Reverse;
                }
                else if (definition is IScrewMateFeatureData screw)
                {
                    item["kind"] = "screw";
                    item["revolutionType"] = screw.RevolutionType;
                    item["revolutionValue"] = screw.RevolutionVal;
                    item["reverse"] = screw.Reverse;
                }
                else if (definition is ILinearCouplerMateFeatureData coupler)
                {
                    item["kind"] = "linearcoupler";
                    item["ratioNumerator"] = coupler.CouplerRatioNumerator;
                    item["ratioDenominator"] = coupler.CouplerRatioDenominator;
                    item["reverse"] = coupler.Reverse;
                }
                else if (definition is ISlotMateFeatureData slot)
                {
                    item["kind"] = "slot";
                    item["constraint"] = slot.Constraint;
                }
                else if (definition is ILockMateFeatureData)
                {
                    item["kind"] = "lock";
                }
                else if (definition is IConcentricMateFeatureData concentric)
                {
                    item["lockRotation"] = concentric.LockRotation;
                }
                else if (definition is IDistanceMateFeatureData distance)
                {
                    try
                    {
                        item["distance"] = distance.Distance;
                        if (distance.MaximumDistance != distance.MinimumDistance)
                        {
                            item["distanceMin"] = distance.MinimumDistance;
                            item["distanceMax"] = distance.MaximumDistance;
                        }
                    }
                    catch { }
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
            // CreateMateData(LOCK) 传组件对象会报 errorStatus=4；选中两个组件再 AddMate5 可以
            { "lock", swMateType_e.swMateLOCK },
        };

        public Dictionary<string, object> AddWorkflowMate(Dictionary<string, object> body)
        {
            string typeName = Str(body, "type").ToLowerInvariant().Replace("_", "");
            if (MechanicalMateTypes.ContainsKey(typeName))
                return AddMechanicalMate(typeName, body);
            if (!MateTypes.ContainsKey(typeName))
                throw new ArgumentException(
                    $"不支持的配合类型 {typeName}，可选 {string.Join("/", MateTypes.Keys.Concat(MechanicalMateTypes.Keys))}");
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
            // 对外约定：ratioNumerator:ratioDenominator = a 的齿数 : b 的齿数（ω_b = ω_a·num/den），
            // reverse=true 表示两者反向转（外啮合）。实测（Animation 量转角）：AddMate5 的齿轮比就是这个含义，
            // 但 SolidWorks 的 Reverse=false 才是反向；IGearMateFeatureData 读回的分子/分母与 AddMate5 相反。
            bool gear = typeName == "gear";
            bool swReverse = gear ? !reverse : reverse;
            bool lockRotation = typeName == "concentric" && Flag(body, "lockRotation", false);
            double distanceMax = Num(body, "distanceMax", distance);
            double distanceMin = Num(body, "distanceMin", distance);
            bool limit = typeName == "distance" && distanceMax > distanceMin + 1e-9;
            if (typeName == "distance" && distanceMax < distanceMin - 1e-9)
                throw new ArgumentException("distanceMax 不能小于 distanceMin");
            if (limit && (distance < distanceMin - 1e-9 || distance > distanceMax + 1e-9))
                distance = Math.Min(Math.Max(distance, distanceMin), distanceMax);

            var before = new HashSet<string>(MateFeatures().Select(f => f.Name));
            ClearAssemblySelection();
            SelectComponentRef(Str(a, "component"), Str(a, "ref"), false, 1);
            SelectComponentRef(Str(b, "component"), Str(b, "ref"), true, 1);
            int errStatus = 0;
            _activeAssembly.AddMate5(
                (int)MateTypes[typeName], align, flip,
                distance, limit ? distanceMax : distance, limit ? distanceMin : distance,
                numerator, denominator,
                angle, angle, angle,
                false, lockRotation, 0, out errStatus);
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
                if (data != null && data.Reverse != swReverse)
                {
                    // 读回的比值是反的，原样写回会把齿轮比颠倒，所以写回前换回来
                    data.GearRatioNumerator = numerator;
                    data.GearRatioDenominator = denominator;
                    data.Reverse = swReverse;
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
            var result = new Dictionary<string, object>
            {
                ["name"] = mateFeature.Name,
                ["type"] = typeName,
                ["warning"] = warning,
            };
            if (lockRotation) result["lockRotation"] = true;
            if (limit)
            {
                result["distanceMin"] = distanceMin;
                result["distanceMax"] = distanceMax;
            }
            return result;
        }

        private static readonly Dictionary<string, swMateType_e> MechanicalMateTypes = new Dictionary<string, swMateType_e>
        {
            { "slot", swMateType_e.swMateSLOT },
            { "rackpinion", swMateType_e.swMateRACKPINION },
            { "screw", swMateType_e.swMateSCREW },
            { "linearcoupler", swMateType_e.swMateLINEARCOUPLER },
        };

        private string ReferenceComponentName(Dictionary<string, object> end)
        {
            if (end.ContainsKey("referenceComponent") && end["referenceComponent"] != null)
                return Convert.ToString(end["referenceComponent"]);
            foreach (var item in _activeAssembly.GetComponents(true) as object[] ?? new object[0])
            {
                var component = item as IComponent2;
                if (component != null && component.IsFixed()) return component.Name2;
            }
            throw new InvalidOperationException("线性耦合需要参考组件：请给 referenceComponent，或先固定一个基座组件");
        }

        /// <summary>
        /// 机械配合（槽口、齿条小齿轮、螺旋、线性耦合、锁定）走 CreateMateData/CreateMate：
        /// 这些类型在 AddMate5 里没有对应的参数位（直径、导程、耦合比、槽口约束）。
        /// a/b 的 ref 语义：
        ///   slot         a=槽口面，b=销的圆柱面/轴
        ///   rackpinion   a=齿条上沿运动方向的直线边，b=小齿轮的圆柱面或圆边
        ///   screw        a=螺杆圆柱面，b=螺母孔圆柱面
        ///   linearcoupler a/b=各自运动方向上的直线边或平面，referenceComponent=测量位移的参考组件（默认固定件）
        /// 锁定配合走 AddMate5（见 MateTypes）。
        /// </summary>
        private Dictionary<string, object> AddMechanicalMate(string typeName, Dictionary<string, object> body)
        {
            var a = Obj(body, "a");
            var b = Obj(body, "b");
            string aName = Str(a, "component");
            string bName = Str(b, "component");
            string aRef = a.ContainsKey("ref") && a["ref"] != null ? Convert.ToString(a["ref"]) : "component";
            string bRef = b.ContainsKey("ref") && b["ref"] != null ? Convert.ToString(b["ref"]) : "component";
            string alignText = body.ContainsKey("align") ? Convert.ToString(body["align"]).ToLowerInvariant() : "closest";
            int align = alignText == "aligned" ? 0 : alignText == "anti" ? 1 : 2;
            bool reverse = Flag(body, "reverse", false);

            var before = new HashSet<string>(MateFeatures().Select(f => f.Name));
            object data = _activeAssembly.CreateMateData((int)MechanicalMateTypes[typeName]);
            if (data == null)
                throw new InvalidOperationException($"CreateMateData({typeName}) 返回空");
            var info = new Dictionary<string, object>();
            try
            {
                FillMechanicalMateData(typeName, data, body, a, b, aName, bName, aRef, bRef, align, reverse, info);
            }
            catch (InvalidCastException)
            {
                int dataType = -1;
                try { dataType = ((IMateFeatureData)data).TypeName; } catch { }
                var supported = new[]
                {
                    typeof(ISlotMateFeatureData), typeof(IRackPinionMateFeatureData), typeof(IScrewMateFeatureData),
                    typeof(ILinearCouplerMateFeatureData), typeof(IGearMateFeatureData), typeof(IMateFeatureData),
                }.Where(t => t.IsInstanceOfType(data)).Select(t => t.Name);
                throw new InvalidOperationException(
                    $"CreateMateData({typeName}) 返回的数据类型不符: TypeName={dataType}, 支持接口={string.Join(",", supported)}");
            }
            return FinishMechanicalMate(typeName, data, before, info);
        }

        private void FillMechanicalMateData(
            string typeName, object data, Dictionary<string, object> body,
            Dictionary<string, object> a, Dictionary<string, object> b,
            string aName, string bName, string aRef, string bRef, int align, bool reverse,
            Dictionary<string, object> info)
        {
            switch (typeName)
            {
                case "slot":
                {
                    var slotData = (ISlotMateFeatureData)data;
                    PreselectMatePair(aName, aRef, bName, bRef, info);
                    slotData.MateAlignment = align;
                    string constraint = body.ContainsKey("slotConstraint")
                        ? Convert.ToString(body["slotConstraint"]).ToLowerInvariant() : "free";
                    int constraintValue = constraint == "center" || constraint == "centered" ? 1
                        : constraint == "distance" ? 2 : constraint == "percent" ? 3 : 0;
                    bool flip = Flag(body, "flip", false);
                    double distance = Num(body, "distance", 0.0);
                    double percent = Num(body, "percent", 50.0) / 100.0;
                    Action<ISlotMateFeatureData> apply = target =>
                    {
                        target.Constraint = constraintValue;
                        if (constraintValue == 2) target.Distance = distance;
                        if (constraintValue == 3) target.Percent = percent;
                        target.FlipDirection = flip;
                    };
                    apply(slotData);
                    info[PostEditKey] = (Action<object>)(definition => apply((ISlotMateFeatureData)definition));
                    info["slotConstraint"] = constraint;
                    break;
                }
                case "rackpinion":
                {
                    var rackData = (IRackPinionMateFeatureData)data;
                    rackData.set_EntitiesToMate(
                        (int)swRackPinionMateEntityType_e.swRackPinionMateEntityType_Rack,
                        ResolveComponentEntity(aName, aRef));
                    rackData.set_EntitiesToMate(
                        (int)swRackPinionMateEntityType_e.swRackPinionMateEntityType_Pinion,
                        ResolveComponentEntity(bName, bRef));
                    bool travel = body.ContainsKey("travelPerRevolution");
                    double value = travel ? Num(body, "travelPerRevolution", 0) : Num(body, "pitchDiameter", 0);
                    if (value <= 0)
                        throw new ArgumentException("rackpinion 需要 pitchDiameter（米）或 travelPerRevolution（米/转）");
                    rackData.DiameterType = travel
                        ? (int)swRackPinionMateDistanceOptions_e.swRackTravelPerRevolution
                        : (int)swRackPinionMateDistanceOptions_e.swPinionPitchDiameter;
                    rackData.DiameterVal = value;
                    rackData.Reverse = reverse;
                    info[travel ? "travelPerRevolution" : "pitchDiameter"] = value;
                    break;
                }
                case "screw":
                {
                    var screwData = (IScrewMateFeatureData)data;
                    double lead = Num(body, "lead", 0);
                    if (lead <= 0) throw new ArgumentException("screw 需要 lead（导程，米/转）");
                    PreselectMatePair(aName, aRef, bName, bRef, info);
                    Action<IScrewMateFeatureData> apply = target =>
                    {
                        target.RevolutionType = (int)swScrewMateDistanceOptions_e.swDistancePerRevolution;
                        target.RevolutionVal = lead;
                        target.Reverse = reverse;
                    };
                    apply(screwData);
                    screwData.MateAlignment = align;
                    info[PostEditKey] = (Action<object>)(definition => apply((IScrewMateFeatureData)definition));
                    info["lead"] = lead;
                    break;
                }
                case "linearcoupler":
                {
                    var couplerData = (ILinearCouplerMateFeatureData)data;
                    string refA = ReferenceComponentName(a);
                    string refB = ReferenceComponentName(b);
                    couplerData.MateEntity1 = ResolveComponentEntity(aName, aRef);
                    couplerData.MateEntity2 = ResolveComponentEntity(bName, bRef);
                    couplerData.ReferenceComponent1 = FindAssemblyComponent(refA);
                    couplerData.ReferenceComponent2 = FindAssemblyComponent(refB);
                    double numerator = Num(body, "ratioNumerator", 1.0);
                    double denominator = Num(body, "ratioDenominator", 1.0);
                    if (numerator <= 0 || denominator <= 0)
                        throw new ArgumentException("耦合比分子分母必须为正");
                    couplerData.CouplerRatioNumerator = numerator;
                    couplerData.CouplerRatioDenominator = denominator;
                    couplerData.Reverse = reverse;
                    info["referenceComponents"] = new[] { refA, refB };
                    info["ratio"] = numerator / denominator;
                    break;
                }
            }
        }

        private const string PreselectedKey = "__preselected";
        private const string PostEditKey = "__postEdit";

        /// <summary>
        /// 槽口、螺旋：赋值 EntitiesToMate 在 SW 2025 上总报 errorStatus=4，改用 Mark=1 预选；
        /// 而预选路径下 CreateMate 会忽略数据对象上的数值参数（螺旋导程回落为默认 1mm/转），
        /// 所以建好后再用 ModifyDefinition 写回参数（见 PostEditKey）。
        /// </summary>
        /// <summary>
        /// 螺旋导程：RevolutionVal 经 ModifyDefinition 写不进去（仍是默认 1mm/转），
        /// 它实际存为配合特征上的一个尺寸，所以回退为直接改该尺寸的 SystemValue。返回最终导程（米/转）。
        /// </summary>
        private double ApplyScrewLead(IFeature mateFeature, double lead)
        {
            double Current() => ((IScrewMateFeatureData)mateFeature.GetDefinition()).RevolutionVal;
            if (Math.Abs(Current() - lead) < 1e-9) return lead;
            var display = mateFeature.GetFirstDisplayDimension() as IDisplayDimension;
            while (display != null)
            {
                var dimension = display.GetDimension2(0);
                if (dimension != null)
                {
                    dimension.SetSystemValue3(lead, (int)swSetValueInConfiguration_e.swSetValue_InAllConfigurations, null);
                    Console.WriteLine($"[SwCommands] screw 导程尺寸 {dimension.FullName} -> {lead}");
                }
                display = mateFeature.GetNextDisplayDimension(display) as IDisplayDimension;
            }
            ((IModelDoc2)_activeAssembly).EditRebuild3();
            double applied = Current();
            if (Math.Abs(applied - lead) > 1e-9)
                Console.WriteLine($"[SwCommands] screw 导程未能写入: 期望 {lead}，实际 {applied}");
            return applied;
        }

        private void PreselectMatePair(string aName, string aRef, string bName, string bRef, Dictionary<string, object> info)
        {
            ClearAssemblySelection();
            SelectComponentRef(aName, aRef, false, 1);
            SelectComponentRef(bName, bRef, true, 1);
            info[PreselectedKey] = true;
        }

        private Dictionary<string, object> FinishMechanicalMate(
            string typeName, object data, HashSet<string> before, Dictionary<string, object> info)
        {
            if (!info.Remove(PreselectedKey)) ClearAssemblySelection();
            Action<object> postEdit = null;
            if (info.TryGetValue(PostEditKey, out var postEditValue))
            {
                postEdit = (Action<object>)postEditValue;
                info.Remove(PostEditKey);
            }
            var created = _activeAssembly.CreateMate(data) as IFeature;
            ClearAssemblySelection();
            var added = MateFeatures().Where(f => !before.Contains(f.Name)).ToList();
            int errorStatus = 0;
            try { errorStatus = ((IMateFeatureData)data).ErrorStatus; } catch { }
            if (created == null || added.Count == 0)
            {
                foreach (var feature in added) DeleteMateByName(feature.Name);
                string reason = errorStatus == 3 ? "对齐方式不可行" : errorStatus == 4 ? "所选实体不能构成该配合"
                    : errorStatus == 5 ? "装配过定义（与已有配合冲突）" : "未知原因";
                throw new InvalidOperationException($"{typeName} 配合失败: errorStatus={errorStatus}（{reason}）");
            }
            var mateFeature = created ?? added[added.Count - 1];
            if (postEdit != null)
            {
                object definition = mateFeature.GetDefinition();
                postEdit(definition);
                if (!mateFeature.ModifyDefinition(definition, _activeAssembly, null))
                {
                    string name = mateFeature.Name;
                    DeleteMateByName(name);
                    throw new InvalidOperationException($"{typeName} 配合参数写回失败，已撤销");
                }
            }
            if (typeName == "screw" && info.TryGetValue("lead", out var leadValue))
                info["leadApplied"] = ApplyScrewLead(mateFeature, Convert.ToDouble(leadValue));
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
            var result = new Dictionary<string, object>
            {
                ["name"] = mateFeature.Name,
                ["type"] = typeName,
                ["warning"] = warning,
            };
            foreach (var pair in info) result[pair.Key] = pair.Value;
            return result;
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
            string componentName, string reference, double rpm, double durationSeconds, bool reverse,
            int studyType = (int)swMotionStudyType_e.swMotionStudyTypeAssembly)
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
                face = InAssembly(component, faces[index]);
                var surface = faces[index].IGetSurface();
                if (surface == null || !surface.IsCylinder())
                    throw new ArgumentException($"face:{index} 不是圆柱面，不能作为旋转马达参考");
            }
            else
            {
                face = FindMotorCylinderFace(component);
            }

            var study = EnsureMotionStudy();
            if (study.StudyType != studyType)
            {
                study.StudyType = studyType;
                if (study.StudyType != studyType)
                    throw new InvalidOperationException($"Motion Study 类型无法设为 {studyType}（需要 SOLIDWORKS Motion 插件）");
            }
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
            bool calculated = false;
            long calculateMs = 0;
            if (studyType != (int)swMotionStudyType_e.swMotionStudyTypeAssembly)
            {
                var watch = System.Diagnostics.Stopwatch.StartNew();
                calculated = study.Calculate();
                calculateMs = watch.ElapsedMilliseconds;
                Console.WriteLine($"[SwCommands] Motion Study 计算 type={studyType}: {calculated}, {calculateMs} ms");
            }
            return new Dictionary<string, object>
            {
                ["studyName"] = study.Name,
                ["studyType"] = study.StudyType,
                ["calculated"] = calculated,
                ["calculateMs"] = calculateMs,
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
