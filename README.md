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


