## 标准骰子 Multi-turn Recipe

当前里程碑由系统账本指定，只执行该里程碑，禁止自行切换或重做已完成面。

- 主体：上视基准面绘制 20mm × 20mm 正方形，中间面拉伸 20mm。
- 圆角：必须用一次 `feature_fillet`，`edgeSelection="all"`，半径 0.0015m；
  禁止逐边圆角。
- 六面点数固定为：`+Y=1`、`-Y=6`、`+Z=2`、`-Z=5`、`+X=3`、`-X=4`。
- 每个点数面必须在一轮内使用：
  1. `select_face_and_sketch` 选中里程碑指定的语义面；
  2. 一次 `sketch_circles` 批量绘制该面全部圆；
  3. 一次 `feature_cut_extrusion`，深度 0.0015m，`endCondition="blind"`。
- 圆半径 0.0015m；点位相对面中心使用 0.004m 偏移。
- 不要根据 ICE 总数猜测哪个面完成；只相信账本和 Observer 的
  `faceKey -> sketch -> circles -> cut feature` 证据。
