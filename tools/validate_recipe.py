#!/usr/bin/env python3
"""damda 레시피 검증기.

이 파일이 통과시킨 레시피는 **전 사용자에게 한 번에** 나간다 (adr/011).
단계적 롤아웃이 없다는 것이 이 검증기가 존재하는 이유다.

사용법:
    tools/validate_recipe.py v1/recipes.json
    tools/validate_recipe.py v1/recipes.json --previous /tmp/prev.json

의존성 없음 — 파이썬 표준 라이브러리만 쓴다. 장애 상황에서 `pip install` 을
기다리게 만들지 않기 위해서다. 정규식의 ECMAScript 문법 확인에만 `node` 를
쓰는데, 없으면 그 검사만 건너뛰고 경고한다 (CI 러너에는 항상 있다).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile

# 앱이 이해하는 최대 schemaVersion.
# 원본: app/lib/data/resolver/recipe/recipe.dart 의 kSupportedRecipeSchemaVersion.
# 이 값을 올리는 것은 **앱을 먼저 릴리스한 뒤**의 일이다. 먼저 올리면 구버전 앱이
# 레시피를 통째로 무시하고 번들 기본값으로 돌아간다 (docs/04 안전 규칙, QA RC-04).
MAX_SCHEMA_VERSION = 1

# YouTube 게이트 (docs/09 §8.1 과 같은 패턴). 레시피는 "원격 배포 설정값" 축이다.
YOUTUBE_PATTERN = re.compile(r"youtube|youtu\.be|ytimg|ytdl|yt-dlp|유튜브", re.I)

# 실행 코드 성격 필드 (docs/09 §1.5 레드라인).
#
# 키를 camelCase/snake_case 단위로 쪼갠 **낱말**과 대조한다. 부분 문자열로 보면
# statusMap 의 "map", canonicalTemplate 의 "template" 같은 정상 키가 걸린다.
BANNED_KEY_WORDS = {
    # 코드 그 자체
    "script", "scripts", "scripting", "code", "codes", "bytecode", "dex",
    "wasm", "js", "javascript", "dart", "lua", "python", "ruby", "binary",
    "plugin", "plugins", "module", "modules", "require", "import",
    # 평가·실행
    "eval", "evaluate", "exec", "execute", "execution", "expr", "expression",
    "expressions", "interpret", "interpreter", "compile", "vm", "sandbox",
    "invoke", "call", "apply", "run", "runner", "command", "cmd", "shell",
    "bash", "sh", "hook", "hooks", "callback", "handler", "listener",
    # 함수·추상화
    "fn", "func", "function", "functions", "lambda", "closure", "macro",
    "procedure", "method",
    # 제어 흐름 (튜링 완전성으로 가는 문)
    "if", "else", "elif", "then", "unless", "when", "cond", "condition",
    "conditions", "conditional", "switch", "case", "branch", "branches",
    "goto", "jump", "loop", "loops", "while", "until", "repeat", "iterate",
    "iteration", "foreach", "recurse", "recursion",
}

# 값 쪽 레드라인. 문자열 값이 이런 스킴을 담으면 거절한다.
BANNED_VALUE_PATTERNS = [
    (re.compile(r"javascript\s*:", re.I), "javascript: 스킴"),
    (re.compile(r"data\s*:\s*text/html", re.I), "data:text/html 스킴"),
    (re.compile(r"\bfunction\s*\(", re.I), "함수 리터럴"),
    (re.compile(r"=>"), "화살표 함수"),
    (re.compile(r"\beval\s*\(", re.I), "eval 호출"),
]

# 반드시 있어야 하는 것. 없으면 앱이 그 소스를 해석하지 못한다.
REQUIRED_TOP_KEYS = ["schemaVersion", "revision", "userAgent", "sources"]
REQUIRED_SOURCES = ["x"]  # TikTok removed 2026-08-17 (app adr/013)
REQUIRED_SOURCE_KEYS = [
    "enabled",
    "hosts",
    "postIdPattern",
    "canonicalTemplate",
    "requestUrlTemplate",
    "requestHeaders",
    "extract",
    "fields",
    "options",
]
REQUIRED_EXTRACT_KEYS = ["kind"]


class Report:
    def __init__(self) -> None:
        self.errors: list[str] = []
        self.warnings: list[str] = []
        self.checks: list[str] = []

    def ok(self, name: str) -> None:
        self.checks.append(name)

    def fail(self, name: str, detail: str) -> None:
        self.errors.append(f"{name}: {detail}")

    def warn(self, detail: str) -> None:
        self.warnings.append(detail)


def split_words(key: str) -> list[str]:
    """`statusMap` → ['status', 'map'], `on_run` → ['on', 'run']."""
    spaced = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", key)
    return [w.lower() for w in re.split(r"[^A-Za-z0-9]+|\s+", spaced) if w]


def walk(node, path: str = "$"):
    """(경로, 키, 값) 을 전부 훑는다."""
    if isinstance(node, dict):
        for key, value in node.items():
            child = f"{path}.{key}"
            yield child, key, value
            yield from walk(value, child)
    elif isinstance(node, list):
        for index, value in enumerate(node):
            child = f"{path}[{index}]"
            yield child, None, value
            yield from walk(value, child)


# ---------------------------------------------------------------- 개별 검사


def check_json(raw: str, report: Report):
    """1. JSON 으로 파싱되는가."""
    try:
        data = json.loads(raw)
    except ValueError as error:
        report.fail("JSON 파싱", str(error))
        return None
    if not isinstance(data, dict):
        report.fail("JSON 파싱", "최상위가 객체가 아니다")
        return None
    report.ok("JSON 파싱")
    return data


def check_schema_version(data: dict, report: Report) -> None:
    """2. 앱이 아는 값 이하인가."""
    value = data.get("schemaVersion")
    if not isinstance(value, int) or isinstance(value, bool):
        report.fail("schemaVersion", f"정수가 아니다 ({value!r})")
        return
    if value < 1:
        report.fail("schemaVersion", f"1 미만 ({value})")
        return
    if value > MAX_SCHEMA_VERSION:
        report.fail(
            "schemaVersion",
            f"{value} > 앱이 아는 {MAX_SCHEMA_VERSION} — 이 레시피는 통째로 "
            "무시되고 앱은 번들 기본값으로 돌아간다 (docs/04 안전 규칙)",
        )
        return
    report.ok(f"schemaVersion ({value} ≤ {MAX_SCHEMA_VERSION})")


def check_revision(data: dict, previous: dict | None, report: Report) -> None:
    """3. 정수이고 이전 커밋보다 증가했는가."""
    value = data.get("revision")
    if not isinstance(value, int) or isinstance(value, bool):
        report.fail("revision", f"정수가 아니다 ({value!r})")
        return
    if value < 0:
        report.fail("revision", f"음수 ({value})")
        return
    if previous is None:
        report.ok(f"revision ({value}, 비교할 이전본 없음)")
        return
    old = previous.get("revision")
    if not isinstance(old, int) or isinstance(old, bool):
        report.warn(f"이전본의 revision 이 정수가 아니다 ({old!r}) — 비교 생략")
        report.ok(f"revision ({value})")
        return
    if value <= old:
        # 내용이 그대로면 올릴 이유가 없다. revision 은 "무엇이 배포됐는지"를
        # 가리키는 표지이므로, 같은 내용에 다른 번호를 붙이면 오히려 진단이
        # 헷갈린다. 워크플로·README 만 고친 커밋이 여기서 걸리던 오탐을 막는다.
        if data == previous:
            report.ok(f"revision ({value}, 내용 변경 없음)")
            return
        report.fail(
            "revision",
            f"{value} ≤ 이전 {old} 인데 내용이 바뀌었다 — 올리지 않으면 진단 "
            "로그가 어떤 레시피에서 난 실패인지 말해 주지 못한다 "
            "(docs/11 §6.4 3단계)",
        )
        return
    report.ok(f"revision ({old} → {value})")


def check_required(data: dict, report: Report) -> None:
    """4. 필수 키가 있는가."""
    missing = [k for k in REQUIRED_TOP_KEYS if k not in data]
    if missing:
        report.fail("필수 키", f"최상위에 없음: {', '.join(missing)}")
        return
    if not isinstance(data.get("userAgent"), str) or not data["userAgent"]:
        report.fail("필수 키", "userAgent 가 비어 있거나 문자열이 아니다")
        return
    sources = data.get("sources")
    if not isinstance(sources, dict):
        report.fail("필수 키", "sources 가 객체가 아니다")
        return
    for name in REQUIRED_SOURCES:
        source = sources.get(name)
        if not isinstance(source, dict):
            report.fail("필수 키", f"sources.{name} 이 없거나 객체가 아니다")
            return
        gone = [k for k in REQUIRED_SOURCE_KEYS if k not in source]
        if gone:
            report.fail("필수 키", f"sources.{name} 에 없음: {', '.join(gone)}")
            return
        hosts = source.get("hosts")
        if not isinstance(hosts, list) or not hosts:
            report.fail("필수 키", f"sources.{name}.hosts 가 비어 있다")
            return
        if not all(isinstance(h, str) and h for h in hosts):
            report.fail("필수 키", f"sources.{name}.hosts 에 문자열이 아닌 항목")
            return
        extract = source.get("extract")
        if not isinstance(extract, dict):
            report.fail("필수 키", f"sources.{name}.extract 가 객체가 아니다")
            return
        gone = [k for k in REQUIRED_EXTRACT_KEYS if k not in extract]
        if gone:
            report.fail(
                "필수 키", f"sources.{name}.extract 에 없음: {', '.join(gone)}"
            )
            return
        if not isinstance(source.get("fields"), dict):
            report.fail("필수 키", f"sources.{name}.fields 가 객체가 아니다")
            return
    report.ok("필수 키 (sources.x 의 hosts · extract 등)")


def check_no_executable_fields(data: dict, report: Report) -> None:
    """5. 실행 코드 성격 필드가 없는가 (docs/09 §1.5 레드라인)."""
    hits = []
    for path, key, value in walk(data):
        if key is not None:
            for word in split_words(key):
                if word in BANNED_KEY_WORDS:
                    hits.append(f"{path} (금지 낱말 '{word}')")
                    break
        if isinstance(value, str):
            for pattern, label in BANNED_VALUE_PATTERNS:
                if pattern.search(value):
                    hits.append(f"{path} (값에 {label})")
                    break
    if hits:
        report.fail(
            "실행 코드 성격 필드",
            "이 경계를 넘는 순간 앱 삭제 사유다 (docs/09 §1.5) → "
            + "; ".join(hits[:8]),
        )
        return
    report.ok("실행 코드 성격 필드 0건")


def collect_regexes(data: dict) -> list[tuple[str, str]]:
    """정규식으로 쓰이는 문자열 전부. (경로, 패턴)."""
    found: list[tuple[str, str]] = []
    for path, key, value in walk(data):
        # extract.patterns 아래는 값이 전부 정규식이다.
        in_patterns = ".patterns." in path
        pattern_key = key is not None and (
            key.endswith("Pattern") or key.endswith("Patterns")
        )
        if isinstance(value, str) and (in_patterns or pattern_key):
            found.append((path, value))
        elif isinstance(value, list) and pattern_key:
            for index, item in enumerate(value):
                if isinstance(item, str):
                    found.append((f"{path}[{index}]", item))
    return found


def check_regexes(data: dict, report: Report) -> None:
    """6. 정규식이 실제로 컴파일되는가."""
    regexes = collect_regexes(data)
    if not regexes:
        report.warn("정규식으로 판정된 필드가 하나도 없다 — 스키마가 바뀌었나?")
        return
    broken = []
    for path, pattern in regexes:
        try:
            re.compile(pattern)
        except re.error as error:
            broken.append(f"{path}: {error}")
    if broken:
        report.fail("정규식 컴파일 (python)", "; ".join(broken))
        return

    # Dart 의 RegExp 는 ECMAScript 문법이다. 파이썬이 받아들이는 것을 Dart 가
    # 거절하는 경우가 있으므로(예: (?P<name>...)), node 로 한 번 더 본다.
    ecma_broken = check_regexes_ecmascript(regexes, report)
    if ecma_broken:
        report.fail("정규식 컴파일 (ECMAScript)", "; ".join(ecma_broken))
        return
    report.ok(f"정규식 컴파일 ({len(regexes)}개)")


def check_regexes_ecmascript(
    regexes: list[tuple[str, str]], report: Report
) -> list[str]:
    script = """
const items = JSON.parse(require('fs').readFileSync(process.argv[1], 'utf8'));
const bad = [];
for (const [path, pattern] of items) {
  try { new RegExp(pattern); } catch (e) { bad.push(path + ': ' + e.message); }
}
process.stdout.write(JSON.stringify(bad));
"""
    handle, payload = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as f:
            json.dump(regexes, f)
        result = subprocess.run(
            ["node", "-e", script, payload],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError):
        report.warn("node 가 없어 ECMAScript 문법 확인을 건너뛰었다")
        return []
    finally:
        os.unlink(payload)
    if result.returncode != 0:
        report.warn(f"node 정규식 확인 실패: {result.stderr.strip()[:200]}")
        return []
    try:
        return json.loads(result.stdout or "[]")
    except ValueError:
        report.warn("node 정규식 확인 출력이 JSON 이 아니다")
        return []


def check_youtube(raw: str, report: Report) -> None:
    """7. YouTube 게이트 (docs/09 §8.1)."""
    hits = sorted({m.group(0) for m in YOUTUBE_PATTERN.finditer(raw)})
    if hits:
        report.fail(
            "YouTube 게이트",
            f"검출: {', '.join(hits)} — 원격 경로로 정책 위반 문자열이 들어가면 "
            "앱 소스가 깨끗해도 소용없다 (docs/09 §8.1 원격 배포 설정값 축)",
        )
        return
    report.ok("YouTube 게이트 (0건)")


def load_whitelist(path: str) -> list[str]:
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.split("#", 1)[0].strip()
            if line:
                entries.append(line.lower())
    return entries


def host_allowed(host: str, whitelist: list[str]) -> bool:
    host = host.lower()
    return any(host == e or host.endswith("." + e) for e in whitelist)


def check_hosts(data: dict, whitelist_path: str, report: Report) -> None:
    """8. hosts 가 앱의 번들 화이트리스트 밖으로 나가지 않는가."""
    try:
        whitelist = load_whitelist(whitelist_path)
    except OSError as error:
        report.fail("호스트 화이트리스트", f"목록을 읽을 수 없다: {error}")
        return
    if not whitelist:
        report.fail("호스트 화이트리스트", "목록이 비어 있다")
        return

    outside = []
    sources = data.get("sources")
    if not isinstance(sources, dict):
        return  # 필수 키 검사가 이미 잡았다.
    for name, source in sources.items():
        if not isinstance(source, dict):
            continue
        for field in ("hosts", "shortLinkHosts"):
            for host in source.get(field) or []:
                if isinstance(host, str) and not host_allowed(host, whitelist):
                    outside.append(f"sources.{name}.{field}: {host}")
    if outside:
        report.fail(
            "호스트 화이트리스트",
            "번들 화이트리스트 밖 → 앱은 이 호스트를 **요청하지 않는다**. "
            "확장은 앱 업데이트 사안이다 (docs/09 §1.5) → " + "; ".join(outside),
        )
        return
    report.ok("호스트 화이트리스트 (전부 번들 목록 안)")


def check_urls_https(data: dict, report: Report) -> None:
    """덤: 레시피 안의 URL 이 전부 https 인가. 앱이 cleartext 를 거절한다."""
    bad = [
        f"{path}: {value}"
        for path, _key, value in walk(data)
        if isinstance(value, str) and value.lower().startswith("http://")
    ]
    if bad:
        report.fail("URL 스킴", "https 가 아니다 → " + "; ".join(bad))
        return
    report.ok("URL 스킴 (전부 https)")


# ---------------------------------------------------------------- 진입점


def validate(path: str, previous_path: str | None, whitelist_path: str) -> Report:
    report = Report()
    with open(path, encoding="utf-8") as f:
        raw = f.read()

    data = check_json(raw, report)
    if data is None:
        return report  # 파싱이 안 되면 나머지는 의미가 없다.

    previous = None
    if previous_path:
        try:
            with open(previous_path, encoding="utf-8") as f:
                loaded = json.load(f)
            previous = loaded if isinstance(loaded, dict) else None
        except (OSError, ValueError) as error:
            report.warn(f"이전본을 읽지 못해 revision 비교를 건너뛴다: {error}")

    check_schema_version(data, report)
    check_revision(data, previous, report)
    check_required(data, report)
    check_no_executable_fields(data, report)
    check_regexes(data, report)
    check_youtube(raw, report)
    check_hosts(data, whitelist_path, report)
    check_urls_https(data, report)
    return report


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    parser = argparse.ArgumentParser(description="damda 레시피 검증기")
    parser.add_argument("recipe", help="검증할 레시피 JSON")
    parser.add_argument(
        "--previous",
        help="이전 커밋의 같은 파일. revision 증가 확인에만 쓴다",
    )
    parser.add_argument(
        "--whitelist",
        default=os.path.join(here, "app_host_whitelist.txt"),
        help="앱 번들 호스트 화이트리스트 사본",
    )
    args = parser.parse_args()

    report = validate(args.recipe, args.previous, args.whitelist)

    for name in report.checks:
        print(f"  PASS  {name}")
    for detail in report.warnings:
        print(f"  WARN  {detail}")
    for detail in report.errors:
        print(f"  FAIL  {detail}")

    if report.errors:
        print(f"\nFAIL: {args.recipe} — {len(report.errors)}건. 배포하지 않는다.")
        return 1
    print(f"\nPASS: {args.recipe} — 검사 {len(report.checks)}건 통과.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
