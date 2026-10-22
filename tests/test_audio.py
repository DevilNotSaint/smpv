from pathlib import Path
import tempfile
import unittest

from mplayer.core import audio_files, audio_folder_commands


class AudioFolderTests(unittest.TestCase):
    def test_scan_audio_only_without_recursion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for name in ['Русская озвучка.MKA', 'English.ac3', 'notes.txt', 'video.mkv']:
                (root / name).touch()
            (root / 'nested').mkdir()
            (root / 'nested' / 'hidden.flac').touch()
            (root / 'folder.wav').mkdir()
            self.assertEqual(audio_files(directory),
                             [str(root / 'English.ac3'), str(root / 'Русская озвучка.MKA')])

    def test_empty_setting_and_missing_folder(self):
        self.assertEqual(audio_files('  '), [])
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(FileNotFoundError):
                audio_files(str(Path(directory) / 'missing'))

    def test_replace_folder_preserves_embedded_and_other_external_tracks(self):
        tracks = [
            {'id': 1, 'type': 'audio'},
            {'id': 2, 'type': 'audio', 'external': True, 'external-filename': '/old.ac3'},
            {'id': 3, 'type': 'audio', 'external': True, 'external-filename': '/manual.flac'},
        ]
        commands, managed = audio_folder_commands(['/new.mka'], tracks, {'/old.ac3'})
        self.assertEqual(commands, [['audio-remove', 2],
                                    ['audio-add', '/new.mka', 'auto', 'new.mka']])
        self.assertEqual(managed, {'/new.mka'})

    def test_reapply_does_not_duplicate_or_claim_existing_tracks(self):
        tracks = [{'id': 2, 'type': 'audio', 'external': True,
                   'external-filename': '/existing.mka'}]
        commands, managed = audio_folder_commands(['/existing.mka'], tracks, set())
        self.assertEqual(commands, [])
        self.assertEqual(managed, set())
        commands, managed = audio_folder_commands(['/existing.mka'], tracks, {'/existing.mka'})
        self.assertEqual(commands, [])
        self.assertEqual(managed, {'/existing.mka'})

    def test_clearing_folder_removes_managed_tracks(self):
        commands, managed = audio_folder_commands([], [
            {'id': 2, 'type': 'audio', 'external': True, 'external-filename': '/old.ac3'},
        ], {'/old.ac3'})
        self.assertEqual(commands, [['audio-remove', 2]])
        self.assertEqual(managed, set())
