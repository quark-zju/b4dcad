# b4dcad

`b4dcad` 是我个人用于 3D 打印参数化建模的 Python 小工具。底层是 Manifold 内核，写法上类似 CadQuery 的链式风格。用普通 `.py` 文件定义模型，跑命令导出 STL 或在浏览器里预览。

## 安装

```bash
pip install -e .
```

核心依赖：`manifold3d`、`numpy`。

如果想在脚本里混用 CadQuery 的倒角/圆角：

```bash
pip install -e ".[cadquery]"
```

如果用 `text()` 或 SVG 功能，还需要 `pycairo` 和 `svgelements`。

## 写模型

创建一个 `.py` 文件，`from b4dcad import *`，用链式调用定义 Solid 变量：

```py
from b4dcad import *

base = cube(48, 28, 4, center=True)

holes = None
for x in (-16, 0, 16):
    hole = cylinder(h=8, r=2.4, center=True).move(x=x)
    holes = hole if holes is None else holes + hole

mounting_plate = (base - holes).align(zmin=0)
support_post = cylinder(h=18, r=4, center=True).align(zmin=0)
rounded_slot = square(30, 8, center=True).offset(2, "round").extrude(3)
```

脚本里所有不以 `_` 开头的 Solid 变量会被 CLI 发现。多个变量当作多个组件导出。变量名以 `show` 开头时只在网页预览中显示，不导出 STL，适合放装配预览。

完整示例见 [examples/multi_part.py](examples/multi_part.py)。

### 混用 CadQuery

可以用 CadQuery 做倒角、圆角等操作，再交给 b4dcad 处理密集孔和阵列：

```py
import cadquery as cq
import b4dcad as b4d

cq_part = cq.Workplane("XY").box(40, 20, 4).edges("|Z").fillet(2)
b4d_part = b4d.from_cq(cq_part, tolerance=0.05)
```

CLI 可以直接识别脚本中的 CadQuery `Workplane` 变量，无需手动转换。

## 导出 STL

```bash
b4dcad stl path/to/model.py ./output_dir
```

输出文件为 `输出目录/源文件名-组件名.stl`。也可以直接在 Python 里调用：

```py
model.stl("part.stl")
```

## 本地预览

```bash
b4dcad preview path/to/model.py
```

浏览器打开 `http://127.0.0.1:8765/`。有多个组件时页面顶部可切换。支持实体/线框/半透明显示。

保存 `.py` 文件后浏览器会自动刷新。

在 `b4dcad preview` 或 `b4dcad stl` 执行的主模型脚本中，
`b4dcad.is_rendering()` 返回 `True`；普通运行或被其他脚本 import 时返回 `False`。
被主模型脚本 import 的模块调用它也会返回 `False`。

## 动机

我一直用 Python + CadQuery + CQ-editor 做 3D 打印模型。OCCT 内核精度出色，但遇到多孔、阵列、重复结构或复杂布尔时很容易卡——而这些恰恰是 3D 打印零件里的家常便饭。

Manifold 用 mesh 做 CSG，性能好得多，精度对 FDM 打印完全够用。所以我把建模中那些"蛮力"部分——打孔、阵列、重复布尔——交给 Manifold，需要精确拓扑操作（比如倒角、圆角、面选择）时仍然回头用 CadQuery。

`b4dcad` 本质就是给 Manifold 包了一层 CadQuery 风格的链式写法，让习惯 CadQuery 的人可以直接上手，同时又能跟 CadQuery 互转：先用 CadQuery 做完倒角圆角，转过来跑密集孔和阵列。

## 来源

代码源自 [wrongbad/badcad](https://github.com/wrongbad/badcad)。原项目偏向 IPython Notebook 使用；这个版本改成普通 Python 脚本工作流，并移除了 notebook 相关逻辑。

## 常用 API

布尔运算：

```py
a + b      # union
a - b      # difference
a & b      # intersection
```

基础实体：

```py
cube(x, y, z, center=True)
cylinder(h=10, r=3, center=True)
sphere(r=5)
circle(r=5)
square(x, y, center=True)
polygon(points, relative=False)
```

变换和对齐：

```py
part.move(x=10, y=0, z=2)
part.rotate(x=90, z=45)
part.rotate_axis("Z", 90)
part.align(x=0, zmin=0)
part.align_to(other, ">X :<Y -Z", dz=1)
```

`align_to()` 用 bbox 面选择器：

- `>X` / `<X`：把当前对象贴到目标的对应面
- `:>X`：把当前对象的 `<X` 贴到目标对象的 `>X`
- `-X`：按该轴居中
- `dx` / `dy` / `dz`：对齐后的额外偏移

2D 到 3D：

```py
profile = square(20, 10, center=True).offset(1, "round")
solid = profile.extrude(4)
```

螺纹：

```py
bolt = threads(d=8, h=16, pitch=1)
```

FDM 悬垂分析和削料：

```py
report = part.detect_overhangs(angle=45)
print(report.area, report.triangle_indices)

trimmed = part.fix_horizontal_overhangs(angle=45, mode="cut")
supported = part.fix_horizontal_overhangs(
    angle=45,
    mode="add",
    directions="<X >X",
)
```

角度从竖直方向量起：竖直墙是 0°，水平底面是 90°。检测仅依据局部
面法线，不识别桥接或下方支撑。修形只处理沿 `+Z` 打印、没有洞且至少
有一条连接边的水平悬垂区域；带洞面和所有边都连接的面保持不变。斜面
由连接边按目标角度向外扩张生成，不依赖输入面的三角剖分；线段端点
用内接 32 边形近似圆锥，径向误差不超过约 0.49%，侧面角度不超过目标值。
凹面、旋转和曲线边界也可处理，顶部不要求等高。
`cut` 削除区域上方距离斜面，`add` 用距离斜面的补集向下加料。
切除边界使用模型尺度的百万分之一作为重叠容差，避免布尔运算留下细碎壳体。
这仍是局部几何修形：不检查结果是否断开、连接侧下方高度是否足够，
也不沿凹区域内部追踪支承路径；不会保证任意模型都无需支撑。

`directions` 默认为 `"auto"`；也可以用 `"<X >X"` 这样的选择器限制连接
方向。旧的 `part.trim_overhangs(angle=45)` 仍可作为 `mode="cut"` 的快捷方式，
其中 `layer_height` 参数仅为兼容旧代码而保留，不再参与计算。
