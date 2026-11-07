# b4dcad

3D 参数化建模 Python 库，主要用于 3D 打印。

## 安装

```bash
make dev-install
```

## 写模型

创建 `a.py`：

```py
from b4dcad import cube, cylinder

x, y, z = 50, 40, 30
t = 4

_outer = cube(x, y, z)
_inner = cube(x - t, y - t, z - t).align_to(_outer, "-X -Y >Z")
box = _outer - _inner

_plate = cube(x, y, t).align_to(box, ":>Z")
_lip = cube(x - t - 0.4, y - t - 0.4, t).align_to(_plate, "-X -Y :<Z")
_knob_cut = cylinder(r=t+0.2, h=t*2).align_to(_lip, "-X -Y <Z")

lid = _plate + _lip - _knob_cut
knob = cylinder(r=t, h=t*3).align_to(_lip, "-X -Y <Z")

show_assembly = box.move(z=-5) + lid + knob
```

使用 `b4dcad previw a.py` 打开浏览器预览。代码中全局变量 `box`, `lid`, `knob` 在浏览器显示为可切换的多元件。修改代码，重新保存后，浏览器预览会自动刷新。

## 导出 STL

使用 `b4dcad stl a.py /tmp/stl` 生成可打印的 stl 文件。`show` 开头的展示用元件会被自动跳过。

### 混用 CadQuery

b4dcad 使用 Manifold 内核，但也可混用 CadQuery 做倒角、圆角等操作：

```py
import cadquery as cq
import b4dcad as b4d

cq_part = cq.Workplane("XY").box(40, 20, 4).edges("|Z").fillet(2)
b4d_part = b4d.from_cq(cq_part, tolerance=0.05)
```

## 为何会有这个项目

我之前用 Python + CadQuery + CQ-editor 做 3D 打印模型。CadQuery 使用的 OCCT 内核强大且精度出色，但遇到多孔、阵列、重复结构或复杂布尔时性能不佳，导致 3D 模型开发中有些场景不好用。

Manifold 牺牲了一些精度，但性能好许多。Manifold 生态丰富，其中 [`badcad`](https://github.com/wrongbad/badcad) 看起来很像 CadQuery 中我比较喜欢的链式写法，仅缺乏像 CQ-editor 那样的实时预览功能。`b4dcad` 补全了实时预览，也迁移了我在用 CadQuery 中的一些顺手小函数（如 `align_to`），另外对 3D 打印的多元件场景做了适配。

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
part.align_to(other, ">X :<Y -Z", dz=1)
```

`align_to()` 的面选择器：

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
