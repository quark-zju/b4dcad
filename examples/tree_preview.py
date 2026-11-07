"""Run with: b4dcad preview examples/tree_preview.py, then open Tree."""

from b4dcad import box

foot = box(50, 24, 4)
left = box(4, 24, 24).move(z=4)
right = box(4, 24, 24).move(x=46, z=4)
uprights = left + right
cap = box(50, 24, 4).move(z=28)

# The anonymous foot + uprights intermediate is hidden; uprights stays expandable.
show_assembly = foot + uprights + cap
