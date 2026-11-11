"""One-off export of the robosuite PandaOmron factory model into a self-contained asset directory."""
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

import mujoco
import numpy as np

PIVOT = np.array([-.21, 0., 0.])


def factory_spec(robosuite_root, namespace="robot_0/"):
    """Use the real local factory, without depending on its task environments."""
    path = str(Path(robosuite_root).resolve())
    sys.path.insert(0, path)
    try:
        from robosuite.models.bases import robot_base_factory
        from robosuite.models.grippers import gripper_factory
        from robosuite.models.robots import create_robot
        robot = create_robot("PandaOmron", idn=0)
        robot.add_base(robot_base_factory(robot.default_base, idn=0))
        robot.update_joints()
        robot.update_actuators()
        robot.add_gripper(gripper_factory(robot.default_gripper["right"], idn="0_right"),
                          robot.eef_name["right"])
        spec = mujoco.MjSpec.from_string(robot.get_xml())
    finally:
        sys.path.remove(path)
    base = spec.body("mobilebase0_base")
    base.add_site(name="base_site", pos=PIVOT, size=[.005, 0, 0], rgba=[0, 0, 0, 0])
    base.add_camera(name="overview", pos=[-1.3, 0., 2.2],
                    quat=[.653288, .2705823, -.2705823, -.653288], fovy=60.)
    scene = mujoco.MjSpec()
    scene.worldbody.add_frame(pos=-PIVOT).attach_body(spec.body("robot0_base"), namespace, "")
    return scene


def export_factory(robosuite_root, output, namespace="robot_0/"):
    """Export referenced meshes plus XML. Repeated exports require a new directory."""
    spec = factory_spec(robosuite_root, namespace)
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    root = ET.fromstring(spec.to_xml())
    for asset in root.find("asset"):
        source = asset.get("file")
        if source:
            source = Path(source)
            target = Path("meshes") / (asset.get("name").replace("/", "_") + source.suffix)
            (output / target).parent.mkdir(exist_ok=True)
            shutil.copyfile(source, output / target)
            asset.set("file", str(target))
    path = output / "model.xml"
    path.write_text(ET.tostring(root, encoding="unicode"))
    shutil.copyfile(Path(robosuite_root) / "LICENSE", output / "LICENSE")
    return path
