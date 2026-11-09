import os
import unittest
from unittest import mock

from lastmile_dataflow.gpu import isolate_from_argv


class IsolateGpuTest(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
