# 上游建模功能导入

本次从 `wrongbad/badcad` 的 `8241020`（2026-10-13）移植功能，分叉基点为
`7f9e01d`。保持本项目的脚本、浏览器预览、CadQuery 互转和 FDM 工作流。
核心依赖为 `manifold3d>=3.5,<4`。

## 组合和放置

```python
from b4dcad import *

plate = cube(40.5, 25.0, 4.0, center=True)
holes = [cylinder(h=8.0, d=3.2, center=True).move(x=x) for x in (-12.0, 0.0, 12.0)]
drilled = difference(plate, holes)
angled_hole = cylinder(h=20.0, d=3.2).orient((1.0, 0.0, 1.0), origin=(0.0, 0.0, -2.0))
part = drilled - angled_hole
rotated = part.rotate(z=22.5, origin=(10.0, 0.0, 0.0))
section_shape = part.section(normal=(1.0, 0.0, 1.0), origin=(0.0, 0.0, 0.0))
```

`union()`、`difference()`、`intersection()` 接受多个参数，或者一个可迭代对象。
一次运算中的对象必须全是 `Solid` 或全是 `Shape`；返回对应维度。
`union()` 和 `intersection()` 无操作数时返回空 `Solid`；传入一个空集合也如此。
空实体参与交集会得到空结果。二维批量布尔可避免逐个布尔的开销；三维实际
性能取决于模型，未做加速倍数承诺。

`orient()` 将局部 +Z 朝向指定方向，将局部原点放到 `origin`。
`x_axis` 决定绕该方向的转角。`section()` 返回截面坐标系内的二维轮廓，
其 `x_axis` 是截面 X 轴方向提示，Y 轴为 normal × X。
`Solid.rotate()` 和 `Shape.rotate()` 都支持 `origin`；角度单位为度。

## 放样和扫掠

```python
from b4dcad import *

transition = loft(
    [square(12.5, 12.5, center=True), circle(d=18.0), circle(d=10.0)],
    [0.0, 8.5, 20.0],
)
tube_profile = circle(d=4.0) - circle(d=2.5)
bent_tube = sweep(tube_profile, [(0.0, 0.0, 0.0), (0.0, 0.0, 10.0), (5.0, 0.0, 20.0)])
```

- `loft(shapes, heights, match="arc")`：每层只支持一个无孔轮廓，高度必须有限且
  严格递增。各层顶点数量可不同；`match="shortest"` 使用另一种侧面配对方式。
  相似轮廓效果最好，配对方式不保证任意轮廓之间都无扭曲或自交。
- `sweep(shape, path, twist=0.0, closed=False, x_axis=None)`：支持带孔截面，
  沿三维折线使用最小旋转标架。曲线须自行采样，弯曲半径应大于截面尺度。
  不接受重复相邻点或原路折返；闭环路径不重复首点，`twist` 必须是 360° 的整数倍。
  不做通用自交检测。

## 实体处理和测量

| API | 用途与限制 |
| --- | --- |
| `part.prune(min_volume=0.001, largest_only=False)` | 按独立实体体积去碎体；会删除符合阈值的小零件，不是表面修复 |
| `part.simplify(tolerance=0.0)` | 在容差内简化网格；0 使用实体自身容差 |
| `part.project()` | 向 XY 平面投影，返回 `Shape` |
| `part.min_gap(other, search_length=None)` | 两实体的最小间隙；指定搜索距离时结果受搜索范围限制 |
| `part.contains(point)` | 七条射线投票判断内部点；表面上的点没有稳定的内外约定 |
| `part.smooth(n=4, min_sharp_angle=52.5, min_smoothness=0.0)` | 平滑细分，可能改变外形尺寸，不用于保持精确尺寸的圆角 |
| `part.minkowski_sum(other)` / `minkowski_difference(other)` | Minkowski 形状运算；成本随模型复杂度增加 |

```python
from b4dcad import square
from b4dcad.contrib import extrude_edges

rounded = extrude_edges(square(20.5, 12.0), height=5.5, radius=0.75, bottom=False)
chamfered = extrude_edges(square(20.5, 12.0), height=5.5, radius=0.75, style="chamfer")
```

`extrude_edges()` 通过轮廓内缩和 Minkowski 运算实现边缘造型，支持孔和多个轮廓。
半径过大时窄特征可能消失；它不是 CadQuery 的任意边选择圆角。

## 轮廓、网格和隐式场

- `dxf(filename=..., data=..., fn=64, spline_tol=0.005)` 支持 LINE、ARC、CIRCLE、
  LWPOLYLINE、控制点 SPLINE，嵌套闭环按奇偶规则成为孔。
  不支持的实体默认报错；可用 `on_unknown(kind, codes)` 返回点路径或 `None`。
  这不是完整的 DXF 文档/块/坐标系解析器。
- `mesh(verts, tris, merge=True)` 接受数组，要求三维顶点和整数三角形索引。
  默认合并重复顶点，支持整体反向绕序，非闭合流形报错。
- `level_set(sdf, bounds, edge_length, level=0.0, tolerance=-1.0)` 从隐式场生成实体。
  `sdf(x, y, z)` **内部为正**，与一些常见 SDF 公式相反；
  bounds 顺序是 `(xmin, ymin, zmin, xmax, ymax, zmax)`。采样精度由 `edge_length` 决定。

## 导出

```python
from b4dcad import cube, save_3mf

part = cube(10.5, 8.0, 3.0)
part.threemf("part.3mf", name="base", color="#6699CC")
save_3mf("assembly.3mf", [("base", part), ("cap", part.move(z=5.0), (0.8, 0.2, 0.1))])
blob = part.threemf()  # bytes
```

3MF 为 Core 格式，保留对象名称、可选颜色和毫米单位，不包含切片器的打印设置。
空实体跳过；顶点以 17 位有效数字写入。当前 CLI 仍通过 `stl` 子命令导出 STL，
3MF 使用上述 Python API。

可选 PNG 导出：安装 `pip install -e '.[render]'` 后调用
`part.png("part.png", elev=25.0, azim=-60.0, max_edge=0.5)`。
默认会细分长边减少 Matplotlib 深度排序瑕疵，`max_edge=0` 禁用细分。
这适合自动生成预览图，复杂遮挡仍可能有渲染瑕疵。

## 类型与兼容性

尺寸、半径、角度、高度、容差使用 `float`，细分数量和索引使用 `int`。
公开 `.pyi` 同步维护返回类型、同维度批量布尔泛型、可选参数和导出重载。

修复了 `genus/get_surface_area/get_volume/precision` 调用旧 Manifold 方法的问题，
以及 `hull_points()` 错误返回 `Shape` 的问题。`split()` 接受 `Solid`，并继续
兼容此前接受的原生 `Manifold`。

验证命令：

```sh
python -m unittest discover -s tests
basedpyright --pythonversion 3.10 --level error tests/typing_modeling.py
```

静态检查需安装 `basedpyright` 和 `typing_extensions`。PNG 测试在没有可选
Matplotlib 时跳过。上游 Notebook、打包元数据替换和 CNC 雕刻 G-code API
重构没有导入；后者会改变现有接口，也与此次建模工作流无直接关系。
