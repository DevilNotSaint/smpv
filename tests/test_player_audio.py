import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


@unittest.skipUnless(importlib.util.find_spec('PySide6'), 'PySide6 is not installed')
class PlayerAudioTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from PySide6.QtWidgets import QApplication
        with patch.dict(os.environ, {'QT_QPA_PLATFORM': 'offscreen'}):
            cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        from mplayer.app import Player
        from mplayer.core import SystemInfo
        info = SystemInfo('Linux', 8, 16, 'GPU', '/usr/bin/mpv', 'wayland')
        with patch('mplayer.app.inspect_system', return_value=info), \
                patch('mplayer.app.load_settings', return_value={}):
            self.window = Player()
        self.window.timer.stop()
        self.socket_patch = patch.object(self.window, 'socket')
        self.socket = self.socket_patch.start()
        self.window.ready = True

    def tearDown(self):
        self.window.close()
        self.socket_patch.stop()
        self.window.deleteLater()
        self.app.processEvents()

    def deliver(self, message):
        self.socket.readAll.return_value = (json.dumps(message) + '\n').encode()
        self.window.read_ipc()

    def sent(self):
        return [json.loads(call.args[0])['command'] for call in self.socket.write.call_args_list]

    def test_file_loaded_attaches_folder_and_reapply_does_not_duplicate(self):
        with tempfile.TemporaryDirectory() as directory:
            audio = Path(directory) / 'Russian.ac3'
            audio.touch()
            self.window.audio_folder.setText(directory)
            self.deliver({'event': 'file-loaded'})
            request_id = self.window.request_id
            self.deliver({'request_id': request_id, 'error': 'success', 'data': []})
            self.assertIn(['audio-add', str(audio), 'auto', 'Russian.ac3'], self.sent())
            self.socket.write.reset_mock()
            self.window.sync_audio_folder()
            self.deliver({'request_id': self.window.request_id, 'error': 'success', 'data': [
                {'id': 2, 'type': 'audio', 'external': True, 'external-filename': str(audio)},
            ]})
            self.assertEqual(self.sent(), [['get_property', 'track-list']])

    def test_track_selector_follows_mpv_and_switches_track(self):
        self.deliver({'event': 'property-change', 'name': 'track-list', 'data': [
            {'id': 1, 'type': 'video'},
            {'id': 3, 'type': 'audio', 'lang': 'rus', 'title': 'Dub', 'external': True},
        ]})
        self.deliver({'event': 'property-change', 'name': 'aid', 'data': 3})
        self.assertEqual(self.window.audio_track.count(), 2)
        self.assertEqual(self.window.audio_track.currentData(), 3)
        self.assertIn('rus', self.window.audio_track.currentText())
        self.window.select_audio_track(0)
        self.assertEqual(self.sent()[-1], ['set_property', 'aid', 'no'])

    def test_old_folder_reply_is_ignored_after_end_of_video(self):
        self.window.media_loaded = True
        self.window.managed_audio = {'/old.ac3'}
        self.window.sync_audio_folder()
        request_id = self.window.request_id
        self.deliver({'event': 'end-file', 'reason': 'eof'})
        self.socket.write.reset_mock()
        self.deliver({'request_id': request_id, 'error': 'success', 'data': [
            {'id': 2, 'type': 'audio', 'external': True, 'external-filename': '/old.ac3'},
        ]})
        self.assertEqual(self.sent(), [])

    def test_missing_saved_folder_keeps_error_visible(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.audio_folder.setText(str(Path(directory) / 'missing'))
            self.deliver({'event': 'file-loaded'})
            self.assertIn('Не удалось прочитать папку аудио', self.window.status.text())
            self.assertEqual(self.sent(), [])

    def test_apply_saves_folder(self):
        with tempfile.TemporaryDirectory() as directory:
            self.window.audio_folder.setText(directory)
            with patch('mplayer.app.save_settings') as save:
                self.window.apply_settings()
            self.assertEqual(save.call_args.args[0]['audio_folder'], directory)
