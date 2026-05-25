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


def build():
    body = cube(40, 20, 4, center=True)

    holes = None
    for x in (-12, 0, 12):
        hole = cylinder(h=8, r=2.2, center=True).move(x=x)
        holes = hole if holes is None else holes + hole

    return body - holes
```

CLI 会读取脚本中的以下任意一个名字：

- `model`
- `solid`
- `part`
- `shape`
- `build()`

如果对象是函数，会调用它并使用返回值。STL 导出需要返回 `Solid`；如果是 `Shape`，请先 `extrude()`。

## 导出 STL

```bash
b4dcad stl part.py part.stl
```

也可以使用短命令：

```bash
b4dcad-stl part.py part.stl
```

指定导出的对象名：

```bash
b4dcad stl part.py part.stl --object build
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

预览页会在浏览器里加载 `/model.stl`。修改脚本后刷新页面即可重新运行模型脚本并显示新的 STL。

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
part.scale(x=2, y=1, z=1)
part.align(x=0, zmin=0)
```

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
- 网页预览用于快速检查模型，不是完整编辑器

后续可以继续补强的方向包括文件监听自动刷新、更好的错误展示、参数面板，以及更接近 CQ-editor 的模型浏览体验。
