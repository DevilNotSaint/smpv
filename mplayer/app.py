import json
from pathlib import Path
import sys
import tempfile
from dataclasses import asdict

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtNetwork import QLocalSocket
from PySide6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QSlider, QTextEdit, QVBoxLayout, QWidget)
from PySide6.QtCore import Qt
from .core import (PROFILES, DropGuard, choose_profile, inspect_system,
                   load_settings, save_settings, audio_files, audio_folder_commands)


class Player(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle('MPlayer Adaptive')
        self.resize(760, 670)
        self.info = inspect_system()
        self.guard = DropGuard()
        self.temp = tempfile.TemporaryDirectory(prefix='mplayer-')
        self.endpoint = str(Path(self.temp.name) / 'mpv.sock')
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
        self.proc.readyReadStandardOutput.connect(self.read_log)
        self.proc.finished.connect(self.finished)
        self.proc.errorOccurred.connect(lambda _: self.status.setText(self.proc.errorString()))
        self.socket = QLocalSocket(self)
        self.socket.connected.connect(self.connected)
        self.socket.readyRead.connect(self.read_ipc)
        self.buffer = b''
        self.pending = {}
        self.request_id = 0
        self.props = {}
        self.active_profile = 'Экономичный'
        self.ready = False
        self.last_media = ''
        self.media_signature = None
        self.media_loaded = False
        self.managed_audio = set()
        self.audio_generation = 0
        root = QWidget()
        self.setCentralWidget(root)
        layout = QVBoxLayout(root)
        title = QLabel('MPlayer Adaptive · mpv')
        title.setStyleSheet('font-size: 24px; font-weight: 600;')
        layout.addWidget(title)
        layout.addWidget(QLabel('Видео открывается в отдельном окне mpv — поддерживаются Wayland и X11.'))
        row = QHBoxLayout()
        self.source = QLineEdit()
        self.source.setPlaceholderText('Путь к видео или URL')
        row.addWidget(self.source)
        self.button(row, 'Файл…', self.browse)
        self.button(row, 'Открыть', self.open_media)
        layout.addLayout(row)
        form = QFormLayout()
        self.audio_folder = QLineEdit()
        self.audio_folder.setPlaceholderText('Необязательно: папка с внешней озвучкой')
        self.audio_folder.setClearButtonEnabled(True)
        audio_row = QHBoxLayout()
        audio_row.addWidget(self.audio_folder)
        self.button(audio_row, 'Папка…', self.browse_audio_folder)
        form.addRow('Папка аудио', audio_row)
        self.audio_track = QComboBox()
        self.audio_track.addItem('Без звука', 'no')
        self.audio_track.activated.connect(self.select_audio_track)
        form.addRow('Аудиодорожка', self.audio_track)
        self.mode = QComboBox()
        self.mode.addItems(['Автоматически', *PROFILES])
        form.addRow('Профиль', self.mode)
        self.scaler = QComboBox()
        self.scaler.addItems(['Из профиля', 'bilinear', 'bicubic', 'lanczos', 'ewa_lanczossharp'])
        form.addRow('Ручной алгоритм', self.scaler)
        self.shader = QLineEdit()
        self.shader.setPlaceholderText('Необязательно: GLSL/HOOK шейдер для mpv')
        shader_row = QHBoxLayout()
        shader_row.addWidget(self.shader)
        self.button(shader_row, 'Выбрать…', self.browse_shader)
        form.addRow('Шейдер', shader_row)
        self.hwdec = QComboBox()
        self.hwdec.addItems(['auto-safe', 'no', 'auto'])
        form.addRow('Декодирование', self.hwdec)
        self.smooth = QCheckBox('Сглаживание mpv (без RIFE / генерации кадров)')
        form.addRow(self.smooth)
        layout.addLayout(form)
        self.reason = QLabel()
        self.reason.setWordWrap(True)
        layout.addWidget(self.reason)
        row = QHBoxLayout()
        self.button(row, 'Применить', self.apply_settings)
        self.button(row, 'Пауза / продолжить', lambda: self.command(['cycle', 'pause']))
        self.button(row, '−10 сек', lambda: self.seek(-10))
        self.button(row, '+10 сек', lambda: self.seek(10))
        self.button(row, 'Полный экран', lambda: self.command(['cycle', 'fullscreen']))
        layout.addLayout(row)
        self.timeline = QSlider(Qt.Orientation.Horizontal)
        self.timeline.setRange(0, 1000)
        self.timeline.sliderReleased.connect(self.seek_absolute)
        layout.addWidget(self.timeline)
        self.position = QLabel('00:00 / 00:00')
        layout.addWidget(self.position)
        volume = QSlider(Qt.Orientation.Horizontal)
        volume.setRange(0, 100)
        volume.setValue(70)
        volume.valueChanged.connect(lambda v: self.command(['set_property', 'volume', v]))
        form = QFormLayout()
        form.addRow('Громкость', volume)
        layout.addLayout(form)
        self.status = QLabel('Готово' if self.info.mpv else 'mpv не найден. Установите пакет mpv.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.log = QTextEdit()
        self.log.setReadOnly(True)
        self.log.document().setMaximumBlockCount(300)
        self.log.setPlainText(json.dumps(asdict(self.info), ensure_ascii=False, indent=2))
        layout.addWidget(self.log)
        saved = load_settings()
        for key, widget in [('mode', self.mode), ('scaler', self.scaler), ('hwdec', self.hwdec)]:
            if saved.get(key) in [widget.itemText(i) for i in range(widget.count())]:
                widget.setCurrentText(saved[key])
        self.shader.setText(str(saved.get('shader', '')))
        self.audio_folder.setText(str(saved.get('audio_folder', '')))
        self.smooth.setChecked(saved.get('smooth') is True)
        self.mode.currentTextChanged.connect(self.mode_changed)
        self.mode_changed()
        self.timer = QTimer(self)
        self.timer.setInterval(1000)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def button(self, layout, text, action):
        button = QPushButton(text)
        button.clicked.connect(action)
        layout.addWidget(button)

    def mode_changed(self):
        manual = self.mode.currentText() != 'Автоматически'
        self.scaler.setEnabled(manual)
        self.shader.setEnabled(manual)
        self.select_profile()

    def select_profile(self):
        if self.mode.currentText() == 'Автоматически':
            screen = self.screen().size()
            p, reason = choose_profile(self.info, self.props.get('width') or 0,
                self.props.get('height') or 0, self.props.get('container-fps') or 0,
                screen.width(), screen.height())
        else:
            p, reason = self.mode.currentText(), 'Ручная настройка; автоматическое снижение отключено.'
        self.active_profile = p
        self.reason.setText(f'{p}: {reason}')

    def browse(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Открыть видео')
        if path:
            self.source.setText(path)
            self.open_media()

    def browse_shader(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Шейдер mpv', filter='Shaders (*.glsl *.hook);;Все файлы (*)')
        if path:
            self.shader.setText(path)

    def browse_audio_folder(self):
        path = QFileDialog.getExistingDirectory(self, 'Папка с аудиодорожками',
                                               self.audio_folder.text())
        if path:
            self.audio_folder.setText(path)
            self.apply_settings()

    def sync_audio_folder(self):
        self.audio_generation += 1
        try:
            files = audio_files(self.audio_folder.text())
        except OSError as exc:
            self.status.setText('Не удалось прочитать папку аудио: ' + str(exc))
            return
        if self.media_loaded:
            self.command(['get_property', 'track-list'],
                         ('audio-folder', self.audio_generation, files))

    def update_audio_tracks(self):
        self.audio_track.clear()
        self.audio_track.addItem('Без звука', 'no')
        for track in self.props.get('track-list') or []:
            if track.get('type') != 'audio':
                continue
            title = track.get('title') or Path(track.get('external-filename') or '').name
            label = f"{track['id']}: {title or 'Аудио'}"
            if track.get('lang'):
                label += ' · ' + track['lang']
            if track.get('external'):
                label += ' (внешняя)'
            self.audio_track.addItem(label, track['id'])
        index = self.audio_track.findData(self.props.get('aid', 'no'))
        self.audio_track.setCurrentIndex(max(0, index))

    def select_audio_track(self, index):
        self.command(['set_property', 'aid', self.audio_track.itemData(index)])

    def open_media(self):
        source = self.source.text().strip()
        if not source or not self.info.mpv:
            return
        if '://' not in source:
            source = str(Path(source).expanduser().resolve())
            if not Path(source).is_file():
                QMessageBox.warning(self, 'Видео', 'Файл не найден.')
                return
        self.last_media = source
        self.media_loaded = False
        self.audio_generation += 1
        self.media_signature = None
        self.guard.reset()
        if self.ready:
            self.command(['loadfile', source, 'replace'])
        elif self.proc.state() == QProcess.ProcessState.NotRunning:
            self.proc.start(self.info.mpv, ['--no-config', '--idle=yes', '--force-window=yes',
                '--vo=gpu-next', '--hwdec=' + self.hwdec.currentText(), '--volume=70',
                '--input-ipc-server=' + self.endpoint])

    def connected(self):
        self.ready = True
        self.buffer = b''
        for i, name in enumerate(['time-pos', 'duration', 'width', 'height', 'container-fps', 'pause', 'track-list', 'aid']):
            self.command(['observe_property', i, name])
        self.apply_settings()
        if self.last_media:
            self.command(['loadfile', self.last_media, 'replace'])

    def command(self, command, tag=None):
        if not self.ready:
            return
        self.request_id += 1
        self.pending[self.request_id] = tag
        self.socket.write((json.dumps({'command': command, 'request_id': self.request_id}) + '\n').encode())

    def read_ipc(self):
        self.buffer += bytes(self.socket.readAll())
        while b'\n' in self.buffer:
            line, self.buffer = self.buffer.split(b'\n', 1)
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if 'request_id' in message:
                tag = self.pending.pop(message['request_id'], None)
                if message.get('error') != 'success':
                    self.log.append('mpv: ' + str(message.get('error')))
                elif isinstance(tag, tuple) and tag[0] == 'audio-folder':
                    if self.media_loaded and tag[1] == self.audio_generation:
                        commands, self.managed_audio = audio_folder_commands(
                            tag[2], message.get('data') or [], self.managed_audio)
                        for command in commands:
                            self.command(command)
                elif tag == 'drops' and not self.props.get('pause'):
                    if self.guard.sample(message.get('data')) and self.mode.currentText() == 'Автоматически':
                        names = list(PROFILES)
                        self.active_profile = names[max(0, names.index(self.active_profile) - 1)]
                        self.reason.setText(self.active_profile + ': устойчивые пропуски кадров; снижена нагрузка.')
                        self.send_profile()
            if message.get('event') == 'property-change':
                self.props[message['name']] = message.get('data')
                if message['name'] in ('width', 'height', 'container-fps'):
                    self.update_media_profile()
                if message['name'] in ('track-list', 'aid'):
                    self.update_audio_tracks()
            if message.get('event') in ('seek', 'playback-restart'):
                self.guard.reset()
            if message.get('event') == 'file-loaded':
                self.media_loaded = True
                self.managed_audio.clear()
                self.status.setText('Воспроизведение')
                self.sync_audio_folder()
                self.guard.reset()
            if message.get('event') == 'end-file':
                self.media_loaded = False
                self.audio_generation += 1
                self.status.setText('Конец видео: ' + message.get('reason', 'unknown') + ' ' + message.get('file_error', ''))

    def update_media_profile(self):
        signature = tuple(self.props.get(key) for key in ('width', 'height', 'container-fps'))
        if not signature[0] or not signature[1] or signature == self.media_signature:
            return
        self.media_signature = signature
        if self.mode.currentText() == 'Автоматически':
            self.select_profile()
            self.send_profile()

    def send_profile(self):
        options = dict(PROFILES[self.active_profile])
        manual = self.mode.currentText() != 'Автоматически'
        if manual and self.scaler.currentText() != 'Из профиля':
            options['scale'] = self.scaler.currentText()
        for key, value in options.items():
            self.command(['set_property', key, value])
        shaders = [self.shader.text().strip()] if manual and self.shader.text().strip() else []
        self.command(['set_property', 'glsl-shaders', shaders])

    def apply_settings(self):
        shader = self.shader.text().strip()
        if self.mode.currentText() != 'Автоматически' and shader and not Path(shader).is_file():
            QMessageBox.warning(self, 'Шейдер', 'Файл шейдера не найден.')
            return
        self.select_profile()
        self.guard.reset()
        self.send_profile()
        self.command(['set_property', 'hwdec', self.hwdec.currentText()])
        self.command(['set_property', 'video-sync', 'display-resample' if self.smooth.isChecked() else 'audio'])
        self.command(['set_property', 'interpolation', self.smooth.isChecked()])
        self.sync_audio_folder()
        try:
            save_settings(dict(mode=self.mode.currentText(), scaler=self.scaler.currentText(),
                hwdec=self.hwdec.currentText(), shader=shader, smooth=self.smooth.isChecked(),
                audio_folder=self.audio_folder.text().strip()))
        except OSError as exc:
            self.status.setText('Не удалось сохранить настройки: ' + str(exc))

    def tick(self):
        if self.proc.state() == QProcess.ProcessState.Running and not self.ready:
            self.socket.abort()
            self.socket.connectToServer(self.endpoint)
        if self.ready:
            self.command(['get_property', 'frame-drop-count'], 'drops')
        pos = self.props.get('time-pos') or 0
        duration = self.props.get('duration') or 0
        self.position.setText(f'{int(pos)//60:02}:{int(pos)%60:02} / {int(duration)//60:02}:{int(duration)%60:02}')
        if not self.timeline.isSliderDown():
            self.timeline.setValue(int(1000 * pos / duration) if duration else 0)

    def seek(self, seconds):
        self.guard.reset()
        self.command(['seek', seconds, 'relative'])

    def seek_absolute(self):
        self.guard.reset()
        self.command(['seek', self.timeline.value() / 10, 'absolute-percent'])

    def read_log(self):
        self.log.append(bytes(self.proc.readAllStandardOutput()).decode(errors='replace').strip())

    def finished(self, code, _status):
        self.ready = False
        self.media_loaded = False
        self.audio_generation += 1
        self.managed_audio.clear()
        self.socket.abort()
        self.pending.clear()
        self.props.clear()
        self.update_audio_tracks()
        self.status.setText(f'mpv завершён (код {code}).')

    def closeEvent(self, event):
        self.timer.stop()
        self.command(['quit'])
        self.socket.flush()
        if not self.proc.waitForFinished(1500):
            self.proc.terminate()
            if not self.proc.waitForFinished(1000):
                self.proc.kill()
                self.proc.waitForFinished(1000)
        self.socket.abort()
        self.temp.cleanup()
        event.accept()


def main():
    app = QApplication(sys.argv)
    window = Player()
    window.show()
    if len(sys.argv) > 1:
        window.source.setText(sys.argv[1])
        QTimer.singleShot(0, window.open_media)
    sys.exit(app.exec())
