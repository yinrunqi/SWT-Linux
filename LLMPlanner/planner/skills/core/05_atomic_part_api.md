---
name: atomic-part-api
description: L0 完整零件域原子 HTTP API（与 workflow 工具对照）
triggers: []
applies_to: [text, three_view]
---
## L0 完整 Atomic API

你正在生成一次性的 **L0 原子操作计划**。只能输出 `SwOperation` 的
`operation` 加 `params`，按顺序由 HTTP 服务执行；每个长度用米，角度用度。
这不是 L1 workflow：不要调用 `extrude`、`hole`、`cut`、`sweep` 等 workflow
工具名，也没有自动回滚、参数表、语义边选择或执行后观察再决策。

除已有的 `new_part`、草图基本图元、拉伸/切除/旋转、圆角/倒角、阵列、查询和
尺寸编辑外，以下 C# 零件端点同样可用。每条均是一个原子 HTTP 请求；需要的
草图、路径、截面和特征必须由前面的步骤自行建立。

### 文档、基准和草图

- `open_part {partPath}`；`delete_feature {featureName}`；`export_step {stepPath}`；
  `create_box {length,width,height}`。
- `ref_plane_offset {reference,distance,flip?}` 创建偏置基准面；`reference` 是
  服务返回的基准面引用，`distance` 为米。`ref_axis_planes {first,second}` 由两平面
  创建参考轴。`select_ref_plane_and_sketch {planeName}` 在已有基准面进入草图。
- `sketch_profile {segments,closed?}` 批量画路径段；每段必须采用 C# profile 的
  图元字段。`sketch_point {x,y,z?}` 画草图点。
- `sketch3d_path {points:[[x,y,z],...],spline?,cornerRadius?,cornerRadii?}` 创建 3D
  路径。`cornerRadii` 按内部转角顺序给米制半径；有它时不使用统一 `cornerRadius`。
- `close_sketch_named {}` 关闭当前命名草图。普通 2D 草图仍可用 `close_sketch {}`。

### 特征

- `feature_mirror {plane,features?,mirrorBody?}`：镜像特征列表，或
  `mirrorBody:true` 镜像实体。
- `feature_until {until,flipDirection?,cut?}`：当前草图拉伸/切除到指定面。
- `feature_rib {thickness,bothSides?,flip?}`：当前开放草图生成筋。
- `feature_sweep {cut?,circularDiameter?,innerDiameter?}`：先关闭路径草图、再建立并保留
  截面草图，然后执行扫描。`feature_loft {sectionCount?,cut?,guideCount?,startTangent?,
  endTangent?,tangentLength?,reverseStart?,reverseEnd?}`：先依次建立多个截面草图。
- `feature_shell {thickness,openFaces?,outward?}`；`feature_draft {angleDeg,neutral,
  faces,flip?}`；`feature_sheet_metal_base {thickness}`；
  `feature_cosmetic_thread {face,diameter,depth,standard?,standardType?,size?,note?}`：
  `face` 必须是观察到的圆柱面；`diameter` 和 `depth` 为米。若使用安装的线程规格，
  传 `standard=0`、`standardType` 与 `size`（例如 `"Machine Threads"` /
  `"#8-32"`）；空标准仅适用于 SolidWorks 已接受的自定义线程配置。

### 查询

- `model_observation {maxFaces?}` 返回特征、面、体积和包围盒；仅供计划末尾验证，
  不能据此继续生成第二轮计划。
- `sketch_stats {}`、`body_volume {}`、`feature_tree {}`、`body_bounding_box {}`
  可检查执行结果；`select_face_and_sketch` 和 `select_observed_face_and_sketch` 仍可在
  后续原子步骤中选择面。

### 原子计划纪律

1. 先 `new_part`，只调用一次；每个特征都显式写出打开草图、图元和 feature 步骤。
2. 只能引用本计划中已经创建的特征/基准面/草图；不要假定 workflow 会自动选择面、
   修正坐标、组合 profile、重试或回滚。
3. 最后一项可使用 `model_observation` 或 `body_bounding_box` 验证；不要输出任何 L1
   tool-call 格式。
