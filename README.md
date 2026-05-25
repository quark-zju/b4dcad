# b4dcad

`b4dcad` 是我个人用于 3D 打印参数化建模的 Python CAD 小工具。

它的目标不是做通用 CAD 软件，而是支持一种直接、脚本化、适合程序员写模型的工作流：

- 用普通 `.py` 文件定义模型
- 用 Python 表达尺寸、循环、组合和参数
- 快速导出 STL 给切片软件
- 用本地网页预览模型
- 不依赖 IPython、Jupyter Notebook 或 notebook widget

## 动机

我之前主要使用 Python + CadQuery + CQ-editor 做 3D 打印模型。CadQuery 的 OCCT 内核精度高、能力完整，但在多孔、阵列、重复结构和复杂布尔运算上经常偏慢。

Manifold CAD 的取舍更适合我的很多 3D 打印场景：它用 mesh/Manifold 的方式做 CSG，通常性能更好，精度损失对 FDM 打印模型可以接受。

`b4dcad` 保留了类似 CadQuery 的链式/组合式写法，但底层使用 `manifold3d`，主要服务于个人模型脚本和 STL 生成。

## 来源

代码源自 [wrongbad/badcad](https://github.com/wrongbad/badcad)。

原项目偏向 IPython Notebook 使用；这个版本改成普通 Python 脚本工作流，并移除了 notebook 相关逻辑。

## 安装

在项目目录中安装为可编辑包：

```bash
pip install -e .
```

核心依赖：

- `manifold3d`
- `numpy`

可选：如果使用 `text()` 或 SVG 相关能力，可能还需要：

```bash
pip install pycairo svgelements
```

## 写模型

创建一个 Python 文件，例如 `part.py`：

```py
from b4dcad import *


body = cube(40, 20, 4, center=True)

holes = None
for x in (-12, 0, 12):
    hole = cylinder(h=8, r=2.2, center=True).move(x=x)
    holes = hole if holes is None else holes + hole

bracket = body - holes
```

CLI 会读取脚本中所有公开的 `Solid` 变量：

- 变量名不能以 `_` 开头
- 变量值必须是 `b4dcad.Solid`
- 多个变量会作为多个组件处理

如果使用 `--object NAME`，也可以显式导出或预览某个变量/函数；函数会被调用，返回值必须是 `Solid`。如果是 `Shape`，请先 `extrude()`。

多组件示例：

```py
from b4dcad import *

plate = cube(40, 20, 4, center=True)
pin = cylinder(h=12, r=3, center=True).align(zmin=0)
_debug = sphere(r=5)  # 以下划线开头，不会被 CLI 导出或预览
```

## 导出 STL

```bash
b4dcad stl part.py ./stl
```

也可以使用短命令：

```bash
b4dcad-stl part.py ./stl
```

输出文件名格式：

```text
输出目录/源文件 basename-组件名.stl
```

例如 `part.py` 中有 `plate` 和 `pin`：

```text
stl/part-plate.stl
stl/part-pin.stl
```

指定单个导出的对象名：

```bash
b4dcad stl part.py ./stl --object plate
```

也可以在 Python 中直接导出：

```py
from b4dcad import *

model = sphere(r=10) - cylinder(h=30, r=3, center=True)
model.stl("part.stl")
```

## 本地预览

启动预览服务：

```bash
b4dcad preview part.py
```

默认地址：

```text
http://127.0.0.1:8765/
```

也可以指定端口：

```bash
b4dcad preview part.py --port 9000
```

如果脚本里有多个公开 `Solid` 变量，网页顶部会显示组件导航，可以按变量名切换当前预览的模型。

预览服务会在后端用简单轮询监控原始 Python 文件。文件修改后，后端控制台会打印检测日志；如果浏览器页面正在打开，会通过 SSE (`EventSource`) 收到变更事件并自动刷新。

后端轮询不依赖浏览器页面是否打开；即使关闭页面，文件变化仍会被检测到。

预览时同时写出 STL：

```bash
b4dcad preview part.py --write-stl ./stl
```

给了 `--write-stl` 后，启动预览时会先导出一次；之后文件变化也会重新导出。

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
part.scale(x=2, y=1, z=1)
part.align(x=0, zmin=0)
part.align_to(other, ">X :<Y -Z", dz=1)
```

`align_to()` 使用 bbox 面选择器：

- `>X` / `<X` / `>Y` / `<Y` / `>Z` / `<Z`：同侧面对齐
- `:>X`：把当前对象的 `<X` 贴到目标对象的 `>X`
- `-X` / `-Y` / `-Z`：按该轴居中
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

后续可以继续补强的方向包括更好的错误展示、参数面板，以及更接近 CQ-editor 的模型浏览体验。
