#!/usr/bin/env python

import os
import os.path
import pathlib
import winreg
from distutils.cmd import Command
from distutils.core import setup
from os import scandir
from subprocess import call, check_output, CalledProcessError, DEVNULL

import txclib.commands
import txclib.utils
from babel.messages import frontend as babel


def get_setup_dir():
    """Return an absolute path to setup.py directory
    Useful to find project files no matter where setup.py is invoked.
    """
    try:
        return get_setup_dir.setup_base_dir
    except AttributeError:
        get_setup_dir.setup_base_dir = pathlib.Path(__file__).absolute().parent
    return get_setup_dir.setup_base_dir


def get_version():
    with open(get_setup_dir() / 'cddagl' / 'VERSION') as version_file:
        return version_file.read().strip()


def log(msg):
    print(msg)


class ExtendedCommand(Command):
    def run_other_command(self, command_name, **kwargs):
        """Runs another command with specified parameters."""
        command = self.reinitialize_command(command_name)

        vars(command).update(**kwargs)
        command.ensure_finalized()

        self.announce(f'running {command_name}', 2)
        command.run()

        return command


class FreezeWithPyInstaller(ExtendedCommand):
    description = 'Build CDDAGL with PyInstaller'
    user_options = [
        ('debug=', None, 'Specify if we are using a debug build with PyInstaller.')
    ]

    def initialize_options(self):
        self.debug = None
        self.locale_dir = os.path.join('cddagl', 'locale')

    def finalize_options(self):
        pass

    def run(self):
        # -w for no console and -c for console
        window_mode = '-c' if bool(self.debug) else '-w'

        makespec_call = [
            'pyi-makespec', '-D', window_mode, '--noupx',
            '--hidden-import=lxml.cssselect',
            '--hidden-import=babel.numbers',
            'cddagl\launcher.py',
            '-i', r'cddagl\resources\launcher.ico'
        ]

        # Check if we have Windows Kits 10 path and add ucrt path
        windowskit_path = None
        try:
            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE,
                                 r'SOFTWARE\Microsoft\Windows Kits\Installed Roots',
                                 access=winreg.KEY_READ | winreg.KEY_WOW64_32KEY)
            value = winreg.QueryValueEx(key, 'KitsRoot10')
            windowskit_path = value[0]
            winreg.CloseKey(key)
        except OSError:
            pass

        if windowskit_path is not None:
            ucrt_path = windowskit_path + 'Redist\\ucrt\\DLLs\\x86\\'
            makespec_call.extend(('-p', ucrt_path))

        # Additional files
        added_files = [
            ('alembic', 'alembic'),
            ('data', 'data'),
            ('cddagl/resources', 'cddagl/resources'),
            ('cddagl/VERSION', 'cddagl')
        ]

        added_binaries = []

        # Let's find and add unrar if available
        try:
            unrar_path = check_output(['where', 'unrar.exe'], stderr=DEVNULL)
            unrar_path = unrar_path.strip().decode('cp437')
            added_files.append((unrar_path, '.'))
        except CalledProcessError:
            log("'unrar.exe' couldn't be found.")

        # Add mo files for localization
        self.run_other_command('compile_catalog')

        if os.path.isdir(self.locale_dir):
            for entry in scandir(self.locale_dir):
                if entry.is_dir():
                    mo_path = os.path.join(entry.path, 'LC_MESSAGES', 'cddagl.mo')
                    if os.path.isfile(mo_path):
                        mo_dir = os.path.dirname(mo_path).replace('\\', '/')
                        mo_path = mo_path.replace('\\', '/')
                        added_files.append((mo_path, mo_dir))

        # Include additional files
        for src, dest in added_files:
            src_dest = src + ';' + dest
            makespec_call.extend(('--add-data', src_dest))

        for src, dest in added_binaries:
            src_dest = src + ';' + dest
            makespec_call.extend(('--add-binary', src_dest))

        # Add debug build
        if bool(self.debug):
            makespec_call.extend(('-d', 'all'))

        # Call the makespec util
        log(f'executing {makespec_call}')
        call(makespec_call)

        # Call pyinstaller
        pyinstaller_call = ['pyinstaller']

        # Add debug info for PyInstaller
        if bool(self.debug):
            pyinstaller_call.append('--clean')
            pyinstaller_call.extend(('--log-level', 'DEBUG'))

        pyinstaller_call.append('--noconfirm')
        pyinstaller_call.append('launcher.spec')

        log(f'executing {pyinstaller_call}')
        call(pyinstaller_call)


class CreateInnoSetupInstaller(ExtendedCommand):
    description = 'Creates a Windows Installer for the project'
    user_options = [
        ('compiler=', None, 'Specify the path to Inno Setup Compiler (Compil32.exe).'),
    ]

    def initialize_options(self):
        self.compiler = r'C:\Program Files (x86)\Inno Setup 6\Compil32.exe'

    def finalize_options(self):
        if not pathlib.Path(self.compiler).exists():
            raise Exception('Inno Setup Compiler (Compil32.exe) not found.')

    def run(self):
        #### Make sure we are running Inno Setup from the project directory
        os.chdir(get_setup_dir())

        self.run_other_command('freeze')
        inno_call = [self.compiler, '/cc', 'launcher.iss']
        log(f'executing {inno_call}')
        call(inno_call)


class TransifexPull(ExtendedCommand):
    description = 'Download translated strings from Transifex service.'
    user_options = [
        ('reviewed-only', None, 'Download only reviewed translations.'),
    ]

    def initialize_options(self):
        self.reviewed_only = False

    def finalize_options(self):
        pass

    def run(self):
        ### Make sure we are running the commands from project directory
        os.chdir(get_setup_dir())

        args = ['--no-interactive', '--all', '--force']
        if self.reviewed_only:
            args.extend(['--mode', 'onlyreviewed'])
        else:
            args.extend(['--mode', 'onlytranslated'])

        txclib.utils.DISABLE_COLORS = True
        txclib.commands.cmd_pull(args, get_setup_dir())
        self.run_other_command('compile_catalog')


class TransifexPush(Command):
    description = 'Push untranslated project strings to Transifex service.'
    user_options = [
        ('push-translations', None, 'Push translations too, this will try to merge translations.'),
    ]

    def initialize_options(self):
        self.push_translations = False

    def finalize_options(self):
        pass

    def run(self):
        ### Make sure we are running the commands from project directory
        os.chdir(get_setup_dir())

        args = ['--no-interactive', '--source', '--force']
        if self.push_translations:
            args.append('--translations')

        txclib.utils.DISABLE_COLORS = True
        txclib.commands.cmd_push(args, get_setup_dir())


class TransifexExtractPush(ExtendedCommand):
    description = 'Extract all translatable strings and push them to Transifex service.'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        ### Make sure we are running the commands from project directory
        os.chdir(get_setup_dir())
        self.run_other_command('extract_messages')
        self.run_other_command('translation_push')


class ExtractMessagesWithDefaults(babel.extract_messages):

    def initialize_options(self):
        super().initialize_options()
        self.output_file = r'cddagl\locale\cddagl.pot'
        self.mapping_file = r'cddagl\locale\mapping.cfg'


class UpdateCatalogWithDefaults(babel.update_catalog):

    def initialize_options(self):
        super().initialize_options()
        self.input_file = r'cddagl\locale\cddagl.pot'
        self.output_dir = r'cddagl\locale'
        self.domain = 'cddagl'


class CompileCatalogWithDefauls(babel.compile_catalog):

    def initialize_options(self):
        super().initialize_options()
        self.directory = r'cddagl\locale'
        self.domain = 'cddagl'
        self.use_fuzzy = True


class ExtractUpdateMessages(ExtendedCommand):
    description = 'Extract all project strings that require translation and update catalogs.'
    user_options = []

    def initialize_options(self):
        pass

    def finalize_options(self):
        pass

    def run(self):
        self.run_other_command('extract_messages')
        self.run_other_command('update_catalog')


setup(
    name='cddagl',
    version=get_version(),
    description=('A Cataclysm: Dark Days Ahead launcher with additional features'),
    author='Rémy Roy',
    author_email='remyroy@remyroy.com',
    url='https://github.com/remyroy/CDDA-Game-Launcher',
    packages=['cddagl'],
    package_data={'cddagl': ['VERSION']},
    cmdclass={
        ### freeze & installer commands
        'freeze': FreezeWithPyInstaller,
        'create_installer': CreateInnoSetupInstaller,
        ### babel commands
        'extract_messages': ExtractMessagesWithDefaults,
        'compile_catalog': CompileCatalogWithDefauls,
        'init_catalog': babel.init_catalog,
        'update_catalog': UpdateCatalogWithDefaults,
        'exup_messages': ExtractUpdateMessages,
        ### transifex related commands
        'translation_push': TransifexPush,
        'translation_expush': TransifexExtractPush,
        'translation_pull': TransifexPull,
    },
)
