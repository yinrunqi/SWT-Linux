using SolidWorks.Interop.sldworks; // 使用 solidworks 的命名空间
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.Linq;
using System.Text;
using System.Threading.Tasks;
using Xarial.XCad.SolidWorks;
using SolidWorks.Interop.swconst;
using System.IO;
using System.Windows;

namespace Chapter1.ConnectToSw
{
    internal class Program
    {


        // 这里写一个获取基准面函数
        // 可以写到外面的扩展方法里面
        public static IEnumerable<IFeature> GetRefPlane(IModelDoc2 doc)
        {
            var feat = doc.FirstFeature() as IFeature;
            while (feat != null)
            {
                var name = feat.GetTypeName2();
                // 如果名字是 "RefPlane"，就是基准面特征
                if (name == "RefPlane")
                {
                    yield return feat;
                }
                feat = feat.GetNextFeature() as IFeature;
            }
        }

        static void Main(string[] args)
        {
            //Console.WriteLine("Hello World!");
            //Console.ReadKey();

            var swProcess = Process.GetProcessesByName("SLDWORKS");

            if (!swProcess.Any())
            {
                Console.WriteLine("SolidWorks unopen");
                Console.ReadKey();

                //这里还可以让 sw 启动
            }
            else { 
                var swApp = SwApplicationFactory.FromProcess(swProcess.First());

                // 弹出一个消息框，上面写着 "Hello SolidWorks"
                // swApp.ShowMessageBox("Hello SolidWorks");

                // api1: 创建一个新的零件文档
                // swApp.Sw.NewPart();

                // 前面带不带 I 的区别
                // var part = swApp.Sw.INewPart();
                // 或者也可以
                //var part = swApp.Sw.NewPart() as IPartDoc;

                // api2: 新建打开文档

               // 获取默认模板路径
               var templatePart = swApp.Sw.GetUserPreferenceStringValue((int)swUserPreferenceStringValue_e.swDefaultTemplatePart);
                if (string.IsNullOrWhiteSpace(templatePart))
                {
                    Console.WriteLine("默认模板为空，尝试从模板目录查找...");

                    var dirs = swApp.Sw.GetUserPreferenceStringValue(
                        (int)swUserPreferenceStringValue_e.swFileLocationsDocumentTemplates);

                    var dirList = dirs.Split(';');

                    foreach (var dir in dirList)
                    {
                        if (!Directory.Exists(dir)) continue;

                        var files = Directory.GetFiles(dir, "*.prtdot");

                        if (files.Length > 0)
                        {
                            templatePart = files[0];
                            Console.WriteLine($"找到模板: {templatePart}");
                            break;
                        }
                    }
                }

               //  使用默认模板创建一个新的零件文档
               var doc = swApp.Sw.INewDocument2(templatePart, 0, 300d, 300d);

                //swApp.ShowMessageBox("Finish Creating New Document");

                // 如何不显示操作过程
                // 先关闭图形界面更新
                //var activeView = doc.ActiveView as IModelView;
                //activeView.EnableGraphicsUpdate = false;
                // 做好操作后再打开图形界面更新
                //activeView.EnableGraphicsUpdate = true;




                // api3: 新建草图特征，绘制直线
                // 获取当前文档
                // var doc = swApp.Sw.IActiveDoc2;

                if (doc.GetType() != (int)swDocumentTypes_e.swDocPART) { 
                    Console.WriteLine("当前文档不是零件");
                    return;
                }

                // 先选择一个基准面
                IFeature refFeat = GetRefPlane(doc).FirstOrDefault();

                // 选中这个基准面
                // 第一个参数 false: 是否加入之前的选择列表，加入就是多选
                // 第二个参数 int Mark
                refFeat.Select2(false, 0);

                // 在基准面上新建一个草图
                var skeMgr = doc.SketchManager;
                skeMgr.InsertSketch(true);
                // 或者直接 
                // doc.SketchManager.InsertSketch(true);

                // 这里可能有激活捕捉设置

                // 绘制一条直线
                // 这里 1 1 就是 x y 坐标，单位是米
                // skeMgr.CreateLine(0, 0, 0, 1, 1, 0);

                // 可以创建其他的草图元素，比如圆，矩形等，都是通过 SketchManager 来创建的
                // api4: 绘制一个中心矩形，并拉伸
                double width = 0.01;
                skeMgr.CreateCenterRectangle(0, 0, 0, width, width, 0);

                double height = 0.01;
                // 拉伸
                var featMgr = doc.FeatureManager;
                featMgr.FeatureExtrusion3(
                    Sd: true, // 是否是单向拉伸
                    Flip: false, // 是否翻转拉伸方向
                    Dir: false,
                    T1: (int)swEndConditions_e.swEndCondBlind, // 结束条件，盲孔
                    T2: (int)swEndConditions_e.swEndCondBlind, // 第二个方向的结束条件，盲孔
                    D1: height, // 拉伸深度
                    D2: 0,
                    Dchk1: false, // True to allow drafting in the first direction
                    Dchk2: false,
                    Ddir1: false,
                    Ddir2: false,
                    Dang1: 0,      // Draft angle in the first direction
                    Dang2: 0,
                    OffsetReverse1: false, // True to reverse the offset direction in the first direction
                    OffsetReverse2: false,
                    TranslateSurface1: false, // True to translate the surface in the first direction
                    TranslateSurface2: false,
                    Merge: true, // True to merge the result with the existing body
                    UseFeatScope: true, // 影响选择的实体还是影响所有实体
                    UseAutoSelect: true,
                    T0: (int)swStartConditions_e.swStartSketchPlane, // 起始条件，草图平面
                    StartOffset: 0,  // 起始偏移距离
                    FlipStartOffset: false
                );


                // api5: 获取选择的实体信息
                // var seleMgr = swApp.Sw.IActiveDoc2.ISelectionManager;
                var seleMgr = doc.ISelectionManager;
                var count = seleMgr.GetSelectedObjectCount2(-1); // -1 代表所有类型的选择
                if (count < 1)
                {
                    MessageBox.Show("没有选择任何实体");
                }

                //var data = new List<string>(count);

                for (int i = 1; i < count + 1; i++)
                {
                    var mark = seleMgr.GetSelectedObjectMark(i);
                    var type = seleMgr.GetSelectedObjectType3(i, mark);
                    var obj = seleMgr.GetSelectedObject6(i, mark);

                    // 选择的位置点
                    var selePosition = seleMgr.GetSelectionPoint2(i, mark) as double[];
                    string info;
                    if (selePosition != null && selePosition.Length >= 3)
                    {
                        info = $"Index {i}: 一个 {type} 类型的实体，标记是 {mark}，位置是 ({selePosition[0]}, {selePosition[1]}, {selePosition[2]})";
                    }
                    else
                    {
                        info = $"Index {i}: 一个 {type} 类型的实体，标记是 {mark}，没有可用的选择点";
                    }

                    Console.WriteLine(info);
                    //data.Add(info);

                    if (obj is IFeature feature)
                    {
                        Console.WriteLine($"这个实体的名称是 {feature.Name}，特征类型是 {feature.GetTypeName2()}");
                    }
                }

                // 缩放选择内容到合适大小
                doc.ViewZoomToSelection();

                // 退出草图
                skeMgr.InsertSketch(true);

            }
        }
    }
}
