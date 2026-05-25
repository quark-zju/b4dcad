from b4dcad import *

_base = cube(48, 28, 4, center=True)

_holes = None
for x in (-16, 0, 16):
    _hole = cylinder(h=8, r=2.4, center=True).move(x=x)
    _holes = _hole if _holes is None else _holes + _hole

mounting_plate = (_base - _holes).align(zmin=0)
show_assembly = mounting_plate

_post = cylinder(h=18, r=4, center=True).align(zmin=0)
_post_cap = sphere(r=4).scale(z=0.35).align_to(_post, ":>Z")
support_post = _post + _post_cap
show_assembly += support_post.align_to(mounting_plate, "-X -Y :>Z")

_slot_profile = square(30, 8, center=True).offset(2, "round")
rounded_slot = _slot_profile.extrude(3).align(zmin=0)
show_assembly += rounded_slot.align_to(mounting_plate, "-X :>Y :>Z")

_debug_marker = sphere(r=3)
