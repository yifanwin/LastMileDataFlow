import os
import unittest
from unittest import mock

from lastmile_dataflow.utils.gpu import select_devices, bind_device, bind_cpu_renderer, egl_device_map


class RenderDeviceTests(unittest.TestCase):
    def test_override_survives_cpu_and_planning_worker_binding(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            select_devices([0, 1], [7, 6])
            bind_cpu_renderer()
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "")
            self.assertEqual(os.environ["MUJOCO_EGL_DEVICE_ID"], "7")
            bind_device(1)
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "1")
            self.assertEqual(os.environ["MUJOCO_EGL_DEVICE_ID"], "6")

    def test_discovery_ignores_inherited_cuda_visibility(self):
        with (
            mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": ""}),
            mock.patch("lastmile_dataflow.utils.gpu.subprocess.run") as run,
        ):
            run.return_value.stdout = '{"0": 7, "1": 6}'
            self.assertEqual(egl_device_map(), {0: 7, 1: 6})
            self.assertNotIn("CUDA_VISIBLE_DEVICES", run.call_args.kwargs["env"])
            self.assertEqual(run.call_args.kwargs["env"]["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")

    def test_disabled_rendering_does_not_require_egl(self):
        with (
            mock.patch.dict(os.environ, {"MUJOCO_GL": "disable"}, clear=True),
            mock.patch("lastmile_dataflow.utils.gpu.egl_device_map") as query,
        ):
            select_devices([0])
            bind_device(0)
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
