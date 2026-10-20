"""Guided Linux bootstrap using the pinned native package manager and builders.

This installs application dependencies only after confirmation. It never starts
a service, reads evidence, calls a model, or changes a running installation.
"""
import argparse
import json
import os
from pathlib import Path
import subprocess
import sys

from deploy_forsic import initialize


BUILD = (
    "from pathlib import Path; "
    "from hermes_cli.source_build import source_build_env, prepare_source_dependencies, "
    "build_source_tui, build_source_web; "
    "p=Path('upstream/hermes').resolve(); e=source_build_env(explicit=True); "
    "prepare_source_dependencies(p, ('ui-tui','web'), env=e, explicit=True); "
    "build_source_tui(p, env=e); build_source_web(p, env=e)"
)


def steps(python):
    return [
        ('실행 도구 준비', [python, '-m', 'pm.cli', 'install', 'python', 'node', 'npm', 'ffmpeg', 'ripgrep']),
        ('웹과 조사 의존성 준비', [python, '-m', 'pm.cli', 'install', '--extra', 'web']),
        ('화면 빌드', [python, 'run.py', '-c', BUILD]),
        ('화면 준비 확인', [python, 'run.py', 'deploy_forsic.py', 'frontend-preflight']),
    ]


def environment(root):
    return {**os.environ, 'HERMES_HOME': str(root / 'state/hermes'),
            'HERMES_RUNTIME_DIR': str(root / 'state/tools'),
            'HERMES_BUNDLED_SKILLS': str(root / 'config/bundled-skills'),
            'HERMES_OPTIONAL_SKILLS': str(root / 'config/bundled-skills'),
            'PYTHONPATH': os.pathsep.join((str(root), str(root / 'upstream/hermes'))),
            'PYTHONDONTWRITEBYTECODE': '1'}


def check_target(root, resume):
    if sys.platform != 'linux':
        raise ValueError('이 설치 도우미는 Linux용입니다. 다른 운영체제는 DEPLOYMENT.txt를 확인하세요.')
    if hasattr(os, 'geteuid') and os.geteuid() == 0:
        raise ValueError('일반 사용자 계정으로 실행해주세요. 관리자 계정으로 서비스를 준비하지 않습니다.')
    state = root / 'state'
    if not state.exists() and not state.is_symlink():
        if resume:
            raise ValueError('이어갈 설치가 없습니다. --resume 없이 실행해주세요.')
        return
    if not resume:
        raise ValueError('기존 실행 자료가 있습니다. 미완료 초기 설치만 --resume으로 이어갈 수 있습니다.')
    marker = state / 'deployment/bootstrap.json'
    cfg = state / 'hermes/config.yaml'
    try:
        pending = json.loads(marker.read_text())
        settings = json.loads(cfg.read_text())['plugins']['entries']['forsic']['settings']
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError('미완료 초기 설치인지 확인할 수 없습니다. 기존 설정은 변경하지 않았습니다.') from None
    if (pending.get('status') != 'preparing' or settings.get('setup_completed') is not False
            or (state / 'hermes/state.db').exists() or (state / 'intake').exists()):
        raise ValueError('이미 실행된 설치는 자동 변경하지 않습니다. DEPLOYMENT.txt의 업데이트 절차를 따르세요.')


def prepare(root, python, *, resume=False, runner=subprocess.run):
    check_target(root, resume)
    if not resume:
        initialize(root, 'http://127.0.0.1:11434/v1', '', Path(python), wizard=True)
    marker = root / 'state/deployment/bootstrap.json'
    marker.write_text(json.dumps({'status': 'preparing'}, ensure_ascii=False) + '\n')
    marker.chmod(0o600)
    for index, (label, command) in enumerate(steps(python), 1):
        print(f'[{index}/4] {label}', flush=True)
        runner(command, cwd=root, env=environment(root), check=True)
    marker.write_text(json.dumps({'status': 'ready'}, ensure_ascii=False) + '\n')
    print('\n설치 준비가 끝났습니다. python3 run.py로 시작한 뒤 브라우저에서 초기 설정을 완료하세요.')
    print('접속 주소: http://127.0.0.1:8860/  ·  원격 서버에서는 로컬 터널로 설정할 수 있습니다.')
    print('이미지·분석 터미널 도구 설치는 ANALYSIS_TOOLS.txt를 확인하세요.')


def main(argv=None):
    parser = argparse.ArgumentParser(description='포식이 초기 설치: 실행 도구와 화면을 준비합니다.')
    parser.add_argument('--yes', action='store_true', help='의존성 다운로드와 설치를 확인 없이 진행')
    parser.add_argument('--plan', action='store_true', help='설치 순서만 표시; 파일 변경과 다운로드 없음')
    parser.add_argument('--resume', action='store_true', help='실행한 적 없는 미완료 초기 설치 이어가기')
    args = parser.parse_args(argv)
    root = Path(__file__).resolve().parent
    print('포식이 설치 준비\n1. 새 로컬 설정\n2. 실행 도구·웹 의존성\n3. 화면 빌드\n4. 브라우저 초기 설정')
    if args.plan:
        print('모델 연결·자료 폴더·접속 방식은 브라우저에서 선택합니다. 조사나 서비스는 자동 시작하지 않습니다.')
        return 0
    try:
        check_target(root, args.resume)
        if not args.yes:
            if not sys.stdin.isatty():
                raise ValueError('다운로드·설치를 진행하려면 대화형 터미널 또는 --yes를 사용해주세요.')
            answer = input('실행 도구와 의존성을 다운로드하고 설치할까요? [y/N] ').strip().lower()
            if answer not in ('y', 'yes'):
                print('취소했습니다. 파일을 변경하지 않았습니다.')
                return 0
        prepare(root, sys.executable, resume=args.resume)
    except subprocess.CalledProcessError:
        print('설치 단계가 완료되지 않았습니다. 위 진단을 확인한 뒤 --resume으로 이어갈 수 있습니다.', file=sys.stderr)
        return 1
    except (ValueError, OSError) as exc:
        # OS errors can contain local paths, but never credential values.
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
