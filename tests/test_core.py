import unittest
from mplayer.core import DropGuard, SystemInfo, choose_profile


class PolicyTests(unittest.TestCase):
    def info(self, gpu='AMD Radeon', ram=16):
        return SystemInfo('Linux', 8, ram, gpu, '/usr/bin/mpv', 'wayland')

    def test_unknown_gpu_is_conservative(self):
        self.assertEqual(choose_profile(self.info('Не определена'))[0], 'Экономичный')

    def test_large_target_and_high_fps_reduce_cost(self):
        self.assertEqual(choose_profile(self.info(), target_width=3840, target_height=2160)[0], 'Экономичный')
        self.assertEqual(choose_profile(self.info(), fps=60)[0], 'Экономичный')

    def test_normal_workload(self):
        self.assertEqual(choose_profile(self.info(), 1280, 720, 24)[0], 'Сбалансированный')

    def test_sustained_drops_only_after_warmup(self):
        guard = DropGuard()
        for count in [0, 10, 20, 30, 33, 36]:
            self.assertFalse(guard.sample(count))
        self.assertTrue(guard.sample(39))

    def test_counter_reset_and_isolated_spikes(self):
        guard = DropGuard()
        for count in [0, 0, 0, 0, 10, 10, 10, 0, 0]:
            self.assertFalse(guard.sample(count))
        guard.reset()
        self.assertFalse(guard.sample(500))


if __name__ == '__main__':
    unittest.main()
