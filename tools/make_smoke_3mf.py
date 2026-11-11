"""Write a small PrusaSlicer project (two parts, painting, per-object settings) for smoke tests.

    python tools/make_smoke_3mf.py out.3mf
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "tests"))

from fixtures import Obj, Part, build_3mf  # noqa: E402

if __name__ == "__main__":
    build_3mf(sys.argv[1], [
        Obj("box", parts=[
            Part("body", extruder=2, paint={0: {"mmu_segmentation": "8"}}),
            Part("mod", offset=(2, 2, 2), scale=2, volume_type="ParameterModifier",
                 settings={"fill_density": "50%"}),
        ], settings={"perimeters": "4"}),
    ])
