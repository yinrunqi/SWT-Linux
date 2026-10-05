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
        #region 原子操作 — 面选择

        /// <summary>
        /// 沿给定射线在所有实体面中查找命中的面，**优先返回最近的平面**；
        /// 仅当射线没有命中任何平面时，才返回离射线起点最近的曲面。
        /// 这样可避免圆角后实体上的大量曲面（圆角面）在选面时与平面竞争而被误选。
        /// </summary>
        private void FindFaceByRay(object[] bodies,
            double rx, double ry, double rz, double dx, double dy, double dz,
            out IFace2 hitFace, out double[] hitPoint)
        {
            IFace2 bestPlanar = null; double bestPlanarT = double.MaxValue; double[] bestPlanarPt = null;
            IFace2 bestCurved = null; double bestCurvedDist = double.MaxValue; double[] bestCurvedPt = null;

            foreach (object bodyObj in bodies)
            {
                var body = bodyObj as IBody2;
                if (body == null) continue;

                var face = (IFace2)body.GetFirstFace();
                while (face != null)
                {
                    var surface = face.IGetSurface();
                    if (surface.IsPlane())
                    {
                        // 平面：求射线与平面交点（P = Origin + t·Dir）
                        var pp = (double[])surface.PlaneParams;
                        double nx = pp[0], ny = pp[1], nz = pp[2];
                        double rootX = pp[3], rootY = pp[4], rootZ = pp[5];
                        double denom = dx * nx + dy * ny + dz * nz;
                        if (Math.Abs(denom) >= 1e-12) // 非平行
                        {
                            double t = ((rootX - rx) * nx + (rootY - ry) * ny + (rootZ - rz) * nz) / denom;
                            if (t >= 0) // 交点在射线正方向
                            {
                                double hx = rx + t * dx, hy = ry + t * dy, hz = rz + t * dz;
                                var closest = (double[])face.GetClosestPointOn(hx, hy, hz);
                                double d = Math.Sqrt(
                                    Math.Pow(closest[0] - hx, 2) +
                                    Math.Pow(closest[1] - hy, 2) +
                                    Math.Pow(closest[2] - hz, 2));
                                if (d < 0.0001 && t < bestPlanarT) // 交点落在面内且更近
                                {
                                    bestPlanarT = t; bestPlanar = face; bestPlanarPt = new[] { hx, hy, hz };
                                }
                            }
                        }
                    }
                    else
                    {
                        // 曲面：用 GetClosestPointOn 取离射线起点最近的点近似。
                        // ⚠️ GetClosestPointOn 不考虑射线方向，必须额外判定该点在射线【前方】，
                        // 否则当射线朝实体外侧时，也会错误命中起点附近、位于射线反方向的圆角曲面，
                        // 从而吞掉本应触发的"反转方向重试"。
                        var closest = (double[])face.GetClosestPointOn(rx, ry, rz);
                        double vx = closest[0] - rx, vy = closest[1] - ry, vz = closest[2] - rz;
                        double forwardDot = vx * dx + vy * dy + vz * dz; // 与射线方向同向才算前方
                        if (forwardDot >= -1e-9)
                        {
                            double d = Math.Sqrt(vx * vx + vy * vy + vz * vz);
                            if (d < bestCurvedDist)
                            {
                                bestCurvedDist = d; bestCurved = face; bestCurvedPt = new[] { closest[0], closest[1], closest[2] };
                            }
                        }
                    }

                    face = (IFace2)face.GetNextFace();
                }
            }

            // 平面优先
            Console.WriteLine($"[FindFaceByRay] 起点({rx},{ry},{rz}) 方向({dx},{dy},{dz}) => " +
                $"平面命中={(bestPlanar != null ? $"是(t={bestPlanarT:F4})" : "否")}, " +
                $"曲面命中={(bestCurved != null ? $"是(d={bestCurvedDist:F4})" : "否")}");
            if (bestPlanar != null) { hitFace = bestPlanar; hitPoint = bestPlanarPt; }
            else { hitFace = bestCurved; hitPoint = bestCurvedPt; }
        }

        /// <summary>
        /// 通过射线拾取方式选中一个面并进入草图编辑模式。
        /// 给定射线起点(rx, ry, rz)和方向(dx, dy, dz)，找到最近的面并选中它。
        /// 坐标单位：米。
        /// 
        /// 返回中包含 sketchTransform 信息，用于调用方将模型坐标转换为草图坐标。
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

            // ---- 遍历所有实体的面，通过射线求交找到目标面 ----
            // 规则：**平面命中优先于曲面**。用于挖孔/凹坑的草图几乎总是画在平面上；
            // 圆角后实体会多出大量曲面（圆角面），若让曲面与平面一起按距离竞争，
            // 当射线起点贴近目标面时极易误选到邻近的圆角曲面，导致在曲面上画草图、切除失败。
            // 因此只要射线命中任一平面就优先选最近的平面，仅当无任何平面命中时才退而选曲面。
            IFace2 bestFace;
            double[] bestHitPt;

            // 正向射线
            FindFaceByRay(bodies, rx, ry, rz, dx, dy, dz, out bestFace, out bestHitPt);

            // 未命中则反转方向重试（容错：LLM 常把射线方向搞反）
            if (bestFace == null)
            {
                Console.WriteLine($"[SwCommands] 射线({rx},{ry},{rz})方向({dx},{dy},{dz})未命中，尝试反转方向...");
                FindFaceByRay(bodies, rx, ry, rz, -dx, -dy, -dz, out bestFace, out bestHitPt);
                if (bestFace != null)
                    Console.WriteLine("[SwCommands] 反转方向后成功命中面！");
            }

            // A valid target face can contain an existing hole exactly under
            // the requested ray. Retry with small deterministic offsets in
            // the plane perpendicular to the ray, starting at 2 mm. This is
            // especially useful when creating several features on one face.
            if (bestFace == null)
            {
                double dLength = Math.Sqrt(dx * dx + dy * dy + dz * dz);
                if (dLength > 1e-12)
                {
                    double ndx = dx / dLength;
                    double ndy = dy / dLength;
                    double ndz = dz / dLength;
                    double ax = Math.Abs(ndx) < 0.9 ? 1.0 : 0.0;
                    double ay = Math.Abs(ndx) < 0.9 ? 0.0 : 1.0;
                    double az = 0.0;
                    double ux = ndy * az - ndz * ay;
                    double uy = ndz * ax - ndx * az;
                    double uz = ndx * ay - ndy * ax;
                    double uLength = Math.Sqrt(
                        ux * ux + uy * uy + uz * uz);
                    ux /= uLength; uy /= uLength; uz /= uLength;
                    double vx = ndy * uz - ndz * uy;
                    double vy = ndz * ux - ndx * uz;
                    double vz = ndx * uy - ndy * ux;
                    var offsets = new[]
                    {
                        new[] { ux, uy, uz },
                        new[] { -ux, -uy, -uz },
                        new[] { vx, vy, vz },
                        new[] { -vx, -vy, -vz },
                    };
                    foreach (double radius in new[] {
                        0.002, 0.005, 0.010, 0.020
                    })
                    {
                        foreach (double[] offset in offsets)
                        {
                            double orx = rx + radius * offset[0];
                            double ory = ry + radius * offset[1];
                            double orz = rz + radius * offset[2];
                            FindFaceByRay(
                                bodies,
                                orx, ory, orz,
                                dx, dy, dz,
                                out bestFace,
                                out bestHitPt);
                            if (bestFace == null)
                            {
                                FindFaceByRay(
                                    bodies,
                                    orx, ory, orz,
                                    -dx, -dy, -dz,
                                    out bestFace,
                                    out bestHitPt);
                            }
                            if (bestFace != null)
                            {
                                var offsetSurface = bestFace.IGetSurface();
                                if (offsetSurface == null
                                    || !offsetSurface.IsPlane())
                                {
                                    bestFace = null;
                                    bestHitPt = null;
                                }
                                else
                                {
                                    var plane = (double[])
                                        offsetSurface.PlaneParams;
                                    double alignment = Math.Abs(
                                        plane[0] * ndx
                                        + plane[1] * ndy
                                        + plane[2] * ndz);
                                    if (alignment < 0.70)
                                    {
                                        bestFace = null;
                                        bestHitPt = null;
                                    }
                                }
                            }
                            if (bestFace != null)
                            {
                                Console.WriteLine(
                                    "[SwCommands] 射线通过垂直偏移 " +
                                    $"{radius * 1000:F0}mm 后命中面");
                                break;
                            }
                        }
                        if (bestFace != null) break;
                    }
                }
            }

            if (bestFace == null)
                throw new InvalidOperationException(
                    $"未能通过射线选中实体面。射线起点({rx},{ry},{rz})，方向({dx},{dy},{dz})及其反方向均未命中。");

            return StartSketchOnFace(bestFace, bestHitPt);
        }

        /// <summary>
        /// 选中给定实体面并在其上新建草图；返回值与 SelectFaceByRayAndStartSketch 相同。
        /// </summary>
        private Dictionary<string, object> StartSketchOnFace(IFace2 bestFace, double[] bestHitPt)
        {
            // 输出选中面的详细信息
            var hitSurface = bestFace.IGetSurface();
            string faceType = hitSurface.IsPlane() ? "平面" : "曲面";
            if (hitSurface.IsPlane())
            {
                var pp = (double[])hitSurface.PlaneParams;
                Console.WriteLine($"[SwCommands] 射线命中实体面: {faceType}，法向量=({pp[0]:F3},{pp[1]:F3},{pp[2]:F3})，" +
                    $"面上点=({pp[3]:F4},{pp[4]:F4},{pp[5]:F4})，交点=({bestHitPt[0]:F4},{bestHitPt[1]:F4},{bestHitPt[2]:F4})");
            }

            // 选中该面：通过实体选中（IEntity.Select4 确保选中的是实体面而不是其他类型）
            var faceEntity = (IEntity)bestFace;
            bool selected = faceEntity.Select4(false, null);
            if (!selected)
                throw new InvalidOperationException("选中实体面失败");

            // 进入草图编辑模式
            ActiveDoc.SketchManager.InsertSketch(true);
            _sketchOnRefPlane = false;  // 标记：草图在实体面上，需要变换坐标
            CaptureActiveSketchName();  // 捕获刚新建的草图名，供切除时显式选中
            double[] faceNormal = new double[] { 0, 0, 0 };
            string faceKey = "";
            if (hitSurface.IsPlane())
            {
                var pp = (double[])hitSurface.PlaneParams;
                faceNormal = new double[] { pp[0], pp[1], pp[2] };
                faceKey = GetAxisFaceKey(
                    bestHitPt,
                    faceNormal,
                    GetBodyBoundingBox());
            }
            RegisterActiveSketch(faceKey);

            // 获取草图坐标变换信息
            var activeSketch = ActiveDoc.SketchManager.ActiveSketch;
            double[] xformData = null;
            if (activeSketch != null)
            {
                // ModelToSketchTransform: 将模型坐标转换为草图坐标
                var xform = activeSketch.ModelToSketchTransform;
                if (xform != null)
                {
                    xformData = (double[])xform.ArrayData;
                    Console.WriteLine($"[SwCommands] 草图变换矩阵 (ModelToSketch): " +
                        $"[{xformData[0]:F3},{xformData[1]:F3},{xformData[2]:F3}," +
                        $" {xformData[3]:F3},{xformData[4]:F3},{xformData[5]:F3}," +
                        $" {xformData[6]:F3},{xformData[7]:F3},{xformData[8]:F3}," +
                        $" {xformData[9]:F4},{xformData[10]:F4},{xformData[11]:F4}]");
                }
            }

            Console.WriteLine($"[SwCommands] 已通过射线选中实体{faceType}并进入草图编辑");

            var result = new Dictionary<string, object>
            {
                { "selected", true },
                { "faceType", faceType },
                { "hitPoint", new Dictionary<string, double> {
                    { "x", bestHitPt[0] }, { "y", bestHitPt[1] }, { "z", bestHitPt[2] }
                }},
                { "message", $"已选中{faceType}并进入草图编辑" },
                { "sketchName", _activeSketchName ?? "" },
                { "faceKey", faceKey },
                { "normal", new Dictionary<string, double> {
                    { "x", faceNormal[0] }, { "y", faceNormal[1] }, { "z", faceNormal[2] }
                }},
            };
            try
            {
                var persist = ActiveDoc.Extension.GetPersistReference3(bestFace) as byte[];
                if (persist != null)
                    result["persistRef"] = Convert.ToBase64String(persist);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"[SwCommands] 获取面持久引用失败（使用几何签名回退）: {ex.Message}");
            }

            // 返回草图变换矩阵，让调用方可以正确计算草图坐标
            if (xformData != null)
            {
                result["sketchTransform"] = xformData;
            }

            return result;
        }

        /// <summary>
        /// Select a planar face from the latest model observation by its
        /// deterministic enumeration index. The face itself supplies a point
        /// on material and its normal, so the caller does not guess a ray.
        /// </summary>
        /// exact=true（L1_v2）时直接选中该面，不再沿面法向发射线：
        /// 射线起点偏离面 5mm，薄壁、凹槽或相邻凸台会让射线先命中别的面。
        public Dictionary<string, object> SelectObservedFaceAndStartSketch(
            int faceIndex, bool exact = false)
        {
            EnsureActiveDoc();
            var partDoc = ActiveDoc as IPartDoc;
            if (partDoc == null)
                throw new InvalidOperationException("当前文档不是零件文档");
            if (faceIndex < 0)
                throw new ArgumentOutOfRangeException(
                    nameof(faceIndex), "faceIndex 不能为负数");

            var bodies = partDoc.GetBodies2(
                (int)swBodyType_e.swSolidBody, true) as object[];
            if (bodies == null || bodies.Length == 0)
                throw new InvalidOperationException("当前零件没有实体");

            IFace2 target = null;
            int currentIndex = 0;
            foreach (object bodyObj in bodies)
            {
                var body = bodyObj as IBody2;
                if (body == null) continue;
                var faces = body.GetFaces() as object[];
                if (faces == null) continue;
                foreach (object faceObj in faces)
                {
                    if (currentIndex == faceIndex)
                    {
                        target = faceObj as IFace2;
                        break;
                    }
                    currentIndex++;
                }
                if (target != null) break;
            }
            if (target == null)
                throw new InvalidOperationException(
                    $"观察面索引 {faceIndex} 不存在；请重新读取 observation.faces");

            var surface = target.IGetSurface();
            if (surface == null || !surface.IsPlane())
                throw new InvalidOperationException(
                    $"观察面索引 {faceIndex} 不是平面，不能创建二维草图");

            var box = target.GetBox() as double[];
            if (box == null || box.Length < 6)
                throw new InvalidOperationException(
                    $"无法读取观察面索引 {faceIndex} 的包围盒");
            double centerX = (box[0] + box[3]) / 2.0;
            double centerY = (box[1] + box[4]) / 2.0;
            double centerZ = (box[2] + box[5]) / 2.0;
            var materialPoint = target.GetClosestPointOn(
                centerX, centerY, centerZ) as double[];
            if (materialPoint == null || materialPoint.Length < 3)
                throw new InvalidOperationException(
                    $"无法定位观察面索引 {faceIndex} 上的实体点");

            if (exact)
            {
                Console.WriteLine($"[SwCommands] 直接选中 observation.faces[{faceIndex}]");
                return StartSketchOnFace(target, materialPoint);
            }

            var plane = (double[])surface.PlaneParams;
            const double offset = 0.005;
            Console.WriteLine(
                $"[SwCommands] 按 observation.faces[{faceIndex}] 选面，" +
                $"材料点=({materialPoint[0]:F4},{materialPoint[1]:F4}," +
                $"{materialPoint[2]:F4})");
            return SelectFaceByRayAndStartSketch(
                materialPoint[0] + plane[0] * offset,
                materialPoint[1] + plane[1] * offset,
                materialPoint[2] + plane[2] * offset,
                -plane[0],
                -plane[1],
                -plane[2]);
        }

        #endregion
    }
}
