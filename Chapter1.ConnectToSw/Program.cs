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
                skeMgr.CreateLine(0, 0, 0, 1, 1, 0);

                // 可以创建其他的草图元素，比如圆，矩形等，都是通过 SketchManager 来创建的

                // 缩放选择内容到合适大小
                doc.ViewZoomToSelection();

                // 退出草图
                skeMgr.InsertSketch(true);


            }
        }
    }
}
