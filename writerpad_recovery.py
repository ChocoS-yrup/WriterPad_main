"""Qt-independent recovery commands and a double-click console entry point."""
import argparse
import json
import sys

from project_archive import (
    create_archive, locate_projects, restore_archive, verify_archive,
)


def interactive():
    print("작가님 힘내세요 · 독립 복구")
    print("앱을 시작하지 않고 저장된 자료만 읽습니다. 원본·기존 목적지는 덮어쓰지 않습니다.")
    print("1 저장 위치 확인  2 자료 사본 보존  3 백업 검증  4 새 독립 폴더에 복원")
    action = input("번호: ").strip()
    if action == "1":
        return {"projects": locate_projects(input("프로그램 자료 루트 폴더: ").strip().strip('"'))}
    source = input("원본 작품 폴더(2) 또는 백업 폴더(3·4): ").strip().strip('"')
    if action == "3":
        result = verify_archive(source)
        return {"verified": True, "files": len(result["files"]), "portable_v1": result["portable_v1"]}
    if action not in {"2", "4"}:
        raise ValueError("지원하는 번호를 선택하세요.")
    target = input("별도 위치의 새 목적지 폴더 경로: ").strip().strip('"')
    if action == "2":
        create_archive(source, target, preservation=True)
        return {"preserved": True, "destination": target, "notice": "자료 사본입니다. 작품 구조 복구 완료 판정은 아닙니다."}
    return restore_archive(source, target)


def main(argv=None):
    parser = argparse.ArgumentParser(description="앱·Qt·로그인 없이 원고 보존, 백업 검증, 독립 복원")
    parser.add_argument("--interactive", action="store_true")
    commands = parser.add_subparsers(dest="command")
    locate = commands.add_parser("locate", help="자료 루트의 작품 위치 조회")
    locate.add_argument("root")
    for name in ("backup", "preserve", "restore"):
        command = commands.add_parser(name)
        command.add_argument("source")
        command.add_argument("destination")
    verify = commands.add_parser("verify")
    verify.add_argument("package")
    args = parser.parse_args(argv)
    try:
        if args.interactive:
            result = interactive()
        elif args.command == "locate":
            result = {"projects": locate_projects(args.root)}
        elif args.command in {"backup", "preserve"}:
            manifest = create_archive(args.source, args.destination, preservation=args.command == "preserve")
            result = {"created": True, "files": len(manifest["files"]), "portable_v1": manifest["portable_v1"]}
        elif args.command == "verify":
            manifest = verify_archive(args.package)
            result = {"verified": True, "files": len(manifest["files"]), "portable_v1": manifest["portable_v1"]}
        elif args.command == "restore":
            result = restore_archive(args.source, args.destination)
        else:
            parser.print_help()
            return 0
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except Exception as error:
        # Deliberately omit parser excerpts, exception bodies and tracebacks:
        # a damaged settings/identity file may contain private manuscript text.
        print("복구 작업을 완료하지 못했습니다 (" + type(error).__name__ + "). 원본과 기존 목적지는 보존했습니다. 경로·권한·백업 무결성을 확인하세요.", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
