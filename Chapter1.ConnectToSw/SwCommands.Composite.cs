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
        #region 组合操作

        /// <summary>
        /// 组合操作：创建一个长方体。
        /// 新建零件 -> 选前视基准面 -> 画矩形 -> 拉伸。
        /// length/width 为矩形的长和宽，height 为拉伸高度，单位：米。
        /// </summary>
        public void CreateBox(double length, double width, double height)
        {
            if (length <= 0) throw new ArgumentOutOfRangeException(nameof(length), "长度必须为正数");
            if (width <= 0) throw new ArgumentOutOfRangeException(nameof(width), "宽度必须为正数");
            if (height <= 0) throw new ArgumentOutOfRangeException(nameof(height), "高度必须为正数");

            NewPart();
            SelectPlaneAndStartSketch(0); // 前视基准面
            SketchRectangle(0, 0, length, width);
            Extrusion(height);

            Console.WriteLine($"[SwCommands] 长方体创建成功 ({length} x {width} x {height} 米)");
        }

        #endregion
    }
}
