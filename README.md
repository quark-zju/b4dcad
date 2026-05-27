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

我之前主要使用 Python + CadQuery + CQ-editor 做 3D 打印模型。CadQuery 的 OCCT 内核精度高、能力完整，但在多孔、阵列、重复结构和复杂布尔运算上经常偏慢。

Manifold CAD 的取舍更适合我的很多 3D 打印场景：它用 mesh/Manifold 的方式做 CSG，通常性能更好，精度损失对 FDM 打印模型可以接受。

但这并不意味着完全替代 CadQuery。实际建模时，有些 OCCT/CadQuery 功能仍然很方便，例如倒角、圆角和依赖拓扑选择器的局部操作；这些不是 Manifold 擅长的方向。

`b4dcad` 保留了类似 CadQuery 的链式/组合式写法，但底层使用 `manifold3d`，主要服务于个人模型脚本和 STL 生成。

因此本项目也提供了一定的 CadQuery 互通能力：可以先用 CadQuery 做少量拓扑敏感操作，再转到 b4dcad/Manifold 处理密集孔、阵列和重复布尔。

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
polygon(points)
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

## 项目边界

`b4dcad` 目前是个人工作流工具，不追求完整替代 CadQuery 或机械 CAD：

- 不包含 notebook 集成
- 不做 STEP 导入导出
- STL 是主要输出格式
- 网页预览用于快速检查和切换组件，不是完整编辑器
