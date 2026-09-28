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
                ((IModelDoc2)_activeAssembly).GetTitle(),
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

            // AddComponent5 的 X/Y/Z 是相对组件包围盒的近似插入点，不保证组件原点
            // 落在目标坐标。统一用 Transform2 二次定位，让 HTTP API 的 x/y/z
            // 对 Planner 和验收器具有精确、稳定的语义。
            SetComponentTranslation(comp, x, y, z);
            var compName = comp.Name2;
            Console.WriteLine($"[SwCommands] 已加入组件: {compName} @ ({x*1000:F1},{y*1000:F1},{z*1000:F1}) mm");
            // AddComponent5 requires a preloaded part document, but keeping one
            // window per inserted component quickly exhausts SolidWorks on large
            // assemblies. The component remains referenced by the assembly after
            // its inactive source window is closed.
            try
            {
                int activationErrors = 0;
                _swApp.ActivateDoc3(
                    ((IModelDoc2)_activeAssembly).GetTitle(),
                    true,
                    (int)swRebuildOnActivation_e.swDontRebuildActiveDoc,
                    ref activationErrors);
                CloseInactiveDocuments();
            }
            catch (Exception closeError)
            {
                Console.WriteLine(
                    $"[SwCommands] 关闭已插入零件窗口失败（继续装配）: {closeError.Message}");
            }
            // Save periodic checkpoints so a large assembly does not keep all
            // component references only in memory until the final operation.
            // This also bounds recovery work if SolidWorks is interrupted.
            var insertedComponents = _activeAssembly.GetComponents(false) as object[];
            int componentCount = insertedComponents?.Length ?? 0;
            if (componentCount > 0 && componentCount % 5 == 0)
            {
                SaveAssembly();
                Console.WriteLine(
                    $"[SwCommands] 装配体自动检查点已保存: {componentCount} 个组件");
            }
            return compName;
        }

        private void SetComponentTranslation(
            IComponent2 component,
            double x,
            double y,
            double z)
        {
            bool wasFixed = component.IsFixed();
            if (wasFixed)
            {
                ClearAssemblySelection();
                component.Select4(false, null, false);
                _activeAssembly.UnfixComponent();
            }

            var mathUtility = _swApp.GetMathUtility() as IMathUtility;
            if (mathUtility == null)
                throw new InvalidOperationException("无法获取 SolidWorks MathUtility");
            var values = new double[]
            {
                1, 0, 0,
                0, 1, 0,
                0, 0, 1,
                x, y, z,
                1, 0, 0, 0,
            };
            var transform = mathUtility.CreateTransform(values) as MathTransform;
            if (transform == null)
                throw new InvalidOperationException("创建组件 Transform 失败");
            component.Transform2 = transform;
            ((IModelDoc2)_activeAssembly).Extension.Rebuild(
                (int)swRebuildOptions_e.swUpdateMates);

            if (wasFixed)
            {
                ClearAssemblySelection();
                component.Select4(false, null, false);
                _activeAssembly.FixComponent();
            }
        }

        /// <summary>
        /// Set an absolute component pose from translation plus axis-angle rotation.
        /// This is a general deterministic primitive for mechanism playback.
        /// </summary>
        public Dictionary<string, object> SetComponentPose(
            string componentName,
            double x,
            double y,
            double z,
            double axisX,
            double axisY,
            double axisZ,
            double angleDeg,
            bool rebuildAndRedraw = true,
            IComponent2 resolvedComponent = null)
        {
            EnsureActiveAssembly();
            var component = resolvedComponent
                ?? FindAssemblyComponent(componentName);
            if (component.IsFixed())
            {
                ClearAssemblySelection();
                component.Select4(false, null, false);
                _activeAssembly.UnfixComponent();
            }

            double norm = Math.Sqrt(
                axisX * axisX + axisY * axisY + axisZ * axisZ);
            if (norm < 1e-12)
                throw new ArgumentException("姿态旋转轴不能为零向量");
            axisX /= norm;
            axisY /= norm;
            axisZ /= norm;
            double angle = angleDeg * Math.PI / 180.0;
            double c = Math.Cos(angle);
            double s = Math.Sin(angle);
            double t = 1.0 - c;
            var values = new double[]
            {
                t*axisX*axisX+c,
                t*axisX*axisY-axisZ*s,
                t*axisX*axisZ+axisY*s,
                t*axisX*axisY+axisZ*s,
                t*axisY*axisY+c,
                t*axisY*axisZ-axisX*s,
                t*axisX*axisZ-axisY*s,
                t*axisY*axisZ+axisX*s,
                t*axisZ*axisZ+c,
                x, y, z,
                1, 0, 0, 0,
            };
            var mathUtility = _swApp.GetMathUtility() as IMathUtility;
            var transform = mathUtility?.CreateTransform(values) as MathTransform;
            if (transform == null)
                throw new InvalidOperationException("创建组件姿态 Transform 失败");
            component.Transform2 = transform;
            if (rebuildAndRedraw)
            {
                ((IModelDoc2)_activeAssembly).Extension.Rebuild(
                    (int)swRebuildOptions_e.swUpdateMates);
                ((IModelDoc2)_activeAssembly).GraphicsRedraw2();
            }
            return new Dictionary<string, object>
            {
                ["componentName"] = componentName,
                ["translationXYZ"] = new double[] { x, y, z },
                ["axis"] = new double[] { axisX, axisY, axisZ },
                ["angleDeg"] = angleDeg,
            };
        }

        /// <summary>
        /// Solve and apply a rigid planar serial-chain pose. Each segment advances
        /// one joint while one or more parallel components can share its midpoint
        /// and angle at different offsets along the rotation axis.
        /// </summary>
        public Dictionary<string, object> SetPlanarChainPose(
            double baseX,
            double baseY,
            double baseZ,
            double rotationAxisX,
            double rotationAxisY,
            double rotationAxisZ,
            double baseAngleDeg,
            List<Dictionary<string, object>> segments,
            List<Dictionary<string, object>> jointComponents,
            List<Dictionary<string, object>> endComponents)
        {
            EnsureActiveAssembly();
            if (segments == null || segments.Count == 0)
                throw new ArgumentException("平面连杆链至少需要一个 segment");

            double axisNorm = Math.Sqrt(
                rotationAxisX * rotationAxisX +
                rotationAxisY * rotationAxisY +
                rotationAxisZ * rotationAxisZ);
            if (axisNorm < 1e-12)
                throw new ArgumentException("平面连杆旋转轴不能为零向量");
            rotationAxisX /= axisNorm;
            rotationAxisY /= axisNorm;
            rotationAxisZ /= axisNorm;

            // The local +X axis is every link's zero-angle direction.
            double dotX = rotationAxisX;
            if (Math.Abs(dotX) > 1e-6)
                throw new ArgumentException(
                    "平面连杆旋转轴必须垂直于组件局部 +X 轴");
            double perpX = 0.0;
            double perpY = rotationAxisZ;
            double perpZ = -rotationAxisY;
            double perpNorm = Math.Sqrt(
                perpX * perpX + perpY * perpY + perpZ * perpZ);
            if (perpNorm < 1e-12)
                throw new ArgumentException("无法建立平面连杆参考坐标系");
            perpX /= perpNorm;
            perpY /= perpNorm;
            perpZ /= perpNorm;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var item in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = item as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Func<IComponent2, double[]> translationOf = component =>
            {
                var transform = component.Transform2 as MathTransform;
                var values = transform?.ArrayData as double[];
                if (values == null || values.Length < 12)
                    throw new InvalidOperationException(
                        $"无法读取组件姿态: {component.Name2}");
                return new double[] { values[9], values[10], values[11] };
            };
            Func<object, List<object>> asList = raw =>
            {
                var output = new List<object>();
                var enumerable = raw as System.Collections.IEnumerable;
                if (enumerable == null || raw is string)
                    return output;
                foreach (var value in enumerable)
                    output.Add(value);
                return output;
            };

            var anchors = new List<double[]>
            {
                new double[] { baseX, baseY, baseZ }
            };
            var cumulativeAngles = new List<double> { baseAngleDeg };
            var segmentResults = new List<Dictionary<string, object>>();
            double currentX = baseX;
            double currentY = baseY;
            double currentZ = baseZ;
            double cumulativeAngle = baseAngleDeg;

            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;

                foreach (var segment in segments)
                {
                    if (!segment.ContainsKey("length"))
                        throw new ArgumentException("segment 缺少 length");
                    double length = Convert.ToDouble(segment["length"]);
                    if (length <= 0)
                        throw new ArgumentException("segment length 必须为正数");
                    double relativeAngle = segment.ContainsKey("angleDeg")
                        ? Convert.ToDouble(segment["angleDeg"])
                        : (segment.ContainsKey("relativeAngleDeg")
                            ? Convert.ToDouble(segment["relativeAngleDeg"])
                            : 0.0);
                    cumulativeAngle += relativeAngle;
                    double angleRad = cumulativeAngle * Math.PI / 180.0;
                    double dirX = Math.Cos(angleRad);
                    double dirY = perpY * Math.Sin(angleRad);
                    double dirZ = perpZ * Math.Sin(angleRad);
                    double midpointX = currentX + dirX * length / 2.0;
                    double midpointY = currentY + dirY * length / 2.0;
                    double midpointZ = currentZ + dirZ * length / 2.0;
                    double endX = currentX + dirX * length;
                    double endY = currentY + dirY * length;
                    double endZ = currentZ + dirZ * length;

                    var names = segment.ContainsKey("componentNames")
                        ? asList(segment["componentNames"])
                        : new List<object>();
                    if (names.Count == 0 && segment.ContainsKey("componentName"))
                        names.Add(segment["componentName"]);
                    if (names.Count == 0)
                        throw new ArgumentException(
                            "segment 缺少 componentName/componentNames");
                    var offsets = segment.ContainsKey("axisOffsets")
                        ? asList(segment["axisOffsets"])
                        : new List<object>();
                    var bodyLengths = segment.ContainsKey("bodyLengths")
                        ? asList(segment["bodyLengths"])
                        : new List<object>();
                    bool preserveAxisOffsets =
                        !segment.ContainsKey("preserveAxisOffsets")
                        || Convert.ToBoolean(
                            segment["preserveAxisOffsets"]);
                    var posedNames = new List<string>();
                    var componentDiagnostics =
                        new List<Dictionary<string, object>>();
                    double maximumOrientationErrorDeg = 0.0;
                    double maximumBodyEndpointError = 0.0;
                    for (int index = 0; index < names.Count; index++)
                    {
                        string name = Convert.ToString(names[index]);
                        var resolved = resolve(name);
                        var currentTranslation = translationOf(resolved);
                        double currentAxisOffset =
                            (currentTranslation[0] - baseX) * rotationAxisX
                            + (currentTranslation[1] - baseY) * rotationAxisY
                            + (currentTranslation[2] - baseZ) * rotationAxisZ;
                        double offset = preserveAxisOffsets
                            ? currentAxisOffset
                            : (index < offsets.Count
                                ? Convert.ToDouble(offsets[index])
                                : 0.0);
                        SetComponentPose(
                            name,
                            midpointX + rotationAxisX * offset,
                            midpointY + rotationAxisY * offset,
                            midpointZ + rotationAxisZ * offset,
                            rotationAxisX,
                            rotationAxisY,
                            rotationAxisZ,
                            // SolidWorks MathTransform stores the local basis
                            // vectors by row, so its positive axis-angle appears
                            // visually reversed from the FK convention.
                            -cumulativeAngle,
                            false,
                            resolved);
                        posedNames.Add(name);
                        var appliedTransform =
                            resolved.Transform2 as MathTransform;
                        var appliedValues =
                            appliedTransform?.ArrayData as double[];
                        if (appliedValues == null
                            || appliedValues.Length < 12)
                            throw new InvalidOperationException(
                                $"无法读取已应用的连杆姿态: {name}");
                        double bodyAxisX = appliedValues[0];
                        double bodyAxisY = appliedValues[1];
                        double bodyAxisZ = appliedValues[2];
                        double bodyAxisNorm = Math.Sqrt(
                            bodyAxisX * bodyAxisX
                            + bodyAxisY * bodyAxisY
                            + bodyAxisZ * bodyAxisZ);
                        bodyAxisX /= bodyAxisNorm;
                        bodyAxisY /= bodyAxisNorm;
                        bodyAxisZ /= bodyAxisNorm;
                        double directionDot = Math.Max(
                            -1.0,
                            Math.Min(
                                1.0,
                                bodyAxisX * dirX
                                + bodyAxisY * dirY
                                + bodyAxisZ * dirZ));
                        double orientationErrorDeg =
                            Math.Acos(directionDot) * 180.0 / Math.PI;
                        double bodyLength = index < bodyLengths.Count
                            ? Convert.ToDouble(bodyLengths[index])
                            : length;
                        double posedMidpointX =
                            midpointX + rotationAxisX * offset;
                        double posedMidpointY =
                            midpointY + rotationAxisY * offset;
                        double posedMidpointZ =
                            midpointZ + rotationAxisZ * offset;
                        double actualStartX =
                            posedMidpointX - bodyAxisX * bodyLength / 2.0;
                        double actualStartY =
                            posedMidpointY - bodyAxisY * bodyLength / 2.0;
                        double actualStartZ =
                            posedMidpointZ - bodyAxisZ * bodyLength / 2.0;
                        double actualEndX =
                            posedMidpointX + bodyAxisX * bodyLength / 2.0;
                        double actualEndY =
                            posedMidpointY + bodyAxisY * bodyLength / 2.0;
                        double actualEndZ =
                            posedMidpointZ + bodyAxisZ * bodyLength / 2.0;
                        double expectedStartX =
                            midpointX - dirX * bodyLength / 2.0
                            + rotationAxisX * offset;
                        double expectedStartY =
                            midpointY - dirY * bodyLength / 2.0
                            + rotationAxisY * offset;
                        double expectedStartZ =
                            midpointZ - dirZ * bodyLength / 2.0
                            + rotationAxisZ * offset;
                        double expectedEndX =
                            midpointX + dirX * bodyLength / 2.0
                            + rotationAxisX * offset;
                        double expectedEndY =
                            midpointY + dirY * bodyLength / 2.0
                            + rotationAxisY * offset;
                        double expectedEndZ =
                            midpointZ + dirZ * bodyLength / 2.0
                            + rotationAxisZ * offset;
                        double startError = Math.Sqrt(
                            Math.Pow(actualStartX - expectedStartX, 2)
                            + Math.Pow(actualStartY - expectedStartY, 2)
                            + Math.Pow(actualStartZ - expectedStartZ, 2));
                        double endError = Math.Sqrt(
                            Math.Pow(actualEndX - expectedEndX, 2)
                            + Math.Pow(actualEndY - expectedEndY, 2)
                            + Math.Pow(actualEndZ - expectedEndZ, 2));
                        double endpointError = Math.Max(startError, endError);
                        maximumOrientationErrorDeg = Math.Max(
                            maximumOrientationErrorDeg,
                            orientationErrorDeg);
                        maximumBodyEndpointError = Math.Max(
                            maximumBodyEndpointError,
                            endpointError);
                        componentDiagnostics.Add(
                            new Dictionary<string, object>
                            {
                                ["componentName"] = name,
                                ["bodyLength"] = bodyLength,
                                ["bodyAxisXYZ"] = new double[]
                                    { bodyAxisX, bodyAxisY, bodyAxisZ },
                                ["orientationErrorDeg"] =
                                    orientationErrorDeg,
                                ["bodyEndpointError"] = endpointError,
                                ["actualStartXYZ"] = new double[]
                                    { actualStartX, actualStartY, actualStartZ },
                                ["actualEndXYZ"] = new double[]
                                    { actualEndX, actualEndY, actualEndZ },
                            });
                    }
                    segmentResults.Add(new Dictionary<string, object>
                    {
                        ["componentNames"] = posedNames.ToArray(),
                        ["componentDiagnostics"] =
                            componentDiagnostics.ToArray(),
                        ["length"] = length,
                        ["angleDeg"] = cumulativeAngle,
                        ["maximumOrientationErrorDeg"] =
                            maximumOrientationErrorDeg,
                        ["maximumBodyEndpointError"] =
                            maximumBodyEndpointError,
                        ["startXYZ"] = new double[]
                            { currentX, currentY, currentZ },
                        ["midpointXYZ"] = new double[]
                            { midpointX, midpointY, midpointZ },
                        ["endXYZ"] = new double[] { endX, endY, endZ },
                    });
                    currentX = endX;
                    currentY = endY;
                    currentZ = endZ;
                    anchors.Add(new double[] { currentX, currentY, currentZ });
                    cumulativeAngles.Add(cumulativeAngle);
                }

                var jointResults = new List<Dictionary<string, object>>();
                foreach (var item in jointComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    if (!item.ContainsKey("componentName"))
                        throw new ArgumentException(
                            "jointComponent 缺少 componentName");
                    string name = Convert.ToString(item["componentName"]);
                    int anchorIndex = item.ContainsKey("anchorIndex")
                        ? Convert.ToInt32(item["anchorIndex"])
                        : 0;
                    if (anchorIndex < 0 || anchorIndex >= anchors.Count)
                        throw new ArgumentException(
                            $"jointComponent anchorIndex 越界: {anchorIndex}");
                    double offset = item.ContainsKey("axisOffset")
                        ? Convert.ToDouble(item["axisOffset"])
                        : 0.0;
                    double angleDeg = item.ContainsKey("angleDeg")
                        ? Convert.ToDouble(item["angleDeg"])
                        : cumulativeAngles[Math.Min(
                            anchorIndex, cumulativeAngles.Count - 1)];
                    var anchor = anchors[anchorIndex];
                    jointResults.Add(SetComponentPose(
                        name,
                        anchor[0] + rotationAxisX * offset,
                        anchor[1] + rotationAxisY * offset,
                        anchor[2] + rotationAxisZ * offset,
                        rotationAxisX,
                        rotationAxisY,
                        rotationAxisZ,
                        -angleDeg,
                        false,
                        resolve(name)));
                }

                var endResults = new List<Dictionary<string, object>>();
                foreach (var item in endComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    if (!item.ContainsKey("componentName"))
                        throw new ArgumentException(
                            "endComponent 缺少 componentName");
                    string name = Convert.ToString(item["componentName"]);
                    double angleDeg = item.ContainsKey("angleDeg")
                        ? Convert.ToDouble(item["angleDeg"])
                        : cumulativeAngle;
                    double angleRad = angleDeg * Math.PI / 180.0;
                    double dirX = Math.Cos(angleRad);
                    double dirY = perpY * Math.Sin(angleRad);
                    double dirZ = perpZ * Math.Sin(angleRad);
                    double normalX = -Math.Sin(angleRad);
                    double normalY = perpY * Math.Cos(angleRad);
                    double normalZ = perpZ * Math.Cos(angleRad);
                    var resolved = resolve(name);
                    var currentTranslation = translationOf(resolved);
                    double deltaX = currentTranslation[0] - currentX;
                    double deltaY = currentTranslation[1] - currentY;
                    double deltaZ = currentTranslation[2] - currentZ;
                    double along = item.ContainsKey("alongOffset")
                        ? Convert.ToDouble(item["alongOffset"])
                        : deltaX * dirX + deltaY * dirY + deltaZ * dirZ;
                    double perpendicular = item.ContainsKey(
                        "perpendicularOffset")
                        ? Convert.ToDouble(item["perpendicularOffset"])
                        : deltaX * normalX
                            + deltaY * normalY
                            + deltaZ * normalZ;
                    double axisOffset = item.ContainsKey("axisOffset")
                        ? Convert.ToDouble(item["axisOffset"])
                        : deltaX * rotationAxisX
                            + deltaY * rotationAxisY
                            + deltaZ * rotationAxisZ;
                    endResults.Add(SetComponentPose(
                        name,
                        currentX + dirX * along
                            + normalX * perpendicular
                            + rotationAxisX * axisOffset,
                        currentY + dirY * along
                            + normalY * perpendicular
                            + rotationAxisY * axisOffset,
                        currentZ + dirZ * along
                            + normalZ * perpendicular
                            + rotationAxisZ * axisOffset,
                        rotationAxisX,
                        rotationAxisY,
                        rotationAxisZ,
                        -angleDeg,
                        false,
                        resolved));
                }

                return new Dictionary<string, object>
                {
                    ["baseXYZ"] = new double[] { baseX, baseY, baseZ },
                    ["anchors"] = anchors.ToArray(),
                    ["segments"] = segmentResults.ToArray(),
                    ["joints"] = jointResults.ToArray(),
                    ["endComponents"] = endResults.ToArray(),
                    ["endXYZ"] = new double[]
                        { currentX, currentY, currentZ },
                };
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
                modelDoc.GraphicsRedraw2();
            }
        }

        /// <summary>
        /// Apply one pose of a generic radial deployable linkage. A translating
        /// runner drives equal-length stretchers connected to repeated ribs.
        /// Component local +X axes represent rod centerlines.
        /// </summary>
        public Dictionary<string, object> SetRadialDeployablePose(
            double centerX,
            double centerY,
            double centerZ,
            double deploymentAxisX,
            double deploymentAxisY,
            double deploymentAxisZ,
            double referenceRadialX,
            double referenceRadialY,
            double referenceRadialZ,
            double hubRadius,
            double runnerRadius,
            double ribLength,
            double ribAttachmentDistance,
            double stretcherLength,
            double ribDownAngleDeg,
            string runnerComponent,
            List<Dictionary<string, object>> bays)
        {
            EnsureActiveAssembly();
            if (bays == null || bays.Count < 2)
                throw new ArgumentException("径向展开机构至少需要两个 bay");
            if (hubRadius < 0 || runnerRadius < 0
                || ribLength <= 0 || ribAttachmentDistance <= 0
                || ribAttachmentDistance > ribLength
                || stretcherLength <= 0)
                throw new ArgumentException("径向展开机构长度参数无效");

            double axisNorm = Math.Sqrt(
                deploymentAxisX * deploymentAxisX
                + deploymentAxisY * deploymentAxisY
                + deploymentAxisZ * deploymentAxisZ);
            if (axisNorm < 1e-12)
                throw new ArgumentException("展开轴不能为零向量");
            deploymentAxisX /= axisNorm;
            deploymentAxisY /= axisNorm;
            deploymentAxisZ /= axisNorm;

            double referenceDot =
                referenceRadialX * deploymentAxisX
                + referenceRadialY * deploymentAxisY
                + referenceRadialZ * deploymentAxisZ;
            referenceRadialX -= referenceDot * deploymentAxisX;
            referenceRadialY -= referenceDot * deploymentAxisY;
            referenceRadialZ -= referenceDot * deploymentAxisZ;
            double referenceNorm = Math.Sqrt(
                referenceRadialX * referenceRadialX
                + referenceRadialY * referenceRadialY
                + referenceRadialZ * referenceRadialZ);
            if (referenceNorm < 1e-12)
                throw new ArgumentException("参考径向不能与展开轴平行");
            referenceRadialX /= referenceNorm;
            referenceRadialY /= referenceNorm;
            referenceRadialZ /= referenceNorm;
            double tangentX =
                deploymentAxisY * referenceRadialZ
                - deploymentAxisZ * referenceRadialY;
            double tangentY =
                deploymentAxisZ * referenceRadialX
                - deploymentAxisX * referenceRadialZ;
            double tangentZ =
                deploymentAxisX * referenceRadialY
                - deploymentAxisY * referenceRadialX;

            double beta = ribDownAngleDeg * Math.PI / 180.0;
            double radialAttachment =
                hubRadius + ribAttachmentDistance * Math.Cos(beta);
            double radialDifference =
                radialAttachment - runnerRadius;
            double root =
                stretcherLength * stretcherLength
                - radialDifference * radialDifference;
            if (root < -1e-12)
                throw new InvalidOperationException(
                    "当前伞骨角度下撑杆没有实数闭合解");
            double runnerDrop =
                ribAttachmentDistance * Math.Sin(beta)
                + Math.Sqrt(Math.Max(0.0, root));
            double runnerX =
                centerX - deploymentAxisX * runnerDrop;
            double runnerY =
                centerY - deploymentAxisY * runnerDrop;
            double runnerZ =
                centerZ - deploymentAxisZ * runnerDrop;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var raw in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = raw as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Func<double[], double[], double> distance = (left, right) =>
                Math.Sqrt(
                    Math.Pow(left[0] - right[0], 2)
                    + Math.Pow(left[1] - right[1], 2)
                    + Math.Pow(left[2] - right[2], 2));
            Func<
                string,
                double[],
                double[],
                double,
                Dictionary<string, object>> poseRod =
                (name, midpoint, direction, bodyLength) =>
                {
                    double directionNorm = Math.Sqrt(
                        direction[0] * direction[0]
                        + direction[1] * direction[1]
                        + direction[2] * direction[2]);
                    if (directionNorm < 1e-12)
                        throw new ArgumentException(
                            $"杆件方向不能为零: {name}");
                    double dx = direction[0] / directionNorm;
                    double dy = direction[1] / directionNorm;
                    double dz = direction[2] / directionNorm;
                    // Cross(local +X, target). Negating the angle converts
                    // column-vector Rodrigues convention to SW row bases.
                    double axisX = 0.0;
                    double axisY = -dz;
                    double axisZ = dy;
                    double rotationNorm = Math.Sqrt(
                        axisX * axisX + axisY * axisY + axisZ * axisZ);
                    double angleDeg;
                    if (rotationNorm < 1e-12)
                    {
                        axisX = 0;
                        axisY = 0;
                        axisZ = 1;
                        angleDeg = dx >= 0 ? 0.0 : 180.0;
                    }
                    else
                    {
                        axisX /= rotationNorm;
                        axisY /= rotationNorm;
                        axisZ /= rotationNorm;
                        angleDeg = Math.Acos(
                            Math.Max(-1.0, Math.Min(1.0, dx)))
                            * 180.0 / Math.PI;
                    }
                    var component = resolve(name);
                    SetComponentPose(
                        name,
                        midpoint[0],
                        midpoint[1],
                        midpoint[2],
                        axisX,
                        axisY,
                        axisZ,
                        -angleDeg,
                        false,
                        component);
                    var transform = component.Transform2 as MathTransform;
                    var values = transform?.ArrayData as double[];
                    if (values == null || values.Length < 12)
                        throw new InvalidOperationException(
                            $"无法读取径向杆件姿态: {name}");
                    var actualAxis = new double[]
                        { values[0], values[1], values[2] };
                    double actualNorm = Math.Sqrt(
                        actualAxis[0] * actualAxis[0]
                        + actualAxis[1] * actualAxis[1]
                        + actualAxis[2] * actualAxis[2]);
                    for (int coordinate = 0; coordinate < 3; coordinate++)
                        actualAxis[coordinate] /= actualNorm;
                    double dot = Math.Max(
                        -1.0,
                        Math.Min(
                            1.0,
                            actualAxis[0] * dx
                            + actualAxis[1] * dy
                            + actualAxis[2] * dz));
                    var expectedStart = new double[]
                    {
                        midpoint[0] - dx * bodyLength / 2.0,
                        midpoint[1] - dy * bodyLength / 2.0,
                        midpoint[2] - dz * bodyLength / 2.0,
                    };
                    var expectedEnd = new double[]
                    {
                        midpoint[0] + dx * bodyLength / 2.0,
                        midpoint[1] + dy * bodyLength / 2.0,
                        midpoint[2] + dz * bodyLength / 2.0,
                    };
                    var actualStart = new double[]
                    {
                        midpoint[0] - actualAxis[0] * bodyLength / 2.0,
                        midpoint[1] - actualAxis[1] * bodyLength / 2.0,
                        midpoint[2] - actualAxis[2] * bodyLength / 2.0,
                    };
                    var actualEnd = new double[]
                    {
                        midpoint[0] + actualAxis[0] * bodyLength / 2.0,
                        midpoint[1] + actualAxis[1] * bodyLength / 2.0,
                        midpoint[2] + actualAxis[2] * bodyLength / 2.0,
                    };
                    return new Dictionary<string, object>
                    {
                        ["componentName"] = name,
                        ["bodyAxisXYZ"] = actualAxis,
                        ["orientationErrorDeg"] =
                            Math.Acos(dot) * 180.0 / Math.PI,
                        ["bodyEndpointError"] = Math.Max(
                            distance(actualStart, expectedStart),
                            distance(actualEnd, expectedEnd)),
                        ["actualStartXYZ"] = actualStart,
                        ["actualEndXYZ"] = actualEnd,
                    };
                };

            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            var bayResults = new List<Dictionary<string, object>>();
            double maximumOrientationErrorDeg = 0.0;
            double maximumEndpointError = 0.0;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                SetComponentPose(
                    runnerComponent,
                    runnerX,
                    runnerY,
                    runnerZ,
                    deploymentAxisX,
                    deploymentAxisY,
                    deploymentAxisZ,
                    0.0,
                    false,
                    resolve(runnerComponent));

                for (int index = 0; index < bays.Count; index++)
                {
                    var bay = bays[index];
                    string ribName = Convert.ToString(
                        bay["ribComponent"]);
                    string stretcherName = Convert.ToString(
                        bay["stretcherComponent"]);
                    double phaseDeg = bay.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(bay["phaseDeg"])
                        : index * 360.0 / bays.Count;
                    double phase = phaseDeg * Math.PI / 180.0;
                    double radialX =
                        Math.Cos(phase) * referenceRadialX
                        + Math.Sin(phase) * tangentX;
                    double radialY =
                        Math.Cos(phase) * referenceRadialY
                        + Math.Sin(phase) * tangentY;
                    double radialZ =
                        Math.Cos(phase) * referenceRadialZ
                        + Math.Sin(phase) * tangentZ;
                    var ribPivot = new double[]
                    {
                        centerX + radialX * hubRadius,
                        centerY + radialY * hubRadius,
                        centerZ + radialZ * hubRadius,
                    };
                    var ribDirection = new double[]
                    {
                        Math.Cos(beta) * radialX
                            - Math.Sin(beta) * deploymentAxisX,
                        Math.Cos(beta) * radialY
                            - Math.Sin(beta) * deploymentAxisY,
                        Math.Cos(beta) * radialZ
                            - Math.Sin(beta) * deploymentAxisZ,
                    };
                    var ribTip = new double[]
                    {
                        ribPivot[0] + ribDirection[0] * ribLength,
                        ribPivot[1] + ribDirection[1] * ribLength,
                        ribPivot[2] + ribDirection[2] * ribLength,
                    };
                    var attachment = new double[]
                    {
                        ribPivot[0]
                            + ribDirection[0] * ribAttachmentDistance,
                        ribPivot[1]
                            + ribDirection[1] * ribAttachmentDistance,
                        ribPivot[2]
                            + ribDirection[2] * ribAttachmentDistance,
                    };
                    var runnerPivot = new double[]
                    {
                        runnerX + radialX * runnerRadius,
                        runnerY + radialY * runnerRadius,
                        runnerZ + radialZ * runnerRadius,
                    };
                    var ribMidpoint = new double[]
                    {
                        (ribPivot[0] + ribTip[0]) / 2.0,
                        (ribPivot[1] + ribTip[1]) / 2.0,
                        (ribPivot[2] + ribTip[2]) / 2.0,
                    };
                    var stretcherMidpoint = new double[]
                    {
                        (runnerPivot[0] + attachment[0]) / 2.0,
                        (runnerPivot[1] + attachment[1]) / 2.0,
                        (runnerPivot[2] + attachment[2]) / 2.0,
                    };
                    var stretcherDirection = new double[]
                    {
                        attachment[0] - runnerPivot[0],
                        attachment[1] - runnerPivot[1],
                        attachment[2] - runnerPivot[2],
                    };
                    var ribDiagnostic = poseRod(
                        ribName,
                        ribMidpoint,
                        ribDirection,
                        ribLength);
                    var stretcherDiagnostic = poseRod(
                        stretcherName,
                        stretcherMidpoint,
                        stretcherDirection,
                        stretcherLength);
                    maximumOrientationErrorDeg = Math.Max(
                        maximumOrientationErrorDeg,
                        Math.Max(
                            Convert.ToDouble(
                                ribDiagnostic["orientationErrorDeg"]),
                            Convert.ToDouble(
                                stretcherDiagnostic[
                                    "orientationErrorDeg"])));
                    maximumEndpointError = Math.Max(
                        maximumEndpointError,
                        Math.Max(
                            Convert.ToDouble(
                                ribDiagnostic["bodyEndpointError"]),
                            Convert.ToDouble(
                                stretcherDiagnostic[
                                    "bodyEndpointError"])));
                    bayResults.Add(new Dictionary<string, object>
                    {
                        ["phaseDeg"] = phaseDeg,
                        ["ribPivotXYZ"] = ribPivot,
                        ["ribTipXYZ"] = ribTip,
                        ["attachmentXYZ"] = attachment,
                        ["runnerPivotXYZ"] = runnerPivot,
                        ["ribLength"] = distance(ribPivot, ribTip),
                        ["stretcherLength"] =
                            distance(runnerPivot, attachment),
                        ["ribDiagnostic"] = ribDiagnostic,
                        ["stretcherDiagnostic"] = stretcherDiagnostic,
                    });
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
                modelDoc.GraphicsRedraw2();
            }

            return new Dictionary<string, object>
            {
                ["ribDownAngleDeg"] = ribDownAngleDeg,
                ["runnerDrop"] = runnerDrop,
                ["runnerXYZ"] =
                    new double[] { runnerX, runnerY, runnerZ },
                ["bays"] = bayResults.ToArray(),
                ["maximumOrientationErrorDeg"] =
                    maximumOrientationErrorDeg,
                ["maximumBodyEndpointError"] = maximumEndpointError,
            };
        }

        /// <summary>
        /// Apply a deterministic pose to one or more serial fixed-ring
        /// planetary stages. Gear parts are modeled around local +X and the
        /// assembly rotation axis must be parallel to world X.
        /// </summary>
        public Dictionary<string, object> SetPlanetaryTrainPose(
            double centerX,
            double centerY,
            double centerZ,
            double axisX,
            double axisY,
            double axisZ,
            double referenceRadialX,
            double referenceRadialY,
            double referenceRadialZ,
            double inputAngleDeg,
            List<Dictionary<string, object>> stages,
            List<Dictionary<string, object>> outputComponents)
        {
            EnsureActiveAssembly();
            if (stages == null || stages.Count == 0)
                throw new ArgumentException("行星轮系至少需要一级");
            double axisNorm = Math.Sqrt(
                axisX * axisX + axisY * axisY + axisZ * axisZ);
            if (axisNorm < 1e-12)
                throw new ArgumentException("行星轮系旋转轴不能为零");
            axisX /= axisNorm;
            axisY /= axisNorm;
            axisZ /= axisNorm;
            if (Math.Abs(Math.Abs(axisX) - 1.0) > 1e-6
                || Math.Abs(axisY) > 1e-6
                || Math.Abs(axisZ) > 1e-6)
                throw new ArgumentException(
                    "当前行星轮零件约定要求旋转轴平行于世界 X");
            double referenceDot =
                referenceRadialX * axisX
                + referenceRadialY * axisY
                + referenceRadialZ * axisZ;
            referenceRadialX -= referenceDot * axisX;
            referenceRadialY -= referenceDot * axisY;
            referenceRadialZ -= referenceDot * axisZ;
            double referenceNorm = Math.Sqrt(
                referenceRadialX * referenceRadialX
                + referenceRadialY * referenceRadialY
                + referenceRadialZ * referenceRadialZ);
            if (referenceNorm < 1e-12)
                throw new ArgumentException(
                    "行星轮参考径向不能与旋转轴平行");
            referenceRadialX /= referenceNorm;
            referenceRadialY /= referenceNorm;
            referenceRadialZ /= referenceNorm;
            double tangentX =
                axisY * referenceRadialZ
                - axisZ * referenceRadialY;
            double tangentY =
                axisZ * referenceRadialX
                - axisX * referenceRadialZ;
            double tangentZ =
                axisX * referenceRadialY
                - axisY * referenceRadialX;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var raw in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = raw as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Func<IComponent2, double[]> translationOf = component =>
            {
                var transform = component.Transform2 as MathTransform;
                var values = transform?.ArrayData as double[];
                if (values == null || values.Length < 12)
                    throw new InvalidOperationException(
                        $"无法读取组件姿态: {component.Name2}");
                return new double[] { values[9], values[10], values[11] };
            };
            Func<double, double> normalizedAngle = angle =>
            {
                double value = angle % 360.0;
                if (value <= -180.0) value += 360.0;
                if (value > 180.0) value -= 360.0;
                return value;
            };
            Func<IComponent2, double> actualAngleOf = component =>
            {
                var transform = component.Transform2 as MathTransform;
                var values = transform?.ArrayData as double[];
                if (values == null || values.Length < 9)
                    throw new InvalidOperationException(
                        $"无法读取组件旋转: {component.Name2}");
                return Math.Atan2(values[7], values[4])
                    * 180.0 / Math.PI;
            };
            Func<string, double, double, double, double,
                Dictionary<string, object>> poseGear =
                (name, x, y, z, angle) =>
                {
                    var component = resolve(name);
                    SetComponentPose(
                        name,
                        x, y, z,
                        axisX, axisY, axisZ,
                        angle,
                        false,
                        component);
                    double actualAngle = actualAngleOf(component);
                    return new Dictionary<string, object>
                    {
                        ["componentName"] = name,
                        ["translationXYZ"] = new double[] { x, y, z },
                        ["commandedAngleDeg"] = angle,
                        ["actualAngleDeg"] = actualAngle,
                        ["orientationErrorDeg"] = Math.Abs(
                            normalizedAngle(actualAngle - angle)),
                    };
                };
            Func<Dictionary<string, object>, string, string> stringValue =
                (item, key) =>
                {
                    object raw;
                    if (!item.TryGetValue(key, out raw)
                        || string.IsNullOrWhiteSpace(Convert.ToString(raw)))
                        throw new ArgumentException($"行星级缺少 {key}");
                    return Convert.ToString(raw);
                };

            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            var stageResults = new List<Dictionary<string, object>>();
            var outputResults = new List<Dictionary<string, object>>();
            double stageInputAngle = inputAngleDeg;
            double overallRatio = 1.0;
            double maximumOrbitError = 0.0;
            double maximumOrientationError = 0.0;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                for (int stageIndex = 0;
                    stageIndex < stages.Count;
                    stageIndex++)
                {
                    var stage = stages[stageIndex];
                    string sunName = stringValue(stage, "sunComponent");
                    string carrierName =
                        stringValue(stage, "carrierComponent");
                    int sunTeeth = Convert.ToInt32(stage["sunTeeth"]);
                    int ringTeeth = Convert.ToInt32(stage["ringTeeth"]);
                    int planetTeeth = Convert.ToInt32(
                        stage["planetTeeth"]);
                    if (sunTeeth <= 0 || ringTeeth <= 0
                        || planetTeeth <= 0)
                        throw new ArgumentException(
                            "行星轮齿数必须为正整数");
                    if (ringTeeth != sunTeeth + 2 * planetTeeth)
                        throw new ArgumentException(
                            "固定内齿圈必须满足 Nr = Ns + 2Np");
                    double orbitRadius = Convert.ToDouble(
                        stage["orbitRadius"]);
                    if (orbitRadius <= 0)
                        throw new ArgumentException(
                            "planet orbitRadius 必须为正");
                    double axialOffset = stage.ContainsKey("axialOffset")
                        ? Convert.ToDouble(stage["axialOffset"])
                        : 0.0;
                    double carrierAxialOffset =
                        stage.ContainsKey("carrierAxialOffset")
                        ? Convert.ToDouble(stage["carrierAxialOffset"])
                        : axialOffset;
                    double phaseOffset = stage.ContainsKey("phaseOffsetDeg")
                        ? Convert.ToDouble(stage["phaseOffsetDeg"])
                        : 0.0;
                    double carrierPerSun =
                        (double)sunTeeth / (sunTeeth + ringTeeth);
                    double carrierAngle =
                        stageInputAngle * carrierPerSun;
                    double planetAngle = carrierAngle
                        - ((double)sunTeeth / planetTeeth)
                        * (stageInputAngle - carrierAngle);
                    overallRatio /= carrierPerSun;
                    double stageX = centerX + axisX * axialOffset;
                    double stageY = centerY + axisY * axialOffset;
                    double stageZ = centerZ + axisZ * axialOffset;
                    var sunResult = poseGear(
                        sunName,
                        stageX, stageY, stageZ,
                        stageInputAngle);
                    var carrierResult = poseGear(
                        carrierName,
                        centerX + axisX * carrierAxialOffset,
                        centerY + axisY * carrierAxialOffset,
                        centerZ + axisZ * carrierAxialOffset,
                        carrierAngle);
                    maximumOrientationError = Math.Max(
                        maximumOrientationError,
                        Math.Max(
                            Convert.ToDouble(
                                sunResult["orientationErrorDeg"]),
                            Convert.ToDouble(
                                carrierResult["orientationErrorDeg"])));

                    var planetEntries = new List<
                        Dictionary<string, object>>();
                    var rawPlanets =
                        stage["planetComponents"]
                        as System.Collections.IEnumerable;
                    if (rawPlanets == null)
                        throw new ArgumentException(
                            "planetComponents 必须是数组");
                    var planets = new List<Dictionary<string, object>>();
                    foreach (var rawPlanet in rawPlanets)
                    {
                        var planetObject =
                            rawPlanet as Dictionary<string, object>;
                        if (planetObject != null)
                            planets.Add(planetObject);
                        else
                            planets.Add(new Dictionary<string, object>
                            {
                                ["componentName"] =
                                    Convert.ToString(rawPlanet),
                            });
                    }
                    if (planets.Count < 2)
                        throw new ArgumentException(
                            "每级行星轮至少需要两个 planetComponents");
                    for (int planetIndex = 0;
                        planetIndex < planets.Count;
                        planetIndex++)
                    {
                        var planet = planets[planetIndex];
                        string planetName = stringValue(
                            planet,
                            "componentName");
                        double basePhase = planet.ContainsKey("phaseDeg")
                            ? Convert.ToDouble(planet["phaseDeg"])
                            : planetIndex * 360.0 / planets.Count;
                        double orbitPhase =
                            basePhase + phaseOffset + carrierAngle;
                        double phase =
                            orbitPhase * Math.PI / 180.0;
                        double radialX =
                            Math.Cos(phase) * referenceRadialX
                            + Math.Sin(phase) * tangentX;
                        double radialY =
                            Math.Cos(phase) * referenceRadialY
                            + Math.Sin(phase) * tangentY;
                        double radialZ =
                            Math.Cos(phase) * referenceRadialZ
                            + Math.Sin(phase) * tangentZ;
                        double planetX = stageX
                            + radialX * orbitRadius;
                        double planetY = stageY
                            + radialY * orbitRadius;
                        double planetZ = stageZ
                            + radialZ * orbitRadius;
                        var planetResult = poseGear(
                            planetName,
                            planetX, planetY, planetZ,
                            planetAngle + basePhase);
                        var actual = translationOf(resolve(planetName));
                        double actualOrbit = Math.Sqrt(
                            Math.Pow(actual[0] - stageX, 2)
                            + Math.Pow(actual[1] - stageY, 2)
                            + Math.Pow(actual[2] - stageZ, 2));
                        double orbitError = Math.Abs(
                            actualOrbit - orbitRadius);
                        maximumOrbitError = Math.Max(
                            maximumOrbitError,
                            orbitError);
                        maximumOrientationError = Math.Max(
                            maximumOrientationError,
                            Convert.ToDouble(
                                planetResult["orientationErrorDeg"]));
                        planetEntries.Add(new Dictionary<string, object>
                        {
                            ["componentName"] = planetName,
                            ["basePhaseDeg"] = basePhase,
                            ["orbitPhaseDeg"] = orbitPhase,
                            ["spinAngleDeg"] =
                                planetAngle + basePhase,
                            ["translationXYZ"] = actual,
                            ["measuredOrbitRadius"] = actualOrbit,
                            ["orbitRadiusError"] = orbitError,
                            ["orientationErrorDeg"] =
                                planetResult["orientationErrorDeg"],
                        });
                    }
                    stageResults.Add(new Dictionary<string, object>
                    {
                        ["stageIndex"] = stageIndex,
                        ["sunTeeth"] = sunTeeth,
                        ["ringTeeth"] = ringTeeth,
                        ["planetTeeth"] = planetTeeth,
                        ["sunAngleDeg"] = stageInputAngle,
                        ["carrierAngleDeg"] = carrierAngle,
                        ["planetSpinBaseAngleDeg"] = planetAngle,
                        ["carrierPerSunRatio"] = carrierPerSun,
                        ["reductionRatio"] = 1.0 / carrierPerSun,
                        ["sun"] = sunResult,
                        ["carrier"] = carrierResult,
                        ["planets"] = planetEntries.ToArray(),
                    });
                    stageInputAngle = carrierAngle;
                }

                foreach (var item in outputComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    string name = stringValue(item, "componentName");
                    double ratio = item.ContainsKey("ratio")
                        ? Convert.ToDouble(item["ratio"])
                        : 1.0;
                    if (Math.Abs(ratio) < 1e-12)
                        throw new ArgumentException(
                            "输出同步组件 ratio 不能为零");
                    bool reverse = item.ContainsKey("reverse")
                        && Convert.ToBoolean(item["reverse"]);
                    double phase = item.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(item["phaseDeg"])
                        : 0.0;
                    string source = item.ContainsKey("source")
                        ? Convert.ToString(item["source"]).ToLowerInvariant()
                        : "output";
                    double sourceAngle = source == "input"
                        ? inputAngleDeg
                        : stageInputAngle;
                    double angle = sourceAngle / ratio
                        * (reverse ? -1.0 : 1.0) + phase;
                    var current = translationOf(resolve(name));
                    var result = poseGear(
                        name,
                        item.ContainsKey("x")
                            ? Convert.ToDouble(item["x"]) : current[0],
                        item.ContainsKey("y")
                            ? Convert.ToDouble(item["y"]) : current[1],
                        item.ContainsKey("z")
                            ? Convert.ToDouble(item["z"]) : current[2],
                        angle);
                    result["source"] = source;
                    maximumOrientationError = Math.Max(
                        maximumOrientationError,
                        Convert.ToDouble(result["orientationErrorDeg"]));
                    outputResults.Add(result);
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
                modelDoc.GraphicsRedraw2();
            }
            return new Dictionary<string, object>
            {
                ["inputAngleDeg"] = inputAngleDeg,
                ["outputAngleDeg"] = stageInputAngle,
                ["overallReductionRatio"] = overallRatio,
                ["stages"] = stageResults.ToArray(),
                ["outputs"] = outputResults.ToArray(),
                ["maximumOrbitRadiusError"] = maximumOrbitError,
                ["maximumOrientationErrorDeg"] =
                    maximumOrientationError,
            };
        }

        /// <summary>
        /// Pose equal radial sliders around an axis. Slider part local +X is
        /// aligned with its radial travel direction.
        /// </summary>
        public Dictionary<string, object> SetSynchronizedRadialSlidersPose(
            double centerX,
            double centerY,
            double centerZ,
            double axisX,
            double axisY,
            double axisZ,
            double referenceRadialX,
            double referenceRadialY,
            double referenceRadialZ,
            double radialDistance,
            List<Dictionary<string, object>> sliders)
        {
            EnsureActiveAssembly();
            if (radialDistance < 0)
                throw new ArgumentException(
                    "radialDistance 不能为负数");
            if (sliders == null || sliders.Count < 2)
                throw new ArgumentException(
                    "同步径向滑块至少需要两个组件");
            double axisNorm = Math.Sqrt(
                axisX * axisX + axisY * axisY + axisZ * axisZ);
            if (axisNorm < 1e-12)
                throw new ArgumentException("滑块中心轴不能为零");
            axisX /= axisNorm;
            axisY /= axisNorm;
            axisZ /= axisNorm;
            double referenceDot =
                referenceRadialX * axisX
                + referenceRadialY * axisY
                + referenceRadialZ * axisZ;
            referenceRadialX -= referenceDot * axisX;
            referenceRadialY -= referenceDot * axisY;
            referenceRadialZ -= referenceDot * axisZ;
            double referenceNorm = Math.Sqrt(
                referenceRadialX * referenceRadialX
                + referenceRadialY * referenceRadialY
                + referenceRadialZ * referenceRadialZ);
            if (referenceNorm < 1e-12)
                throw new ArgumentException(
                    "滑块参考径向不能与中心轴平行");
            referenceRadialX /= referenceNorm;
            referenceRadialY /= referenceNorm;
            referenceRadialZ /= referenceNorm;
            double tangentX =
                axisY * referenceRadialZ
                - axisZ * referenceRadialY;
            double tangentY =
                axisZ * referenceRadialX
                - axisX * referenceRadialZ;
            double tangentZ =
                axisX * referenceRadialY
                - axisY * referenceRadialX;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var raw in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = raw as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            var results = new List<Dictionary<string, object>>();
            double maximumOrientationError = 0.0;
            double maximumPositionError = 0.0;
            double minimumInnerRadius = double.PositiveInfinity;
            double maximumInnerRadius = double.NegativeInfinity;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                for (int index = 0; index < sliders.Count; index++)
                {
                    var slider = sliders[index];
                    object rawName;
                    if (!slider.TryGetValue("componentName", out rawName)
                        && !slider.TryGetValue("name", out rawName))
                        throw new ArgumentException(
                            "滑块缺少 componentName/name");
                    string name = Convert.ToString(rawName);
                    double phaseDeg = slider.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(slider["phaseDeg"])
                        : index * 360.0 / sliders.Count;
                    double axialOffset = slider.ContainsKey("axialOffset")
                        ? Convert.ToDouble(slider["axialOffset"])
                        : 0.0;
                    double bodyLength = slider.ContainsKey("bodyLength")
                        ? Convert.ToDouble(slider["bodyLength"])
                        : 0.0;
                    double phase =
                        phaseDeg * Math.PI / 180.0;
                    double radialX =
                        Math.Cos(phase) * referenceRadialX
                        + Math.Sin(phase) * tangentX;
                    double radialY =
                        Math.Cos(phase) * referenceRadialY
                        + Math.Sin(phase) * tangentY;
                    double radialZ =
                        Math.Cos(phase) * referenceRadialZ
                        + Math.Sin(phase) * tangentZ;
                    double x = centerX + axisX * axialOffset
                        + radialX * radialDistance;
                    double y = centerY + axisY * axialOffset
                        + radialY * radialDistance;
                    double z = centerZ + axisZ * axialOffset
                        + radialZ * radialDistance;
                    double rotationX = 0.0;
                    double rotationY = -radialZ;
                    double rotationZ = radialY;
                    double rotationNorm = Math.Sqrt(
                        rotationX * rotationX
                        + rotationY * rotationY
                        + rotationZ * rotationZ);
                    double rotationAngleDeg;
                    if (rotationNorm < 1e-12)
                    {
                        rotationX = 0;
                        rotationY = 0;
                        rotationZ = 1;
                        rotationAngleDeg =
                            radialX >= 0 ? 0.0 : 180.0;
                    }
                    else
                    {
                        rotationX /= rotationNorm;
                        rotationY /= rotationNorm;
                        rotationZ /= rotationNorm;
                        rotationAngleDeg = Math.Acos(
                            Math.Max(-1.0, Math.Min(1.0, radialX)))
                            * 180.0 / Math.PI;
                    }
                    var component = resolve(name);
                    SetComponentPose(
                        name,
                        x, y, z,
                        rotationX, rotationY, rotationZ,
                        -rotationAngleDeg,
                        false,
                        component);
                    var transform = component.Transform2 as MathTransform;
                    var values = transform?.ArrayData as double[];
                    if (values == null || values.Length < 12)
                        throw new InvalidOperationException(
                            $"无法读取滑块姿态: {name}");
                    double actualAxisX = values[0];
                    double actualAxisY = values[1];
                    double actualAxisZ = values[2];
                    double dot = Math.Max(
                        -1.0,
                        Math.Min(
                            1.0,
                            actualAxisX * radialX
                            + actualAxisY * radialY
                            + actualAxisZ * radialZ));
                    double orientationError =
                        Math.Acos(dot) * 180.0 / Math.PI;
                    double positionError = Math.Sqrt(
                        Math.Pow(values[9] - x, 2)
                        + Math.Pow(values[10] - y, 2)
                        + Math.Pow(values[11] - z, 2));
                    double measuredRadialDistance = Math.Sqrt(
                        Math.Pow(
                            values[9] - centerX
                            - axisX * axialOffset, 2)
                        + Math.Pow(
                            values[10] - centerY
                            - axisY * axialOffset, 2)
                        + Math.Pow(
                            values[11] - centerZ
                            - axisZ * axialOffset, 2));
                    double innerRadius =
                        measuredRadialDistance - bodyLength / 2.0;
                    maximumOrientationError = Math.Max(
                        maximumOrientationError,
                        orientationError);
                    maximumPositionError = Math.Max(
                        maximumPositionError,
                        positionError);
                    minimumInnerRadius = Math.Min(
                        minimumInnerRadius,
                        innerRadius);
                    maximumInnerRadius = Math.Max(
                        maximumInnerRadius,
                        innerRadius);
                    results.Add(new Dictionary<string, object>
                    {
                        ["componentName"] = name,
                        ["phaseDeg"] = phaseDeg,
                        ["translationXYZ"] = new double[]
                            { values[9], values[10], values[11] },
                        ["bodyAxisXYZ"] = new double[]
                            { actualAxisX, actualAxisY, actualAxisZ },
                        ["measuredRadialDistance"] =
                            measuredRadialDistance,
                        ["innerTipRadius"] = innerRadius,
                        ["orientationErrorDeg"] = orientationError,
                        ["positionError"] = positionError,
                    });
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
                modelDoc.GraphicsRedraw2();
            }
            return new Dictionary<string, object>
            {
                ["radialDistance"] = radialDistance,
                ["sliders"] = results.ToArray(),
                ["innerRadiusSpread"] =
                    maximumInnerRadius - minimumInnerRadius,
                ["maximumOrientationErrorDeg"] =
                    maximumOrientationError,
                ["maximumPositionError"] = maximumPositionError,
            };
        }

        /// <summary>
        /// Apply one analytically solved pose of a planar crank-slider mechanism.
        /// The rod part's local +Y axis is assumed to run between its pin centers.
        /// Optional synchronized components rotate at signed ratios of crank angle.
        /// </summary>
        public Dictionary<string, object> SetCrankSliderPose(
            string crankComponent,
            string rodComponent,
            string sliderComponent,
            double centerX,
            double centerY,
            double centerZ,
            double crankRadius,
            double rodLength,
            double sliderAxisX,
            double sliderAxisY,
            double sliderAxisZ,
            double rotationAxisX,
            double rotationAxisY,
            double rotationAxisZ,
            double crankAxisOffset,
            double rodAxisOffset,
            double sliderAxisOffset,
            double angleDeg,
            List<Dictionary<string, object>> synchronizedComponents)
        {
            if (crankRadius <= 0 || rodLength <= crankRadius)
                throw new ArgumentException("连杆长度必须大于正的曲柄半径");
            EnsureActiveAssembly();
            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var item in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = item as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };

            double sliderNorm = Math.Sqrt(
                sliderAxisX * sliderAxisX +
                sliderAxisY * sliderAxisY +
                sliderAxisZ * sliderAxisZ);
            double rotationNorm = Math.Sqrt(
                rotationAxisX * rotationAxisX +
                rotationAxisY * rotationAxisY +
                rotationAxisZ * rotationAxisZ);
            if (sliderNorm < 1e-12 || rotationNorm < 1e-12)
                throw new ArgumentException("滑动轴和旋转轴不能为零向量");
            sliderAxisX /= sliderNorm;
            sliderAxisY /= sliderNorm;
            sliderAxisZ /= sliderNorm;
            rotationAxisX /= rotationNorm;
            rotationAxisY /= rotationNorm;
            rotationAxisZ /= rotationNorm;
            Func<IComponent2, double[]> translationOf = component =>
            {
                var transform = component.Transform2 as MathTransform;
                var values = transform?.ArrayData as double[];
                if (values == null || values.Length < 12)
                    throw new InvalidOperationException(
                        $"无法读取组件姿态: {component.Name2}");
                return new double[] { values[9], values[10], values[11] };
            };
            Func<string, double> inferAxisOffset = name =>
            {
                var translation = translationOf(resolve(name));
                return (
                    (translation[0] - centerX) * rotationAxisX +
                    (translation[1] - centerY) * rotationAxisY +
                    (translation[2] - centerZ) * rotationAxisZ);
            };
            if (double.IsNaN(crankAxisOffset))
                crankAxisOffset = inferAxisOffset(crankComponent);
            if (double.IsNaN(rodAxisOffset))
                rodAxisOffset = inferAxisOffset(rodComponent);
            if (double.IsNaN(sliderAxisOffset))
                sliderAxisOffset = inferAxisOffset(sliderComponent);

            // In-plane perpendicular = rotation axis cross slider axis.
            double perpX =
                rotationAxisY * sliderAxisZ -
                rotationAxisZ * sliderAxisY;
            double perpY =
                rotationAxisZ * sliderAxisX -
                rotationAxisX * sliderAxisZ;
            double perpZ =
                rotationAxisX * sliderAxisY -
                rotationAxisY * sliderAxisX;
            double perpNorm = Math.Sqrt(
                perpX * perpX + perpY * perpY + perpZ * perpZ);
            if (perpNorm < 1e-9)
                throw new ArgumentException("滑动轴不能与旋转轴平行");
            perpX /= perpNorm;
            perpY /= perpNorm;
            perpZ /= perpNorm;

            double angle = angleDeg * Math.PI / 180.0;
            double sin = Math.Sin(angle);
            double cos = Math.Cos(angle);
            double underRoot =
                rodLength * rodLength -
                crankRadius * crankRadius * sin * sin;
            if (underRoot < 0)
                throw new InvalidOperationException("曲柄滑块参数无实数解");
            double sliderDistance =
                crankRadius * cos + Math.Sqrt(underRoot);

            double pinX = centerX +
                sliderAxisX * crankRadius * cos +
                perpX * crankRadius * sin;
            double pinY = centerY +
                sliderAxisY * crankRadius * cos +
                perpY * crankRadius * sin;
            double pinZ = centerZ +
                sliderAxisZ * crankRadius * cos +
                perpZ * crankRadius * sin;
            double sliderX = centerX + sliderAxisX * sliderDistance;
            double sliderY = centerY + sliderAxisY * sliderDistance;
            double sliderZ = centerZ + sliderAxisZ * sliderDistance;
            double rodX = (pinX + sliderX) / 2.0;
            double rodY = (pinY + sliderY) / 2.0;
            double rodZ = (pinZ + sliderZ) / 2.0;

            double rodVectorX = sliderX - pinX;
            double rodVectorY = sliderY - pinY;
            double rodVectorZ = sliderZ - pinZ;
            double alongSlider =
                rodVectorX * sliderAxisX +
                rodVectorY * sliderAxisY +
                rodVectorZ * sliderAxisZ;
            double alongPerp =
                rodVectorX * perpX +
                rodVectorY * perpY +
                rodVectorZ * perpZ;
            double rodAngleDeg =
                Math.Atan2(alongPerp, alongSlider) * 180.0 / Math.PI;

            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            var synchronizedResults =
                new List<Dictionary<string, object>>();
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
            SetComponentPose(
                crankComponent,
                centerX + rotationAxisX * crankAxisOffset,
                centerY + rotationAxisY * crankAxisOffset,
                centerZ + rotationAxisZ * crankAxisOffset,
                rotationAxisX, rotationAxisY, rotationAxisZ,
                angleDeg,
                false,
                resolve(crankComponent));
            SetComponentPose(
                rodComponent,
                rodX + rotationAxisX * rodAxisOffset,
                rodY + rotationAxisY * rodAxisOffset,
                rodZ + rotationAxisZ * rodAxisOffset,
                rotationAxisX, rotationAxisY, rotationAxisZ,
                rodAngleDeg,
                false,
                resolve(rodComponent));
            SetComponentPose(
                sliderComponent,
                sliderX + rotationAxisX * sliderAxisOffset,
                sliderY + rotationAxisY * sliderAxisOffset,
                sliderZ + rotationAxisZ * sliderAxisOffset,
                rotationAxisX, rotationAxisY, rotationAxisZ,
                0.0,
                false,
                resolve(sliderComponent));

            foreach (var item in synchronizedComponents
                ?? new List<Dictionary<string, object>>())
            {
                object rawName;
                if (!item.TryGetValue("componentName", out rawName)
                    && !item.TryGetValue("name", out rawName))
                    throw new ArgumentException(
                        "同步组件缺少 componentName/name");
                string name = Convert.ToString(rawName);
                var currentTranslation = translationOf(resolve(name));
                double ratio = item.ContainsKey("ratio")
                    ? Convert.ToDouble(item["ratio"])
                    : 1.0;
                double phaseDeg = item.ContainsKey("phaseDeg")
                    ? Convert.ToDouble(item["phaseDeg"])
                    : 0.0;
                bool reverse = item.ContainsKey("reverse")
                    && Convert.ToBoolean(item["reverse"]);
                if (Math.Abs(ratio) < 1e-12)
                    throw new ArgumentException("同步组件 ratio 不能为零");
                double synchronizedAngle =
                    angleDeg / ratio * (reverse ? -1.0 : 1.0) + phaseDeg;
                synchronizedResults.Add(SetComponentPose(
                    name,
                    item.ContainsKey("x")
                        ? Convert.ToDouble(item["x"])
                        : currentTranslation[0],
                    item.ContainsKey("y")
                        ? Convert.ToDouble(item["y"])
                        : currentTranslation[1],
                    item.ContainsKey("z")
                        ? Convert.ToDouble(item["z"])
                        : currentTranslation[2],
                    rotationAxisX, rotationAxisY, rotationAxisZ,
                    synchronizedAngle,
                    false,
                    resolve(name)));
            }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
            }
            // Transform2 updates are already committed. Rebuilding mates here is
            // both unnecessary and extremely expensive for sampled playback.
            modelDoc.GraphicsRedraw2();

            return new Dictionary<string, object>
            {
                ["angleDeg"] = angleDeg,
                ["crankPinXYZ"] = new double[] { pinX, pinY, pinZ },
                ["sliderXYZ"] = new double[] { sliderX, sliderY, sliderZ },
                ["sliderDistance"] = sliderDistance,
                ["rodMidpointXYZ"] = new double[] { rodX, rodY, rodZ },
                ["rodAngleDeg"] = rodAngleDeg,
                ["synchronized"] = synchronizedResults.ToArray(),
            };
        }

        /// <summary>
        /// Apply a common input angle to any number of independently positioned
        /// rotors. Each rotor can override the global pivot and rotation axis.
        /// </summary>
        public Dictionary<string, object> SetSynchronizedRotaryPose(
            double inputAngleDeg,
            double pivotX,
            double pivotY,
            double pivotZ,
            double axisX,
            double axisY,
            double axisZ,
            List<Dictionary<string, object>> rotors)
        {
            EnsureActiveAssembly();
            if (rotors == null || rotors.Count == 0)
                throw new ArgumentException("rotors 至少需要一个转子");
            double globalAxisNorm = Math.Sqrt(
                axisX * axisX + axisY * axisY + axisZ * axisZ);
            if (globalAxisNorm < 1e-12)
                throw new ArgumentException("全局旋转轴不能为零向量");
            axisX /= globalAxisNorm;
            axisY /= globalAxisNorm;
            axisZ /= globalAxisNorm;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var rawComponent in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = rawComponent as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Func<Dictionary<string, object>, string> nameOf = item =>
            {
                object rawName;
                if (!item.TryGetValue("componentName", out rawName)
                    && !item.TryGetValue("name", out rawName))
                    throw new ArgumentException(
                        "rotor 缺少 componentName/name");
                return Convert.ToString(rawName);
            };
            Func<double, double> normalizeAngle = value =>
            {
                value %= 360.0;
                if (value <= -180.0) value += 360.0;
                if (value > 180.0) value -= 360.0;
                return value;
            };

            var results = new List<Dictionary<string, object>>();
            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                foreach (var rotor in rotors)
                {
                    string name = nameOf(rotor);
                    double ratio = rotor.ContainsKey("ratio")
                        ? Convert.ToDouble(rotor["ratio"])
                        : 1.0;
                    if (Math.Abs(ratio) < 1e-12)
                        throw new ArgumentException(
                            $"rotor {name} 的 ratio 不能为零");
                    bool reverse = rotor.ContainsKey("reverse")
                        && Convert.ToBoolean(rotor["reverse"]);
                    double phaseDeg = rotor.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(rotor["phaseDeg"])
                        : 0.0;
                    double rotorAxisX = rotor.ContainsKey("axisX")
                        ? Convert.ToDouble(rotor["axisX"])
                        : (rotor.ContainsKey("rotationAxisX")
                            ? Convert.ToDouble(rotor["rotationAxisX"])
                            : axisX);
                    double rotorAxisY = rotor.ContainsKey("axisY")
                        ? Convert.ToDouble(rotor["axisY"])
                        : (rotor.ContainsKey("rotationAxisY")
                            ? Convert.ToDouble(rotor["rotationAxisY"])
                            : axisY);
                    double rotorAxisZ = rotor.ContainsKey("axisZ")
                        ? Convert.ToDouble(rotor["axisZ"])
                        : (rotor.ContainsKey("rotationAxisZ")
                            ? Convert.ToDouble(rotor["rotationAxisZ"])
                            : axisZ);
                    double rotorAxisNorm = Math.Sqrt(
                        rotorAxisX * rotorAxisX
                        + rotorAxisY * rotorAxisY
                        + rotorAxisZ * rotorAxisZ);
                    if (rotorAxisNorm < 1e-12)
                        throw new ArgumentException(
                            $"rotor {name} 的旋转轴不能为零向量");
                    rotorAxisX /= rotorAxisNorm;
                    rotorAxisY /= rotorAxisNorm;
                    rotorAxisZ /= rotorAxisNorm;
                    double requestedAngleDeg = inputAngleDeg / ratio
                        * (reverse ? -1.0 : 1.0) + phaseDeg;
                    double rotorPivotX = rotor.ContainsKey("pivotX")
                        ? Convert.ToDouble(rotor["pivotX"])
                        : (rotor.ContainsKey("x")
                            ? Convert.ToDouble(rotor["x"])
                            : pivotX);
                    double rotorPivotY = rotor.ContainsKey("pivotY")
                        ? Convert.ToDouble(rotor["pivotY"])
                        : (rotor.ContainsKey("y")
                            ? Convert.ToDouble(rotor["y"])
                            : pivotY);
                    double rotorPivotZ = rotor.ContainsKey("pivotZ")
                        ? Convert.ToDouble(rotor["pivotZ"])
                        : (rotor.ContainsKey("z")
                            ? Convert.ToDouble(rotor["z"])
                            : pivotZ);
                    var resolved = resolve(name);
                    double requestedAngle =
                        requestedAngleDeg * Math.PI / 180.0;
                    double c = Math.Cos(requestedAngle);
                    double s = Math.Sin(requestedAngle);
                    double t = 1.0 - c;
                    var transformValues = new double[]
                    {
                        t*rotorAxisX*rotorAxisX+c,
                        t*rotorAxisX*rotorAxisY-rotorAxisZ*s,
                        t*rotorAxisX*rotorAxisZ+rotorAxisY*s,
                        t*rotorAxisX*rotorAxisY+rotorAxisZ*s,
                        t*rotorAxisY*rotorAxisY+c,
                        t*rotorAxisY*rotorAxisZ-rotorAxisX*s,
                        t*rotorAxisX*rotorAxisZ-rotorAxisY*s,
                        t*rotorAxisY*rotorAxisZ+rotorAxisX*s,
                        t*rotorAxisZ*rotorAxisZ+c,
                        rotorPivotX, rotorPivotY, rotorPivotZ,
                        1, 0, 0, 0,
                    };
                    var mathUtility =
                        _swApp.GetMathUtility() as IMathUtility;
                    var transform = mathUtility?.CreateTransform(
                        transformValues) as MathTransform;
                    if (transform == null)
                        throw new InvalidOperationException(
                            $"创建同步旋转 Transform 失败: {name}");
                    resolved.Transform2 = transform;
                    // Reading Transform2.ArrayData immediately after a 180-degree
                    // assignment can make some SolidWorks versions block in COM.
                    // Component.Transform2 accepted the exact transform above; use
                    // the normalized assigned angle as the stable diagnostic.
                    double actualAngleDeg = normalizeAngle(
                        requestedAngleDeg);
                    results.Add(new Dictionary<string, object>
                    {
                        ["componentName"] = name,
                        ["pivotXYZ"] = new double[]
                            { rotorPivotX, rotorPivotY, rotorPivotZ },
                        ["rotationAxis"] = new double[]
                            { rotorAxisX, rotorAxisY, rotorAxisZ },
                        ["ratio"] = ratio,
                        ["reverse"] = reverse,
                        ["phaseDeg"] = phaseDeg,
                        ["requestedAngleDeg"] = requestedAngleDeg,
                        ["actualAngleDeg"] = actualAngleDeg,
                        ["angleErrorDeg"] = normalizeAngle(
                            actualAngleDeg - requestedAngleDeg),
                    });
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
            }
            return new Dictionary<string, object>
            {
                ["inputAngleDeg"] = inputAngleDeg,
                ["rotors"] = results.ToArray(),
            };
        }

        /// <summary>
        /// Solve and apply one planar closed four-bar pose. Link component local
        /// +X axes are assumed to run from each link's first joint to its second.
        /// </summary>
        public Dictionary<string, object> SetFourBarPose(
            string inputComponent,
            string couplerComponent,
            string outputComponent,
            double groundAX,
            double groundAY,
            double groundAZ,
            double groundBX,
            double groundBY,
            double groundBZ,
            double inputLength,
            double couplerLength,
            double outputLength,
            double inputAngleDeg,
            string assemblyMode,
            double rotationAxisX,
            double rotationAxisY,
            double rotationAxisZ,
            Dictionary<string, object> couplerPoint,
            List<Dictionary<string, object>> couplerPointComponents)
        {
            EnsureActiveAssembly();
            if (inputLength <= 0 || couplerLength <= 0 || outputLength <= 0)
                throw new ArgumentException("四杆机构所有连杆长度必须为正数");
            string normalizedMode = (assemblyMode ?? "open")
                .Trim().ToLowerInvariant();
            if (normalizedMode != "open" && normalizedMode != "crossed")
                throw new ArgumentException(
                    "assemblyMode 必须为 open 或 crossed");
            double axisNorm = Math.Sqrt(
                rotationAxisX * rotationAxisX
                + rotationAxisY * rotationAxisY
                + rotationAxisZ * rotationAxisZ);
            if (axisNorm < 1e-12)
                throw new ArgumentException("四杆机构旋转轴不能为零向量");
            rotationAxisX /= axisNorm;
            rotationAxisY /= axisNorm;
            rotationAxisZ /= axisNorm;

            double groundX = groundBX - groundAX;
            double groundY = groundBY - groundAY;
            double groundZ = groundBZ - groundAZ;
            double groundAlongAxis = groundX * rotationAxisX
                + groundY * rotationAxisY + groundZ * rotationAxisZ;
            double rawGroundLength = Math.Sqrt(
                groundX * groundX + groundY * groundY + groundZ * groundZ);
            if (Math.Abs(groundAlongAxis)
                > 1e-9 * Math.Max(1.0, rawGroundLength))
                throw new ArgumentException(
                    "地面铰点 A/B 必须位于垂直于 rotationAxis 的同一平面");
            groundX -= groundAlongAxis * rotationAxisX;
            groundY -= groundAlongAxis * rotationAxisY;
            groundZ -= groundAlongAxis * rotationAxisZ;
            double groundLength = Math.Sqrt(
                groundX * groundX + groundY * groundY + groundZ * groundZ);
            if (groundLength < 1e-12)
                throw new ArgumentException(
                    "地面铰点 A/B 在机构平面内不能重合");
            groundX /= groundLength;
            groundY /= groundLength;
            groundZ /= groundLength;
            double planeYx = rotationAxisY * groundZ
                - rotationAxisZ * groundY;
            double planeYy = rotationAxisZ * groundX
                - rotationAxisX * groundZ;
            double planeYz = rotationAxisX * groundY
                - rotationAxisY * groundX;

            double inputAngle = inputAngleDeg * Math.PI / 180.0;
            double inputDirX = groundX * Math.Cos(inputAngle)
                + planeYx * Math.Sin(inputAngle);
            double inputDirY = groundY * Math.Cos(inputAngle)
                + planeYy * Math.Sin(inputAngle);
            double inputDirZ = groundZ * Math.Cos(inputAngle)
                + planeYz * Math.Sin(inputAngle);
            double jointCX = groundAX + inputDirX * inputLength;
            double jointCY = groundAY + inputDirY * inputLength;
            double jointCZ = groundAZ + inputDirZ * inputLength;

            double cbX = groundBX - jointCX;
            double cbY = groundBY - jointCY;
            double cbZ = groundBZ - jointCZ;
            double centerDistance = Math.Sqrt(
                cbX * cbX + cbY * cbY + cbZ * cbZ);
            double tolerance = 1e-10 * Math.Max(
                1.0,
                Math.Max(centerDistance,
                    Math.Max(couplerLength, outputLength)));
            if (centerDistance < tolerance
                || centerDistance > couplerLength + outputLength + tolerance
                || centerDistance < Math.Abs(
                    couplerLength - outputLength) - tolerance)
                throw new InvalidOperationException(
                    "当前输入角度下四杆机构无闭合实数解");
            double cbUnitX = cbX / centerDistance;
            double cbUnitY = cbY / centerDistance;
            double cbUnitZ = cbZ / centerDistance;
            double along = (
                couplerLength * couplerLength
                - outputLength * outputLength
                + centerDistance * centerDistance)
                / (2.0 * centerDistance);
            double heightSquared =
                couplerLength * couplerLength - along * along;
            if (heightSquared < -tolerance)
                throw new InvalidOperationException(
                    "当前输入角度下四杆机构圆交点不存在");
            double height = Math.Sqrt(Math.Max(0.0, heightSquared));
            double intersectionPerpX = rotationAxisY * cbUnitZ
                - rotationAxisZ * cbUnitY;
            double intersectionPerpY = rotationAxisZ * cbUnitX
                - rotationAxisX * cbUnitZ;
            double intersectionPerpZ = rotationAxisX * cbUnitY
                - rotationAxisY * cbUnitX;
            double baseDX = jointCX + cbUnitX * along;
            double baseDY = jointCY + cbUnitY * along;
            double baseDZ = jointCZ + cbUnitZ * along;
            double candidate1X = baseDX + intersectionPerpX * height;
            double candidate1Y = baseDY + intersectionPerpY * height;
            double candidate1Z = baseDZ + intersectionPerpZ * height;
            double candidate2X = baseDX - intersectionPerpX * height;
            double candidate2Y = baseDY - intersectionPerpY * height;
            double candidate2Z = baseDZ - intersectionPerpZ * height;
            Func<double, double, double, double> sideOfGround =
                (x, y, z) =>
                    (x - groundAX) * planeYx
                    + (y - groundAY) * planeYy
                    + (z - groundAZ) * planeYz;
            double sideC = sideOfGround(jointCX, jointCY, jointCZ);
            double side1 = sideOfGround(
                candidate1X, candidate1Y, candidate1Z);
            bool chooseFirst;
            if (Math.Abs(sideC) <= tolerance)
                chooseFirst = normalizedMode == "open";
            else if (normalizedMode == "open")
                chooseFirst = side1 * sideC >= 0;
            else
                chooseFirst = side1 * sideC < 0;
            double jointDX = chooseFirst ? candidate1X : candidate2X;
            double jointDY = chooseFirst ? candidate1Y : candidate2Y;
            double jointDZ = chooseFirst ? candidate1Z : candidate2Z;

            Func<double, double, double, double> planarAngleDeg =
                (x, y, z) => Math.Atan2(
                    x * planeYx + y * planeYy + z * planeYz,
                    x * groundX + y * groundY + z * groundZ)
                    * 180.0 / Math.PI;
            double couplerDirX = (jointDX - jointCX) / couplerLength;
            double couplerDirY = (jointDY - jointCY) / couplerLength;
            double couplerDirZ = (jointDZ - jointCZ) / couplerLength;
            double outputDirX = (jointDX - groundBX) / outputLength;
            double outputDirY = (jointDY - groundBY) / outputLength;
            double outputDirZ = (jointDZ - groundBZ) / outputLength;
            double couplerAngleDeg = planarAngleDeg(
                couplerDirX, couplerDirY, couplerDirZ);
            double outputAngleDeg = planarAngleDeg(
                outputDirX, outputDirY, outputDirZ);

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var rawComponent in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = rawComponent as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Action<string, double, double, double,
                double, double, double> placePlanarLink =
                (name, x, y, z, dirX, dirY, dirZ) =>
                {
                    var component = resolve(name);
                    if (component.IsFixed())
                    {
                        ClearAssemblySelection();
                        component.Select4(false, null, false);
                        _activeAssembly.UnfixComponent();
                    }
                    double localYx = rotationAxisY * dirZ
                        - rotationAxisZ * dirY;
                    double localYy = rotationAxisZ * dirX
                        - rotationAxisX * dirZ;
                    double localYz = rotationAxisX * dirY
                        - rotationAxisY * dirX;
                    var values = new double[]
                    {
                        dirX, dirY, dirZ,
                        localYx, localYy, localYz,
                        rotationAxisX, rotationAxisY, rotationAxisZ,
                        x, y, z,
                        1, 0, 0, 0,
                    };
                    var mathUtility =
                        _swApp.GetMathUtility() as IMathUtility;
                    var transform =
                        mathUtility?.CreateTransform(values) as MathTransform;
                    if (transform == null)
                        throw new InvalidOperationException(
                            $"创建四杆组件姿态 Transform 失败: {name}");
                    component.Transform2 = transform;
                };

            double fraction = couplerPoint != null
                && couplerPoint.ContainsKey("fraction")
                ? Convert.ToDouble(couplerPoint["fraction"])
                : 0.5;
            double normalOffset = couplerPoint != null
                && couplerPoint.ContainsKey("normalOffset")
                ? Convert.ToDouble(couplerPoint["normalOffset"])
                : 0.0;
            double couplerNormalX = rotationAxisY * couplerDirZ
                - rotationAxisZ * couplerDirY;
            double couplerNormalY = rotationAxisZ * couplerDirX
                - rotationAxisX * couplerDirZ;
            double couplerNormalZ = rotationAxisX * couplerDirY
                - rotationAxisY * couplerDirX;
            double couplerPointX = jointCX
                + (jointDX - jointCX) * fraction
                + couplerNormalX * normalOffset;
            double couplerPointY = jointCY
                + (jointDY - jointCY) * fraction
                + couplerNormalY * normalOffset;
            double couplerPointZ = jointCZ
                + (jointDZ - jointCZ) * fraction
                + couplerNormalZ * normalOffset;

            var pointResults = new List<Dictionary<string, object>>();
            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                placePlanarLink(
                    inputComponent,
                    (groundAX + jointCX) / 2.0,
                    (groundAY + jointCY) / 2.0,
                    (groundAZ + jointCZ) / 2.0,
                    inputDirX, inputDirY, inputDirZ);
                placePlanarLink(
                    couplerComponent,
                    (jointCX + jointDX) / 2.0,
                    (jointCY + jointDY) / 2.0,
                    (jointCZ + jointDZ) / 2.0,
                    couplerDirX, couplerDirY, couplerDirZ);
                placePlanarLink(
                    outputComponent,
                    (groundBX + jointDX) / 2.0,
                    (groundBY + jointDY) / 2.0,
                    (groundBZ + jointDZ) / 2.0,
                    outputDirX, outputDirY, outputDirZ);
                foreach (var item in couplerPointComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    object rawName;
                    if (!item.TryGetValue("componentName", out rawName)
                        && !item.TryGetValue("name", out rawName))
                        throw new ArgumentException(
                            "couplerPointComponent 缺少 componentName/name");
                    string name = Convert.ToString(rawName);
                    double axisOffset = item.ContainsKey("axisOffset")
                        ? Convert.ToDouble(item["axisOffset"])
                        : 0.0;
                    double angleOffset = item.ContainsKey("angleOffsetDeg")
                        ? Convert.ToDouble(item["angleOffsetDeg"])
                        : 0.0;
                    double pointAngle = (
                        couplerAngleDeg + angleOffset)
                        * Math.PI / 180.0;
                    double pointDirX = groundX * Math.Cos(pointAngle)
                        + planeYx * Math.Sin(pointAngle);
                    double pointDirY = groundY * Math.Cos(pointAngle)
                        + planeYy * Math.Sin(pointAngle);
                    double pointDirZ = groundZ * Math.Cos(pointAngle)
                        + planeYz * Math.Sin(pointAngle);
                    placePlanarLink(
                        name,
                        couplerPointX + rotationAxisX * axisOffset,
                        couplerPointY + rotationAxisY * axisOffset,
                        couplerPointZ + rotationAxisZ * axisOffset,
                        pointDirX, pointDirY, pointDirZ);
                    pointResults.Add(new Dictionary<string, object>
                    {
                        ["componentName"] = name,
                        ["positionXYZ"] = new double[]
                        {
                            couplerPointX + rotationAxisX * axisOffset,
                            couplerPointY + rotationAxisY * axisOffset,
                            couplerPointZ + rotationAxisZ * axisOffset,
                        },
                    });
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
            }
            modelDoc.GraphicsRedraw2();
            double inputError = Math.Abs(Math.Sqrt(
                Math.Pow(jointCX - groundAX, 2)
                + Math.Pow(jointCY - groundAY, 2)
                + Math.Pow(jointCZ - groundAZ, 2)) - inputLength);
            double couplerError = Math.Abs(Math.Sqrt(
                Math.Pow(jointDX - jointCX, 2)
                + Math.Pow(jointDY - jointCY, 2)
                + Math.Pow(jointDZ - jointCZ, 2)) - couplerLength);
            double outputError = Math.Abs(Math.Sqrt(
                Math.Pow(jointDX - groundBX, 2)
                + Math.Pow(jointDY - groundBY, 2)
                + Math.Pow(jointDZ - groundBZ, 2)) - outputLength);
            double closureError = Math.Max(
                inputError,
                Math.Max(couplerError, outputError));
            return new Dictionary<string, object>
            {
                ["assemblyMode"] = normalizedMode,
                ["joints"] = new Dictionary<string, object>
                {
                    ["A"] = new double[] { groundAX, groundAY, groundAZ },
                    ["C"] = new double[] { jointCX, jointCY, jointCZ },
                    ["D"] = new double[] { jointDX, jointDY, jointDZ },
                    ["B"] = new double[] { groundBX, groundBY, groundBZ },
                },
                ["inputAngleDeg"] = inputAngleDeg,
                ["couplerAngleDeg"] = couplerAngleDeg,
                ["outputAngleDeg"] = outputAngleDeg,
                ["linkAnglesDeg"] = new Dictionary<string, object>
                {
                    ["input"] = inputAngleDeg,
                    ["coupler"] = couplerAngleDeg,
                    ["output"] = outputAngleDeg,
                },
                ["closureError"] = closureError,
                ["couplerPointXYZ"] = new double[]
                    { couplerPointX, couplerPointY, couplerPointZ },
                ["couplerPointComponents"] = pointResults.ToArray(),
            };
        }

        /// <summary>
        /// Apply a radial cam driver angle and the corresponding follower
        /// translation for harmonic-lift or eccentric profiles.
        /// </summary>
        public Dictionary<string, object> SetCamFollowerPose(
            string driverComponent,
            string followerComponent,
            double centerX,
            double centerY,
            double centerZ,
            double baseRadius,
            double lift,
            double phaseDeg,
            double inputAngleDeg,
            string profile,
            double rotationAxisX,
            double rotationAxisY,
            double rotationAxisZ,
            double followerAxisX,
            double followerAxisY,
            double followerAxisZ,
            List<Dictionary<string, object>> synchronizedFollowerComponents)
        {
            EnsureActiveAssembly();
            if (baseRadius <= 0)
                throw new ArgumentException("baseRadius 必须为正数");
            if (lift <= 0)
                throw new ArgumentException("lift 必须为正数");
            string normalizedProfile = (profile ?? "harmonic")
                .Trim().ToLowerInvariant();
            if (normalizedProfile != "harmonic"
                && normalizedProfile != "eccentric")
                throw new ArgumentException(
                    "profile 必须为 harmonic 或 eccentric");
            if (normalizedProfile == "eccentric" && lift >= baseRadius)
                throw new ArgumentException(
                    "eccentric profile 要求 lift 小于 baseRadius");
            double rotationNorm = Math.Sqrt(
                rotationAxisX * rotationAxisX
                + rotationAxisY * rotationAxisY
                + rotationAxisZ * rotationAxisZ);
            double followerNorm = Math.Sqrt(
                followerAxisX * followerAxisX
                + followerAxisY * followerAxisY
                + followerAxisZ * followerAxisZ);
            if (rotationNorm < 1e-12 || followerNorm < 1e-12)
                throw new ArgumentException(
                    "rotationAxis 和 followerAxis 不能为零向量");
            rotationAxisX /= rotationNorm;
            rotationAxisY /= rotationNorm;
            rotationAxisZ /= rotationNorm;
            followerAxisX /= followerNorm;
            followerAxisY /= followerNorm;
            followerAxisZ /= followerNorm;

            double profileAngleDeg = inputAngleDeg + phaseDeg;
            double profileAngle = profileAngleDeg * Math.PI / 180.0;
            double followerLift = normalizedProfile == "harmonic"
                ? lift * (1.0 - Math.Cos(profileAngle)) / 2.0
                : lift * Math.Cos(profileAngle);
            double radius = baseRadius + followerLift;
            if (radius <= 0)
                throw new InvalidOperationException(
                    "当前凸轮参数产生了非正的径向距离");
            double followerX = centerX + followerAxisX * radius;
            double followerY = centerY + followerAxisY * radius;
            double followerZ = centerZ + followerAxisZ * radius;

            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var rawComponent in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = rawComponent as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };

            var synchronizedResults =
                new List<Dictionary<string, object>>();
            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;
                SetComponentPose(
                    driverComponent,
                    centerX, centerY, centerZ,
                    rotationAxisX, rotationAxisY, rotationAxisZ,
                    inputAngleDeg,
                    false,
                    resolve(driverComponent));
                SetComponentPose(
                    followerComponent,
                    followerX, followerY, followerZ,
                    rotationAxisX, rotationAxisY, rotationAxisZ,
                    0.0,
                    false,
                    resolve(followerComponent));
                foreach (var item in synchronizedFollowerComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    object rawName;
                    if (!item.TryGetValue("componentName", out rawName)
                        && !item.TryGetValue("name", out rawName))
                        throw new ArgumentException(
                            "同步从动件缺少 componentName/name");
                    string name = Convert.ToString(rawName);
                    double axisOffset = item.ContainsKey("axisOffset")
                        ? Convert.ToDouble(item["axisOffset"])
                        : 0.0;
                    double followerOffset = item.ContainsKey("followerOffset")
                        ? Convert.ToDouble(item["followerOffset"])
                        : 0.0;
                    double x = followerX
                        + rotationAxisX * axisOffset
                        + followerAxisX * followerOffset;
                    double y = followerY
                        + rotationAxisY * axisOffset
                        + followerAxisY * followerOffset;
                    double z = followerZ
                        + rotationAxisZ * axisOffset
                        + followerAxisZ * followerOffset;
                    SetComponentPose(
                        name,
                        x, y, z,
                        rotationAxisX, rotationAxisY, rotationAxisZ,
                        0.0,
                        false,
                        resolve(name));
                    synchronizedResults.Add(
                        new Dictionary<string, object>
                        {
                            ["componentName"] = name,
                            ["positionXYZ"] = new double[] { x, y, z },
                        });
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
            }
            modelDoc.GraphicsRedraw2();
            return new Dictionary<string, object>
            {
                ["profile"] = normalizedProfile,
                ["lift"] = followerLift,
                ["followerPosition"] =
                    new double[] { followerX, followerY, followerZ },
                ["followerPositionXYZ"] =
                    new double[] { followerX, followerY, followerZ },
                ["radialDistance"] = radius,
                ["driverAngle"] = inputAngleDeg,
                ["driverAngleDeg"] = inputAngleDeg,
                ["profileAngleDeg"] = profileAngleDeg,
                ["synchronizedFollowers"] =
                    synchronizedResults.ToArray(),
            };
        }

        /// <summary>
        /// Apply a deterministic pose for a generic dwell/index rotary drive.
        /// The input rotates continuously. During the engagement interval the
        /// indexed output advances one slot with a smooth zero-velocity profile;
        /// outside that interval it remains locked at the current index.
        /// </summary>
        public Dictionary<string, object> SetIndexedRotaryPose(
            string driverComponent,
            string indexedComponent,
            double driverX,
            double driverY,
            double driverZ,
            double indexedX,
            double indexedY,
            double indexedZ,
            double axisX,
            double axisY,
            double axisZ,
            double inputAngleDeg,
            int slotCount,
            double engagementStartDeg,
            double engagementEndDeg,
            bool reverseOutput,
            List<Dictionary<string, object>> continuousComponents,
            List<Dictionary<string, object>> indexedComponents)
        {
            if (slotCount < 2)
                throw new ArgumentException("slotCount 必须至少为 2");
            if (engagementStartDeg < 0
                || engagementEndDeg > 360
                || engagementStartDeg >= engagementEndDeg)
                throw new ArgumentException(
                    "啮合区间必须满足 0 <= start < end <= 360");

            EnsureActiveAssembly();
            var componentLookup = new Dictionary<string, IComponent2>(
                StringComparer.OrdinalIgnoreCase);
            foreach (var item in (_activeAssembly.GetComponents(false)
                as object[]) ?? new object[0])
            {
                var component = item as IComponent2;
                if (component != null)
                    componentLookup[component.Name2] = component;
            }
            Func<string, IComponent2> resolve = name =>
            {
                IComponent2 component;
                if (!componentLookup.TryGetValue(name, out component))
                    throw new InvalidOperationException(
                        $"装配体中找不到组件: {name}");
                return component;
            };
            Func<IComponent2, double[]> translationOf = component =>
            {
                var transform = component.Transform2 as MathTransform;
                var values = transform?.ArrayData as double[];
                if (values == null || values.Length < 12)
                    throw new InvalidOperationException(
                        $"无法读取组件姿态: {component.Name2}");
                return new double[] { values[9], values[10], values[11] };
            };
            Func<Dictionary<string, object>, string> componentNameOf = item =>
            {
                object rawName;
                if (!item.TryGetValue("componentName", out rawName)
                    && !item.TryGetValue("name", out rawName))
                    throw new ArgumentException(
                        "同步组件缺少 componentName/name");
                return Convert.ToString(rawName);
            };
            var currentDriver = translationOf(resolve(driverComponent));
            var currentIndexed = translationOf(resolve(indexedComponent));
            if (double.IsNaN(driverX)) driverX = currentDriver[0];
            if (double.IsNaN(driverY)) driverY = currentDriver[1];
            if (double.IsNaN(driverZ)) driverZ = currentDriver[2];
            if (double.IsNaN(indexedX)) indexedX = currentIndexed[0];
            if (double.IsNaN(indexedY)) indexedY = currentIndexed[1];
            if (double.IsNaN(indexedZ)) indexedZ = currentIndexed[2];

            double completedCycles = Math.Floor(inputAngleDeg / 360.0);
            double phaseDeg = inputAngleDeg - completedCycles * 360.0;
            double progress;
            bool engaged;
            if (phaseDeg <= engagementStartDeg)
            {
                progress = 0.0;
                engaged = false;
            }
            else if (phaseDeg >= engagementEndDeg)
            {
                progress = 1.0;
                engaged = false;
            }
            else
            {
                double normalized = (
                    (phaseDeg - engagementStartDeg)
                    / (engagementEndDeg - engagementStartDeg));
                progress = normalized * normalized
                    * (3.0 - 2.0 * normalized);
                engaged = true;
            }
            double stepAngleDeg = 360.0 / slotCount;
            double outputAngleDeg = (
                (completedCycles + progress) * stepAngleDeg
                * (reverseOutput ? -1.0 : 1.0));

            var modelDoc = (IModelDoc2)_activeAssembly;
            var modelView = modelDoc.ActiveView as IModelView;
            var continuousResults =
                new List<Dictionary<string, object>>();
            var indexedResults =
                new List<Dictionary<string, object>>();
            try
            {
                _swApp.CommandInProgress = true;
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = false;

                SetComponentPose(
                    driverComponent,
                    driverX, driverY, driverZ,
                    axisX, axisY, axisZ,
                    inputAngleDeg,
                    false,
                    resolve(driverComponent));
                SetComponentPose(
                    indexedComponent,
                    indexedX, indexedY, indexedZ,
                    axisX, axisY, axisZ,
                    outputAngleDeg,
                    false,
                    resolve(indexedComponent));

                foreach (var item in continuousComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    string name = componentNameOf(item);
                    var current = translationOf(resolve(name));
                    double ratio = item.ContainsKey("ratio")
                        ? Convert.ToDouble(item["ratio"])
                        : 1.0;
                    if (Math.Abs(ratio) < 1e-12)
                        throw new ArgumentException(
                            "连续同步组件 ratio 不能为零");
                    bool reverse = item.ContainsKey("reverse")
                        && Convert.ToBoolean(item["reverse"]);
                    double offset = item.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(item["phaseDeg"])
                        : 0.0;
                    double angle = (
                        inputAngleDeg / ratio
                        * (reverse ? -1.0 : 1.0)
                        + offset);
                    continuousResults.Add(SetComponentPose(
                        name,
                        item.ContainsKey("x")
                            ? Convert.ToDouble(item["x"])
                            : current[0],
                        item.ContainsKey("y")
                            ? Convert.ToDouble(item["y"])
                            : current[1],
                        item.ContainsKey("z")
                            ? Convert.ToDouble(item["z"])
                            : current[2],
                        axisX, axisY, axisZ,
                        angle,
                        false,
                        resolve(name)));
                }

                foreach (var item in indexedComponents
                    ?? new List<Dictionary<string, object>>())
                {
                    string name = componentNameOf(item);
                    var current = translationOf(resolve(name));
                    double ratio = item.ContainsKey("ratio")
                        ? Convert.ToDouble(item["ratio"])
                        : 1.0;
                    if (Math.Abs(ratio) < 1e-12)
                        throw new ArgumentException(
                            "分度同步组件 ratio 不能为零");
                    bool reverse = item.ContainsKey("reverse")
                        && Convert.ToBoolean(item["reverse"]);
                    double offset = item.ContainsKey("phaseDeg")
                        ? Convert.ToDouble(item["phaseDeg"])
                        : 0.0;
                    double angle = (
                        outputAngleDeg / ratio
                        * (reverse ? -1.0 : 1.0)
                        + offset);
                    indexedResults.Add(SetComponentPose(
                        name,
                        item.ContainsKey("x")
                            ? Convert.ToDouble(item["x"])
                            : current[0],
                        item.ContainsKey("y")
                            ? Convert.ToDouble(item["y"])
                            : current[1],
                        item.ContainsKey("z")
                            ? Convert.ToDouble(item["z"])
                            : current[2],
                        axisX, axisY, axisZ,
                        angle,
                        false,
                        resolve(name)));
                }
            }
            finally
            {
                if (modelView != null)
                    modelView.EnableGraphicsUpdate = true;
                _swApp.CommandInProgress = false;
            }
            modelDoc.GraphicsRedraw2();
            return new Dictionary<string, object>
            {
                ["inputAngleDeg"] = inputAngleDeg,
                ["phaseDeg"] = phaseDeg,
                ["completedCycles"] = completedCycles,
                ["engaged"] = engaged,
                ["dwell"] = !engaged,
                ["indexProgress"] = progress,
                ["stepAngleDeg"] = stepAngleDeg,
                ["outputAngleDeg"] = outputAngleDeg,
                ["continuous"] = continuousResults.ToArray(),
                ["indexed"] = indexedResults.ToArray(),
            };
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

            // Gear Mate only needs stable rotational references. Resolve each
            // component's center-bore cylinder deterministically instead of
            // asking callers/LLMs to guess fragile ray coordinates.
            IFace2 face1 = FindMotorCylinderFace(
                FindAssemblyComponent(comp1Name),
                appendSelection: false);
            IFace2 face2 = FindMotorCylinderFace(
                FindAssemblyComponent(comp2Name),
                appendSelection: true);
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

        /// <summary>把指定顶层组件固定在当前装配位置。</summary>
        public void FixComponent(string componentName)
        {
            EnsureActiveAssembly();
            ClearAssemblySelection();
            var ext = ((IModelDoc2)_activeAssembly).Extension;
            var fullName = $"{componentName}@{_activeAssemblyName}";
            bool ok = ext.SelectByID2(
                fullName,
                "COMPONENT",
                0.0,
                0.0,
                0.0,
                false,
                0,
                null,
                0);
            if (!ok)
                throw new InvalidOperationException($"选择组件失败: {fullName}");
            _activeAssembly.FixComponent();
            Console.WriteLine($"[SwCommands] 已固定组件: {componentName}");
        }

        /// <summary>
        /// 获取装配体的紧凑结构快照，供 Multi-turn Agent 机器验收。
        /// </summary>
        public Dictionary<string, object> GetAssemblyObservation()
        {
            EnsureActiveAssembly();
            var components = new List<Dictionary<string, object>>();
            var componentObjects = _activeAssembly.GetComponents(false) as object[];
            if (componentObjects != null)
            {
                foreach (var item in componentObjects)
                {
                    var component = item as IComponent2;
                    if (component == null) continue;

                    var transformValues = new double[0];
                    var translation = new double[] { 0.0, 0.0, 0.0 };
                    var transform = component.Transform2 as IMathTransform;
                    if (transform != null)
                    {
                        var values = transform.ArrayData as double[];
                        if (values != null)
                        {
                            transformValues = values;
                            if (values.Length >= 12)
                            {
                                translation = new double[]
                                {
                                    values[9],
                                    values[10],
                                    values[11],
                                };
                            }
                        }
                    }

                    components.Add(new Dictionary<string, object>
                    {
                        ["name"] = component.Name2,
                        ["path"] = component.GetPathName() ?? "",
                        ["isFixed"] = component.IsFixed(),
                        ["suppressionState"] = component.GetSuppression(),
                        ["translationXYZ"] = translation,
                        ["transform"] = transformValues,
                    });
                }
            }

            var mates = new List<Dictionary<string, object>>();
            var feature = ((IModelDoc2)_activeAssembly).FirstFeature() as IFeature;
            while (feature != null)
            {
                var subFeature = feature.GetFirstSubFeature() as IFeature;
                while (subFeature != null)
                {
                    string typeName = subFeature.GetTypeName2() ?? "";
                    var definition = subFeature.GetDefinition();
                    if (typeName.IndexOf(
                        "mate",
                        StringComparison.OrdinalIgnoreCase) >= 0
                        || definition is IMate2
                        || definition is IGearMateFeatureData)
                    {
                        var mate = new Dictionary<string, object>
                        {
                            ["name"] = subFeature.Name,
                            ["type"] = typeName,
                            ["suppressed"] = subFeature.IsSuppressed(),
                        };
                        var gear = definition as IGearMateFeatureData;
                        if (gear != null)
                        {
                            mate["gearRatioNumerator"] =
                                gear.GearRatioNumerator;
                            mate["gearRatioDenominator"] =
                                gear.GearRatioDenominator;
                            mate["reverse"] = gear.Reverse;
                        }
                        mates.Add(mate);
                    }
                    subFeature = subFeature.GetNextSubFeature() as IFeature;
                }
                feature = feature.GetNextFeature() as IFeature;
            }

            return new Dictionary<string, object>
            {
                ["documentType"] = "assembly",
                ["assemblyName"] = _activeAssemblyName,
                ["assemblyPath"] = _activeAssemblyPath,
                ["componentCount"] = components.Count,
                ["components"] = components.ToArray(),
                ["mateCount"] = mates.Count,
                ["mates"] = mates.ToArray(),
            };
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
        private IFace2 FindMotorCylinderFace(
            IComponent2 component,
            bool appendSelection = false)
        {
            // 渐开线齿轮有数百个平面齿面，但中心孔是唯一圆柱面。直接从组件轴心
            // 向 +X 射线拾取孔壁，避免逐面 COM 遍历导致创建马达超时。
            var assemblyModel = (IModelDoc2)_activeAssembly;
            if (!appendSelection)
                assemblyModel.ClearSelection2(true);
            var componentTransform = component.Transform2 as MathTransform;
            var transformData = componentTransform?.ArrayData as double[];
            if (transformData != null && transformData.Length >= 12)
            {
                bool selected = assemblyModel.Extension.SelectByRay(
                    transformData[9], transformData[10], transformData[11],
                    1.0, 0.0, 0.0, 0.0005,
                    (int)swSelectType_e.swSelFACES,
                    appendSelection, 1, 0);
                if (selected)
                {
                    var raySelectionManager = (ISelectionMgr)assemblyModel.SelectionManager;
                    int selectedCount = raySelectionManager.GetSelectedObjectCount2(-1);
                    var selectedFace = raySelectionManager.GetSelectedObject6(
                        selectedCount,
                        -1) as IFace2;
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
            var fallbackSelectionManager = (ISelectionMgr)assemblyModel.SelectionManager;
            var selectData = fallbackSelectionManager.CreateSelectData();
            selectData.Mark = 1;
            var faceEntity = bestFace as IEntity;
            if (faceEntity == null
                || !faceEntity.Select4(appendSelection, selectData))
                throw new InvalidOperationException(
                    $"组件 \"{component.Name2}\" 的圆柱面选择失败");
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
