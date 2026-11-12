import os
import unittest
from unittest import mock

from lastmile_dataflow.gpu import select_devices, bind_device, bind_cpu_renderer, egl_device_map
from lastmile_dataflow.station_map.rl.devices import isolate_from_argv


class IsolateGpuTest(unittest.TestCase):
    def setUp(self):
        query = mock.patch("lastmile_dataflow.gpu.egl_device_map", return_value={gpu: 7 - gpu for gpu in range(8)})
        query.start()
        self.addCleanup(query.stop)

    def test_exposes_only_requested_physical_card(self):
        with mock.patch.dict(os.environ, clear=True):
            argv = ["train", "--gpu", "4", "--seed", "1"]
            self.assertEqual(isolate_from_argv(argv), "4")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "4")
            self.assertEqual(argv, ["train", "--gpu", "0", "--seed", "1"])

    def test_index_is_relative_to_existing_visible_devices(self):
        with mock.patch.dict(os.environ, {"CUDA_VISIBLE_DEVICES": "7,6"}, clear=True):
            argv = ["train", "--gpu=1"]
            self.assertEqual(isolate_from_argv(argv), "6")
            self.assertEqual(os.environ["CUDA_VISIBLE_DEVICES"], "6")
            self.assertEqual(argv, ["train", "--gpu", "0"])

    def test_help_path_without_gpu_is_untouched(self):
        with mock.patch.dict(os.environ, clear=True):
            self.assertIsNone(isolate_from_argv(["train", "--help"]))
            self.assertNotIn("CUDA_VISIBLE_DEVICES", os.environ)


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
            mock.patch("lastmile_dataflow.gpu.subprocess.run") as run,
        ):
            run.return_value.stdout = '{"0": 7, "1": 6}'
            self.assertEqual(egl_device_map(), {0: 7, 1: 6})
            self.assertNotIn("CUDA_VISIBLE_DEVICES", run.call_args.kwargs["env"])
            self.assertEqual(run.call_args.kwargs["env"]["CUDA_DEVICE_ORDER"], "PCI_BUS_ID")

    def test_disabled_rendering_does_not_require_egl(self):
        with (
            mock.patch.dict(os.environ, {"MUJOCO_GL": "disable"}, clear=True),
            mock.patch("lastmile_dataflow.gpu.egl_device_map") as query,
        ):
            select_devices([0])
            bind_device(0)
            query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
